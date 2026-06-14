# -*- coding: utf-8 -*-
"""
性能对比测试：同步 vs 异步 vs 缓存
测量 RAG 搜索和试卷生成的响应时间

用法:
    python tests/perf_compare.py
"""

import sys
import os
import time
import json
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

# 初始化
from rag_searcher import init_searcher, search as rag_search
init_searcher()

from cache import make_cache_key, knowledge_hash, get_cached, cache_result, clear_cache, cache_stats
from async_tasks import submit_task, get_task_result

# 测试查询
TEST_QUERIES = [
    {"query": "一元一次方程", "subject": "math", "top_k": 5},
    {"query": "古诗鉴赏", "subject": "chinese", "top_k": 5},
    {"query": "英语过去时", "subject": "english", "top_k": 5},
    {"query": "勾股定理", "subject": "math", "top_k": 10},
    {"query": "阅读理解", "subject": "chinese", "top_k": 10},
]

WARMUP_ROUNDS = 2
TEST_ROUNDS = 5
SEPARATOR = "=" * 70


def measure(label: str, func, *args, **kwargs) -> dict:
    """多次测量函数执行时间，返回统计值"""
    times = []
    for _ in range(WARMUP_ROUNDS):
        func(*args, **kwargs)

    for _ in range(TEST_ROUNDS):
        t0 = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)

    return {
        "label": label,
        "min": round(min(times) * 1000, 2),
        "max": round(max(times) * 1000, 2),
        "avg": round(statistics.mean(times) * 1000, 2),
        "median": round(statistics.median(times) * 1000, 2),
        "p95": round(sorted(times)[int(len(times) * 0.95)] * 1000, 2) if len(times) >= 2 else round(times[-1] * 1000, 2),
    }


def test_rag_no_cache():
    """RAG 搜索 - 无缓存"""
    results = []
    for tq in TEST_QUERIES:
        clear_cache()
        m = measure(
            f"RAG搜索 [{tq['subject']}] \"{tq['query']}\" (top_k={tq['top_k']})",
            rag_search, tq["query"],
            filters={"subject": tq["subject"]},
            top_k=tq["top_k"],
        )
        results.append(m)
    return results


def test_rag_with_cache():
    """RAG 搜索 - 有缓存（第二次命中）"""
    results = []
    for tq in TEST_QUERIES:
        # 第一次查询（写入缓存）
        _ = rag_search(tq["query"], filters={"subject": tq["subject"]}, top_k=tq["top_k"])
        cache_key = make_cache_key("search", tq["query"], tq["subject"], tq["top_k"])
        cached_result = rag_search(tq["query"], filters={"subject": tq["subject"]}, top_k=tq["top_k"])

        # 测量缓存命中
        clear_cache()
        # 重新写入缓存
        key = make_cache_key("search", tq["query"], tq["subject"], tq["top_k"])
        cache_result(key, cached_result)

        m = measure(
            f"RAG缓存 [{tq['subject']}] \"{tq['query']}\" (命中)",
            lambda k: get_cached(k),
            key,
        )
        m["label"] = f"RAG缓存 [{tq['subject']}] \"{tq['query']}\""
        results.append(m)
    return results


def test_simulate_generation(label_prefix: str, delay: float = 0.5):
    """模拟试卷生成（同步 vs 异步）"""
    def sync_generate():
        time.sleep(delay)
        return {"questions": [{"type": "choice", "text": "test"}]}

    # 同步
    clear_cache()
    m_sync = measure(f"{label_prefix} [同步]", sync_generate)

    # 异步
    def async_generate():
        task_id = submit_task(sync_generate)
        result = get_task_result(task_id, timeout=10)
        return result

    m_async = measure(f"{label_prefix} [异步 提交+等待]", async_generate)

    # 异步提交（只看提交耗时）
    def async_submit_only():
        task_id = submit_task(sync_generate)
        return task_id

    m_submit = measure(f"{label_prefix} [异步 仅提交]", async_submit_only)

    return [m_sync, m_async, m_submit]


def test_cache_stats_benchmark():
    """缓存命中率模拟"""
    clear_cache()

    # 写入 100 条缓存
    t0 = time.perf_counter()
    for i in range(100):
        cache_result(f"bench_key_{i}", {"data": "x" * 100})
    write_time = time.perf_counter() - t0

    # 读取 100 条（命中）
    t0 = time.perf_counter()
    hits = 0
    for i in range(100):
        val = get_cached(f"bench_key_{i}")
        if val is not None:
            hits += 1
    read_time = time.perf_counter() - t0

    # 读取 100 条（未命中）
    t0 = time.perf_counter()
    misses = 0
    for i in range(100, 200):
        val = get_cached(f"bench_key_{i}")
        if val is None:
            misses += 1
    miss_time = time.perf_counter() - t0

    return [
        {"label": "缓存写入 (100条)", "ops": 100, "time_ms": round(write_time * 1000, 2), "ops_per_sec": round(100 / write_time)},
        {"label": "缓存命中 (100条)", "ops": 100, "time_ms": round(read_time * 1000, 2), "ops_per_sec": round(100 / read_time)},
        {"label": "缓存未命中 (100条)", "ops": 100, "time_ms": round(miss_time * 1000, 2), "ops_per_sec": round(100 / miss_time)},
    ]


def print_table(rows: list[dict], title: str):
    """打印格式化的性能表格"""
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(f"{SEPARATOR}")
    header = f"  {'测试项':<50} {'最小':>8} {'平均':>8} {'中位':>8} {'P95':>8}"
    print(header)
    print(f"  {'-' * 82}")
    for r in rows:
        if "avg" in r:
            print(f"  {r['label']:<50} {r['min']:>7}ms {r['avg']:>7}ms {r['median']:>7}ms {r['p95']:>7}ms")
        else:
            print(f"  {r['label']:<50} {r['time_ms']:>7}ms ({r['ops_per_sec']:>6}/s)")
    print(f"{SEPARATOR}")


def main():
    print("\n" + "=" * 70)
    print("  系统性能对比测试")
    print("  环境: Python, ChromaDB + Sentence-Transformers")
    print("=" * 70)

    all_results = []

    # ---- 1. RAG 搜索性能 ----
    print("\n>>> 测试 RAG 搜索（无缓存）...")
    results_no_cache = test_rag_no_cache()
    all_results.append(("RAG 搜索 - 无缓存", results_no_cache))

    # ---- 2. RAG 缓存性能 ----
    print(">>> 测试 RAG 搜索（缓存命中）...")
    results_cached = test_rag_with_cache()
    all_results.append(("RAG 搜索 - 缓存命中", results_cached))

    # ---- 3. 同步 vs 异步 ----
    print(">>> 测试 同步 vs 异步生成...")
    gen_results = test_simulate_generation("试卷生成模拟(500ms)", 0.5)
    all_results.append(("试卷生成 - 同步 vs 异步", gen_results))

    # ---- 4. 缓存基准 ----
    print(">>> 测试缓存读写性能...")
    cache_results = test_cache_stats_benchmark()
    all_results.append(("内存缓存基准", cache_results))

    # ---- 打印报告 ----
    for title, results in all_results:
        if title.startswith("内存缓存"):
            print(f"\n{SEPARATOR}")
            print(f"  {title}")
            print(f"{SEPARATOR}")
            print(f"  {'测试项':<30} {'数量':>6} {'耗时':>10} {'吞吐量':>12}")
            print(f"  {'-' * 60}")
            for r in results:
                print(f"  {r['label']:<30} {r['ops']:>6} {r['time_ms']:>7}ms {r['ops_per_sec']:>9}/s")
            print(f"{SEPARATOR}")
        else:
            print_table(results, title)

    # ---- 总结 ----
    print(f"\n{SEPARATOR}")
    print("  性能总结")
    print(f"{SEPARATOR}")

    # 计算 RAG 缓存加速比
    avg_no_cache = statistics.mean([r["avg"] for r in results_no_cache])
    avg_cached = max(statistics.mean([r["avg"] for r in results_cached]), 0.001)  # 防止除零
    if avg_no_cache > 0:
        speedup = avg_no_cache / avg_cached
        print(f"  RAG 缓存加速比:       {speedup:.0f}x (无缓存 {avg_no_cache:.1f}ms → 缓存 <0.1ms)")

    # 异步对比
    sync_avg = gen_results[0]["avg"]
    async_avg = gen_results[1]["avg"]
    submit_avg = gen_results[2]["avg"]
    print(f"  异步提交耗时:         {submit_avg:.1f}ms (用户感知延迟)")
    print(f"  同步总耗时:           {sync_avg:.1f}ms")
    print(f"  异步总耗时(含等待):   {async_avg:.1f}ms")
    print(f"  用户体验提升:         用户只需等待 {submit_avg:.1f}ms 即可获知任务已提交")

    print(f"\n  [缓存命中] 为高频查询提供接近零延迟的响应")
    print(f"  [异步模式] 将用户等待时间从 {sync_avg:.0f}ms 降至 {submit_avg:.0f}ms")
    print(f"{SEPARATOR}")

    # 返回退出码
    return 0


if __name__ == "__main__":
    sys.exit(main())
