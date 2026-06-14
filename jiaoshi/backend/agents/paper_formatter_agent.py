# -*- coding: utf-8 -*-
"""
试卷格式化 Agent (PaperFormatterAgent)
======================================
Harness 角色: PaperAssembler + PaperRenderer — 将题目组装为符合地域特色的试卷格式。

职责边界：
  只管组装题目 + 渲染 HTML，不检索、不生成新题、不检查质量。

输入：
  /tmp/generated_questions.json（由 QAGeneratorAgent 产出）

输出：
  /tmp/paper_output.html

护栏：
  - 输出前校验：题量 ≥ request.question_count 的 80%
  - 输出前校验：每道题有非空答案
  - 输出前校验：总分值 > 0
  - 输出前校验：试卷包含标题和分区结构
  - 校验不通过 → 输出错误报告而非残缺试卷
"""

import json
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

from backend.paper_styles.style_registry import StyleRegistry, get_style
from backend.paper_styles.content_adapters import get_adapter
from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter

logger = logging.getLogger("agent.paper_formatter")

# LangSmith 追踪（可选）
try:
    from backend.tracing.tracer import trace_paper_generation, get_tracer
    _TRACING_ENABLED = True
except ImportError:
    _TRACING_ENABLED = False
    def trace_paper_generation(func=None, **kwargs):
        if func is None:
            return lambda f: f
        return func
    def get_tracer():
        return None

# ---------------------------------------------------------------------------
# Jinja2 模板引擎
# ---------------------------------------------------------------------------
from jinja2 import Environment, FileSystemLoader

_template_env = None
TEMPLATE_DIR = ROOT / "backend" / "paper_styles" / "templates"


def _get_template_env() -> Environment:
    """延迟初始化 Jinja2 环境"""
    global _template_env
    if _template_env is None:
        if TEMPLATE_DIR.exists():
            _template_env = Environment(
                loader=FileSystemLoader(str(TEMPLATE_DIR)),
                autoescape=False,
            )
        else:
            _template_env = Environment(
                loader=FileSystemLoader(str(ROOT / "frontend")),
                autoescape=False,
            )
    return _template_env


# ---------------------------------------------------------------------------
# 契约文件路径
# ---------------------------------------------------------------------------
GENERATED_QUESTIONS_PATH = ROOT / "tmp" / "generated_questions.json"
PAPER_REQUEST_PATH = ROOT / "tmp" / "paper_request.json"
PAPER_OUTPUT_PATH = ROOT / "tmp" / "paper_output.html"

# ---------------------------------------------------------------------------
# 护栏常量
# ---------------------------------------------------------------------------
MIN_QUESTION_RATIO = 0.80   # 题量最低覆盖比例


class PaperFormatterAgent:
    """
    试卷格式化 Agent（支持地域样式渲染）。

    用法:
        # 自动从 paper_request.json 读取地域信息
        agent = PaperFormatterAgent()
        result = agent.format()

        # 手动指定地域
        agent = PaperFormatterAgent(region="beijing")
        result = agent.format()
    """

    def __init__(self, region: str = None):
        self._region = region
        self._style_registry = StyleRegistry()
        self._adapter: Optional[BaseContentAdapter] = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    @trace_paper_generation
    def format(self, questions_path: Path = None, request_path: Path = None) -> dict:
        """
        将题目组装成 HTML 试卷。

        返回:
            {
                "success": True/False,
                "validation": { "question_count_ok": bool, "answers_complete": bool, ... },
                "output_file": "..."
            }
        """
        _start_time = time.time()
        if questions_path is None:
            questions_path = GENERATED_QUESTIONS_PATH
        if request_path is None:
            request_path = PAPER_REQUEST_PATH

        # ---- 读取输入 ----
        if not questions_path.exists():
            return self._error("generated_questions.json 不存在，请先运行 QAGeneratorAgent")
        if not request_path.exists():
            return self._error("paper_request.json 不存在，请先运行 ParameterParserAgent")

        with open(questions_path, "r", encoding="utf-8") as f:
            questions_obj = json.load(f)
        with open(request_path, "r", encoding="utf-8") as f:
            request_obj = json.load(f)

        request = request_obj.get("request", request_obj)
        all_questions = questions_obj.get("combined_questions", questions_obj.get("questions", []))

        # ---- 检测地域 ----
        region = self._region or request.get("region", "")
        subject = request.get("subject", "math")

        # ---- 初始化样式与适配器 ----
        style = self._style_registry.get_style(region, subject)
        self._adapter = get_adapter(region) if region else BaseContentAdapter()

        logger.info(f"地域样式: region={region or 'default'}, template={style['template']}, adapter={type(self._adapter).__name__}")

        # ---- 适配器：逐题修改 ----
        adapted_questions = []
        for q in all_questions:
            adapted_q = self._adapter.adapt_question(q, request)
            adapted_questions.append(adapted_q)

        # ---- 校验 ----
        validation = self._validate(adapted_questions, request)
        if not validation["all_passed"]:
            logger.error(f"试卷校验不通过: {validation}")
            return {
                "success": False,
                "error": "试卷校验不通过，详见 validation 字段",
                "validation": validation,
                "output_file": str(PAPER_OUTPUT_PATH),
            }

        # ---- 组装试卷结构（使用样式配置的题型顺序和分值） ----
        paper = self._assemble(adapted_questions, request, style)

        # ---- 适配器：调整试卷结构 ----
        paper = self._adapter.adapt_paper_structure(paper, request)

        # ---- 适配器：添加地域特色内容 ----
        paper = self._adapter.add_regional_content(paper, request)

        # ---- 添加引导语 ----
        intro = self._adapter.get_intro_text(request)
        if intro:
            paper["intro_text"] = intro

        # ---- 渲染 HTML（使用 Jinja2 模板） ----
        html = self._render_html(paper, style, region)

        # ---- 输出 ----
        PAPER_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PAPER_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(html)

        _elapsed = (time.time() - _start_time) * 1000
        logger.info(f"试卷生成完成: {paper['question_count']} 题, {paper['total_score']} 分 ({_elapsed:.0f}ms) → {PAPER_OUTPUT_PATH}")

        return {
            "success": True,
            "paper_meta": {
                "title": paper["title"],
                "question_count": paper["question_count"],
                "total_score": paper["total_score"],
                "sections": len(paper["sections"]),
            },
            "validation": validation,
            "output_file": str(PAPER_OUTPUT_PATH),
        }

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def _validate(self, questions: list[dict], request: dict) -> dict:
        """校验试卷完整性"""
        target_count = request.get("question_count", 10)
        actual_count = len(questions)
        min_required = max(1, int(target_count * MIN_QUESTION_RATIO))

        # 1. 题量检查
        q_count_ok = actual_count >= min_required

        # 2. 答案完整度检查
        missing_answers = []
        for i, q in enumerate(questions):
            if not q.get("answer", "").strip():
                missing_answers.append(i + 1)
        answers_ok = len(missing_answers) == 0

        # 3. 总分检查
        score_per_q = max(1, int(100 / max(actual_count, 1)))
        total_score = actual_count * score_per_q
        score_ok = total_score > 0

        # 4. 结构检查
        has_title = bool(request.get("title", ""))
        has_subject = bool(request.get("subject", ""))
        has_grade = bool(request.get("grade", ""))

        all_passed = q_count_ok and answers_ok and score_ok and has_title and has_subject and has_grade

        return {
            "all_passed": all_passed,
            "question_count_ok": q_count_ok,
            "target_count": target_count,
            "actual_count": actual_count,
            "answers_complete": answers_ok,
            "missing_answer_indices": missing_answers,
            "score_ok": score_ok,
            "total_score": total_score,
            "structure_ok": has_title and has_subject and has_grade,
        }

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------

    def _assemble(self, questions: list[dict], request: dict, style: dict = None) -> dict:
        """将题目列表按题型分组为试卷结构（使用样式配置）"""
        if style is None:
            style = DEFAULT_STYLE_CONFIG

        score_map = style.get("score_map", {})
        section_order = style.get("section_order", ["choice", "fill_blank", "true_false", "short_answer", "calculation", "essay"])

        # 按题型分组
        sections_map = {}
        for q in questions:
            qt = q.get("question_type", "short_answer")
            sections_map.setdefault(qt, []).append(q)

        section_labels = {
            "choice": "选择题", "fill_blank": "填空题", "true_false": "判断题",
            "short_answer": "简答题", "calculation": "计算题", "essay": "论述题",
        }

        sections = []
        seq = 1
        total_score = 0

        for order, qt in enumerate(section_order, 1):
            qs = sections_map.get(qt, [])
            if not qs:
                continue

            score_per_q = score_map.get(qt, 5)
            section_total = len(qs) * score_per_q
            total_score += section_total

            numbered_qs = []
            for i, q in enumerate(qs):
                numbered_qs.append({
                    "sequence_number": seq,
                    "section_sequence": i + 1,
                    "score": score_per_q,
                    **{k: v for k, v in q.items() if not k.startswith("_") and k not in ("sequence_number", "section_sequence", "score")},
                })
                seq += 1

            sections.append({
                "section_order": order,
                "section_title": f"{self._section_number(order)}、{section_labels.get(qt, qt)}",
                "question_type": qt,
                "score_per_question": score_per_q,
                "total_score": section_total,
                "questions": numbered_qs,
            })

        return {
            "title": request.get("title", "模拟试卷"),
            "subtitle": self._build_subtitle(request),
            "total_score": total_score,
            "question_count": seq - 1,
            "time_limit_minutes": request.get("time_limit_minutes", 120),
            "sections": sections,
        }

    # 回退默认配置（不依赖 style_registry 时使用）
    DEFAULT_STYLE_CONFIG = {
        "score_map": {"choice": 3, "fill_blank": 3, "true_false": 2,
                       "short_answer": 5, "calculation": 8, "essay": 15},
        "section_order": ["choice", "fill_blank", "true_false", "short_answer", "calculation", "essay"],
    }

    @staticmethod
    def _section_number(n: int) -> str:
        nums = ["一", "二", "三", "四", "五", "六", "七", "八"]
        return nums[n - 1] if 1 <= n <= len(nums) else str(n)

    def _build_subtitle(self, request: dict) -> str:
        region_cn = {"beijing": "北京", "shanghai": "上海", "guangdong": "广东"}
        region = region_cn.get(request.get("region", ""), "")
        grade = request.get("grade", "").replace("grade_", "")
        subject = {"math": "数学", "chinese": "语文", "english": "英语"}.get(
            request.get("subject", ""), request.get("subject", ""))
        parts = []
        if region:
            parts.append(f"{region}卷")
        parts.append(f"{grade}年级")
        parts.append(subject)
        return " ".join(parts) + "模拟试卷"

    # ------------------------------------------------------------------
    # 渲染（Jinja2 模板引擎）
    # ------------------------------------------------------------------

    def _render_html(self, paper: dict, style: dict, region: str = "") -> str:
        """
        使用 Jinja2 模板渲染试卷为 HTML。
        根据地域选择不同的模板文件。
        """
        template_name = style.get("template", "default_template.html")
        env = _get_template_env()

        try:
            template = env.get_template(template_name)
        except Exception as e:
            logger.warning(f"模板 '{template_name}' 加载失败 ({e})，回退到默认模板")
            template = env.get_template("default_template.html")

        html = template.render(paper=paper, style=style, region=region)

        return html

    def _error(self, msg: str) -> dict:
        logger.error(msg)
        return {"success": False, "error": msg, "output_file": str(PAPER_OUTPUT_PATH)}


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def format_paper(questions_path: str = None, request_path: str = None) -> dict:
    """便捷函数：格式化试卷"""
    agent = PaperFormatterAgent()
    return agent.format(
        Path(questions_path) if questions_path else None,
        Path(request_path) if request_path else None,
    )


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    from backend.agents.parameter_parser_agent import ParameterParserAgent
    parser = ParameterParserAgent()
    r = parser.parse({"subject": "数学", "grade": "初三", "region": "beijing",
                       "knowledge_points": ["一元一次方程"], "question_count": 5})
    if not r["success"]:
        print(f"参数解析失败: {r['errors']}")
        sys.exit(1)

    from backend.agents.question_retriever_agent import QuestionRetrieverAgent
    retriever = QuestionRetrieverAgent()
    retriever.retrieve()

    from backend.agents.qa_generator_agent import QAGeneratorAgent
    generator = QAGeneratorAgent()
    generator.generate()

    agent = PaperFormatterAgent()
    result = agent.format()
    print(f"\n格式化结果: success={result['success']}")
    if result["success"]:
        print(f"  标题: {result['paper_meta']['title']}")
        print(f"  题数: {result['paper_meta']['question_count']}")
        print(f"  总分: {result['paper_meta']['total_score']}")
        print(f"  输出: {result['output_file']}")
