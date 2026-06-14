# -*- coding: utf-8 -*-
"""
LangSmith 追踪器 (Tracer)
=========================
提供全流程可观测性：RAG 检索 → LLM 调用 → 试卷生成。

三种追踪模式：
  1. @traceable 装饰器模式（LangSmith 原生，推荐）
  2. RunTree 显式追踪（需要精确控制时使用）
  3. 回退模式（未配置 LANGCHAIN_API_KEY 时自动降级为本地日志）

环境变量（按优先级）：
  LANGCHAIN_API_KEY     — LangSmith API 密钥
  LANGCHAIN_PROJECT     — 项目名称（默认: "exam-paper-generator"）
  LANGSMITH_TRACING     — 是否启用追踪（默认: "true"）
  LANGSMITH_ENDPOINT    — API 端点（默认: https://api.smith.langchain.com）

用法:
    from backend.tracing import trace_rag_retrieval, trace_llm_call, trace_paper_generation

    @trace_rag_retrieval
    def search(query, filters, top_k=5):
        ...

    @trace_paper_generation
    def generate_paper(params):
        ...
"""

import os
import sys
import time
import json
import functools
import logging
from pathlib import Path
from typing import Optional, Callable
from contextlib import contextmanager

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("tracing")

# ---------------------------------------------------------------------------
# LangSmith 可用性检测
# ---------------------------------------------------------------------------

_LANGSMITH_AVAILABLE = False
_traceable = None
_run_helpers = None

try:
    from langsmith import traceable as _ls_traceable
    from langsmith import run_helpers as _ls_run_helpers
    _traceable = _ls_traceable
    _run_helpers = _ls_run_helpers
    _LANGSMITH_AVAILABLE = True
except ImportError:
    logger.info("langsmith 未安装，追踪功能将以本地日志模式运行")


def is_tracing_available() -> bool:
    """检测 LangSmith 追踪是否可用"""
    if not _LANGSMITH_AVAILABLE:
        return False
    api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if not api_key:
        # 检查 .env 文件
        env_path = ROOT / "backend" / ".env"
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            if "LANGCHAIN_API_KEY" in content:
                return True
        return False
    return True


def _get_project_name() -> str:
    """获取 LangSmith 项目名称"""
    return os.environ.get("LANGCHAIN_PROJECT", "exam-paper-generator")

# ---------------------------------------------------------------------------
# No-op 装饰器（LangSmith 不可用时的回退）
# ---------------------------------------------------------------------------

def _noop_trace(func: Callable = None, *, run_type: str = "chain", name: str = None,
                tags: list = None, metadata: dict = None):
    """无操作装饰器：LangSmith 不可用时返回原函数"""
    if func is None:
        return lambda f: f
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# LangSmith 装饰器封装
# ---------------------------------------------------------------------------

def trace_rag_retrieval(func: Callable = None, *, name: str = None, tags: list = None):
    """
    追踪 RAG 检索过程。

    装饰后的函数会自动记录：
      - inputs: 查询文本、过滤条件、top_k
      - outputs: 检索结果数量和文档 ID 列表
      - metadata: 检索耗时、地域过滤状态

    用法:
        @trace_rag_retrieval
        def search(query, filters=None, top_k=5, ...):
            ...
    """
    if not is_tracing_available():
        return _noop_trace(func)

    metadata = {
        "component": "rag_retrieval",
        "retriever_type": "chromadb",
    }
    decorator = _traceable(
        run_type="retriever",
        name=name or "RAG 检索",
        project_name=_get_project_name(),
        tags=tags or ["rag", "retrieval", "exam-paper"],
        metadata=metadata,
    )
    if func is None:
        return decorator
    return decorator(func)


def trace_llm_call(func: Callable = None, *, name: str = None, tags: list = None):
    """
    追踪 LLM 调用过程。

    装饰后的函数会自动记录：
      - inputs: prompt 文本（截取前 500 字符）
      - outputs: 生成的答案文本（截取前 500 字符）
      - metadata: 模型名称、token 用量、耗时

    用法:
        @trace_llm_call
        def call_claude(prompt, ...):
            ...
    """
    if not is_tracing_available():
        return _noop_trace(func)

    metadata = {
        "component": "llm_call",
    }
    decorator = _traceable(
        run_type="llm",
        name=name or "LLM 调用",
        project_name=_get_project_name(),
        tags=tags or ["llm", "generation", "exam-paper"],
        metadata=metadata,
    )
    if func is None:
        return decorator
    return decorator(func)


def trace_paper_generation(func: Callable = None, *, name: str = None, tags: list = None):
    """
    追踪完整试卷生成流程。

    装饰后的函数会自动记录：
      - inputs: 请求参数（科目、年级、地域、知识点等）
      - outputs: 试卷标题、题数、总分
      - metadata: 总耗时、各阶段耗时

    用法:
        @trace_paper_generation
        def run(paper_request):
            ...
    """
    if not is_tracing_available():
        return _noop_trace(func)

    metadata = {
        "component": "paper_generation",
    }
    decorator = _traceable(
        run_type="chain",
        name=name or "试卷生成",
        project_name=_get_project_name(),
        tags=tags or ["paper-generation", "orchestrator", "exam-paper"],
        metadata=metadata,
    )
    if func is None:
        return decorator
    return decorator(func)


# ---------------------------------------------------------------------------
# Tracer 类（手动追踪 + 本地回退日志）
# ---------------------------------------------------------------------------

class Tracer:
    """
    手动追踪器，支持显式创建 span 和本地回退日志。

    当 LangSmith 不可用时，所有 span 记录到本地的 traces/ 目录。
    """

    def __init__(self):
        self._enabled = is_tracing_available()
        self._trace_stack: list[dict] = []
        if not self._enabled:
            self._log_dir = ROOT / "logs" / "traces"
            self._log_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Tracer: 本地日志回退模式 (LangSmith 未配置)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @contextmanager
    def span(self, name: str, run_type: str = "chain", inputs: dict = None,
             tags: list = None, metadata: dict = None):
        """
        创建一个追踪 span。

        用法:
            with tracer.span("RAG检索", run_type="retriever", inputs={"query": "..."}) as span:
                results = do_search()
                span.set_outputs({"count": len(results)})
        """
        start_time = time.time()
        span_data = {
            "name": name,
            "run_type": run_type,
            "inputs": inputs or {},
            "tags": tags or [],
            "metadata": metadata or {},
            "start_time": start_time,
            "outputs": {},
            "parent_id": self._trace_stack[-1]["id"] if self._trace_stack else None,
        }

        if self._enabled:
            import uuid
            span_data["id"] = str(uuid.uuid4())[:8]
            span_data["_langsmith_span"] = self._start_langsmith_span(span_data)
            self._trace_stack.append(span_data)

            try:
                yield _SpanWrapper(span_data)
            finally:
                self._trace_stack.pop()
                self._end_langsmith_span(span_data)
        else:
            span_data["id"] = str(time.time()).replace(".", "")[-8:]
            self._trace_stack.append(span_data)

            try:
                yield _SpanWrapper(span_data)
            finally:
                self._trace_stack.pop()
                elapsed = time.time() - start_time
                span_data["elapsed_ms"] = round(elapsed * 1000, 1)
                self._log_local(span_data)
                logger.info(
                    f"[trace] {name} ({run_type}) — {span_data['elapsed_ms']}ms "
                    f"inputs={json.dumps(inputs, ensure_ascii=False)[:100]}"
                )

    def _start_langsmith_span(self, span_data: dict):
        """启动 LangSmith span"""
        try:
            from langsmith.run_trees import RunTree
            rt = RunTree(
                name=span_data["name"],
                run_type=span_data["run_type"],
                inputs=span_data["inputs"],
                tags=span_data["tags"],
                extra={"metadata": span_data["metadata"]},
                project_name=_get_project_name(),
            )
            if span_data["parent_id"]:
                parent = self._trace_stack[-1]["_langsmith_span"] if self._trace_stack else None
                if parent:
                    rt.parent_run = parent
            rt.post()
            return rt
        except Exception as e:
            logger.debug(f"LangSmith span 创建失败: {e}")
            return None

    def _end_langsmith_span(self, span_data: dict):
        """结束 LangSmith span"""
        rt = span_data.get("_langsmith_span")
        if rt:
            try:
                elapsed = time.time() - span_data["start_time"]
                rt.metadata.update({
                    "elapsed_ms": round(elapsed * 1000, 1),
                    "elapsed_s": round(elapsed, 3),
                })
                rt.end(outputs=span_data.get("outputs", {}))
                rt.patch()
            except Exception as e:
                logger.debug(f"LangSmith span 结束失败: {e}")

    def _log_local(self, span_data: dict):
        """记录追踪数据到本地日志文件"""
        now_str = time.strftime("%Y%m%d_%H%M%S")
        log_file = self._log_dir / f"trace_{now_str}.jsonl"
        record = {
            "name": span_data["name"],
            "run_type": span_data["run_type"],
            "inputs": str(span_data.get("inputs", {}))[:200],
            "outputs": str(span_data.get("outputs", {}))[:200],
            "elapsed_ms": span_data.get("elapsed_ms", 0),
            "tags": span_data.get("tags", []),
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class _SpanWrapper:
    """Span 包装器，提供 set_outputs / set_metadata 方法"""

    def __init__(self, data: dict):
        self._data = data
        self.output_count = 0

    def set_outputs(self, outputs: dict):
        self._data["outputs"] = outputs

    def set_metadata(self, key: str, value):
        self._data["metadata"][key] = value

    def add_tag(self, tag: str):
        if tag not in self._data["tags"]:
            self._data["tags"].append(tag)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_tracer_instance: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """获取全局 Tracer 实例"""
    global _tracer_instance
    if _tracer_instance is None:
        _tracer_instance = Tracer()
    return _tracer_instance


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def log_retrieval(query: str, results_count: int, elapsed_ms: float, filters: dict = None):
    """记录一次 RAG 检索事件（无需装饰器）"""
    tracer = get_tracer()
    if not tracer.enabled:
        logger.info(f"[RAG检索] query='{query[:50]}' → {results_count}条 ({elapsed_ms:.0f}ms)")
    # LangSmith 追踪由 @trace_rag_retrieval 装饰器自动处理


def log_llm_call(model: str, prompt_len: int, output_len: int, elapsed_ms: float):
    """记录一次 LLM 调用事件"""
    tracer = get_tracer()
    if not tracer.enabled:
        logger.info(f"[LLM调用] model={model} prompt={prompt_len}chars output={output_len}chars ({elapsed_ms:.0f}ms)")


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    print("=" * 60)
    print("LangSmith 追踪器状态")
    print("=" * 60)
    print(f"  LangSmith 可用: {_LANGSMITH_AVAILABLE}")
    print(f"  API Key 已配置: {is_tracing_available()}")
    print(f"  项目名称: {_get_project_name()}")
    print(f"  API 端点: {os.environ.get('LANGSMITH_ENDPOINT', 'https://api.smith.langchain.com')}")

    # 测试 Tracer 本地回退模式
    print("\n测试 Tracer (本地回退模式):")
    tracer = get_tracer()
    with tracer.span("测试RAG检索", run_type="retriever",
                     inputs={"query": "一元二次方程"}, tags=["test"]) as span:
        time.sleep(0.01)  # 模拟检索
        span.set_outputs({"count": 5, "top3_ids": ["q1", "q2", "q3"]})
    print("  Span 已完成（本地日志已写入 logs/traces/）")

    # 测试装饰器（在无 LangSmith 环境下为 no-op）
    @trace_rag_retrieval
    def mock_search(query):
        return {"results": 5, "query": query}

    result = mock_search("测试查询")
    print(f"  @trace_rag_retrieval 装饰器: result={result}")

    print("\n追踪器就绪。配置 LANGCHAIN_API_KEY 环境变量以启用 LangSmith 云端追踪。")
