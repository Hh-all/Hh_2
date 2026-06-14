# -*- coding: utf-8 -*-
"""
Harness 试卷验证脚本
====================
验证 assembled_paper.json 是否符合 SPECS.md 中定义的契约。

这是 Harness 框架的 Script 组件——把软约束变成硬性可执行校验。

用法：
  python harness/scripts/validate_paper.py harness/contracts/assembled_paper.json
  python harness/scripts/validate_paper.py --plan harness/contracts/paper_plan.json --paper harness/contracts/assembled_paper.json
"""

import json
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ---------------------------------------------------------------------------
# 校验规则
# ---------------------------------------------------------------------------

REQUIRED_TOP_FIELDS = ["_meta", "paper_meta", "questions", "answer_key"]
REQUIRED_META_FIELDS = ["generated_at", "generated_by", "plan_ref", "batch_ref"]
REQUIRED_PAPER_META_FIELDS = ["title", "total_score", "question_count", "sections"]
REQUIRED_QUESTION_FIELDS = ["sequence_number", "section_order", "question"]
REQUIRED_QUESTION_INNER = ["id", "subject", "grade", "knowledge_tags", "difficulty", "question_text", "answer"]
VALID_QUESTION_TYPES = {"choice", "fill_blank", "true_false", "short_answer", "essay", "calculation"}
VALID_GRADE_LEVELS = {"primary", "junior", "senior"}


def validate(file_path: str, plan_path: str = None) -> tuple[bool, list[str]]:
    """验证试卷文件，返回 (通过?, 错误列表)"""
    errors = []

    # 1. 文件存在
    paper_file = Path(file_path)
    if not paper_file.exists():
        return False, [f"文件不存在: {file_path}"]

    # 2. JSON 解析
    try:
        with open(paper_file, "r", encoding="utf-8") as f:
            paper = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"JSON 解析失败: {e}"]

    # 3. 顶层字段检查
    for field in REQUIRED_TOP_FIELDS:
        if field not in paper:
            errors.append(f"缺少顶层字段: {field}")

    # 4. _meta 检查
    meta = paper.get("_meta", {})
    for field in REQUIRED_META_FIELDS:
        if field not in meta:
            errors.append(f"_meta 缺少字段: {field}")
    if meta.get("generated_by") != "PaperAssembler":
        errors.append(f"_meta.generated_by 必须为 PaperAssembler，实际: {meta.get('generated_by')}")

    # 5. paper_meta 检查
    pm = paper.get("paper_meta", {})
    for field in REQUIRED_PAPER_META_FIELDS:
        if field not in pm:
            errors.append(f"paper_meta 缺少字段: {field}")
    if pm.get("total_score", 0) <= 0:
        errors.append(f"paper_meta.total_score 必须 > 0，实际: {pm.get('total_score')}")

    # 6. questions 检查
    questions = paper.get("questions", [])
    if not questions:
        errors.append("questions 数组为空")
    else:
        seq_nums = []
        for i, q_wrapper in enumerate(questions):
            prefix = f"questions[{i}]"
            for field in REQUIRED_QUESTION_FIELDS:
                if field not in q_wrapper:
                    errors.append(f"{prefix} 缺少字段: {field}")

            q = q_wrapper.get("question", {})
            for field in REQUIRED_QUESTION_INNER:
                if field not in q:
                    errors.append(f"{prefix}.question 缺少字段: {field}")

            # 题型枚举
            qt = q.get("question_type", "")
            if qt and qt not in VALID_QUESTION_TYPES:
                errors.append(f"{prefix}.question.question_type 无效: {qt}")

            # 难度范围
            diff = q.get("difficulty")
            if diff is not None and (not isinstance(diff, int) or diff < 1 or diff > 5):
                errors.append(f"{prefix}.question.difficulty 需在 1-5: {diff}")

            # grade_level 枚举
            gl = q.get("grade_level", "")
            if gl and gl not in VALID_GRADE_LEVELS:
                errors.append(f"{prefix}.question.grade_level 无效: {gl}")

            # 答案非空
            if not q.get("answer", "").strip():
                errors.append(f"{prefix}.question.answer 为空")

            seq_nums.append(q_wrapper.get("sequence_number", -1))

        # sequence_number 连续性
        if seq_nums:
            expected = list(range(1, len(seq_nums) + 1))
            if seq_nums != expected:
                errors.append(f"sequence_number 不连续: 期望 {expected[:5]}... 实际 {seq_nums[:5]}...")

    # 7. answer_key 检查
    ak = paper.get("answer_key", {})
    ak_answers = ak.get("answers", [])
    if len(ak_answers) != len(questions):
        errors.append(f"answer_key 数量({len(ak_answers)}) ≠ questions 数量({len(questions)})")
    for i, ans in enumerate(ak_answers):
        if not ans.get("answer", "").strip():
            errors.append(f"answer_key[{i}].answer 为空")

    # 8. 与 Plan 对照（如果提供）
    if plan_path:
        try:
            with open(Path(plan_path), "r", encoding="utf-8") as f:
                plan = json.load(f)
            plan_count = sum(s.get("count", 0) for s in plan.get("sections", []))
            if len(questions) < plan_count * 0.85:
                errors.append(f"题目数({len(questions)}) < Plan 的 85%({int(plan_count*0.85)})")
        except Exception as e:
            errors.append(f"Plan 对照失败: {e}")

    return (len(errors) == 0, errors)


def main():
    parser = argparse.ArgumentParser(description="Harness 试卷验证脚本")
    parser.add_argument("paper", help="assembled_paper.json 路径")
    parser.add_argument("--plan", "-p", help="paper_plan.json 路径（可选对照）")
    args = parser.parse_args()

    passed, errors = validate(args.paper, args.plan)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] 验证: {args.paper}")
    print("-" * 50)

    if passed:
        print("结果: PASS")
        return 0
    else:
        print(f"结果: FAIL ({len(errors)} 个错误)")
        print("-" * 50)
        for i, err in enumerate(errors, 1):
            print(f"  [{i}] {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
