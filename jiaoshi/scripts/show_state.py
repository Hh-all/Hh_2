# -*- coding: utf-8 -*-
"""
状态机仪表盘脚本 (show_state.py)
===============================
实时显示所有活跃会话的状态机运行情况。

数据来源:
  - logs/state_machine.log (JSONL)
  - SessionManager (内存中的活跃会话)

用法:
  # 查看实时状态（每 2 秒刷新）
  python scripts/show_state.py --watch

  # 单次快照
  python scripts/show_state.py

  # 查看指定工作流详情
  python scripts/show_state.py --workflow wf_20260614_1030_abc

  # 查看历史迁移轨迹
  python scripts/show_state.py --history
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

STATE_LOG = ROOT / "logs" / "state_machine.log"

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
C = {
    "R": "\033[0;31m",
    "G": "\033[0;32m",
    "Y": "\033[1;33m",
    "B": "\033[0;34m",
    "C": "\033[0;36m",
    "M": "\033[0;35m",
    "W": "\033[1;37m",
    "N": "\033[0m",
}

STATE_COLORS = {
    "IDLE": C["W"],
    "PARSING": C["C"],
    "RETRIEVING": C["B"],
    "GENERATING": C["M"],
    "FORMATTING": C["Y"],
    "COMPLETED": C["G"],
    "FAILED": C["R"],
    "RETRY": C["Y"],
}


def load_entries() -> list[dict]:
    """从 JSONL 日志加载所有状态迁移记录"""
    if not STATE_LOG.exists():
        return []
    entries = []
    with open(STATE_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def group_by_workflow(entries: list[dict]) -> dict[str, list[dict]]:
    """按 workflow_id 分组"""
    groups = defaultdict(list)
    for e in entries:
        groups[e.get("workflow_id", "unknown")].append(e)
    return dict(groups)


def print_header():
    """打印仪表盘头部"""
    print(f"\n{C['W']}╔══════════════════════════════════════════════════════════════════╗{C['N']}")
    print(f"{C['W']}║          智能试卷生成系统 — 状态机实时仪表盘                  ║{C['N']}")
    print(f"{C['W']}╚══════════════════════════════════════════════════════════════════╝{C['N']}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: {STATE_LOG}")


def print_workflow_detail(entries: list[dict]):
    """打印单个工作流的详细迁移轨迹"""
    if not entries:
        print("  (无记录)")
        return

    wf_id = entries[0].get("workflow_id", "?")
    first_ts = entries[0].get("timestamp", "")
    last_ts = entries[-1].get("timestamp", "")
    final_state = entries[-1].get("to_state", "?")

    state_color = STATE_COLORS.get(final_state, C["N"])
    print(f"\n{C['W']}工作流: {wf_id}{C['N']}")
    print(f"  时间: {first_ts[:19]} → {last_ts[:19]}")
    print(f"  终态: {state_color}{final_state}{C['N']}")
    print(f"  迁移次数: {len(entries)}")
    print()

    # 可视化迁移
    for entry in entries:
        fs = entry.get("from_state", "?")
        ts = entry.get("to_state", "?")
        ev = entry.get("event", "?")
        retry = entry.get("retry_count", 0)
        meta = entry.get("metadata", {})

        fs_color = STATE_COLORS.get(fs, C["N"])
        ts_color = STATE_COLORS.get(ts, C["N"])

        extra = ""
        if retry > 0:
            extra += f" retry={retry}"
        if meta.get("agent"):
            extra += f" agent={meta['agent']}"
        if meta.get("reason"):
            extra += f" reason={str(meta['reason'])[:40]}"
        if meta.get("retrieved_count"):
            extra += f" count={meta['retrieved_count']}"

        print(f"  {fs_color}{fs:12s}{C['N']} ──{ev:22s}──▶ {ts_color}{ts:12s}{C['N']}{extra}")

    print()


def print_dashboard(entries: list[dict], active_only: bool = True):
    """打印仪表盘总览"""
    groups = group_by_workflow(entries)

    # 去 INIT 条目找到最新状态
    wf_states = {}
    for wf_id, wf_entries in groups.items():
        real_entries = [e for e in wf_entries if e.get("event") != "SYSTEM_INIT"]
        if not real_entries:
            wf_states[wf_id] = wf_entries[-1] if wf_entries else None
        else:
            wf_states[wf_id] = real_entries[-1]

    # 过滤
    if active_only:
        active_states = {
            wid: entry for wid, entry in wf_states.items()
            if entry and entry.get("to_state") not in ("COMPLETED", "FAILED", "IDLE")
        }
    else:
        active_states = wf_states

    if not active_states:
        print(f"\n  {C['Y']}(无活跃会话){C['N']}")
        return

    # 统计
    state_counts = defaultdict(int)
    for entry in active_states.values():
        if entry:
            state_counts[entry.get("to_state", "?")] += 1

    print(f"\n{C['W']}活跃会话: {len(active_states)}{C['N']}")
    print(f"  状态分布: ", end="")
    parts = []
    for state, count in sorted(state_counts.items()):
        color = STATE_COLORS.get(state, C["N"])
        parts.append(f"{color}{state}={count}{C['N']}")
    print(", ".join(parts))

    # 表格
    print(f"\n{'ID':36s} {'状态':12s} {'重试':4s} {'耗时':8s} {'事件':20s}")
    print("-" * 82)

    for wf_id, entry in sorted(active_states.items()):
        if not entry:
            continue
        state = entry.get("to_state", "?")
        color = STATE_COLORS.get(state, C["N"])
        retry = entry.get("retry_count", 0)
        elapsed = entry.get("elapsed_s", 0)
        event = entry.get("event", "")

        print(f" {wf_id:35s} "
              f"{color}{state:12s}{C['N']} "
              f"{retry:4d} "
              f"{elapsed:7.1f}s "
              f"{event:20s}")

    print()


def print_history(entries: list[dict], limit: int = 20):
    """打印最近迁移历史"""
    real_entries = [e for e in entries if e.get("event") != "SYSTEM_INIT"]
    recent = real_entries[-limit:]

    print(f"\n{C['W']}最近 {len(recent)} 次状态迁移:{C['N']}")
    print(f"{'时间':20s} {'工作流':36s} {'迁移':40s}")
    print("-" * 98)

    for entry in recent:
        ts = entry.get("timestamp", "")[:19]
        wf = entry.get("workflow_id", "?")
        fs = entry.get("from_state", "?")
        ts_s = entry.get("to_state", "?")
        ev = entry.get("event", "?")
        fs_color = STATE_COLORS.get(fs, C["N"])
        ts_color = STATE_COLORS.get(ts_s, C["N"])
        transition = f"{fs_color}{fs}{C['N']} → {ts_color}{ts_s}{C['N']} ({ev})"
        print(f" {ts:19s} {wf:35s} {transition}")


def watch_mode(interval: float = 2.0):
    """实时监控模式"""
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print_header()
            entries = load_entries()
            print_dashboard(entries, active_only=True)
            print(f"\n  按 Ctrl+C 退出 | 刷新间隔: {interval}s")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n{C['Y']}监控已停止{C['N']}")


def main():
    parser = argparse.ArgumentParser(description="状态机实时仪表盘")
    parser.add_argument("--watch", "-w", action="store_true", help="实时监控模式")
    parser.add_argument("--interval", type=float, default=2.0, help="刷新间隔秒数")
    parser.add_argument("--workflow", type=str, help="查看指定工作流详情")
    parser.add_argument("--history", action="store_true", help="查看迁移历史")
    parser.add_argument("--all", action="store_true", help="显示所有会话（含已完成）")
    args = parser.parse_args()

    # 加载数据
    if args.watch:
        watch_mode(args.interval)
        return

    entries = load_entries()
    if not entries:
        print(f"\n{C['Y']}无状态迁移记录 ({STATE_LOG} 为空或不存在){C['N']}")
        print("运行 orchestrator 以产生状态迁移日志。")
        return

    print_header()

    if args.workflow:
        groups = group_by_workflow(entries)
        wf_entries = groups.get(args.workflow, [])
        if wf_entries:
            print_workflow_detail(wf_entries)
        else:
            print(f"\n{C['R']}工作流未找到: {args.workflow}{C['N']}")
            print(f"可用 ID: {list(groups.keys())[:10]}")
    elif args.history:
        print_history(entries)
    else:
        print_dashboard(entries, active_only=not args.all)

    print()


if __name__ == "__main__":
    main()
