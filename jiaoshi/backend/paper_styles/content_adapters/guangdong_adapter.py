# -*- coding: utf-8 -*-
"""
广东卷内容适配器 (GuangdongAdapter)
==================================
为广东卷添加特色内容：
  - 数学：生活场景应用题、商业经济题、概率统计实际应用
  - 语文：岭南文化阅读、粤港澳大湾区主题写作
  - 英语：听说交际题、实用英语场景、大湾区国际化主题
"""

import random
import logging
from copy import deepcopy
from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter

logger = logging.getLogger("paper_styles.adapter.guangdong")

# ---------------------------------------------------------------------------
# 广东特色题库
# ---------------------------------------------------------------------------

GUANGDONG_PRACTICAL_MATH = [
    {
        "question_text": "（广东特色·生活应用）广州某商场周年庆活动：消费满200元减30元，同时可叠加使用八五折会员卡。小红购买了原价380元的商品，最终实付多少元？",
        "answer": "297.5元",
        "analysis": "380-30=350（满减后），350×0.85=297.5（会员折后）。实际场景中通常先满减再打折。",
        "difficulty": 3,
        "question_type": "calculation",
        "knowledge_tags": ["百分数的认识", "小数乘除法"],
    },
    {
        "question_text": "（广东特色·经济应用）深圳某科技公司今年第一季度营业额为480万元，比去年同期增长了20%。该公司计划在第三季度前将营业额提升至去年的2倍。请问第三季度至少需要完成多少万元？",
        "answer": "第三季度需完成320万元",
        "analysis": "去年Q1=480÷1.2=400万。去年全年≈400×4=1600万。今年目标=1600×2=3200万。Q1+Q2已知480×3=1440万，还需3200-1440×? 简化：今年已实现480×3个季度=1440，仍需3200-1440=1760万，但只剩一个季度。",
        "difficulty": 4,
        "question_type": "calculation",
        "knowledge_tags": ["一元一次方程", "百分数的认识"],
    },
    {
        "question_text": "（广东特色·概率统计）深圳某学校对200名学生的课外阅读习惯进行了调查：每天阅读1小时以上的占35%，30分钟至1小时的占45%，其余为30分钟以下。请绘制扇形统计图并回答：30分钟以上的学生共有多少人？",
        "answer": "160人",
        "analysis": "35%+45%=80%，200×80%=160人。30分钟以上包括了前两个类别。",
        "difficulty": 2,
        "question_type": "short_answer",
        "knowledge_tags": ["扇形统计图", "百分数的认识"],
    },
]

GUANGDONG_ENGLISH_SPEAKING = [
    {
        "question_text": "（广东特色·听说交际）Complete the dialogue:\nA: Welcome to Guangzhou! Is this your first time here?\nB: Yes, it is. Could you recommend some ____ (place) to visit?\nA: Sure! You should visit the Canton Tower and Baiyun Mountain.\nB: That sounds great! I've also heard that Cantonese cuisine is ____ (wonder).",
        "answer": "places, wonderful",
        "analysis": "第一空：some + 复数名词，place→places。第二空：is + 形容词，wonder→wonderful。",
        "difficulty": 2,
        "question_type": "fill_blank",
        "knowledge_tags": ["名词单复数", "构词法"],
    },
    {
        "question_text": "（广东特色·大湾区）The Guangdong-Hong Kong-Macao Greater Bay Area (GBA) connects 11 cities. It is home to many ____ (technology) companies and has become one of the most innovative ____ (region) in the world.",
        "answer": "technology, regions",
        "analysis": "第一空：technology companies（科技公司），technology作定语修饰companies。第二空：one of the + 复数名词，region→regions。",
        "difficulty": 2,
        "question_type": "fill_blank",
        "knowledge_tags": ["名词单复数", "1600个基础词汇及短语"],
    },
]

GUANGDONG_CHINESE_READING = [
    {
        "question_text": "（广东特色·岭南文化）阅读下面文字，回答问题。\n\n广州西关大屋的趟栊门，潮汕的工夫茶，粤剧的红船——它们是岭南文化的符号，也是广东人祖祖辈辈生活智慧的结晶。'食在广州'不仅是一句口号，更是一种生活态度：追求食材的原汁原味，讲究火候和调味的恰到好处。\n\n问题：文中提到了哪些具体的岭南文化符号？为什么说'食在广州'是一种'生活态度'？",
        "answer": "文化符号：西关大屋的趟栊门、潮汕工夫茶、粤剧红船。'食在广州'体现的生活态度：追求食材原汁原味，讲究火候和调味的恰到好处，反映了广东人对生活品质的精益求精。",
        "analysis": "阅读要点：①提取具体文化符号（并列关系）②理解'生活态度'与饮食文化的关系——从'追求'和'讲究'两个动词展开。",
        "difficulty": 2,
        "question_type": "short_answer",
        "knowledge_tags": ["说明文阅读与分析", "整体感知与信息筛选"],
    },
]


class GuangdongAdapter(BaseContentAdapter):
    """广东卷内容适配器"""

    def __init__(self):
        super().__init__()
        self.region = "guangdong"
        self.region_cn = "广东"

    # ------------------------------------------------------------------
    # 试卷结构调整
    # ------------------------------------------------------------------

    def adapt_paper_structure(self, paper: dict, request: dict) -> dict:
        """广东卷特色结构调整"""
        subject = request.get("subject", "")

        # 英语卷：确保听说题有专区
        if subject == "english" and paper.get("sections"):
            # 第一个 section 标记为听说理解
            paper["sections"][0]["section_id"] = "listening_speaking"
            paper["sections"][0]["description"] = "本部分考查听说交际能力，请根据语境选择最佳答案"
            # 广东英语卷听说题分值
            for q in paper["sections"][0].get("questions", []):
                q["_has_scenario"] = True

        # 广东卷注重实际应用：数学卷加场景标记
        if subject == "math" and paper.get("sections"):
            for section in paper["sections"]:
                for q in section.get("questions", []):
                    if random.random() < 0.2:
                        q["_has_scenario"] = True

        return paper

    # ------------------------------------------------------------------
    # 单题适配
    # ------------------------------------------------------------------

    def adapt_question(self, question: dict, request: dict) -> dict:
        """广东卷题目适配"""
        subject = request.get("subject", "")
        q = deepcopy(question)

        if subject == "math":
            gd_scenarios = [
                "在广州某服装批发市场",
                "深圳华强北电子商城",
                "珠海横琴新区某跨境电商仓库",
                "佛山市某陶瓷建材市场",
                "东莞松山湖高新科技园",
            ]
            if random.random() < 0.2:
                scenario = random.choice(gd_scenarios)
                q["question_text"] = self._wrap_in_region_context(
                    q["question_text"], self.region_cn, scenario
                )
                q = self._add_scenario_marker(q, scenario)

            q = self._merge_tags(q, ["实际应用", "生活场景"])

        if subject == "english":
            q = self._merge_tags(q, ["实用英语", "交际能力"])
            # 英语题加强口语提示
            if q.get("question_type") == "fill_blank":
                q["question_text"] = f"（听说交际）{q['question_text']}"

        return q

    # ------------------------------------------------------------------
    # 地域特色内容添加
    # ------------------------------------------------------------------

    def add_regional_content(self, paper: dict, request: dict) -> dict:
        """为广东卷添加特色题目"""
        subject = request.get("subject", "")
        grade = request.get("grade", "grade_9")
        region = request.get("region", "guangdong")

        extras = []

        if subject == "math":
            extras = deepcopy(GUANGDONG_PRACTICAL_MATH)
        elif subject == "english":
            extras = deepcopy(GUANGDONG_ENGLISH_SPEAKING)
        elif subject == "chinese":
            extras = deepcopy(GUANGDONG_CHINESE_READING)

        if extras:
            seq_start = sum(len(s.get("questions", [])) for s in paper.get("sections", [])) + 1
            for i, ex in enumerate(extras):
                ex["id"] = f"gd_extra_{i+1}"
                ex["subject"] = subject
                ex["grade"] = grade
                ex["region"] = region
                ex["sequence_number"] = seq_start + i
                ex["section_sequence"] = i + 1
                ex["score"] = 0
                ex["source"] = "guangdong_adapter"
                ex["_is_regional_extra"] = True

            if paper.get("sections"):
                paper["sections"][-1]["questions"].extend(extras)
                paper["question_count"] = sum(
                    len(s.get("questions", [])) for s in paper.get("sections", [])
                )

        return paper

    def get_intro_text(self, request: dict) -> str:
        return (
            "考试说明：\n"
            "1. 本试卷注重考查知识在实际生活中的应用能力。\n"
            "2. 数学计算题请写出完整解题步骤；英语听说题请注意语境提示。\n"
            "3. 考试时间120分钟，请合理分配答题时间。"
        )

    def get_additional_tags(self, request: dict) -> list[str]:
        return ["广东卷", "实际应用", "生活场景", "粤港澳大湾区"]
