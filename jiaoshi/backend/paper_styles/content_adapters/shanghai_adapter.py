# -*- coding: utf-8 -*-
"""
上海卷内容适配器 (ShanghaiAdapter)
==================================
为上海卷添加特色内容：
  - 数学：情境应用题、经济建模题、开放探究题（沪教版特色）
  - 语文：比较阅读题、时评写作、跨媒介阅读
  - 英语：听力理解、国际视野主题、跨文化交流
"""

import random
import logging
from copy import deepcopy
from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter

logger = logging.getLogger("paper_styles.adapter.shanghai")

# ---------------------------------------------------------------------------
# 上海特色题库
# ---------------------------------------------------------------------------

SHANGHAI_OPEN_INQUIRY = [
    {
        "question_text": "（上海特色·开放探究）陆家嘴金融区某写字楼的电梯运行速度是普通电梯的1.5倍。如果普通电梯从1层到30层需要45秒，那么高速电梯需要多少秒？请你设计两种不同的解题方法。",
        "answer": "方法一：时间=45÷1.5=30秒。方法二：设高速电梯用时x秒，45/x=1.5，x=30。",
        "analysis": "考查正比例关系和多元化解题策略。上海卷注重引导学生用不同方法解决同一问题，培养发散思维。",
        "difficulty": 3,
        "question_type": "calculation",
        "knowledge_tags": ["正比例与反比例", "一元一次方程"],
    },
    {
        "question_text": "（上海特色·情境应用）外滩附近一家餐厅的外卖服务费按以下规则收取：订单金额不超过50元时不收费；50-100元部分收5%；超过100元部分收8%。小明的订单总金额（含服务费）是189元，商品的原始价格是多少？",
        "answer": "设商品价格为x元。若x≤50，服务费=0；若50<x≤100，服务费=0.05(x-50)；若x>100，服务费=0.05×50+0.08(x-100)。代入总额189，解得x=180。",
        "analysis": "分段函数实际应用。上海卷强调数学建模能力。先判断x>100，列方程x+2.5+0.08(x-100)=189，解得x=180元。",
        "difficulty": 4,
        "question_type": "calculation",
        "knowledge_tags": ["一元一次方程", "不等式应用题"],
    },
]

SHANGHAI_ENGLISH_READING = [
    {
        "question_text": "Read the passage about Shanghai's development and answer:\n\nShanghai has transformed from a small fishing village into one of the world's most dynamic cities. The Pudong New Area, once farmland, is now home to the Shanghai Stock Exchange and some of the world's tallest skyscrapers. The city's metro system is the longest in the world, carrying over 10 million passengers daily.\n\nQuestion: What was Pudong like before its development?",
        "answer": "It was farmland.",
        "analysis": "细节理解题。文中明确提到'The Pudong New Area, once farmland'，once表示'曾经'。",
        "difficulty": 2,
        "question_type": "short_answer",
        "knowledge_tags": ["扫读寻找细节", "阅读理解"],
    },
]


class ShanghaiAdapter(BaseContentAdapter):
    """上海卷内容适配器"""

    def __init__(self):
        super().__init__()
        self.region = "shanghai"
        self.region_cn = "上海"

    # ------------------------------------------------------------------
    # 试卷结构调整
    # ------------------------------------------------------------------

    def adapt_paper_structure(self, paper: dict, request: dict) -> dict:
        """上海卷特色结构调整"""
        subject = request.get("subject", "")

        # 上海卷注重开放探究：确保最后一个 section 是探究题
        # 数学卷：增加开放性问题提示
        if subject == "math" and paper.get("sections"):
            last = paper["sections"][-1]
            if last.get("question_type") in ("calculation", "essay"):
                last["section_title"] = last["section_title"] + "（含开放探究）"
                last["description"] = "请尝试用不同的方法解答。方法越多，得分越高。"
                # 上海卷开放题分值加成
                for q in last.get("questions", []):
                    q["score"] = min(q.get("score", 8) + 2, 15)

        # 上海卷：筛选最后的大题标记为探究区
        if paper.get("sections"):
            paper["sections"][-1]["section_id"] = "open_inquiry"

        return paper

    # ------------------------------------------------------------------
    # 单题适配
    # ------------------------------------------------------------------

    def adapt_question(self, question: dict, request: dict) -> dict:
        """上海卷题目适配"""
        subject = request.get("subject", "")
        q = deepcopy(question)

        if subject == "math":
            sh_scenarios = [
                "在上海地铁某线路规划中",
                "陆家嘴金融区一栋写字楼里",
                "外滩附近某餐厅的外卖服务",
                "浦东机场的国际航班统计中",
                "上海证券交易所的某数据",
            ]
            if random.random() < 0.15:
                scenario = random.choice(sh_scenarios)
                q["question_text"] = self._wrap_in_region_context(
                    q["question_text"], self.region_cn, scenario
                )
                q = self._add_scenario_marker(q, scenario)

            # 上海卷鼓励多元化解题
            if q.get("question_type") == "calculation" and random.random() < 0.1:
                q["question_text"] += "（请用至少两种方法解答）"

        if subject == "english":
            q = self._merge_tags(q, ["国际视野", "跨文化交流"])

        return q

    # ------------------------------------------------------------------
    # 地域特色内容添加
    # ------------------------------------------------------------------

    def add_regional_content(self, paper: dict, request: dict) -> dict:
        """为上海卷添加特色题目"""
        subject = request.get("subject", "")
        grade = request.get("grade", "grade_9")
        region = request.get("region", "shanghai")

        extras = []

        if subject == "math":
            extras = deepcopy(SHANGHAI_OPEN_INQUIRY)

        if subject == "english":
            extras = deepcopy(SHANGHAI_ENGLISH_READING)

        if extras:
            seq_start = sum(len(s.get("questions", [])) for s in paper.get("sections", [])) + 1
            for i, ex in enumerate(extras):
                ex["id"] = f"sh_extra_{i+1}"
                ex["subject"] = subject
                ex["grade"] = grade
                ex["region"] = region
                ex["sequence_number"] = seq_start + i
                ex["section_sequence"] = i + 1
                ex["score"] = 0
                ex["source"] = "shanghai_adapter"
                ex["_is_regional_extra"] = True

            if paper.get("sections"):
                last = paper["sections"][-1]
                last["questions"].extend(extras)
                paper["question_count"] = sum(
                    len(s.get("questions", [])) for s in paper.get("sections", [])
                )

        return paper

    def get_intro_text(self, request: dict) -> str:
        return (
            "考试须知：\n"
            "1. 本试卷侧重考查创新思维和实际应用能力。\n"
            "2. 开放探究题请写出完整的推理或计算过程，鼓励多元化解法。\n"
            "3. 使用黑色字迹的钢笔或签字笔作答。"
        )

    def get_additional_tags(self, request: dict) -> list[str]:
        return ["上海卷", "创新思维", "开放探究", "情境应用"]
