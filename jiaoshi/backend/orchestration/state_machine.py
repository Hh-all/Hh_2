# -*- coding: utf-8 -*-
"""
状态机引擎 (StateMachine)
=========================
定义试卷生成全流程的状态和迁移规则，记录每一步迁移到 JSONL 日志。

状态:
  IDLE         — 空闲，等待请求
  PARSING      — 正在解析参数
  RETRIEVING   — 正在检索题目
  GENERATING   — 正在生成/补充题目
  FORMATTING   — 正在格式化试卷
  COMPLETED    — 成功完成
  FAILED       — 失败（可重试或不可恢复）
  RETRY        — 重试中

事件:
  PARSE_SUCCESS       — 参数解析成功
  PARSE_FAILED        — 参数解析失败
  RETRIEVE_SUCCESS    — 检索成功（全部覆盖）
  RETRIEVE_PARTIAL    — 检索部分成功（有覆盖缺口）
  RETRIEVE_FAILED     — 检索完全失败
  GENERATE_SUCCESS    — 生成成功
  GENERATE_FAILED     — 生成失败
  FORMAT_SUCCESS      — 格式化成功
  FORMAT_FAILED       — 格式化失败
  RETRY_LIMIT_EXCEEDED — 重试次数超过上限
  RESET               — 重置状态

迁移规则（状态机转换表）:
  IDLE       + PARSE_SUCCESS       → PARSING
  PARSING    + PARSE_SUCCESS       → RETRIEVING
  PARSING    + PARSE_FAILED        → FAILED
  RETRIEVING + RETRIEVE_SUCCESS    → GENERATING
  RETRIEVING + RETRIEVE_PARTIAL    → GENERATING  (带警告)
  RETRIEVING + RETRIEVE_FAILED     → RETRY
  GENERATING + GENERATE_SUCCESS    → FORMATTING
  GENERATING + GENERATE_FAILED     → RETRY
  FORMATTING + FORMAT_SUCCESS      → COMPLETED
  FORMATTING + FORMAT_FAILED       → RETRY
  RETRY      + (any retry event)   → (前一状态) 或 FAILED
  RETRY      + RETRY_LIMIT_EXCEEDED → FAILED
  FAILED     + RESET               → IDLE

日志格式 (logs/state_machine.log):
  {"workflow_id": "wf_...", "session_id": "...", "timestamp": "...",
   "from_state": "IDLE", "to_state": "PARSING", "event": "PARSE_SUCCESS",
   "metadata": {"agent": "ParameterParser", "elapsed_ms": 12.5, ...}}
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("orchestration.state_machine")

# ---------------------------------------------------------------------------
# 状态与事件枚举
# ---------------------------------------------------------------------------

class State(Enum):
    IDLE = "IDLE"
    PARSING = "PARSING"
    RETRIEVING = "RETRIEVING"
    GENERATING = "GENERATING"
    FORMATTING = "FORMATTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY = "RETRY"


class Event(Enum):
    PARSE_SUCCESS = "PARSE_SUCCESS"
    PARSE_FAILED = "PARSE_FAILED"
    RETRIEVE_SUCCESS = "RETRIEVE_SUCCESS"
    RETRIEVE_PARTIAL = "RETRIEVE_PARTIAL"
    RETRIEVE_FAILED = "RETRIEVE_FAILED"
    GENERATE_SUCCESS = "GENERATE_SUCCESS"
    GENERATE_FAILED = "GENERATE_FAILED"
    FORMAT_SUCCESS = "FORMAT_SUCCESS"
    FORMAT_FAILED = "FORMAT_FAILED"
    RETRY_LIMIT_EXCEEDED = "RETRY_LIMIT_EXCEEDED"
    RESET = "RESET"


# ---------------------------------------------------------------------------
# 状态迁移表
# ---------------------------------------------------------------------------

# 格式: (from_state, event) → to_state
TRANSITION_TABLE: dict[tuple[State, Event], State] = {
    # 正常路径
    (State.IDLE,       Event.PARSE_SUCCESS):     State.PARSING,
    (State.PARSING,    Event.PARSE_SUCCESS):     State.RETRIEVING,
    (State.RETRIEVING, Event.RETRIEVE_SUCCESS):  State.GENERATING,
    (State.RETRIEVING, Event.RETRIEVE_PARTIAL):  State.GENERATING,
    (State.GENERATING, Event.GENERATE_SUCCESS):  State.FORMATTING,
    (State.FORMATTING, Event.FORMAT_SUCCESS):    State.COMPLETED,

    # 失败路径
    (State.PARSING,    Event.PARSE_FAILED):      State.FAILED,
    (State.RETRIEVING, Event.RETRIEVE_FAILED):   State.RETRY,
    (State.GENERATING, Event.GENERATE_FAILED):   State.RETRY,
    (State.FORMATTING, Event.FORMAT_FAILED):     State.RETRY,

    # 重试回路
    (State.RETRY, Event.PARSE_SUCCESS):     State.PARSING,
    (State.RETRY, Event.RETRIEVE_SUCCESS):   State.RETRIEVING,
    (State.RETRY, Event.RETRIEVE_PARTIAL):   State.RETRIEVING,
    (State.RETRY, Event.GENERATE_SUCCESS):   State.GENERATING,
    (State.RETRY, Event.FORMAT_SUCCESS):     State.FORMATTING,
    (State.RETRY, Event.RETRIEVE_FAILED):    State.RETRY,
    (State.RETRY, Event.GENERATE_FAILED):    State.RETRY,
    (State.RETRY, Event.FORMAT_FAILED):      State.RETRY,
    (State.RETRY, Event.RETRY_LIMIT_EXCEEDED): State.FAILED,

    # 重置
    (State.FAILED, Event.RESET): State.IDLE,
    (State.COMPLETED, Event.RESET): State.IDLE,
}

# 哪些事件会增加 retry 计数
RETRY_TRIGGER_EVENTS = {
    Event.RETRIEVE_FAILED,
    Event.GENERATE_FAILED,
    Event.FORMAT_FAILED,
}

# 哪些事件不改变当前状态（用于记录 metadata-only 事件）
PASSTHROUGH_EVENTS = set()

# 有效的前一状态集合（用于校验）
VALID_PREV_STATES_FOR_RETRY = {State.RETRIEVING, State.GENERATING, State.FORMATTING}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

STATE_LOG_PATH = ROOT / "logs" / "state_machine.log"

# 文件 writer（线程安全通过 append mode）
_state_log_writer = None


def _get_log_writer():
    global _state_log_writer
    if _state_log_writer is None:
        STATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _state_log_writer = open(STATE_LOG_PATH, "a", encoding="utf-8")
    return _state_log_writer


def _write_log_entry(entry: dict):
    """原子写入一条 JSONL 日志"""
    writer = _get_log_writer()
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    writer.write(line)
    writer.flush()


# ---------------------------------------------------------------------------
# StateMachine 类
# ---------------------------------------------------------------------------

class StateMachine:
    """
    试卷生成流程状态机。

    用法:
        sm = StateMachine("wf_20260614_1030_abc")
        sm.transition(Event.PARSE_SUCCESS, metadata={"agent": "ParameterParser", "elapsed_ms": 12.5})
        print(sm.current_state)  # State.PARSING
        print(sm.history)        # [Transition(...), ...]
    """

    def __init__(self, workflow_id: str = None, session_id: str = None,
                 max_retries: int = 3):
        """
        参数:
            workflow_id: 工作流 ID（全局唯一）
            session_id:  会话 ID（用于分组）
            max_retries: 最大重试次数
        """
        self.workflow_id = workflow_id or f"wf_{int(time.time()*1000)}"
        self.session_id = session_id or self.workflow_id
        self.max_retries = max_retries

        self.current_state = State.IDLE
        self.previous_state = None
        self.retry_count = 0
        self.history: list[dict] = []
        self.start_time = datetime.now(timezone.utc)

        # 记录创建
        self._record("INIT", State.IDLE, "SYSTEM_INIT", {
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "max_retries": self.max_retries,
        })

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------

    def transition(self, event: Event, metadata: dict = None) -> tuple[bool, State]:
        """
        执行一次状态迁移。

        参数:
            event:    触发事件
            metadata: 附加元数据（如 agent 名称、耗时、结果摘要）

        返回:
            (迁移是否合法, 迁移后的状态)
        """
        metadata = metadata or {}

        # 查找迁移规则
        to_state = TRANSITION_TABLE.get((self.current_state, event))

        if to_state is None:
            # 非法迁移
            self._record(self.current_state, self.current_state, f"ILLEGAL_{event.value}", {
                "error": f"无法从 {self.current_state.value} 通过 {event.value} 迁移",
                **metadata,
            })
            logger.warning(
                f"[{self.workflow_id}] 非法迁移: {self.current_state.value} "
                f"+ {event.value} (无匹配规则)"
            )
            return False, self.current_state

        # 更新重试计数
        if event in RETRY_TRIGGER_EVENTS:
            self.retry_count += 1
            metadata["retry_count"] = self.retry_count

        # 检查重试上限
        if self.retry_count > self.max_retries and to_state == State.RETRY:
            # 强制转为 FAILED
            to_state = State.FAILED
            event = Event.RETRY_LIMIT_EXCEEDED
            metadata["reason"] = f"重试 {self.retry_count} 次超过上限 {self.max_retries}"

        # 执行迁移
        from_state = self.current_state
        self.previous_state = from_state
        self.current_state = to_state

        # 记录
        self._record(from_state, to_state, event.value, metadata)

        # 重试后回到前一状态的逻辑（RETRY 状态需要知道回哪里）
        if to_state == State.RETRY:
            metadata["retry_from"] = from_state.value

        logger.info(
            f"[{self.workflow_id}] {from_state.value} "
            f"──{event.value}──▶ {to_state.value}"
            f"{' (retry=' + str(self.retry_count) + ')' if self.retry_count > 0 else ''}"
        )

        return True, to_state

    def can_transition(self, event: Event) -> bool:
        """检查给定事件是否可以触发有效迁移"""
        return (self.current_state, event) in TRANSITION_TABLE

    def reset(self):
        """重置状态机"""
        self.transition(Event.RESET, {"reason": "manual_reset"})

    # ------------------------------------------------------------------
    # 便捷迁移方法（语义化 API）
    # ------------------------------------------------------------------

    def on_parse_success(self, agent: str = "ParameterParser", **meta):
        return self.transition(Event.PARSE_SUCCESS, {"agent": agent, **meta})

    def on_parse_failed(self, reason: str = "", **meta):
        return self.transition(Event.PARSE_FAILED, {"reason": reason, **meta})

    def on_retrieve_success(self, count: int = 0, **meta):
        return self.transition(Event.RETRIEVE_SUCCESS, {"retrieved_count": count, **meta})

    def on_retrieve_partial(self, count: int = 0, gaps: int = 0, **meta):
        return self.transition(Event.RETRIEVE_PARTIAL, {
            "retrieved_count": count, "coverage_gaps": gaps, **meta,
        })

    def on_retrieve_failed(self, reason: str = "", **meta):
        return self.transition(Event.RETRIEVE_FAILED, {"reason": reason, **meta})

    def on_generate_success(self, count: int = 0, mode: str = "", **meta):
        return self.transition(Event.GENERATE_SUCCESS, {
            "generated_count": count, "mode": mode, **meta,
        })

    def on_generate_failed(self, reason: str = "", **meta):
        return self.transition(Event.GENERATE_FAILED, {"reason": reason, **meta})

    def on_format_success(self, paper_title: str = "", question_count: int = 0, **meta):
        return self.transition(Event.FORMAT_SUCCESS, {
            "paper_title": paper_title, "question_count": question_count, **meta,
        })

    def on_format_failed(self, reason: str = "", **meta):
        return self.transition(Event.FORMAT_FAILED, {"reason": reason, **meta})

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def _record(self, from_state: State, to_state: State, event_name: str,
                metadata: dict):
        """记录一次状态迁移"""
        entry = {
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_state": from_state.value if isinstance(from_state, State) else str(from_state),
            "to_state": to_state.value if isinstance(to_state, State) else str(to_state),
            "event": event_name,
            "retry_count": self.retry_count,
            "elapsed_s": round(
                (datetime.now(timezone.utc) - self.start_time).total_seconds(), 3
            ),
            "metadata": metadata,
        }
        self.history.append(entry)
        _write_log_entry(entry)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """是否处于终态"""
        return self.current_state in (State.COMPLETED, State.FAILED)

    def is_active(self) -> bool:
        """是否处于活跃状态（未完成且未失败）"""
        return self.current_state not in (State.IDLE, State.COMPLETED, State.FAILED)

    def summary(self) -> dict:
        """返回状态摘要"""
        return {
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "current_state": self.current_state.value,
            "previous_state": self.previous_state.value if self.previous_state else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "is_terminal": self.is_terminal(),
            "is_active": self.is_active(),
            "transition_count": len(self.history),
            "start_time": self.start_time.isoformat(),
            "elapsed_s": round(
                (datetime.now(timezone.utc) - self.start_time).total_seconds(), 1
            ),
        }

    def get_visual_trace(self) -> str:
        """生成 ASCII 状态迁移可视化字符串"""
        if not self.history:
            return "(无迁移记录)"

        lines = []
        for i, entry in enumerate(self.history):
            arrow = " --" + entry["event"] + "--> "
            lines.append(
                f"  {entry['from_state']:12s} {arrow:30s} {entry['to_state']:12s}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 全局会话管理器
# ---------------------------------------------------------------------------

class SessionManager:
    """管理多个并发 StateMachine 实例"""

    def __init__(self):
        self._sessions: dict[str, StateMachine] = {}

    def create(self, workflow_id: str = None, session_id: str = None, **kwargs) -> StateMachine:
        """创建新的状态机会话"""
        sm = StateMachine(workflow_id, session_id, **kwargs)
        self._sessions[sm.session_id] = sm
        # 清理旧会话（保留最近 100 个）
        if len(self._sessions) > 100:
            oldest = sorted(self._sessions.keys())[0]
            del self._sessions[oldest]
        return sm

    def get(self, session_id: str) -> Optional[StateMachine]:
        """获取会话"""
        return self._sessions.get(session_id)

    def get_active(self) -> list[StateMachine]:
        """获取所有活跃会话"""
        return [sm for sm in self._sessions.values() if sm.is_active()]

    def list_all(self) -> list[dict]:
        """列出所有会话摘要"""
        return [sm.summary() for sm in self._sessions.values()]

    def count(self) -> int:
        return len(self._sessions)

    def cleanup_completed(self):
        """清理已完成的会话"""
        self._sessions = {
            sid: sm for sid, sm in self._sessions.items()
            if sm.is_active()
        }


# 全局单例
_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    print("=" * 60)
    print("状态机测试")
    print("=" * 60)

    # 正常流程
    sm = StateMachine("test_normal")
    print(f"\n初始状态: {sm.current_state.value}")

    assert sm.transition(Event.PARSE_SUCCESS, {"agent": "ParameterParser", "elapsed_ms": 12.5})[0]
    print(f"解析完成 → {sm.current_state.value}")

    assert sm.transition(Event.RETRIEVE_SUCCESS, {"retrieved_count": 15})[0]
    print(f"检索完成 → {sm.current_state.value}")

    assert sm.transition(Event.GENERATE_SUCCESS, {"generated_count": 5, "mode": "fallback"})[0]
    print(f"生成完成 → {sm.current_state.value}")

    assert sm.transition(Event.FORMAT_SUCCESS, {"paper_title": "测试卷", "question_count": 20})[0]
    print(f"格式化完成 → {sm.current_state.value}")

    assert sm.is_terminal()
    print(f"终态: {sm.summary()['current_state']}")

    # 迁移历史
    print(f"\n迁移历史 ({len(sm.history)} 步):")
    for entry in sm.history:
        print(f"  {entry['from_state']:12s} ──{entry['event']:20s}──▶ {entry['to_state']:12s}")

    # 重试流程
    print(f"\n--- 重试流程 ---")
    sm2 = StateMachine("test_retry")
    sm2.transition(Event.PARSE_SUCCESS)
    sm2.transition(Event.RETRIEVE_FAILED, {"reason": "索引未就绪"})
    print(f"检索失败 → RETRY (retry={sm2.retry_count})")
    sm2.transition(Event.RETRIEVE_SUCCESS, {"retrieved_count": 8})
    print(f"重试成功 → {sm2.current_state.value}")

    # 重试上限
    print(f"\n--- 重试上限 ---")
    sm3 = StateMachine("test_limit", max_retries=2)
    sm3.transition(Event.PARSE_SUCCESS)
    sm3.transition(Event.RETRIEVE_FAILED)  # retry=1
    sm3.transition(Event.RETRIEVE_FAILED)  # retry=2
    sm3.transition(Event.RETRIEVE_FAILED)  # retry=3 > max=2 → FAILED
    print(f"超过上限: state={sm3.current_state.value}, retry={sm3.retry_count}")

    print(f"\n日志文件: {STATE_LOG_PATH}")
    print(f"日志大小: {STATE_LOG_PATH.stat().st_size if STATE_LOG_PATH.exists() else 0} bytes")
