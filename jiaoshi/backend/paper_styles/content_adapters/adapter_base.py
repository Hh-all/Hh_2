# -*- coding: utf-8 -*-
"""
内容适配器基类 (BaseContentAdapter)
===================================
所有地域适配器的抽象基类，定义了适配器接口和默认行为。

职责：
  - 在试卷格式化前，根据地域特色添加/修改内容
  - 为题目追加地域特色的知识标签
  - 调整试卷结构以符合当地考试规范
"""

import logging
from copy import deepcopy
from typing import Optional

logger = logging.getLogger("paper_styles.adapter")


class BaseContentAdapter:
    """
    内容适配器基类，提供默认的空实现。
    子类覆盖以下方法以实现地域特色：

      - add_regional_content:   为试卷添加地域特色题目或区块
      - adapt_question:         修改单道题目的文本或元数据
      - adapt_paper_structure:  调整试卷整体结构（题型顺序、分值等）
      - get_intro_text:         返回试卷开头的引导语
    """

    def __init__(self):
        self.region = "default"
        self.region_cn = "全国"

    # ------------------------------------------------------------------
    # 公开接口（子类覆盖）
    # ------------------------------------------------------------------

    def add_regional_content(self, paper: dict, request: dict) -> dict:
        """
        为试卷添加地域特色内容。
        在 _assemble 完成后、渲染前调用。

        参数:
            paper:   组装后的试卷结构 dict
            request: 原始请求参数

        返回:
            修改后的 paper dict
        """
        return paper

    def adapt_question(self, question: dict, request: dict) -> dict:
        """
        修改单道题目的文本或元数据，使其体现地域特色。
        在组装阶段为每道题调用。

        参数:
            question: 题目 dict
            request:  请求参数

        返回:
            修改后的题目 dict
        """
        return question

    def adapt_paper_structure(self, paper: dict, request: dict) -> dict:
        """
        调整试卷整体结构：题型顺序、分值分布、附加区块等。

        参数:
            paper:   试卷结构 dict
            request: 请求参数

        返回:
            修改后的 paper dict
        """
        return paper

    def get_intro_text(self, request: dict) -> Optional[str]:
        """
        返回试卷开头的引导语（如北京卷的"注意事项"、上海卷的"考试须知"）。

        返回:
            引导语字符串，None 表示不需要
        """
        return None

    def get_additional_tags(self, request: dict) -> list[str]:
        """
        返回需要追加到试卷的知识标签。

        返回:
            标签列表
        """
        return []

    # ------------------------------------------------------------------
    # 辅助方法（供子类使用）
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_in_region_context(text: str, region_cn: str, scenario: str) -> str:
        """将题目文本包装为地域情景题"""
        return f"（{region_cn}{scenario}）{text}"

    @staticmethod
    def _add_scenario_marker(question: dict, scenario: str) -> dict:
        """为题目添加场景标记"""
        q = deepcopy(question)
        q["_has_scenario"] = True
        q["_scenario"] = scenario
        return q

    @staticmethod
    def _merge_tags(question: dict, tags: list[str]) -> dict:
        """合并知识标签"""
        q = deepcopy(question)
        existing = set(q.get("knowledge_tags", []))
        for t in tags:
            existing.add(t)
        q["knowledge_tags"] = list(existing)
        return q
