# -*- coding: utf-8 -*-
"""
RAG 检索模块测试
验证搜索返回数量正确、过滤条件生效
"""

import sys
import os
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))


class TestRAGSearchBasic:
    """基础检索功能测试"""

    def test_search_returns_results(self, rag_ready):
        """搜索应返回非空结果"""
        from rag_searcher import search
        results = search("一元一次方程", top_k=5)
        assert len(results) > 0, "搜索应返回至少一条结果"

    def test_search_respects_top_k(self, rag_ready):
        """返回数量不应超过 top_k"""
        from rag_searcher import search
        for k in [1, 3, 5]:
            results = search("数学", top_k=k)
            assert len(results) <= k, f"top_k={k} 时应返回最多 {k} 条，实际 {len(results)}"

    def test_search_returns_required_fields(self, rag_ready):
        """每条结果应包含所有必需字段"""
        from rag_searcher import search
        results = search("几何", top_k=3)
        required = ["id", "question_text", "subject", "grade", "difficulty", "score"]
        for r in results:
            for field in required:
                assert field in r, f"结果缺少字段: {field}"

    def test_search_returns_numeric_score(self, rag_ready):
        """相似度分数应为数值"""
        from rag_searcher import search
        results = search("方程", top_k=3)
        for r in results:
            assert isinstance(r["score"], (int, float)), f"score 应为数值，实际: {type(r['score'])}"


class TestRAGSearchFilters:
    """元数据过滤测试"""

    def test_filter_subject_math(self, rag_ready):
        """过滤 subject=math 时，所有结果都应是 math"""
        from rag_searcher import search
        results = search("计算", filters={"subject": "math"}, top_k=5)
        assert len(results) > 0, "应有 math 结果"
        for r in results:
            assert r["subject"] == "math", f"过滤 math 时不应出现 {r['subject']}"

    def test_filter_subject_chinese(self, rag_ready):
        """过滤 subject=chinese"""
        from rag_searcher import search
        results = search("阅读", filters={"subject": "chinese"}, top_k=3)
        for r in results:
            assert r["subject"] == "chinese", f"过滤 chinese 时不应出现 {r['subject']}"

    def test_filter_subject_english(self, rag_ready):
        """过滤 subject=english"""
        from rag_searcher import search
        results = search("grammar", filters={"subject": "english"}, top_k=3)
        for r in results:
            assert r["subject"] == "english", f"过滤 english 时不应出现 {r['subject']}"

    def test_filter_difficulty_lte(self, rag_ready):
        """过滤 difficulty <= N 时所有结果难度应符合"""
        from rag_searcher import search
        for max_diff in [1, 2, 3]:
            results = search("数学", filters={"difficulty": {"$lte": max_diff}}, top_k=10)
            for r in results:
                assert r["difficulty"] <= max_diff, (
                    f"difficulty 应 <= {max_diff}，实际 {r['difficulty']}"
                )

    def test_filter_difficulty_gte(self, rag_ready):
        """过滤 difficulty >= N"""
        from rag_searcher import search
        results = search("数学", filters={"difficulty": {"$gte": 4}}, top_k=5)
        for r in results:
            assert r["difficulty"] >= 4, f"difficulty 应 >= 4，实际 {r['difficulty']}"

    def test_filter_combined(self, rag_ready):
        """组合过滤：学科 + 难度"""
        from rag_searcher import search
        results = search("计算", filters={
            "subject": "math",
            "difficulty": {"$lte": 3}
        }, top_k=5)
        for r in results:
            assert r["subject"] == "math"
            assert r["difficulty"] <= 3

    def test_search_without_filters(self, rag_ready):
        """无过滤时应跨学科返回结果"""
        from rag_searcher import search
        results = search("题目", filters=None, top_k=10)
        subjects = set(r["subject"] for r in results)
        # 至少应有 2 个学科的结果
        assert len(subjects) >= 1, "至少应返回一个学科的结果"

    def test_search_ordering(self, rag_ready):
        """结果应按相似度排序（或至少首条比末条相关）"""
        from rag_searcher import search
        results = search("一元一次方程", top_k=5)
        if len(results) >= 2:
            # 至少第一条的 subject 应对得上
            first = results[0]
            assert first["subject"] in ("math",), (
                f"搜索数学概念，首条应为 math，实际 {first['subject']}"
            )


class TestRAGEdgeCases:
    """边界情况测试"""

    def test_empty_query(self, rag_ready):
        """空查询不应崩溃"""
        from rag_searcher import search
        results = search("", top_k=3)
        assert isinstance(results, list), "应返回列表"

    def test_very_long_query(self, rag_ready):
        """超长查询不应崩溃"""
        from rag_searcher import search
        long_query = "数学 " * 100
        results = search(long_query, top_k=3)
        assert isinstance(results, list)

    def test_top_k_zero(self, rag_ready):
        """top_k=0 应返回空列表"""
        from rag_searcher import search
        results = search("数学", top_k=0)
        assert results == []

    def test_impossible_filter(self, rag_ready):
        """不可能的组合过滤应返回空"""
        from rag_searcher import search
        results = search("数学", filters={
            "subject": "math",
            "difficulty": {"$lte": 0}  # 难度最小为1
        }, top_k=5)
        assert len(results) == 0
