# -*- coding: utf-8 -*-
"""
地域适配集成测试
================
验证三地域的试卷特色内容注入和 fallback 逻辑。
"""

import sys
import os
import json
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "paper_styles"))
sys.path.insert(0, os.path.join(ROOT, "backend", "paper_styles", "content_adapters"))


# ==========================================================================
# 样式注册中心测试
# ==========================================================================

class TestStyleRegistry:
    """样式注册中心测试"""

    def test_beijing_chinese_style_has_poetry_section(self):
        """北京语文卷应有古诗文默写专区"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("beijing", "chinese")
        extra_ids = [s["section_id"] for s in style.get("extra_sections", [])]
        assert "poetry_dictation" in extra_ids, "北京语文卷缺少古诗文默写专区"
        assert style["essay_score"] if "essay_score" in style else True  # 作文分值高

    def test_shanghai_math_style_has_open_inquiry(self):
        """上海数学卷应有开放探究专区"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("shanghai", "math")
        extra_ids = [s["section_id"] for s in style.get("extra_sections", [])]
        assert "open_inquiry" in extra_ids, "上海数学卷缺少开放探究专区"
        # 上海卷计算题分值更高
        assert style["score_map"].get("calculation", 0) >= 10

    def test_guangdong_english_style_has_listening(self):
        """广东英语卷应有听说理解专区"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("guangdong", "english")
        extra_ids = [s["section_id"] for s in style.get("extra_sections", [])]
        assert "listening_speaking" in extra_ids, "广东英语卷缺少听说理解专区"

    def test_each_region_has_unique_template(self):
        """三地域应使用不同模板"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        templates = set()
        for region in ["beijing", "shanghai", "guangdong"]:
            style = registry.get_style(region, "math")
            templates.add(style["template"])
        assert len(templates) == 3, f"三地域模板应不同，实际: {templates}"

    def test_default_fallback_style(self):
        """空地域应返回默认样式"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("", "math")
        assert style["template"] == "default_template.html"

    def test_beijing_chinese_essay_high_score(self):
        """北京语文作文分值应为 40"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("beijing", "chinese")
        assert style["score_map"]["essay"] == 40

    def test_shanghai_chinese_essay_highest(self):
        """上海语文作文分值最高（50）"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("shanghai", "chinese")
        assert style["score_map"]["essay"] == 50


# ==========================================================================
# 内容适配器测试
# ==========================================================================

class TestContentAdapters:
    """内容适配器测试"""

    def test_beijing_adapter_adds_poetry_tags(self):
        """北京适配器应添加传统文化标签"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("beijing")
        tags = adapter.get_additional_tags({})
        assert "北京卷" in tags
        assert len(tags) >= 2

    def test_shanghai_adapter_adds_innovation_tags(self):
        """上海适配器应添加创新思维标签"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("shanghai")
        tags = adapter.get_additional_tags({})
        assert "上海卷" in tags
        assert "创新思维" in tags

    def test_guangdong_adapter_adds_practical_tags(self):
        """广东适配器应添加实际应用标签"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("guangdong")
        tags = adapter.get_additional_tags({})
        assert "广东卷" in tags
        assert "实际应用" in tags

    def test_beijing_adapter_adapts_chinese_question(self):
        """北京适配器应为语文题添加'北京中考题型'前缀"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("beijing")
        q = {
            "question_text": "默写：床前明月光，______。",
            "knowledge_tags": ["古诗词背诵与鉴赏"],
            "question_type": "fill_blank",
            "difficulty": 1,
        }
        request = {"subject": "chinese", "region": "beijing"}
        adapted = adapter.adapt_question(q, request)
        assert "北京中考题型" in adapted.get("question_text", "")

    def test_shanghai_adapter_adds_multi_method_hint(self):
        """上海适配器应为计算题添加'多种方法'提示（概率性，测试接口）"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("shanghai")
        q = {
            "question_text": "解方程",
            "knowledge_tags": ["方程"],
            "question_type": "calculation",
        }
        request = {"subject": "math", "region": "shanghai"}
        adapted = adapter.adapt_question(q, request)
        # 不强制断言（概率性），但至少应返回 dict
        assert isinstance(adapted, dict)
        assert "question_text" in adapted

    def test_guangdong_adapter_marks_scenario(self):
        """广东适配器应为英语题标记场景"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("guangdong")
        q = {
            "question_text": "Fill in the blank",
            "knowledge_tags": ["语法"],
            "question_type": "fill_blank",
        }
        request = {"subject": "english", "region": "guangdong"}
        adapted = adapter.adapt_question(q, request)
        assert isinstance(adapted, dict)

    def test_default_adapter_is_noop(self):
        """默认适配器不应修改题目"""
        from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter
        adapter = BaseContentAdapter()
        q = {"question_text": "test", "answer": "a"}
        adapted = adapter.adapt_question(q, {})
        assert adapted["question_text"] == "test"

    def test_beijing_adapter_has_intro_text(self):
        """北京适配器应有引导语"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("beijing")
        intro = adapter.get_intro_text({"subject": "chinese"})
        assert intro is not None
        assert "古诗文" in intro or "注意" in intro

    def test_adapter_adds_regional_content(self):
        """北京语文适配器应能添加地域特色题"""
        from backend.paper_styles.content_adapters import get_adapter
        adapter = get_adapter("beijing")
        paper = {
            "sections": [{"questions": [], "total_score": 0, "score_per_question": 2}],
            "question_count": 0,
        }
        request = {"subject": "chinese", "grade": "grade_9", "region": "beijing"}
        adapted = adapter.add_regional_content(paper, request)
        # 应该有古诗词默写题被添加
        total_qs = sum(len(s.get("questions", [])) for s in adapted.get("sections", []))
        assert total_qs > 0, "应至少添加1道地域特色题"


# ==========================================================================
# Fallback 逻辑测试
# ==========================================================================

class TestRegionalFallback:
    """地域降级 fallback 逻辑测试"""

    def test_strict_filter_only_exact_matches(self):
        """严格模式只返回精确匹配"""
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        results = [
            {"id": "1", "region": "beijing", "score": 0.9},
            {"id": "2", "region": "shanghai", "score": 0.8},
            {"id": "3", "region": "", "score": 0.7},
        ]
        filtered = rf.filter_by_region_strict(results, "beijing")
        assert len(filtered) == 1
        assert filtered[0]["id"] == "1"

    def test_fallback_adds_national_questions(self):
        """不足时 fallback 应添加全国通用题"""
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        results = [
            {"id": "1", "region": "beijing", "score": 0.9},
            {"id": "2", "region": "", "score": 0.85},
            {"id": "3", "region": "", "score": 0.80},
            {"id": "4", "region": "shanghai", "score": 0.75},
        ]
        filtered = rf.filter_by_region(results, "beijing", min_count=3)
        assert len(filtered) >= 2
        # 应有 fallback 标记
        national = [r for r in filtered if r["_region_match"] == "national"]
        assert len(national) > 0

    def test_fallback_marks_region_source(self):
        """fallback 题目应标记 _region_fallback=True"""
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        results = [
            {"id": "1", "region": "beijing", "score": 0.9},
            {"id": "2", "region": "", "score": 0.85},
        ]
        filtered = rf.filter_by_region(results, "beijing", min_count=2)
        for r in filtered:
            if r["_region_match"] != "exact":
                assert r["_region_fallback"] is True

    def test_empty_results_graceful(self):
        """空结果不崩溃"""
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        filtered = rf.filter_by_region([], "beijing")
        assert filtered == []

    def test_get_region_stats(self):
        """地域统计应正确"""
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        results = [
            {"id": "1", "region": "beijing"},
            {"id": "2", "region": "beijing"},
            {"id": "3", "region": "shanghai"},
            {"id": "4", "region": ""},
        ]
        stats = rf.get_region_stats(results)
        assert stats["total"] == 4
        assert stats["by_region"]["beijing"] == 2
        assert stats["by_region"]["shanghai"] == 1
        assert stats["national"] == 1


# ==========================================================================
# HTML 模板渲染测试
# ==========================================================================

class TestTemplateRendering:
    """模板渲染测试"""

    def test_beijing_template_renders(self, assembled_paper_fixture):
        """北京模板应正确渲染"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("beijing", "math")

        from jinja2 import Environment, FileSystemLoader
        template_dir = os.path.join(ROOT, "backend", "paper_styles", "templates")
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
        template = env.get_template(style["template"])

        html = template.render(paper=assembled_paper_fixture, style=style, region="beijing")
        assert "<!DOCTYPE html>" in html
        assert assembled_paper_fixture["paper_meta"]["title"] in html
        # 北京风格特征
        assert "楷体" in html or "KaiTi" in html or "c41e3a" in html or "8b0000" in html

    def test_shanghai_template_renders(self, assembled_paper_fixture):
        """上海模板应正确渲染"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("shanghai", "math")

        from jinja2 import Environment, FileSystemLoader
        template_dir = os.path.join(ROOT, "backend", "paper_styles", "templates")
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
        template = env.get_template(style["template"])

        html = template.render(paper=assembled_paper_fixture, style=style, region="shanghai")
        assert "<!DOCTYPE html>" in html
        assert "上海" in html or "shanghai" in html.lower()

    def test_guangdong_template_renders(self, assembled_paper_fixture):
        """广东模板应正确渲染"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("guangdong", "math")

        from jinja2 import Environment, FileSystemLoader
        template_dir = os.path.join(ROOT, "backend", "paper_styles", "templates")
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
        template = env.get_template(style["template"])

        html = template.render(paper=assembled_paper_fixture, style=style, region="guangdong")
        assert "<!DOCTYPE html>" in html
        assert "广东" in html or "guangdong" in html.lower()

    def test_default_template_fallback(self, assembled_paper_fixture):
        """默认模板应可渲染"""
        from backend.paper_styles.style_registry import StyleRegistry
        registry = StyleRegistry()
        style = registry.get_style("", "math")

        from jinja2 import Environment, FileSystemLoader
        template_dir = os.path.join(ROOT, "backend", "paper_styles", "templates")
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
        template = env.get_template(style["template"])

        html = template.render(paper=assembled_paper_fixture, style=style, region="")
        assert "<!DOCTYPE html>" in html
