# -*- coding: utf-8 -*-
"""
试卷渲染模块
使用 Jinja2 模板将题目数据渲染为 HTML，并通过 WeasyPrint 导出 PDF
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from jinja2 import Environment, FileSystemLoader


# ---------- 模板引擎 ----------
_template_env = None


def _get_env():
    global _template_env
    if _template_env is None:
        template_dir = os.path.join(ROOT, "frontend")
        _template_env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=False,  # 信任模板内容，不做转义
        )
    return _template_env


# ---------- 题目分组 ----------

def _group_questions(questions: list[dict]) -> list[dict]:
    """
    将平铺的题目列表按题型分组为 sections

    输入：
        [{"type": "choice", "text": "...", ...}, ...]

    输出：
        [
            {
                "label": "选择题",
                "type": "choice",
                "questions": [...],
                "total_score": ...
            },
            ...
        ]
    """
    type_order = ["choice", "fill_blank", "short_answer"]
    type_labels = {
        "choice": "选择题",
        "fill_blank": "填空题",
        "short_answer": "解答题",
    }

    groups = {}
    for q in questions:
        t = q.get("type", "short_answer")
        if t not in groups:
            groups[t] = []
        groups[t].append(q)

    sections = []
    for t in type_order:
        if t in groups:
            count = len(groups[t])
            # 每题默认分值：选择3分、填空3分、解答题剩余分（取整）
            if t == "choice":
                per_score = 3
            elif t == "fill_blank":
                per_score = 3
            else:
                per_score = 10
            sections.append({
                "label": type_labels.get(t, t),
                "type": t,
                "questions": groups[t],
                "total_score": count * per_score,
            })

    return sections


# ---------- 渲染 ----------

def render_to_html(
    questions: list[dict],
    metadata: dict = None,
    output_path: str = None,
    template_name: str = "paper_template.html",
) -> str:
    """
    将题目列表渲染为试卷 HTML

    参数：
        questions:   题目列表，每项包含 type/text/options/answer/analysis
        metadata:    试卷元数据
            {
                "title":             "北京市2026年中考数学模拟卷",
                "subject":           "数学",
                "grade":             "八年级",
                "region":            "北京",
                "total_score":       100,
                "duration_minutes":  90,
            }
        output_path: 输出文件路径，默认 output/paper.html
        template_name: Jinja2 模板文件名

    返回：
        生成的 HTML 字符串
    """
    if metadata is None:
        metadata = {}

    metadata.setdefault("title", f"{metadata.get('region', '')}{metadata.get('subject', '')}模拟卷")
    metadata.setdefault("subject", "")
    metadata.setdefault("grade", "")
    metadata.setdefault("region", "")
    metadata.setdefault("total_score", 100)
    metadata.setdefault("duration_minutes", 90)

    # 分组
    sections = _group_questions(questions)
    total_questions = sum(len(s["questions"]) for s in sections)

    # 渲染
    env = _get_env()
    template = env.get_template(template_name)
    html = template.render(
        metadata=metadata,
        sections=sections,
        total_questions=total_questions,
    )

    # 写入文件
    if output_path is None:
        output_path = os.path.join(ROOT, "output", "paper.html")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[render] HTML 已保存: {output_path}")
    return html


# ---------- PDF 导出 ----------

def export_pdf(html_path: str = None, html_content: str = None,
               pdf_path: str = None) -> str:
    """
    将 HTML 导出为 PDF

    参数：
        html_path:   HTML 文件路径
        html_content: HTML 字符串（与 html_path 二选一）
        pdf_path:    PDF 输出路径，默认 output/paper.pdf

    返回：
        PDF 文件路径，失败返回 None
    """
    if pdf_path is None:
        pdf_path = os.path.join(ROOT, "output", "paper.pdf")

    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # 方案 1: WeasyPrint
    try:
        from weasyprint import HTML
        if html_path:
            HTML(filename=html_path).write_pdf(pdf_path)
        else:
            HTML(string=html_content).write_pdf(pdf_path)
        print(f"[render] PDF 已导出 (WeasyPrint): {pdf_path}")
        return pdf_path
    except ImportError:
        print("[render] weasyprint 未安装")
    except Exception as e:
        print(f"[render] WeasyPrint 导出失败: {e}")

    return None


# ---------- 一键生成 ----------

def generate_paper_html_and_pdf(
    questions: list[dict],
    metadata: dict = None,
    output_dir: str = None,
) -> dict:
    """
    一站式：渲染 HTML + 导出 PDF

    返回：
        {"html_path": "...", "pdf_path": "..." 或 None}
    """
    if output_dir is None:
        output_dir = os.path.join(ROOT, "output")
    os.makedirs(output_dir, exist_ok=True)

    html_path = os.path.join(output_dir, "paper.html")
    pdf_path = os.path.join(output_dir, "paper.pdf")

    html = render_to_html(questions, metadata, output_path=html_path)
    pdf_result = export_pdf(html_content=html, pdf_path=pdf_path)

    return {
        "html_path": html_path,
        "html": html,
        "pdf_path": pdf_result,
    }


# ---------- 命令行测试 ----------

if __name__ == "__main__":
    # 构造模拟题目数据
    sample_questions = [
        {
            "type": "choice",
            "text": "下列方程中，是一元一次方程的是（　）",
            "options": [
                "A. x² + 2x - 3 = 0",
                "B. 2x + 3y = 5",
                "C. 3x - 7 = 2x + 1",
                "D. 1/x + 2 = 0",
            ],
            "answer": "C",
            "analysis": "A是二次方程，B是二元方程，D分母含未知数不是整式方程"
        },
        {
            "type": "choice",
            "text": "方程 2(x - 3) = 4 的解是（　）",
            "options": [
                "A. x = 1",
                "B. x = 5",
                "C. x = 3",
                "D. x = 7",
            ],
            "answer": "B",
            "analysis": "2(x-3)=4 → x-3=2 → x=5"
        },
        {
            "type": "fill_blank",
            "text": "若 3x + a = 10 的解是 x = 2，则 a = ______。",
            "answer": "4",
            "analysis": "代入 x=2：6+a=10，a=4"
        },
        {
            "type": "fill_blank",
            "text": "已知方程 5x - 3 = 2x + 9，则 x = ______。",
            "answer": "4",
            "analysis": "5x-2x=9+3 → 3x=12 → x=4"
        },
        {
            "type": "short_answer",
            "text": "甲、乙两人从相距 30km 的两地同时出发相向而行，甲的速度是 4km/h，乙的速度是 6km/h。问：经过几小时两人相遇？请列方程并求解。",
            "answer": "设经过 x 小时相遇，则 4x + 6x = 30，10x = 30，x = 3。答：经过 3 小时两人相遇。",
            "analysis": "相向而行：甲路程+乙路程=总路程。评分：设未知数1分，列方程2分，解方程2分，答1分，共6分"
        },
    ]

    metadata = {
        "title": "北京市2026年中考数学模拟卷（一）",
        "subject": "数学",
        "grade": "八年级",
        "region": "北京",
        "total_score": 100,
        "duration_minutes": 90,
    }

    result = generate_paper_html_and_pdf(sample_questions, metadata)
    print(f"\nHTML: {result['html_path']}")
    print(f"PDF:  {result['pdf_path'] or '导出失败'}")
