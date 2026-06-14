# -*- coding: utf-8 -*-
"""
RAG 检索模块测试脚本
验证索引构建和向量检索功能
"""

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from rag_indexer import build_index
from rag_searcher import init_searcher, search


def main():
    print("=" * 60)
    print("RAG 检索模块 - 集成测试")
    print("=" * 60)

    # ---------- Step 1: 构建索引 ----------
    print("\n>>> Step 1: 构建向量索引")
    build_index(force_rebuild=True)

    # ---------- Step 2: 初始化检索器 ----------
    print("\n>>> Step 2: 初始化检索器")
    init_searcher()

    # ---------- Step 3: 多场景检索测试 ----------
    test_cases = [
        {
            "label": "无过滤 - 一元一次方程应用题",
            "query": "一元一次方程应用题",
            "filters": None,
            "top_k": 5,
        },
        {
            "label": "学科过滤 - 数学 + 难度 <= 3",
            "query": "函数图像",
            "filters": {"subject": "math", "difficulty": {"$lte": 3}},
            "top_k": 3,
        },
        {
            "label": "学科过滤 - 语文诗词",
            "query": "古诗鉴赏",
            "filters": {"subject": "chinese"},
            "top_k": 3,
        },
        {
            "label": "学科过滤 - 英语时态",
            "query": "过去进行时",
            "filters": {"subject": "english"},
            "top_k": 3,
        },
        {
            "label": "难度过滤 - 仅困难题目",
            "query": "三角形全等证明",
            "filters": {"difficulty": {"$gte": 4}},
            "top_k": 3,
        },
    ]

    all_passed = True

    for tc in test_cases:
        print(f"\n{'─' * 50}")
        print(f"[测试] {tc['label']}")
        print(f"  查询: \"{tc['query']}\"")
        if tc["filters"]:
            print(f"  过滤: {tc['filters']}")

        try:
            results = search(tc["query"], filters=tc["filters"], top_k=tc["top_k"])
        except Exception as e:
            print(f"  [FAIL] 检索异常: {e}")
            all_passed = False
            continue

        if len(results) == 0:
            print(f"  [WARN] 未检索到结果（数据集中可能缺少匹配项）")
        else:
            print(f"  返回 {len(results)} 条结果:")
            for r in results:
                print(f"    #{r['id']} [{r['subject']}] sim={r['score']:.4f} "
                      f"难度={r['difficulty']} 年级={r['grade']}")
                print(f"    题目: {r['question_text'][:70]}{'...' if len(r['question_text'])>70 else ''}")

        # 验证过滤条件是否生效
        if tc["filters"] and results:
            for r in results:
                if "subject" in tc["filters"]:
                    expected = tc["filters"]["subject"]
                    if r["subject"] != expected:
                        print(f"  [FAIL] 过滤失败: subject 应为 {expected}，实际为 {r['subject']}")
                        all_passed = False
                if "difficulty" in tc["filters"]:
                    cond = tc["filters"]["difficulty"]
                    if "$lte" in cond and r["difficulty"] > cond["$lte"]:
                        print(f"  [FAIL] 过滤失败: difficulty 应 <= {cond['$lte']}，实际为 {r['difficulty']}")
                        all_passed = False
                    if "$gte" in cond and r["difficulty"] < cond["$gte"]:
                        print(f"  [FAIL] 过滤失败: difficulty 应 >= {cond['$gte']}，实际为 {r['difficulty']}")
                        all_passed = False

    # ---------- 结果 ----------
    print(f"\n{'=' * 60}")
    if all_passed:
        print("全部测试通过")
    else:
        print("部分测试未通过，请检查上述 [FAIL] 项")
    print("=" * 60)


if __name__ == "__main__":
    main()
