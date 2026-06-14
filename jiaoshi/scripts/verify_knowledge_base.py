#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
知识库完整性验证脚本
====================
检查 RAG 知识库的数据完整性，输出各学科/年级/地域的题目数量统计。
"""

import sys, os, json, logging
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("verify_kb")

QUESTIONS_PATH = ROOT / "data" / "processed" / "questions.jsonl"
KG_PATH = ROOT / "data" / "knowledge_graph.json"
SCHEMA_PATH = ROOT / "data" / "schema.json"

PASS_MARK = "[OK]"
FAIL_MARK = "[FAIL]"
WARN_MARK = "[WARN]"

def main():
    results = {"passed": 0, "failed": 0, "warnings": 0}

    def check(condition, msg, severity="fail"):
        if condition:
            print(f"  {PASS_MARK} {msg}")
            results["passed"] += 1
        elif severity == "warn":
            print(f"  {WARN_MARK} {msg}")
            results["warnings"] += 1
        else:
            print(f"  {FAIL_MARK} {msg}")
            results["failed"] += 1

    # ============================================================
    # 1. 题库文件检查
    # ============================================================
    print("=" * 60)
    print("1. 题库数据检查")
    print("=" * 60)

    check(QUESTIONS_PATH.exists(), f"题库文件存在: {QUESTIONS_PATH}")

    if QUESTIONS_PATH.exists():
        questions = []
        with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        questions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        total = len(questions)
        check(total >= 100, f"题库总量: {total} 题 (≥100)", "warn" if total < 100 else "pass")
        check(total >= 1000, f"题量充足: {total} 题 (≥1000)", "warn" if total < 1000 else "pass")

        # 必填字段检查
        required = ["id", "subject", "question_text", "answer"]
        missing_fields = defaultdict(int)
        for q in questions:
            for field in required:
                if not q.get(field):
                    missing_fields[field] += 1

        if missing_fields:
            for field, count in missing_fields.items():
                check(False, f"缺失字段 '{field}': {count} 题")
        else:
            check(True, "所有必填字段完整")

        # 学科分布
        print(f"\n  学科分布:")
        subjects = Counter(q.get("subject", "unknown") for q in questions)
        for subj, count in sorted(subjects.items()):
            pct = count / total * 100
            bar = "█" * int(pct / 2)
            print(f"    {subj:15s}: {count:5d} ({pct:5.1f}%) {bar}")

        # 年级分布
        print(f"\n  学段分布:")
        stages = Counter()
        for q in questions:
            grade = q.get("grade", "")
            if "grade_" in str(grade):
                try:
                    n = int(str(grade).replace("grade_", ""))
                    if n <= 6: stages["小学(1-6)"] += 1
                    elif n <= 9: stages["初中(7-9)"] += 1
                    else: stages["高中(10-12)"] += 1
                except: stages["未知"] += 1
            else:
                stages["未知"] += 1
        for stage, count in sorted(stages.items()):
            print(f"    {stage}: {count}")

        # 地域分布
        print(f"\n  地域分布:")
        regions = Counter(q.get("region", "") for q in questions)
        region_labels = {"beijing": "北京", "shanghai": "上海", "guangdong": "广东", "": "全国通用"}
        for region, count in sorted(regions.items()):
            label = region_labels.get(region, region or "全国通用")
            print(f"    {label:10s}: {count:5d}")

        # 难度分布
        print(f"\n  难度分布:")
        difficulties = Counter(q.get("difficulty", 0) for q in questions)
        for d in range(1, 6):
            count = difficulties.get(d, 0)
            bar = "█" * (count * 50 // max(total, 1))
            print(f"    {d}星: {count:5d} ({count/total*100:5.1f}%) {bar}")

        # 答案覆盖率
        with_answer = sum(1 for q in questions if q.get("answer", "").strip())
        with_analysis = sum(1 for q in questions if q.get("analysis", "").strip())
        check(with_answer == total, f"答案覆盖率: {with_answer}/{total} ({with_answer/total*100:.1f}%)")
        check(with_analysis / max(total, 1) >= 0.7,
              f"解析覆盖率: {with_analysis}/{total} ({with_analysis/total*100:.1f}%) ≥ 70%",
              "warn" if with_analysis / max(total, 1) < 0.7 else "pass")

    # ============================================================
    # 2. 知识图谱检查
    # ============================================================
    print(f"\n{'=' * 60}")
    print("2. 知识图谱检查")
    print("=" * 60)

    check(KG_PATH.exists(), "知识图谱文件存在")

    if KG_PATH.exists():
        with open(KG_PATH, "r", encoding="utf-8") as f:
            kg = json.load(f)
        subjects = [k for k in kg.keys() if not k.startswith("_")]
        print(f"  学科: {subjects}")
        leaf_count = 0
        for subj in subjects:
            subj_data = kg.get(subj, {})
            for stage in subj_data:
                if stage in ("description",): continue
                stage_data = subj_data[stage]
                for module in stage_data:
                    if module in ("description",): continue
                    for subcat_points in stage_data[module].get("子类", {}).values():
                        leaf_count += len(subcat_points)
        check(leaf_count >= 300, f"叶子知识点: {leaf_count} (≥300)")

    # ============================================================
    # 3. 导入检查
    # ============================================================
    print(f"\n{'=' * 60}")
    print("3. 核心模块导入检查")
    print("=" * 60)

    modules = [
        ("KnowledgeGraph", "backend.knowledge.knowledge_graph"),
        ("SchemaValidator", "tests.validators.schema_validator"),
        ("DuplicateDetector", "tests.validators.duplicate_detector"),
        ("StyleRegistry", "backend.paper_styles.style_registry"),
        ("RegionalFilter", "backend.knowledge.regional_filter"),
        ("ParameterParserAgent", "backend.agents.parameter_parser_agent"),
        ("PaperFormatterAgent", "backend.agents.paper_formatter_agent"),
        ("StateMachine", "backend.orchestration.state_machine"),
        ("GuardrailChecker", "backend.guardrails.guardrail_checker"),
        ("MultiCache", "backend.multi_cache"),
        ("TestDatasetBuilder", "tests.rag_evaluation.test_dataset_builder"),
        ("RAGEvaluator", "tests.rag_evaluation.evaluator"),
    ]

    for name, module_path in modules:
        try:
            __import__(module_path)
            print(f"  {PASS_MARK} {name} ({module_path})")
            results["passed"] += 1
        except Exception as e:
            print(f"  {FAIL_MARK} {name}: {e}")
            results["failed"] += 1

    # ============================================================
    # 总结
    # ============================================================
    print(f"\n{'=' * 60}")
    print("验收总结")
    print("=" * 60)
    print(f"  通过: {results['passed']}")
    print(f"  失败: {results['failed']}")
    print(f"  警告: {results['warnings']}")

    if results["failed"] == 0:
        print(f"\n  [OK] 知识库验证通过")
    else:
        print(f"\n  [FAIL] 存在 {results['failed']} 项失败，请检查")

    return results["failed"] == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
