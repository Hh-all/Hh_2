# -*- coding: utf-8 -*-
"""
总控编排器 (Orchestrator)
========================
实现 WORKFLOW.md 中定义的状态机，按序调用各 Sub-Agent。
所有 Agent 间通过文件传递中间结果，不依赖对话上下文。

状态机:
  IDLE → VALIDATING → RETRIEVING → GENERATING → FORMATTING → DONE
              │            │            │            │
              ▼            ▼            ▼            ▼
         INVALID       COVERAGE      GEN_FAILED    FORMAT_ERROR
         _REQUEST      _GAP          (bypass)      (retry)
              │                          │
              ▼                          ▼
          返回错误                   FATAL (3次失败)

用法:
  python backend/orchestrator.py --subject math --grade 初三 --region beijing --points "一元二次方程,二次函数" --count 10

  # 从 JSON 文件读取参数
  python backend/orchestrator.py --config request.json

  # 编程调用
  from backend.orchestrator import Orchestrator
  orch = Orchestrator()
  result = orch.run({"subject": "math", "grade": "grade_9", "region": "beijing"})
"""

import json
import os
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from backend.agents.parameter_parser_agent import ParameterParserAgent
from backend.agents.question_retriever_agent import QuestionRetrieverAgent
from backend.agents.qa_generator_agent import QAGeneratorAgent
from backend.agents.paper_formatter_agent import PaperFormatterAgent
from backend.orchestration.state_machine import StateMachine, State, Event, get_session_manager
from backend.guardrails.guardrail_checker import GuardrailChecker, get_checker

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

ORCH_LOG_PATH = LOG_DIR / "orchestration.log"

# 文件日志 handler
_file_handler = logging.FileHandler(ORCH_LOG_PATH, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_file_handler.setLevel(logging.DEBUG)

logger = logging.getLogger("orchestrator")
logger.addHandler(_file_handler)
logger.setLevel(logging.DEBUG)

# 控制台日志
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"))
_console.setLevel(logging.INFO)
logger.addHandler(_console)

# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------

class OrchestratorState(Enum):
    IDLE = "IDLE"
    VALIDATING_REQUEST = "VALIDATING_REQUEST"
    RETRIEVING = "RETRIEVING"
    GENERATING = "GENERATING"
    FORMATTING = "FORMATTING"
    DONE = "DONE"
    INVALID_REQUEST = "INVALID_REQUEST"
    COVERAGE_GAP = "COVERAGE_GAP"
    FATAL = "FATAL"


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    总控编排器，实现 WORKFLOW.md 状态机。

    特性:
      - 按序调用 Agent（ParameterParser → QuestionRetriever → QAGenerator → PaperFormatter）
      - 异常路径：Agent 失败时根据重试策略重试或进入 FATAL
      - 每一步通过文件传递中间结果
      - 完整执行轨迹写入 logs/orchestration.log
      - 每步状态迁移写入 logs/state_machine.log (JSONL)
      - 所有活跃会话通过 SessionManager 管理
    """

    MAX_RETRIES = 3

    def __init__(self):
        self.state = OrchestratorState.IDLE
        self.retry_counts = {
            "validating": 0,
            "retrieving": 0,
            "generating": 0,
            "formatting": 0,
        }
        self.trace: list[dict] = []
        self.start_time = None
        self.workflow_id = None
        # 精细状态机
        self._sm: Optional[StateMachine] = None
        # 护栏检查器
        self._guardrail = get_checker()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, raw_input: dict) -> dict:
        """
        执行完整的试卷生成流程。

        参数:
            raw_input: 用户请求 dict

        返回:
            {
                "success": True/False,
                "state": "DONE" | "FATAL" | ...,
                "output_file": "..." (仅在 DONE 时),
                "trace": [...]
            }
        """
        self.start_time = datetime.now(timezone.utc)
        self.workflow_id = f"wf_{self.start_time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(3).hex()}"
        self.state = OrchestratorState.IDLE

        # 初始化精细状态机
        self._sm = get_session_manager().create(
            workflow_id=self.workflow_id,
            max_retries=self.MAX_RETRIES,
        )

        logger.info("=" * 60)
        logger.info(f"工作流启动: {self.workflow_id}")
        logger.info(f"请求参数: {json.dumps(raw_input, ensure_ascii=False)[:200]}")
        logger.info("=" * 60)

        self._record_trace("IDLE", "工作流启动")

        # 护栏前置检查：参数合法性
        if self._guardrail.enabled:
            ok, _ = self._guardrail.check_before_action("parse", {
                "subject": raw_input.get("subject", ""),
                "grade": raw_input.get("grade", ""),
                "region": raw_input.get("region", ""),
            })
            if not ok:
                logger.error("[护栏] 前置检查失败，参数不合法")
                self._sm.transition(Event.PARSE_FAILED, {"reason": "guardrail_blocked"})
                return self._finalize(False, "GUARDRAIL_BLOCKED", ["护栏前置检查拦截"])

        # ---- Phase 1: 参数验证 ----
        self.state = OrchestratorState.VALIDATING_REQUEST
        self._sm.transition(Event.PARSE_SUCCESS, {"phase": "VALIDATING_REQUEST"})
        parse_result = self._run_phase(
            "VALIDATING_REQUEST",
            self._do_validate,
            raw_input,
        )

        if not parse_result.get("success"):
            self.state = OrchestratorState.INVALID_REQUEST
            self._sm.on_parse_failed(reason=str(parse_result.get("errors", [])))
            self._record_trace("INVALID_REQUEST", f"参数验证失败: {parse_result.get('errors', [])}")
            return self._finalize(False, "VALIDATION_FAILED", parse_result.get("errors", []))

        self._sm.on_parse_success(
            agent="ParameterParser",
            subject=parse_result.get("request", {}).get("subject", ""),
            elapsed_ms=parse_result.get("elapsed_ms", 0),
        )

        # ---- Phase 2: 题目检索 ----
        self.state = OrchestratorState.RETRIEVING
        # 护栏前置检查
        if self._guardrail.enabled:
            ok, _ = self._guardrail.check_before_action("retrieve", {
                "top_k": raw_input.get("question_count", 5) * 3,
            })
            if not ok:
                logger.warning("[护栏] 检索参数超限，已自动截断")

        retrieve_result = self._run_phase(
            "RETRIEVING",
            self._do_retrieve,
        )
        if retrieve_result.get("has_gaps"):
            self._sm.on_retrieve_partial(
                count=retrieve_result.get("total_retrieved", 0),
                gaps=len(retrieve_result.get("coverage_gaps", [])),
            )
        else:
            self._sm.on_retrieve_success(
                count=retrieve_result.get("total_retrieved", 0),
            )

        # ---- Phase 3: 题目生成（补充检索不足）----
        self.state = OrchestratorState.GENERATING
        generate_result = self._run_phase(
            "GENERATING",
            self._do_generate,
        )
        if generate_result.get("success"):
            self._sm.on_generate_success(
                count=generate_result.get("generated_count", 0),
                mode=generate_result.get("mode", ""),
            )
        else:
            self._sm.on_generate_failed(
                reason=generate_result.get("error", "unknown"),
            )

        # ---- Phase 4: 试卷格式化 ----
        self.state = OrchestratorState.FORMATTING
        format_result = self._run_phase(
            "FORMATTING",
            self._do_format,
        )
        if format_result.get("success"):
            paper_meta = format_result.get("paper_meta", {})
            self._sm.on_format_success(
                paper_title=paper_meta.get("title", ""),
                question_count=paper_meta.get("question_count", 0),
            )
            # 护栏后置检查：验证最终产物
            if self._guardrail.enabled:
                ok, violations = self._guardrail.check_after_result(
                    format_result, action="format"
                )
                if not ok:
                    logger.error(f"[护栏] 后置检查失败: {len(violations)} 项违规")
                    for v in violations:
                        if v.severity == "BLOCKER":
                            logger.error(f"  [BLOCKER] {v.rule_id}: {v.message}")
                    self._record_trace("GUARDRAIL_VIOLATION",
                                       f"后置检查 {len(violations)} 项违规")
        else:
            self._sm.on_format_failed(
                reason=format_result.get("error", str(format_result.get("validation", {}))),
            )

        # ---- 完成 ----
        self.state = OrchestratorState.DONE
        self._record_trace("DONE", f"输出: {format_result.get('output_file', '')}")
        self._record_trace("STATE_SUMMARY", self._sm.summary())

        return self._finalize(True, "DONE", None, format_result)

    # ------------------------------------------------------------------
    # 各阶段执行
    # ------------------------------------------------------------------

    def _run_phase(self, phase_name: str, func, *args) -> dict:
        """执行一个阶段，含重试逻辑"""
        retry_key = phase_name.lower().replace("_request", "").replace("ing", "").replace("validat", "validating")

        for attempt in range(self.MAX_RETRIES + 1):
            self.retry_counts[retry_key] = attempt
            self._record_trace(phase_name, f"开始 (第{attempt+1}次尝试)")

            try:
                result = func(*args)
            except Exception as e:
                logger.error(f"{phase_name} 异常: {e}", exc_info=True)
                self._record_trace(f"{phase_name}_ERROR", str(e))
                if attempt < self.MAX_RETRIES:
                    time.sleep(1 * (attempt + 1))
                    continue
                else:
                    self.state = OrchestratorState.FATAL
                    self._record_trace("FATAL", f"{phase_name} 重试{self.MAX_RETRIES}次后仍失败: {e}")
                    return {"success": False, "error": str(e)}

            if result.get("success"):
                self._record_trace(phase_name, f"完成")
                return result

            # 非致命失败（如覆盖缺口），可以继续
            if phase_name in ("RETRIEVING", "GENERATING"):
                logger.warning(f"{phase_name} 非致命: {result.get('error', result.get('coverage_gaps', []))}")
                self._record_trace(f"{phase_name}_WARNING", str(result.get('coverage_gaps', result.get('error', ''))))
                return result

            # 致命失败，重试
            if attempt < self.MAX_RETRIES:
                logger.warning(f"{phase_name} 失败，{1}s 后重试 ({attempt+1}/{self.MAX_RETRIES})")
                # 记录状态机：失败 → 重试
                fail_event_map = {
                    "RETRIEVING": Event.RETRIEVE_FAILED,
                    "GENERATING": Event.GENERATE_FAILED,
                    "FORMATTING": Event.FORMAT_FAILED,
                }
                event = fail_event_map.get(phase_name)
                if event and self._sm:
                    self._sm.transition(event, {"attempt": attempt + 1, "reason": str(result.get("error", ""))[:100]})
                time.sleep(1 * (attempt + 1))
            else:
                self.state = OrchestratorState.FATAL
                if self._sm:
                    self._sm.transition(Event.RETRY_LIMIT_EXCEEDED, {"phase": phase_name, "attempts": self.MAX_RETRIES + 1})
                self._record_trace("FATAL", f"{phase_name} 重试{self.MAX_RETRIES}次后仍失败")
                return result

        return {"success": False}

    def _do_validate(self, raw_input: dict) -> dict:
        """Phase 1: 参数解析"""
        agent = ParameterParserAgent()
        return agent.parse(raw_input)

    def _do_retrieve(self) -> dict:
        """Phase 2: 题目检索"""
        agent = QuestionRetrieverAgent()
        return agent.retrieve()

    def _do_generate(self) -> dict:
        """Phase 3: 题目生成"""
        agent = QAGeneratorAgent()
        return agent.generate()

    def _do_format(self) -> dict:
        """Phase 4: 试卷格式化"""
        agent = PaperFormatterAgent()
        return agent.format()

    # ------------------------------------------------------------------
    # 轨迹与日志
    # ------------------------------------------------------------------

    def _record_trace(self, state_name: str, message: str):
        """记录执行轨迹"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state_name,
            "message": message,
        }
        self.trace.append(entry)
        logger.info(f"[{state_name}] {message}")

    def _finalize(self, success: bool, final_state: str, errors: list = None,
                  format_result: dict = None) -> dict:
        """封装最终返回结果"""
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds() if self.start_time else 0

        result = {
            "success": success,
            "state": final_state,
            "workflow_id": self.workflow_id,
            "elapsed_seconds": round(elapsed, 2),
            "retry_counts": dict(self.retry_counts),
            "trace_count": len(self.trace),
        }

        if errors:
            result["errors"] = errors

        if format_result and format_result.get("success"):
            result["output_file"] = format_result.get("output_file")
            result["paper_meta"] = format_result.get("paper_meta", {})

        # 写入轨迹文件
        trace_path = LOG_DIR / f"trace_{self.workflow_id}.json"
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump({"workflow_id": self.workflow_id, "trace": self.trace,
                        "result": result}, f, ensure_ascii=False, indent=2)

        logger.info("=" * 60)
        logger.info(f"工作流结束: {self.workflow_id} → {final_state} (耗时 {elapsed:.1f}s)")
        logger.info(f"轨迹文件: {trace_path}")
        logger.info("=" * 60)

        return result

    # ------------------------------------------------------------------
    # CLI
    # ------------------------------------------------------------------

    @classmethod
    def from_cli(cls, args: argparse.Namespace) -> dict:
        """从命令行参数构建请求并执行"""
        if args.config:
            with open(args.config, "r", encoding="utf-8") as f:
                raw_input = json.load(f)
        else:
            raw_input = {}
            if args.subject:
                raw_input["subject"] = args.subject
            if args.grade:
                raw_input["grade"] = args.grade
            if args.region:
                raw_input["region"] = args.region
            if args.points:
                raw_input["knowledge_points"] = [p.strip() for p in args.points.split(",")]
            if args.count:
                raw_input["question_count"] = args.count
            if args.difficulty:
                raw_input["difficulty"] = args.difficulty
            if args.title:
                raw_input["title"] = args.title

        if not raw_input:
            logger.error("请提供 --config 或至少 --subject")
            return {"success": False, "error": "缺少参数"}

        orch = cls()
        return orch.run(raw_input)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="智能试卷生成系统 - 总控编排器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python backend/orchestrator.py --subject math --grade 初三 --region beijing \\
      --points "一元二次方程,二次函数" --count 10

  python backend/orchestrator.py --config request.json
        """,
    )
    parser.add_argument("--config", type=str, help="请求参数 JSON 文件路径")
    parser.add_argument("--subject", type=str, help="学科 (math/chinese/english/...)")
    parser.add_argument("--grade", type=str, help="年级 (grade_7 / 初三 / ...)")
    parser.add_argument("--region", type=str, help="地域 (beijing/shanghai/guangdong)")
    parser.add_argument("--points", type=str, help="知识点，逗号分隔")
    parser.add_argument("--count", type=int, default=10, help="题目数量")
    parser.add_argument("--difficulty", type=int, default=3, help="难度 1-5")
    parser.add_argument("--title", type=str, help="试卷标题")
    args = parser.parse_args()

    result = Orchestrator.from_cli(args)

    print("\n" + "=" * 50)
    print("编排结果")
    print("=" * 50)
    print(f"  状态: {result.get('state', 'UNKNOWN')}")
    print(f"  成功: {result.get('success', False)}")
    print(f"  耗时: {result.get('elapsed_seconds', 0):.1f}s")
    if result.get("output_file"):
        print(f"  输出: {result['output_file']}")
    if result.get("paper_meta"):
        print(f"  试卷: {result['paper_meta']}")
    if result.get("errors"):
        print(f"  错误: {result['errors']}")
    print(f"  重试: {result.get('retry_counts', {})}")
    print("=" * 50)

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
