# -*- coding: utf-8 -*-
"""
Schema 校验器 (SchemaValidator)
===============================
校验生成的试卷是否符合 Harness SPECS.md 中定义的数据模型契约。

校验层级：
  L1: 顶层结构 —— paper_meta / questions / answer_key 必须存在
  L2: 题目必填字段 —— question_text / answer / analysis 缺一不可
  L3: 题型与答案匹配 —— choice 必须有 options、fill_blank 必须有空白标记
  L4: 数值范围 —— difficulty 1-5、score >= 0、sequence_number 连续
  L5: 枚举值 —— subject / grade_level / question_type 必须在 schema 枚举中
  L6: 跨字段一致性 —— grade 与 grade_level 必须匹配

用法:
    validator = SchemaValidator()
    result = validator.validate(paper_dict)
    # → { "valid": True/False, "errors": [...], "warnings": [...] }
"""

import json
import os
import sys
import re
import logging
from pathlib import Path
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("validator.schema")

# ---------------------------------------------------------------------------
# 枚举常量（与 data/schema.json 和 data/schema/unified_question.json 对齐）
# ---------------------------------------------------------------------------
VALID_SUBJECTS = {"math", "chinese", "english", "physics", "chemistry", "biology", "history", "geography", "politics"}
VALID_GRADE_LEVELS = {"primary", "junior", "senior"}
VALID_QUESTION_TYPES = {"choice", "fill_blank", "true_false", "short_answer", "essay", "calculation"}
VALID_REGIONS = {"beijing", "shanghai", "guangdong", ""}
GRADE_TO_LEVEL = {
    **{f"grade_{i}": "primary" for i in range(1, 7)},
    **{f"grade_{i}": "junior" for i in range(7, 10)},
    **{f"grade_{i}": "senior" for i in range(10, 13)},
}

# 统一试题必填字段
QUESTION_REQUIRED_FIELDS = [
    "id", "subject", "grade", "grade_level",
    "knowledge_tags", "difficulty", "question_type",
    "question_text", "answer", "source",
]

# 题型 → 必须匹配的约束
QUESTION_TYPE_CONSTRAINTS = {
    "choice": {
        "must_have_options": True,
        "min_options": 2,
        "answer_format_hint": "应为选项字母如 A/B/C/D",
    },
    "fill_blank": {
        "must_have_blank_marker": True,
        "blank_marker_pattern": r"____|______|\([^)]*\)",
    },
    "true_false": {
        "valid_answers": ["正确", "错误", "对", "错", "T", "F", "True", "False", "true", "false", "√", "×"],
    },
}


class SchemaValidator:
    """
    Schema 校验器，验证试卷数据是否符合 SPECS.md 契约。

    用法:
        validator = SchemaValidator()
        result = validator.validate(assembled_paper)
        if not result["valid"]:
            for err in result["errors"]:
                print(f"  违反: {err}")
    """

    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def validate(self, paper: dict) -> dict:
        """
        对试卷执行全量 Schema 校验。

        参数:
            paper: 试卷 dict（assembled_paper.json 格式）

        返回:
            {
                "valid": True/False,
                "errors": ["错误1", "错误2", ...],
                "warnings": ["警告1", ...],
                "error_count": N,
                "warning_count": M,
                "checks": {
                    "L1_structure": "PASS"/"FAIL",
                    "L2_required_fields": "PASS"/"FAIL",
                    "L3_type_constraints": "PASS"/"FAIL",
                    "L4_value_ranges": "PASS"/"FAIL",
                    "L5_enums": "PASS"/"FAIL",
                    "L6_cross_field": "PASS"/"FAIL",
                }
            }
        """
        self.errors = []
        self.warnings = []
        checks = {}

        # L1: 顶层结构
        checks["L1_structure"] = "PASS" if self._check_structure(paper) else "FAIL"

        # L2: 必填字段
        checks["L2_required_fields"] = "PASS" if self._check_required_fields(paper) else "FAIL"

        # L3: 题型约束
        checks["L3_type_constraints"] = "PASS" if self._check_type_constraints(paper) else "FAIL"

        # L4: 数值范围
        checks["L4_value_ranges"] = "PASS" if self._check_value_ranges(paper) else "FAIL"

        # L5: 枚举值
        checks["L5_enums"] = "PASS" if self._check_enums(paper) else "FAIL"

        # L6: 跨字段一致性
        checks["L6_cross_field"] = "PASS" if self._check_cross_field(paper) else "FAIL"

        valid = all(v == "PASS" for v in checks.values())

        return {
            "valid": valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "checks": checks,
        }

    # ------------------------------------------------------------------
    # L1: 顶层结构校验
    # ------------------------------------------------------------------

    def _check_structure(self, paper: dict) -> bool:
        ok = True

        # paper_meta
        pm = paper.get("paper_meta", paper)
        required_top = ["title", "total_score", "question_count"]
        for field in required_top:
            if field not in pm:
                self.errors.append(f"L1: 缺少 paper_meta.{field}")
                ok = False

        # questions
        questions = paper.get("questions", [])
        if not questions:
            self.errors.append("L1: questions 数组为空")
            ok = False

        # answer_key（如果存在）
        if "answer_key" in paper:
            ak = paper["answer_key"]
            if "answers" not in ak:
                self.errors.append("L1: answer_key.answers 缺失")
                ok = False
            elif len(ak.get("answers", [])) != len(questions):
                self.warnings.append(f"L1: answer_key 数量({len(ak.get('answers',[]))}) ≠ questions 数量({len(questions)})")

        return ok

    # ------------------------------------------------------------------
    # L2: 必填字段校验（每道题 10 个必填字段）
    # ------------------------------------------------------------------

    def _check_required_fields(self, paper: dict) -> bool:
        ok = True
        questions = paper.get("questions", [])

        for wrapper in questions:
            seq = wrapper.get("sequence_number", "?")
            q = wrapper.get("question", wrapper)  # 兼容两种格式

            for field in QUESTION_REQUIRED_FIELDS:
                if not q.get(field):
                    # analysis 可为空但需记录
                    if field == "analysis":
                        self.warnings.append(f"L2: 题#{seq} 缺少解析 (analysis)")
                    elif field == "options" and q.get("question_type") != "choice":
                        continue  # 非选择题可无 options
                    else:
                        self.errors.append(f"L2: 题#{seq} 缺少必填字段 '{field}'")
                        ok = False

            # knowledge_tags 必须是非空数组
            tags = q.get("knowledge_tags", [])
            if not tags or not isinstance(tags, list) or len(tags) == 0:
                self.errors.append(f"L2: 题#{seq} knowledge_tags 为空")
                ok = False

        return ok

    # ------------------------------------------------------------------
    # L3: 题型约束校验
    # ------------------------------------------------------------------

    def _check_type_constraints(self, paper: dict) -> bool:
        ok = True
        questions = paper.get("questions", [])

        for wrapper in questions:
            seq = wrapper.get("sequence_number", "?")
            q = wrapper.get("question", wrapper)
            qt = q.get("question_type", "")

            if qt not in VALID_QUESTION_TYPES:
                continue  # L5 会处理

            constraints = QUESTION_TYPE_CONSTRAINTS.get(qt, {})

            # 选择题必须有 options 且 >= 2 个
            if constraints.get("must_have_options"):
                options = q.get("options", [])
                min_opts = constraints.get("min_options", 2)
                if len(options) < min_opts:
                    self.errors.append(f"L3: 题#{seq} 是选择题但 options 不足 {min_opts} 个 (实际 {len(options)})")
                    ok = False

            # 填空题应有空白标记
            if constraints.get("must_have_blank_marker"):
                text = q.get("question_text", "")
                pattern = constraints.get("blank_marker_pattern", "")
                if pattern and not re.search(pattern, text):
                    self.warnings.append(f"L3: 题#{seq} 是填空题但题干中未检测到空白标记")

            # 判断题答案校验
            valid_answers = constraints.get("valid_answers", [])
            if valid_answers:
                answer = str(q.get("answer", "")).strip()
                if answer and answer not in valid_answers:
                    self.warnings.append(f"L3: 题#{seq} 是判断题但答案 '{answer[:20]}' 不在标准答案中")

        return ok

    # ------------------------------------------------------------------
    # L4: 数值范围校验
    # ------------------------------------------------------------------

    def _check_value_ranges(self, paper: dict) -> bool:
        ok = True
        questions = paper.get("questions", [])

        seq_nums = []
        total_score = 0

        for wrapper in questions:
            seq = wrapper.get("sequence_number", wrapper.get("question", {}).get("id", "?"))
            q = wrapper.get("question", wrapper)
            score = wrapper.get("assigned_score", wrapper.get("score", 0))
            difficulty = q.get("difficulty", 0)

            # 难度 1-5
            if not isinstance(difficulty, int) or difficulty < 1 or difficulty > 5:
                self.errors.append(f"L4: 题#{seq} difficulty={difficulty} (需在 1-5)")
                ok = False

            # 分值 >= 0
            if score < 0:
                self.errors.append(f"L4: 题#{seq} 分值为负 ({score})")
                ok = False

            seq_nums.append(wrapper.get("sequence_number", 0))
            total_score += score

        # sequence_number 连续性
        if seq_nums:
            expected = list(range(1, len(seq_nums) + 1))
            if seq_nums != expected:
                self.warnings.append(f"L4: sequence_number 不连续 (期望 1..{len(seq_nums)}, 实际 min={min(seq_nums)} max={max(seq_nums)})")

        # 总分一致性
        paper_total = paper.get("paper_meta", paper).get("total_score", 0)
        if paper_total > 0 and abs(paper_total - total_score) > 2:
            self.warnings.append(f"L4: paper_meta.total_score({paper_total}) ≠ 实际分值和({total_score})")

        return ok

    # ------------------------------------------------------------------
    # L5: 枚举值校验
    # ------------------------------------------------------------------

    def _check_enums(self, paper: dict) -> bool:
        ok = True
        questions = paper.get("questions", [])

        for wrapper in questions:
            seq = wrapper.get("sequence_number", "?")
            q = wrapper.get("question", wrapper)

            # subject
            subj = q.get("subject", "")
            if subj and subj not in VALID_SUBJECTS:
                self.errors.append(f"L5: 题#{seq} subject='{subj}' 不在枚举中")
                ok = False

            # grade_level
            gl = q.get("grade_level", "")
            if gl and gl not in VALID_GRADE_LEVELS:
                self.errors.append(f"L5: 题#{seq} grade_level='{gl}' 不在枚举中")
                ok = False

            # question_type
            qt = q.get("question_type", "")
            if qt and qt not in VALID_QUESTION_TYPES:
                self.errors.append(f"L5: 题#{seq} question_type='{qt}' 不在枚举中")
                ok = False

            # grade format
            grade = q.get("grade", "")
            if grade and not re.match(r"^grade_(1[0-2]|[1-9])$", grade):
                self.errors.append(f"L5: 题#{seq} grade='{grade}' 格式不正确")
                ok = False

            # region
            region = q.get("region", "")
            if region not in VALID_REGIONS:
                self.warnings.append(f"L5: 题#{seq} region='{region}' 不在已知地域中")

        return ok

    # ------------------------------------------------------------------
    # L6: 跨字段一致性校验
    # ------------------------------------------------------------------

    def _check_cross_field(self, paper: dict) -> bool:
        ok = True
        questions = paper.get("questions", [])

        for wrapper in questions:
            seq = wrapper.get("sequence_number", "?")
            q = wrapper.get("question", wrapper)

            grade = q.get("grade", "")
            grade_level = q.get("grade_level", "")

            # grade 与 grade_level 一致性
            if grade and grade in GRADE_TO_LEVEL:
                expected_level = GRADE_TO_LEVEL[grade]
                if grade_level and grade_level != expected_level:
                    self.errors.append(
                        f"L6: 题#{seq} grade='{grade}' 应对应 grade_level='{expected_level}'，"
                        f"实际为 '{grade_level}'"
                    )
                    ok = False

        return ok

    # ------------------------------------------------------------------
    # 专项便捷方法
    # ------------------------------------------------------------------

    def validate_question(self, question: dict) -> dict:
        """校验单道题目"""
        return self.validate({"questions": [question]})

    def validate_questions_batch(self, questions: list[dict]) -> dict:
        """校验一批题目"""
        return self.validate({"questions": questions})


# ---------------------------------------------------------------------------
# pytest 集成
# ---------------------------------------------------------------------------

def validate_paper_schema(paper: dict) -> tuple[bool, str]:
    """pytest 友好的验证函数，返回 (通过, 错误信息)"""
    validator = SchemaValidator()
    result = validator.validate(paper)
    if result["valid"]:
        return True, "PASS"
    return False, f"{result['error_count']} 个错误: {'; '.join(result['errors'][:3])}"


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 模拟一张有效试卷
    valid_paper = {
        "paper_meta": {
            "title": "北京市九年级数学模拟试卷",
            "total_score": 100,
            "question_count": 3,
        },
        "questions": [
            {
                "sequence_number": 1,
                "section_order": 1,
                "score": 3,
                "question": {
                    "id": "q1", "subject": "math", "grade": "grade_9",
                    "grade_level": "junior", "region": "beijing",
                    "knowledge_tags": ["一元二次方程"],
                    "difficulty": 2, "question_type": "choice",
                    "question_text": "方程x²-4=0的解是？",
                    "options": [{"label": "A", "text": "±2"}, {"label": "B", "text": "2"},
                                {"label": "C", "text": "-2"}, {"label": "D", "text": "4"}],
                    "answer": "A", "analysis": "x²=4, x=±2",
                    "source": "test",
                },
            },
            {
                "sequence_number": 2,
                "section_order": 2,
                "score": 8,
                "question": {
                    "id": "q2", "subject": "math", "grade": "grade_9",
                    "grade_level": "junior", "region": "beijing",
                    "knowledge_tags": ["一元二次方程"],
                    "difficulty": 3, "question_type": "calculation",
                    "question_text": "解方程：x²-5x+6=0",
                    "answer": "x₁=2, x₂=3", "analysis": "因式分解法",
                    "source": "test",
                },
            },
            {
                "sequence_number": 3,
                "section_order": 2,
                "score": 8,
                "question": {
                    "id": "q3", "subject": "math", "grade": "grade_9",
                    "grade_level": "junior", "region": "",
                    "knowledge_tags": ["一元二次方程"],
                    "difficulty": 4, "question_type": "calculation",
                    "question_text": "求x²-3x+2=0的根",
                    "answer": "x₁=1, x₂=2",
                    "source": "test",
                },
            },
        ],
    }

    validator = SchemaValidator()
    result = validator.validate(valid_paper)
    print(f"有效试卷: valid={result['valid']}, errors={result['error_count']}, warnings={result['warning_count']}")
    for check, status in result["checks"].items():
        print(f"  {check}: {status}")

    # 模拟一张有问题的试卷
    invalid_paper = {
        "paper_meta": {"title": "测试", "total_score": 100, "question_count": 3},
        "questions": [
            {
                "sequence_number": 1,
                "question": {
                    "id": "q1", "subject": "math", "grade": "grade_15",  # 无效年级
                    "grade_level": "college",  # 无效学段
                    "knowledge_tags": [],  # 空标签
                    "difficulty": 6,  # 超出范围
                    "question_type": "choice",  # 选择题
                    "question_text": "这题有问题",
                    # 缺少 answer
                    # 缺少 analysis
                    # 缺少 source
                    # 缺少 options
                },
            },
        ],
    }

    result = validator.validate(invalid_paper)
    print(f"\n无效试卷: valid={result['valid']}, errors={result['error_count']}, warnings={result['warning_count']}")
    for err in result["errors"]:
        print(f"  [ERROR] {err}")
    for warn in result["warnings"]:
        print(f"  [WARN]  {warn}")
