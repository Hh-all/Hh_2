# -*- coding: utf-8 -*-
"""
试卷样式注册中心 (StyleRegistry)
================================
为每个地域×学科组合定义差异化的试卷样式配置。

北京卷特色：
  - 注重传统文化考查，增加古诗词默写、文言文阅读题
  - 试题结构严谨，强调知识体系的系统性
  - 分值分布：基础知识 30%，综合应用 40%，拓展提升 30%

上海卷特色：
  - 注重创新思维，增加开放性问题、应用题情境题
  - 鼓励多元化解题思路
  - 分值分布：基础 25%，应用 35%，开放探究 40%

广东卷特色：
  - 注重实际应用，数学题增加生活场景、英语增加听说题
  - 与粤港澳大湾区经济社会发展相结合
  - 分值分布：基础 30%，应用 45%，拓展 25%
"""

import logging
from copy import deepcopy

logger = logging.getLogger("paper_styles.registry")

# ===================================================================
# 基础默认配置
# ===================================================================

DEFAULT_STYLE = {
    "template": "default_template.html",
    "paper_size": "A4",
    "font_family": '"SimSun", "宋体", serif',
    "font_size": "14pt",
    "header_color": "#333",
    "accent_color": "#1a5276",
    "seal_line_style": "standard",
    # 题型顺序（各地域可覆盖）
    "section_order": ["choice", "fill_blank", "true_false", "short_answer", "calculation", "essay"],
    # 各题型分值（各地域可覆盖）
    "score_map": {
        "choice": 3, "fill_blank": 3, "true_false": 2,
        "short_answer": 5, "calculation": 8, "essay": 15,
    },
    # 难度分布建议
    "difficulty_ratio": {"easy": 0.30, "medium": 0.50, "hard": 0.20},
    # 特色标签（用于内容适配器）
    "feature_tags": [],
    # 页眉
    "header_template": "standard",
    # 附加内容区（如北京卷的古诗词默写专区）
    "extra_sections": [],
}

# ===================================================================
# 地域样式配置
# ===================================================================

BEIJING_STYLE = {
    "template": "beijing_template.html",
    "font_family": '"KaiTi", "楷体", "SimSun", "宋体", serif',
    "font_size": "15pt",
    "header_color": "#8b0000",       # 暗红色——传统文化底蕴
    "accent_color": "#c41e3a",       # 中国红
    "seal_line_style": "beijing_seal",  # 北京特色密封线样式
    "section_order": [
        "choice",        # 一、选择题（基础知识与运用）
        "fill_blank",    # 二、填空题（古诗文默写）
        "true_false",    # 三、判断题
        "short_answer",  # 四、简答题（文言文/现代文阅读）
        "calculation",   # 五、计算与应用
        "essay",         # 六、写作
    ],
    "score_map": {
        "choice": 3, "fill_blank": 2, "true_false": 2,
        "short_answer": 6, "calculation": 8, "essay": 40,  # 北京卷作文分值高
    },
    "difficulty_ratio": {"easy": 0.30, "medium": 0.40, "hard": 0.30},
    "feature_tags": [
        "传统文化", "古诗文默写", "文言文阅读", "名著阅读",
        "综合性学习", "文化常识", "北京地域文化",
    ],
    "header_template": "beijing_header",
    "extra_sections": [
        {
            "section_id": "poetry_dictation",
            "title": "古诗文积累与默写",
            "description": "考查课内古诗文背诵和默写能力",
            "order": 0,  # 在试卷最前面（北京卷特色）
        },
    ],
}

SHANGHAI_STYLE = {
    "template": "shanghai_template.html",
    "font_family": '"Microsoft YaHei", "微软雅黑", "SimHei", "黑体", sans-serif',
    "font_size": "14pt",
    "header_color": "#003366",       # 深蓝——理性、国际化
    "accent_color": "#0077b6",       # 科技蓝
    "seal_line_style": "shanghai_seal",
    "section_order": [
        "choice",        # 一、选择题
        "fill_blank",    # 二、填空题
        "short_answer",  # 三、简答与应用题（开放性问题）
        "calculation",   # 四、计算与建模
        "essay",         # 五、探究与写作
        "true_false",    # 六、判断与辨析（上海特色题型）
    ],
    "score_map": {
        "choice": 3, "fill_blank": 3, "true_false": 3,
        "short_answer": 6, "calculation": 8, "essay": 20,  # 开放探究占高分
    },
    "difficulty_ratio": {"easy": 0.25, "medium": 0.35, "hard": 0.40},
    "feature_tags": [
        "创新思维", "开放探究", "情境应用", "数学建模",
        "跨学科融合", "实际问题解决", "批判性思维",
    ],
    "header_template": "shanghai_header",
    "extra_sections": [
        {
            "section_id": "open_inquiry",
            "title": "开放探究题",
            "description": "考查创新思维和多元化解题能力",
            "order": 99,  # 试卷结尾的压轴探究
        },
    ],
}

GUANGDONG_STYLE = {
    "template": "guangdong_template.html",
    "font_family": '"SimSun", "宋体", "Microsoft YaHei", "微软雅黑", sans-serif',
    "font_size": "14pt",
    "header_color": "#1b5e20",       # 深绿——岭南特色
    "accent_color": "#2e7d32",       # 广东绿
    "seal_line_style": "guangdong_seal",
    "section_order": [
        "choice",        # 一、选择题（含听说理解）
        "fill_blank",    # 二、填空题
        "true_false",    # 三、判断题
        "short_answer",  # 四、简答题（生活场景题）
        "calculation",   # 五、计算与应用（更多商业/生活场景）
        "essay",         # 六、表达与交流
    ],
    "score_map": {
        "choice": 3, "fill_blank": 3, "true_false": 2,
        "short_answer": 5, "calculation": 8, "essay": 25,
    },
    "difficulty_ratio": {"easy": 0.30, "medium": 0.45, "hard": 0.25},
    "feature_tags": [
        "实际应用", "生活场景", "商业数学", "粤港澳大湾区",
        "岭南文化", "实用英语", "听说交际",
    ],
    "header_template": "guangdong_header",
    "extra_sections": [
        {
            "section_id": "listening_speaking",
            "title": "听说理解",
            "description": "考查英语听力和口语交际能力",
            "order": 0,
        },
    ],
}

# ===================================================================
# 学科级别的微调配置
# ===================================================================

SUBJECT_OVERRIDES = {
    "math": {
        "beijing": {
            "extra_sections": [],  # 北京数学卷无古诗文专区
            "score_map": {"choice": 4, "fill_blank": 4, "true_false": 3,
                          "short_answer": 7, "calculation": 10, "essay": 14},
            "difficulty_ratio": {"easy": 0.30, "medium": 0.40, "hard": 0.30},
        },
        "shanghai": {
            "score_map": {"choice": 4, "fill_blank": 4, "true_false": 3,
                          "short_answer": 7, "calculation": 12, "essay": 14},  # 计算题分值更高
        },
        "guangdong": {
            "feature_tags": ["实际应用", "生活场景", "商业数学", "经济问题"],
        },
    },
    "chinese": {
        "beijing": {
            "feature_tags": ["传统文化", "古诗文默写", "文言文阅读",
                             "名著导读", "北京文化", "综合性学习"],
            "extra_sections": [
                {"section_id": "poetry_dictation", "title": "古诗文默写",
                 "description": "默写课内古诗文名篇名句", "order": 0},
            ],
            "score_map": {"choice": 3, "fill_blank": 2, "true_false": 2,
                          "short_answer": 6, "calculation": 0, "essay": 40},
        },
        "shanghai": {
            "feature_tags": ["创新阅读", "比较鉴赏", "时评写作", "跨媒介阅读"],
            "score_map": {"choice": 3, "fill_blank": 3, "true_false": 2,
                          "short_answer": 6, "calculation": 0, "essay": 50},
        },
        "guangdong": {
            "feature_tags": ["岭南文化", "广府文化", "客家文化", "时代精神"],
        },
    },
    "english": {
        "beijing": {
            "feature_tags": ["文化意识", "中国传统", "阅读理解", "书面表达"],
        },
        "shanghai": {
            "feature_tags": ["国际视野", "跨文化交流", "听说能力", "批判性阅读"],
            "score_map": {"choice": 2, "fill_blank": 2, "true_false": 2,
                          "short_answer": 5, "calculation": 0, "essay": 25},
            "extra_sections": [
                {"section_id": "listening", "title": "听力理解",
                 "description": "听对话和独白，选择正确答案", "order": 0},
            ],
        },
        "guangdong": {
            "feature_tags": ["实用英语", "听说交际", "商务英语", "大湾区"],
            "extra_sections": [
                {"section_id": "listening_speaking", "title": "听说理解",
                 "description": "听短文作答，模仿朗读和角色扮演", "order": 0},
            ],
            "score_map": {"choice": 2, "fill_blank": 2, "true_false": 2,
                          "short_answer": 5, "calculation": 0, "essay": 20},
        },
    },
    # 其他学科使用默认配置
    "physics": {"beijing": {}, "shanghai": {}, "guangdong": {}},
    "chemistry": {"beijing": {}, "shanghai": {}, "guangdong": {}},
    "biology": {"beijing": {}, "shanghai": {}, "guangdong": {}},
    "history": {"beijing": {}, "shanghai": {}, "guangdong": {}},
    "geography": {"beijing": {}, "shanghai": {}, "guangdong": {}},
    "politics": {"beijing": {}, "shanghai": {}, "guangdong": {}},
}

# ===================================================================
# 样式注册中心
# ===================================================================

class StyleRegistry:
    """
    试卷样式注册中心，提供地域×学科维度的样式查询。

    用法:
        registry = StyleRegistry()
        style = registry.get_style("beijing", "chinese")
        # → 包含 template, score_map, feature_tags 等完整样式配置
    """

    def __init__(self):
        self._style_cache = {}

    def get_style(self, region: str, subject: str = "math") -> dict:
        """
        获取指定地域×学科组合的完整样式配置。

        参数:
            region:  地域代码 (beijing / shanghai / guangdong / "")
            subject: 学科代码 (math / chinese / english / ...)

        返回:
            合并地域基本样式 + 学科覆盖后的完整样式 dict
        """
        cache_key = f"{region}_{subject}"
        if cache_key in self._style_cache:
            return deepcopy(self._style_cache[cache_key])

        # 1. 从默认样式开始
        style = deepcopy(DEFAULT_STYLE)

        # 2. 应用地域基本样式
        region_styles = {
            "beijing": BEIJING_STYLE,
            "shanghai": SHANGHAI_STYLE,
            "guangdong": GUANGDONG_STYLE,
        }
        if region in region_styles:
            self._deep_merge(style, region_styles[region])

        # 3. 应用学科覆盖
        subject_overrides = SUBJECT_OVERRIDES.get(subject, {})
        region_overrides = subject_overrides.get(region, {})
        if region_overrides:
            self._deep_merge(style, region_overrides)

        self._style_cache[cache_key] = deepcopy(style)
        return style

    def get_template_name(self, region: str, subject: str = "math") -> str:
        """获取模板文件名"""
        style = self.get_style(region, subject)
        return style.get("template", "default_template.html")

    def get_score_map(self, region: str, subject: str = "math") -> dict:
        """获取分值映射"""
        style = self.get_style(region, subject)
        return style.get("score_map", DEFAULT_STYLE["score_map"])

    def get_feature_tags(self, region: str, subject: str = "math") -> list[str]:
        """获取地域特色标签"""
        style = self.get_style(region, subject)
        return style.get("feature_tags", [])

    def get_section_order(self, region: str, subject: str = "math") -> list[str]:
        """获取题型排列顺序"""
        style = self.get_style(region, subject)
        return style.get("section_order", DEFAULT_STYLE["section_order"])

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """深度合并 override 到 base（原地修改 base）"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                StyleRegistry._deep_merge(base[key], value)
            elif key in base and isinstance(base[key], list) and isinstance(value, list):
                # 列表完全替换（如 section_order、extra_sections）
                base[key] = value
            else:
                base[key] = value


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

# 全局单例
_registry = StyleRegistry()


def get_style(region: str, subject: str = "math") -> dict:
    """便捷函数：获取地域样式配置"""
    return _registry.get_style(region, subject)


def get_template_name(region: str, subject: str = "math") -> str:
    """便捷函数：获取模板文件名"""
    return _registry.get_template_name(region, subject)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    registry = StyleRegistry()

    print("=" * 60)
    print("地域样式配置测试")
    print("=" * 60)

    for region in ["beijing", "shanghai", "guangdong", ""]:
        for subject in ["math", "chinese", "english"]:
            style = registry.get_style(region, subject)
            print(f"\n[{region or 'default'}/{subject}]")
            print(f"  模板: {style['template']}")
            print(f"  题型顺序: {style['section_order'][:4]}...")
            print(f"  分值: {style['score_map']}")
            print(f"  特色标签: {style.get('feature_tags', [])[:4]}")
            print(f"  附加区: {[s['section_id'] for s in style.get('extra_sections', [])]}")
