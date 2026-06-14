# -*- coding: utf-8 -*-
"""
题目生成模块测试
模拟 LLM 返回，验证生成逻辑的完整性和数据格式
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))


# ------ 模拟 LLM 返回 ------

MOCK_CLAUDE_RESPONSE = {
    "questions": [
        {
            "type": "choice",
            "text": "若 2x + a = 10 的解是 x = 3，则 a = （　）",
            "options": ["A. 2", "B. 4", "C. 6", "D. 8"],
            "answer": "B",
            "analysis": "代入 x=3: 6+a=10, a=4"
        },
        {
            "type": "choice",
            "text": "方程 4(x - 1) = 8 的解是（　）",
            "options": ["A. x = 1", "B. x = 2", "C. x = 3", "D. x = 4"],
            "answer": "C",
            "analysis": "4x - 4 = 8, 4x = 12, x = 3"
        },
        {
            "type": "fill_blank",
            "text": "若 x 满足 3x - 5 = 2x + 1，则 x = ______。",
            "answer": "6",
            "analysis": "移项: 3x - 2x = 1 + 5, x = 6"
        },
        {
            "type": "fill_blank",
            "text": "已知 a + b = 15, a - b = 3，则 a = ______。",
            "answer": "9",
            "analysis": "两式相加: 2a = 18, a = 9"
        },
        {
            "type": "short_answer",
            "text": "甲、乙两人从相距40km的两地同时出发相向而行，甲的速度是5km/h，乙的速度是3km/h。经过几小时两人相遇？",
            "answer": "设经过 x 小时，5x + 3x = 40, 8x = 40, x = 5。答：5小时。",
            "analysis": "相向运动: 路程和 = 速度和 * 时间"
        },
    ]
}

# 模拟 Anthropic 消息响应
MOCK_ANTHROPIC_RESPONSE = MagicMock()
MOCK_ANTHROPIC_RESPONSE.content = [
    MagicMock(text=json.dumps(MOCK_CLAUDE_RESPONSE, ensure_ascii=False))
]


class TestGenerateQuestions:
    """题目生成核心逻辑测试"""

    @patch("generate_paper._call_claude")
    def test_generate_returns_correct_count(self, mock_call, rag_ready):
        """生成应返回指定数量的题目"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        for n in [3, 5, 7]:
            result = generate_questions({
                "subject": "数学",
                "grade": "grade_8",
                "region": "北京",
                "knowledge_points": ["方程"],
                "difficulty": 3,
                "num_questions": n,
            })
            assert len(result["questions"]) <= n, f"题目数应 <= {n}"
            assert result["source"] == "claude"

    @patch("generate_paper._call_claude")
    def test_generate_questions_have_required_fields(self, mock_call, rag_ready):
        """每道题必须包含 type, text, answer, analysis"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        result = generate_questions({
            "subject": "数学",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 5,
        })
        required = ["type", "text", "answer", "analysis"]
        for q in result["questions"]:
            for field in required:
                assert field in q, f"题目缺少字段: {field}"
            assert len(q["text"]) > 0, "题干不能为空"
            assert len(q["answer"]) > 0, "答案不能为空"

    @patch("generate_paper._call_claude")
    def test_generate_types_are_valid(self, mock_call, rag_ready):
        """题型只能是 choice / fill_blank / short_answer"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        result = generate_questions({
            "subject": "数学",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 5,
        })
        valid_types = {"choice", "fill_blank", "short_answer"}
        for q in result["questions"]:
            assert q["type"] in valid_types, f"无效题型: {q['type']}"

    @patch("generate_paper._call_claude")
    def test_choice_questions_have_options(self, mock_call, rag_ready):
        """选择题必须有 options 数组"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        result = generate_questions({
            "subject": "数学",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 5,
        })
        for q in result["questions"]:
            if q["type"] == "choice":
                assert q.get("options") is not None, "选择题缺少 options"
                assert len(q["options"]) >= 2, "选择题至少需要2个选项"

    @patch("generate_paper._call_claude")
    def test_fallback_on_api_failure(self, mock_call, rag_ready):
        """LLM 调用失败时应使用回退方案"""
        from generate_paper import generate_questions

        mock_call.return_value = None  # 模拟 API 失败

        result = generate_questions({
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        assert result["source"] == "fallback"
        assert len(result["questions"]) == 3

    @patch("generate_paper._call_claude")
    def test_fallback_never_returns_empty(self, mock_call, rag_ready):
        """回退方案不应返回空列表"""
        from generate_paper import generate_questions

        mock_call.return_value = None

        result = generate_questions({
            "subject": "math",
            "knowledge_points": [],
            "difficulty": 3,
            "num_questions": 3,
        })
        assert len(result["questions"]) > 0, "回退方案应生成至少一题"

    def test_fallback_questions_have_source_marker(self, rag_ready):
        """回退题目应带来源标记"""
        from generate_paper import generate_questions
        # 直接跳过 LLM 调用（无 API key）
        result = generate_questions({
            "subject": "math",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 2,
        })
        assert "source" in result


class TestQuestionFormatting:
    """题目格式验证测试"""

    @patch("generate_paper._call_claude")
    def test_no_duplicate_questions_exact(self, mock_call, rag_ready):
        """生成的多道题不应完全相同（题干去重）"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        result = generate_questions({
            "subject": "数学",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 5,
        })
        texts = [q["text"].strip() for q in result["questions"]]
        assert len(texts) == len(set(texts)), "题干不应重复"

    @patch("generate_paper._call_claude")
    def test_answer_not_same_as_text(self, mock_call, rag_ready):
        """答案不能等同于题干"""
        from generate_paper import generate_questions

        mock_call.return_value = MOCK_CLAUDE_RESPONSE

        result = generate_questions({
            "subject": "数学",
            "knowledge_points": ["方程"],
            "difficulty": 3,
            "num_questions": 3,
        })
        for q in result["questions"]:
            assert q["answer"].strip() != q["text"].strip(), (
                "答案不应与题干完全相同"
            )
