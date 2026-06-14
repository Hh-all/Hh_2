# -*- coding: utf-8 -*-
"""
试卷质量检查脚本
自动检查试卷中是否存在：重复题目、答案缺失、格式错误等问题

用法：
    python tests/quality_check.py output/paper.html          # 检查 HTML 渲染的试卷
    python tests/quality_check.py --json data/training_data.json   # 检查题库数据
    python tests/quality_check.py                            # 使用内置样本数据
"""

import json
import os
import re
import sys
from collections import Counter


class QualityReport:
    """质量检查报告"""

    def __init__(self):
        self.issues = []       # 问题列表
        self.warnings = []     # 警告列表
        self.stats = {}        # 统计信息

    def add_issue(self, category: str, detail: str):
        self.issues.append({"category": category, "detail": detail})

    def add_warning(self, category: str, detail: str):
        self.warnings.append({"category": category, "detail": detail})

    def is_perfect(self) -> bool:
        return len(self.issues) == 0

    def print_report(self):
        print("\n" + "=" * 60)
        print("  试卷质量检查报告")
        print("=" * 60)

        if self.stats:
            print("\n[统计信息]")
            for k, v in self.stats.items():
                print(f"  {k}: {v}")

        if self.warnings:
            print(f"\n[警告] 共 {len(self.warnings)} 条:")
            for w in self.warnings:
                print(f"  [{w['category']}] {w['detail']}")

        if self.issues:
            print(f"\n[问题] 共 {len(self.issues)} 条:")
            for i in self.issues:
                print(f"  [{i['category']}] {i['detail']}")
        else:
            print("\n[结果] 未发现问题")

        print("=" * 60)
        return not self.issues  # 返回是否通过


def check_questions(questions: list[dict], source_label: str = "") -> QualityReport:
    """
    对题目列表执行质量检查

    检查项：
    1. 必需字段完整性
    2. 题干/答案非空
    3. 重复题目检测
    4. 选择题选项完整性
    5. 答案格式合法性
    6. 解析非空
    """
    report = QualityReport()
    report.stats["题目总数"] = len(questions)
    report.stats["来源"] = source_label or "未知"

    if len(questions) == 0:
        report.add_issue("空试卷", "题目列表为空")
        return report

    # 题型统计
    type_counts = Counter(q.get("type", "unknown") for q in questions)
    report.stats["题型分布"] = dict(type_counts)

    # 逐题检查
    seen_texts = []
    for i, q in enumerate(questions):
        idx_label = f"第{i+1}题"

        # 1. 必需字段
        for field in ["type", "text", "answer"]:
            if field not in q or q[field] is None:
                report.add_issue("缺少字段", f"{idx_label}: 缺少 '{field}'")
            elif isinstance(q[field], str) and len(q[field].strip()) == 0:
                report.add_issue("空内容", f"{idx_label}: '{field}' 为空")

        # 2. 题型合法性
        valid_types = {"choice", "fill_blank", "short_answer"}
        if q.get("type") not in valid_types:
            report.add_issue("无效题型", f"{idx_label}: type='{q.get('type')}'")

        # 3. 选择题必须有选项
        if q.get("type") == "choice":
            opts = q.get("options")
            if not opts or len(opts) < 2:
                report.add_issue("选项缺失", f"{idx_label}: 选择题选项不足 ({len(opts or [])} 个)")
            elif len(opts) < 4:
                report.add_warning("选项偏少", f"{idx_label}: 建议4个选项，实际 {len(opts)} 个")

        # 4. 填空题应包含填空标记
        if q.get("type") == "fill_blank":
            text = q.get("text", "")
            if "______" not in text and "___" not in text and "（　）" not in text:
                report.add_warning("缺少填空标记", f"{idx_label}: 填空题题干中未找到填空标记 (______)")

        # 5. 解析检查
        if "analysis" not in q or not q.get("analysis", "").strip():
            report.add_warning("缺少解析", f"{idx_label}: 无解析内容")

        # 6. 收集题干用于去重检查
        text = q.get("text", "").strip()
        if text:
            seen_texts.append(text)

    # 重复检测
    dupes = [t for t, c in Counter(seen_texts).items() if c > 1]
    if dupes:
        for d in dupes:
            report.add_issue("重复题目", f"题干出现 {Counter(seen_texts)[d]} 次: {d[:60]}...")

    # 学科一致性（如果 questions 中有 subject 字段）
    subjects = set(q.get("subject", "unknown") for q in questions if "subject" in q)
    if len(subjects) > 1:
        report.add_warning("学科混杂", f"同一份试卷包含多学科: {subjects}")

    # 难度分布
    difficulties = [q.get("difficulty", 0) for q in questions if "difficulty" in q]
    if difficulties:
        report.stats["难度范围"] = f"{min(difficulties)} - {max(difficulties)}"
        report.stats["平均难度"] = round(sum(difficulties) / len(difficulties), 1)
        # 难度值合理性
        for i, d in enumerate(difficulties):
            if d < 1 or d > 5:
                report.add_issue("难度异常", f"第{i+1}题: difficulty={d}（合法范围 1-5）")

    return report


def check_html_paper(html_path: str) -> QualityReport:
    """从渲染后的 HTML 试卷中提取题目并检查"""
    if not os.path.exists(html_path):
        report = QualityReport()
        report.add_issue("文件不存在", f"HTML 文件未找到: {html_path}")
        return report

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 提取题目文本
    questions = []
    # 匹配 <div class="question"> 中的题目
    pattern = r'<span class="q-text">(.*?)</span>'
    matches = re.findall(pattern, html, re.DOTALL)
    for m in matches:
        text = re.sub(r'<[^>]+>', '', m).strip()
        questions.append({"text": text, "type": "unknown", "answer": ""})

    report = check_questions(questions, source_label=f"HTML ({html_path})")
    report.stats["HTML文件"] = os.path.basename(html_path)
    return report


def check_json_data(json_path: str) -> QualityReport:
    """检查 JSON 题库数据"""
    if not os.path.exists(json_path):
        report = QualityReport()
        report.add_issue("文件不存在", f"JSON 文件未找到: {json_path}")
        return report

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return check_questions(data, source_label=os.path.basename(json_path))
    elif isinstance(data, dict) and "questions" in data:
        return check_questions(data["questions"], source_label=os.path.basename(json_path))
    else:
        report = QualityReport()
        report.add_issue("格式错误", "JSON 根应为题目列表或包含 questions 字段的对象")
        return report


# ---------- 命令行入口 ----------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--json" and len(sys.argv) > 2:
            report = check_json_data(sys.argv[2])
        elif arg.endswith(".html"):
            report = check_html_paper(arg)
        elif arg.endswith(".json"):
            report = check_json_data(arg)
        else:
            print(f"用法: python {sys.argv[0]} [output/paper.html | --json data/training_data.json]")
            sys.exit(1)
    else:
        # 使用内置样本数据
        sample = [
            {"type": "choice", "text": "方程 2x + 3 = 7 的解是（　）", "options": ["A. 1", "B. 2", "C. 3", "D. 4"], "answer": "B", "analysis": "2x=4, x=2", "difficulty": 2},
            {"type": "choice", "text": "下列哪个是偶数（　）", "options": ["A. 1", "B. 3", "C. 5", "D. 4"], "answer": "D", "analysis": "偶数是能被2整除的整数", "difficulty": 1},
            {"type": "fill_blank", "text": "三角形内角和为 ______ 度。", "answer": "180", "analysis": "内角和定理", "difficulty": 1},
            {"type": "fill_blank", "text": "方程 3x = 15 的解为 x = ______。", "answer": "5", "analysis": "3x=15, x=5", "difficulty": 2},
            {"type": "short_answer", "text": "求方程 5x - 3 = 2x + 9 的解。", "answer": "x = 4", "analysis": "5x-2x=9+3, 3x=12, x=4", "difficulty": 3},
            # 故意插入一个有问题题目测试检测能力
            {"type": "choice", "text": "方程 2x + 3 = 7 的解是（　）", "options": ["A. 1", "B. 2"], "answer": "", "analysis": "", "difficulty": 2},
        ]
        report = check_questions(sample, source_label="内置样本（含1个故意错误）")

    passed = report.print_report()
    sys.exit(0 if passed else 1)
