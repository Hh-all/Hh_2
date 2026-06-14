# -*- coding: utf-8 -*-
"""
北京卷内容适配器 (BeijingAdapter)
=================================
为北京卷添加特色内容：
  - 语文：古诗词默写专区、文言文实词虚词考查、北京文化阅读理解
  - 数学：北京中考真题风格，注重逻辑推理和综合运用
  - 英语：冬奥会/北京文化主题阅读，传统文化英语表达
"""

import random
import logging
from copy import deepcopy
from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter

logger = logging.getLogger("paper_styles.adapter.beijing")

# ---------------------------------------------------------------------------
# 北京特色题库
# ---------------------------------------------------------------------------

BEIJING_CHINESE_EXTRAS = [
    {
        "question_text": "默写：______，疑是地上霜。（李白《静夜思》）",
        "answer": "床前明月光",
        "analysis": "考查李白《静夜思》的背诵默写。注意'床'字不要误写为'窗'。",
        "difficulty": 1,
        "question_type": "fill_blank",
        "knowledge_tags": ["古诗词背诵与鉴赏"],
    },
    {
        "question_text": "默写：海内存知己，______。（王勃《送杜少府之任蜀州》）",
        "answer": "天涯若比邻",
        "analysis": "考查名句默写。注意'涯'、'邻'的正确书写。",
        "difficulty": 1,
        "question_type": "fill_blank",
        "knowledge_tags": ["古诗词背诵与鉴赏"],
    },
    {
        "question_text": "默写：______，蜡炬成灰泪始干。（李商隐《无题》）",
        "answer": "春蚕到死丝方尽",
        "analysis": "名句默写，以春蚕和蜡烛为喻，表达至死不渝的深情。",
        "difficulty": 1,
        "question_type": "fill_blank",
        "knowledge_tags": ["古诗词背诵与鉴赏"],
    },
    {
        "question_text": "默写：先天下之忧而忧，______。（范仲淹《岳阳楼记》）",
        "answer": "后天下之乐而乐",
        "analysis": "考查《岳阳楼记》名句，体现以天下为己任的胸怀。",
        "difficulty": 2,
        "question_type": "fill_blank",
        "knowledge_tags": ["古诗词背诵与鉴赏"],
    },
    {
        "question_text": "解释下列句中加点字的意思：学而时习之，不亦'说'乎？",
        "answer": "说通悦，愉快、高兴",
        "analysis": "通假字。'说'与'悦'古音相同，在古文中常通用。",
        "difficulty": 2,
        "question_type": "short_answer",
        "knowledge_tags": ["文言文实词与虚词"],
    },
]

BEIJING_ENGLISH_EXTRAS = [
    {
        "question_text": "The Forbidden City, also known as the Palace Museum, is one of the most famous ____ (attract) in Beijing.",
        "answer": "attractions",
        "analysis": "名词复数形式。one of the + 复数名词。attract → attraction → attractions。",
        "difficulty": 2,
        "question_type": "fill_blank",
        "knowledge_tags": ["1600个基础词汇及短语"],
    },
    {
        "question_text": "During the 2022 Winter Olympics, athletes from all over the world ____ (compete) in Beijing.",
        "answer": "competed",
        "analysis": "一般过去时。2022年冬奥会已经发生，用过去式competed。",
        "difficulty": 2,
        "question_type": "fill_blank",
        "knowledge_tags": ["九大时态"],
    },
]


class BeijingAdapter(BaseContentAdapter):
    """北京卷内容适配器"""

    def __init__(self):
        super().__init__()
        self.region = "beijing"
        self.region_cn = "北京"

    # ------------------------------------------------------------------
    # 试卷结构调整
    # ------------------------------------------------------------------

    def adapt_paper_structure(self, paper: dict, request: dict) -> dict:
        """北京卷特色结构调整"""
        subject = request.get("subject", "")

        # 语文卷：确保古诗词默写有专区
        if subject == "chinese":
            paper = self._ensure_poetry_section(paper, request)

        # 北京卷作文分值高，调整最后一题分值
        if paper.get("sections"):
            last_section = paper["sections"][-1]
            if last_section.get("question_type") == "essay":
                for q in last_section.get("questions", []):
                    q["score"] = 40
                last_section["score_per_question"] = 40
                last_section["total_score"] = 40 * len(last_section.get("questions", []))
                # 重新计算总分
                paper["total_score"] = sum(s["total_score"] for s in paper["sections"])

        return paper

    def _ensure_poetry_section(self, paper: dict, request: dict) -> dict:
        """确保语文卷有古诗词默写专区"""
        existing_types = {s.get("question_type") for s in paper.get("sections", [])}

        # 将填空题重命名为"古诗文默写"专区
        for section in paper.get("sections", []):
            if section.get("question_type") == "fill_blank":
                section["section_title"] = section["section_title"].replace("填空题", "古诗文默写与填空")
                section["description"] = "本部分考查课内古诗文名篇名句的背诵与默写"
                break

        return paper

    # ------------------------------------------------------------------
    # 单题适配
    # ------------------------------------------------------------------

    def adapt_question(self, question: dict, request: dict) -> dict:
        """北京卷题目适配"""
        subject = request.get("subject", "")
        q = deepcopy(question)

        # 数学：添加北京地域情境
        if subject == "math":
            bj_scenarios = [
                "在故宫博物院研学活动中",
                "测量颐和园长廊的长度时",
                "北京地铁某线路的票价计算",
                "天坛公园回音壁的声学原理中",
            ]
            if random.random() < 0.15:
                scenario = random.choice(bj_scenarios)
                q["question_text"] = self._wrap_in_region_context(
                    q["question_text"], self.region_cn, scenario
                )
                q = self._add_scenario_marker(q, scenario)

        # 语文：追加文化标签
        if subject == "chinese":
            q = self._merge_tags(q, ["北京中考题型", "传统文化"])
            # 文言文/古诗词类题目额外标注
            kps = q.get("knowledge_tags", [])
            if any("古诗词" in k or "文言文" in k or "文言" in k for k in kps):
                q["question_text"] = f"（北京中考题型）{q['question_text']}"

        return q

    # ------------------------------------------------------------------
    # 地域特色内容添加
    # ------------------------------------------------------------------

    def add_regional_content(self, paper: dict, request: dict) -> dict:
        """为北京卷添加地域特色题目"""
        subject = request.get("subject", "")
        grade = request.get("grade", "grade_9")
        region = request.get("region", "beijing")

        extras = []

        # 语文：添加古诗词默写和文言文题
        if subject == "chinese":
            extras = deepcopy(BEIJING_CHINESE_EXTRAS)

        # 英语：添加北京文化主题题
        if subject == "english":
            extras = deepcopy(BEIJING_ENGLISH_EXTRAS)

        if extras:
            # 为每道附加题补齐字段
            seq_start = sum(len(s.get("questions", [])) for s in paper.get("sections", [])) + 1
            for i, ex in enumerate(extras):
                ex["id"] = f"bj_extra_{i+1}"
                ex["subject"] = subject
                ex["grade"] = grade
                ex["region"] = region
                ex["sequence_number"] = seq_start + i
                ex["section_sequence"] = i + 1
                ex["score"] = 0
                ex["source"] = "beijing_adapter"
                ex["_is_regional_extra"] = True

            # 附加到第一个合适的 section
            if paper.get("sections"):
                paper["sections"][0]["questions"].extend(extras)
                paper["sections"][0]["total_score"] = (
                    paper["sections"][0].get("total_score", 0)
                    + len(extras) * paper["sections"][0].get("score_per_question", 0)
                )
                paper["question_count"] = sum(
                    len(s.get("questions", [])) for s in paper.get("sections", [])
                )

        return paper

    def get_intro_text(self, request: dict) -> str:
        """北京卷注意事项"""
        return (
            "注意事项：\n"
            "1. 本试卷满分100分（含作文），考试时间120分钟。\n"
            "2. 请在答题卡上作答，在试卷上作答无效。\n"
            "3. 古诗文默写部分须使用正楷字书写，字迹潦草不得分。\n"
            "4. 考试结束后，将本试卷和答题卡一并交回。"
        )

    def get_additional_tags(self, request: dict) -> list[str]:
        return ["北京卷", "传统文化", "基础扎实"]
