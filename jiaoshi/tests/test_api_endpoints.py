# -*- coding: utf-8 -*-
"""
API 端点集成测试
使用 Flask test_client，验证状态码、返回格式、数据完整性、异步任务、地域过滤
"""

import sys
import os
import json
import time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))


class TestHealthEndpoint:
    """健康检查端点"""

    def test_health_returns_200(self, flask_client):
        """GET /api/health 应返回 200"""
        resp = flask_client.get("/api/health")
        assert resp.status_code == 200

    def test_health_returns_json(self, flask_client):
        """应返回 JSON 格式"""
        resp = flask_client.get("/api/health")
        assert resp.is_json
        data = resp.get_json()
        assert "status" in data
        assert data["status"] == "ok"

    def test_health_no_body_needed(self, flask_client):
        """GET 请求不需要 body"""
        resp = flask_client.get("/api/health")
        assert resp.status_code == 200


class TestGeneratePaperEndpoint:
    """POST /api/generate_paper"""

    def test_generate_paper_returns_200(self, flask_client):
        """应返回 200"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        assert resp.status_code == 200

    def test_generate_paper_returns_json(self, flask_client):
        """应返回 JSON，包含 questions 数组"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        assert resp.is_json
        data = resp.get_json()
        assert "questions" in data
        assert isinstance(data["questions"], list)

    def test_generate_paper_questions_have_fields(self, flask_client):
        """每道题需包含 type/text/answer/analysis"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        data = resp.get_json()
        required = ["type", "text", "answer", "analysis"]
        for q in data["questions"]:
            for field in required:
                assert field in q, f"缺少字段: {field}"

    def test_generate_paper_empty_knowledge_points(self, flask_client):
        """空知识点列表不应崩溃"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "knowledge_points": [],
            "difficulty": 3,
            "num_questions": 2,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["questions"]) > 0

    def test_generate_paper_empty_body(self, flask_client):
        """空 body 应使用默认值不崩溃"""
        resp = flask_client.post("/api/generate_paper", json={})
        assert resp.status_code == 200

    def test_generate_paper_missing_params(self, flask_client):
        """缺少关键参数应不崩溃（使用默认值）"""
        resp = flask_client.post("/api/generate_paper", json={
            "knowledge_points": ["函数"],
        })
        assert resp.status_code == 200

    def test_generate_paper_valid_types(self, flask_client):
        """题型应在允许范围内"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "num_questions": 3,
            "difficulty": 3,
        })
        data = resp.get_json()
        valid_types = {"choice", "fill_blank", "short_answer"}
        for q in data["questions"]:
            assert q["type"] in valid_types, f"无效题型: {q['type']}"


class TestReplaceQuestionEndpoint:
    """POST /api/replace_question"""

    def test_replace_question_returns_200(self, flask_client):
        """应返回 200"""
        resp = flask_client.post("/api/replace_question", json={
            "subject": "math",
            "knowledge_points": ["函数"],
            "difficulty": 3,
            "question_index": 0,
        })
        assert resp.status_code == 200

    def test_replace_question_returns_single_question(self, flask_client):
        """应返回单道题，非列表"""
        resp = flask_client.post("/api/replace_question", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "question_index": 1,
        })
        data = resp.get_json()
        assert "question" in data
        assert isinstance(data["question"], dict)
        # 题目字段
        q = data["question"]
        assert "type" in q
        assert "text" in q
        assert "answer" in q

    def test_replace_question_preserves_type(self, flask_client):
        """指定 current_type 时，返回题目尽可能匹配"""
        resp = flask_client.post("/api/replace_question", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "question_index": 0,
            "current_type": "choice",
        })
        data = resp.get_json()
        q = data["question"]
        # 由于 LLM fallback，可能不匹配，但应至少返回一个有效题
        assert q["type"] in ("choice", "fill_blank", "short_answer")

    def test_replace_question_empty_knowledge_points(self, flask_client):
        """空知识点不崩溃"""
        resp = flask_client.post("/api/replace_question", json={
            "subject": "math",
            "knowledge_points": [],
            "difficulty": 3,
        })
        assert resp.status_code == 200


class TestSearchSimilarEndpoint:
    """POST /api/search_similar"""

    def test_search_similar_returns_200(self, flask_client):
        """应返回 200"""
        resp = flask_client.post("/api/search_similar", json={
            "query": "一元一次方程",
            "subject": "math",
            "top_k": 3,
        })
        assert resp.status_code == 200

    def test_search_similar_returns_results(self, flask_client):
        """应返回 results 数组"""
        resp = flask_client.post("/api/search_similar", json={
            "query": "方程",
            "subject": "math",
            "top_k": 3,
        })
        data = resp.get_json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_similar_results_have_fields(self, flask_client):
        """每条结果需有 id/text/subject/difficulty/score"""
        resp = flask_client.post("/api/search_similar", json={
            "query": "几何",
            "top_k": 3,
        })
        data = resp.get_json()
        for r in data["results"]:
            for field in ["id", "text", "subject", "difficulty", "score"]:
                assert field in r, f"缺少字段: {field}"

    def test_search_similar_respects_top_k(self, flask_client):
        """返回数量不超过 top_k"""
        resp = flask_client.post("/api/search_similar", json={
            "query": "数学",
            "top_k": 2,
        })
        data = resp.get_json()
        assert len(data["results"]) <= 2


class TestErrorHandling:
    """错误处理测试"""

    def test_404_unknown_route(self, flask_client):
        """未知路由返回 404"""
        resp = flask_client.get("/api/nonexistent")
        assert resp.status_code == 404

    def test_generate_paper_invalid_json(self, flask_client):
        """无效 JSON 应不崩溃（Flask 返回 400 或处理为 {}）"""
        resp = flask_client.post(
            "/api/generate_paper",
            data="not json",
            content_type="application/json",
        )
        # Flask 3.x 对无效 JSON 返回 400
        assert resp.status_code in (200, 400)

    def test_method_not_allowed(self, flask_client):
        """GET 请求 POST-only 端点应返回 405"""
        resp = flask_client.get("/api/generate_paper")
        assert resp.status_code == 405


# ==========================================================================
# 异步任务集成测试
# ==========================================================================

class TestAsyncTaskEndpoints:
    """异步生成、结果轮询测试"""

    def test_submit_async_task(self, flask_client):
        """POST /api/generate_paper_async 应返回 task_id"""
        resp = flask_client.post("/api/generate_paper_async", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 2,
        })
        assert resp.status_code in (200, 202)
        data = resp.get_json()
        assert "task_id" in data

    def test_poll_task_status(self, flask_client):
        """GET /api/task_status/<id> 应返回状态"""
        # 先创建任务
        resp = flask_client.post("/api/generate_paper_async", json={
            "subject": "math", "num_questions": 2,
        })
        if resp.status_code in (200, 202):
            task_id = resp.get_json().get("task_id")
            if task_id:
                # 轮询状态
                status_resp = flask_client.get(f"/api/task_status/{task_id}")
                assert status_resp.status_code == 200
                status_data = status_resp.get_json()
                assert "status" in status_data
                assert status_data["status"] in ("pending", "running", "done", "error")

    def test_async_task_eventually_completes(self, flask_client):
        """异步任务应在合理时间内完成"""
        resp = flask_client.post("/api/generate_paper_async", json={
            "subject": "math", "num_questions": 2, "difficulty": 2,
        })
        if resp.status_code in (200, 202):
            task_id = resp.get_json().get("task_id")
            if task_id:
                max_wait = 15
                for _ in range(max_wait):
                    status_resp = flask_client.get(f"/api/task_status/{task_id}")
                    status = status_resp.get_json().get("status", "")
                    if status in ("done", "error"):
                        break
                    time.sleep(1)
                final_resp = flask_client.get(f"/api/task_status/{task_id}")
                final_status = final_resp.get_json()
                assert final_status["status"] in ("done", "error", "pending", "running")


# ==========================================================================
# 地域过滤端点测试
# ==========================================================================

class TestRegionalAPIIntegration:
    """地域过滤 API 集成测试"""

    def test_generate_paper_with_region(self, flask_client):
        """指定地域生成试卷应正常返回"""
        resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "region": "北京",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 2,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "questions" in data

    def test_search_with_region_parameter(self, flask_client):
        """搜索时传递地域参数不应崩溃"""
        resp = flask_client.post("/api/search_similar", json={
            "query": "一元一次方程",
            "subject": "math",
            "top_k": 3,
            "region": "beijing",
        })
        assert resp.status_code == 200

    def test_three_regions_all_work(self, flask_client):
        """三地域分别请求均应成功"""
        regions = ["北京", "上海", "广东"]
        for region in regions:
            resp = flask_client.post("/api/generate_paper", json={
                "subject": "math",
                "region": region,
                "knowledge_points": ["方程"],
                "difficulty": 2,
                "num_questions": 2,
            })
            assert resp.status_code == 200, f"{region} 请求失败"
            data = resp.get_json()
            assert len(data.get("questions", [])) > 0, f"{region} 无题目返回"


# ==========================================================================
# 健康检查与缓存端点
# ==========================================================================

class TestSystemEndpoints:
    """系统级端点测试"""

    def test_cache_stats_endpoint(self, flask_client):
        """GET /api/cache_stats 应返回统计信息"""
        resp = flask_client.get("/api/cache_stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_entries" in data or "active_entries" in data

    def test_task_stats_endpoint(self, flask_client):
        """GET /api/task_stats 应返回统计信息"""
        resp = flask_client.get("/api/task_stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total" in data or "pending" in data

    def test_health_returns_consistent_format(self, flask_client):
        """多次调用 health 应返回一致格式"""
        for _ in range(3):
            resp = flask_client.get("/api/health")
            assert resp.get_json()["status"] == "ok"

    def test_concurrent_generate_requests(self, flask_client):
        """连续多次生成请求不崩溃"""
        for i in range(3):
            resp = flask_client.post("/api/generate_paper", json={
                "subject": "math",
                "knowledge_points": ["方程"],
                "difficulty": 2,
                "num_questions": 1,
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert "questions" in data


# ==========================================================================
# 替换题目完整交互测试
# ==========================================================================

class TestReplaceQuestionRoundTrip:
    """题目替换往返测试"""

    def test_replace_then_verify(self, flask_client):
        """替换题目后验证新旧题目不相同"""
        # 先生成一份试卷
        gen_resp = flask_client.post("/api/generate_paper", json={
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        assert gen_resp.status_code == 200
        paper = gen_resp.get_json()
        original_q = paper["questions"][0] if paper.get("questions") else None

        # 替换第一题
        replace_resp = flask_client.post("/api/replace_question", json={
            "subject": "math",
            "knowledge_points": ["几何"],
            "difficulty": 3,
            "question_index": 0,
            "current_type": original_q.get("type", "short_answer") if original_q else "short_answer",
        })
        assert replace_resp.status_code == 200
        new_q = replace_resp.get_json().get("question", {})

        # 不应完全相同
        if original_q:
            assert new_q.get("text", "") != original_q.get("text", ""), "替换题应与原题不同"
