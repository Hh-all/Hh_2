# -*- coding: utf-8 -*-
"""
数据导入与清洗脚本
==================
支持三种数据源接入：
  - JSON 文件（批量导入，支持 CMMaTH / EduAdapt / EduEval 等学术数据集格式）
  - CSV 文件（从 360 题库等导出的表格）
  - 学科网 API（需配置 API Key，详见 CONFIG 区域）

清洗管线：
  1. 去重：基于 question_text 的哈希精确去重 + 编辑距离模糊去重
  2. 标签规范化：将外部知识点标签映射到 data/knowledge_graph.json 的标准体系
  3. 地域推断：如果数据源不含地域信息，根据题干关键词（如"北京市中考"）标注
  4. 难度校准：统一将各种难度表示映射到 1-5 区间

输出：data/processed/questions.jsonl（每行一条 JSON）

用法示例：
  # 导入 JSON 文件
  python scripts/import_clean_data.py --input data/raw/cmmath.json --source cmmath

  # 导入 CSV 文件
  python scripts/import_clean_data.py --input data/raw/export.csv --source 360tiku

  # 从学科网 API 拉取（需先在脚本内配置 API_KEY）
  python scripts/import_clean_data.py --source xkw --api-subject math --api-grade 9

  # 指定输出路径
  python scripts/import_clean_data.py --input data/raw/batch.json --source cmmath --output data/processed/out.jsonl
"""

import json
import os
import re
import sys
import csv
import hashlib
import difflib
import argparse
import logging
from typing import Optional
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ---------------------------------------------------------------------------
# 项目路径
# ---------------------------------------------------------------------------
ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
SCHEMA_PATH = DATA_DIR / "schema" / "unified_question.json"
KG_PATH = DATA_DIR / "knowledge_graph.json"
SYS_SCHEMA_PATH = DATA_DIR / "schema.json"
PROCESSED_DIR = DATA_DIR / "processed"
DEFAULT_OUTPUT = PROCESSED_DIR / "questions.jsonl"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("import_clean")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# 学科网 API 凭据（申请后填入）
XKW_API_KEY = os.environ.get("XKW_API_KEY", "")
XKW_API_SECRET = os.environ.get("XKW_API_SECRET", "")
XKW_BASE_URL = "https://open.xkw.com/api"

# 去重相似度阈值（0-1，越高越严格）
DEDUP_SIMILARITY_THRESHOLD = 0.88

# 标签模糊匹配阈值（可在 KnowledgeGraphIndex 构造时覆盖）
DEFAULT_TAG_MATCH_THRESHOLD = 0.70


# ===================================================================
# 第一部分：统一数据模型
# ===================================================================

# 学段推导规则：grade_1~6 → primary, grade_7~9 → junior, grade_10~12 → senior
def grade_to_level(grade: str) -> str:
    """根据年级标识推导学段"""
    m = re.search(r"(\d+)", grade)
    if not m:
        return ""
    g = int(m.group(1))
    if g <= 6:
        return "primary"
    elif g <= 9:
        return "junior"
    else:
        return "senior"


# 系统支持的学科代码映射（外部分类名称 → 内部代码）
SUBJECT_MAP = {
    # 中文名
    "数学": "math", "语文": "chinese", "英语": "english",
    "物理": "physics", "化学": "chemistry", "生物": "biology",
    "历史": "history", "地理": "geography", "政治": "politics",
    "道德与法治": "politics", "历史与社会": "history",
    # 英文名
    "math": "math", "mathematics": "math",
    "chinese": "chinese",
    "english": "english",
    "physics": "physics", "physic": "physics",
    "chemistry": "chemistry",
    "biology": "biology", "biolog": "biology",
    "history": "history",
    "geography": "geography",
    "politics": "politics", "political": "politics",
    # 计算机 / 科学 → 归入通用（保留原文但不强制要求）
    "computer science": "math",
    "ecology": "biology",
    "geology": "geography",
    "medicine": "biology",
    "meteorology": "geography",
    "science": "physics",
}


# 地域名称映射（中文名 → 内部代码）
REGION_NAME_MAP = {
    "北京": "beijing", "北京市": "beijing",
    "上海": "shanghai", "上海市": "shanghai",
    "广东": "guangdong", "广东省": "guangdong",
    "广州": "guangdong", "广州市": "guangdong",
    "深圳": "guangdong", "深圳市": "guangdong",
}


# 题型映射（外部表示 → 内部代码）
QUESTION_TYPE_MAP = {
    "选择题": "choice", "单选题": "choice", "多项选择题": "choice",
    "choice": "choice", "multiple_choice": "choice", "mcq": "choice",
    "填空题": "fill_blank", "填空": "fill_blank",
    "fill_blank": "fill_blank", "fill": "fill_blank",
    "判断题": "true_false", "判断": "true_false",
    "true_false": "true_false", "true/false": "true_false",
    "简答题": "short_answer", "简答": "short_answer", "问答题": "short_answer",
    "short_answer": "short_answer",
    "论述题": "essay", "材料题": "essay", "材料解析题": "essay",
    "essay": "essay",
    "计算题": "calculation", "计算": "calculation",
    "calculation": "calculation",
    "综合题": "essay",
    "free_form": "short_answer",
    "开放式问答": "essay",
}


# 难度映射（外部表示 → 1-5）
def normalize_difficulty(raw) -> int:
    """将各种难度表示统一映射到 1-5"""
    if raw is None:
        return 3  # 默认中等
    if isinstance(raw, (int, float)):
        if 1 <= raw <= 5:
            return int(raw)
        if 0 <= raw <= 1:
            # 0-1 区间 → 1-5
            return max(1, min(5, round(raw * 5)))
        return 3
    raw_str = str(raw).strip().lower()
    mapping = {
        "easy": 1, "容易": 1, "简单": 1, "基础": 1,
        "较易": 2, "偏易": 2, "basic": 2,
        "medium": 3, "中等": 3, "一般": 3, "normal": 3,
        "较难": 4, "偏难": 4, "提高": 4, "advanced": 4,
        "hard": 5, "困难": 5, "拓展": 5, "挑战": 5,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
        "★": 1, "★★": 2, "★★★": 3, "★★★★": 4, "★★★★★": 5,
    }
    return mapping.get(raw_str, 3)


# ===================================================================
# 第二部分：知识点图谱索引
# ===================================================================

class KnowledgeGraphIndex:
    """从 knowledge_graph.json 构建扁平化知识点索引，用于标签规范化"""

    def __init__(self, kg_path: Path = KG_PATH, tag_match_threshold: float = None):
        if tag_match_threshold is None:
            tag_match_threshold = DEFAULT_TAG_MATCH_THRESHOLD
        self.tag_match_threshold = tag_match_threshold
        with open(kg_path, "r", encoding="utf-8") as f:
            self.kg = json.load(f)
        # 扁平化知识点列表：[(层级路径, "知识点名称"), ...]
        self.flat_tags: list[tuple[str, str]] = []
        # 所有叶子知识点名称（用于模糊匹配）
        self.tag_names: list[str] = []
        # 别名表（手工维护，将常见外部用词映射到标准名）
        self.alias_map: dict[str, str] = {}
        self._build_index()

    def _build_index(self):
        """遍历知识图谱，收集所有叶子知识点"""
        for subject_name, subject_data in self.kg.items():
            if subject_name.startswith("_"):
                continue
            for stage_name, stage_data in subject_data.items():
                if stage_name in ("description",):
                    continue
                for module_name, module_data in stage_data.items():
                    if module_name in ("description",):
                        continue
                    subcats = module_data.get("子类", {})
                    for subcat_name, points in subcats.items():
                        for point in points:
                            full_path = f"{subject_name}>{stage_name}>{module_name}>{subcat_name}>{point}"
                            self.flat_tags.append((full_path, point))
                            self.tag_names.append(point)

        # 构建去重集合
        self.tag_names = list(dict.fromkeys(self.tag_names))

        # 别名表：将常见简写/变体映射到标准标签
        self._build_alias_map()

    def _build_alias_map(self):
        """手工维护常见别名 → 标准标签的映射"""
        aliases = {
            # 数学
            "一元一次方程": "一元一次方程",
            "一次方程": "一元一次方程",
            "二元一次方程组": "二元一次方程组",
            "方程组": "二元一次方程组",
            "二次函数": "二次函数",
            "一次函数": "一次函数",
            "勾股定理": "勾股定理",
            "全等三角形": "全等三角形的判定",
            "三角形内角和": "三角形内角和定理",
            "分数运算": "分数加减法",
            "代数": "整式的加减",
            "几何": "三角形、四边形的认识",
            "函数": "一次函数表达式与图像",
            "统计与概率": "平均数",
            "概率": "可能性（随机事件）",
            # 语文
            "阅读理解": "记叙文阅读与分析",
            "古诗词": "古诗词背诵与鉴赏",
            "文言文": "文言文实词与虚词",
            "成语": "成语与文化",
            "病句修改": "搭配不当",
            "作文": "记叙文写作",
            "修辞": "比喻、拟人、夸张",
            # 英语
            "词汇": "1600个基础词汇及短语",
            "语法": "九大时态",
            "时态": "九大时态",
            "被动语态": "被动语态",
            "从句": "宾语从句与定语从句",
            "写作": "应用文（书信、通知、演讲）",
            "语音": "语音语调的自然流畅",
        }
        self.alias_map = aliases

    def resolve(self, raw_tag: str) -> tuple[str, float]:
        """
        将原始标签映射到标准知识点。
        返回 (标准标签名, 匹配置信度 0-1)。
        """
        tag = raw_tag.strip()
        if not tag:
            return ("", 0.0)

        # 1. 精确匹配
        if tag in self.tag_names:
            return (tag, 1.0)

        # 2. 别名映射
        if tag in self.alias_map:
            mapped = self.alias_map[tag]
            if mapped in self.tag_names:
                return (mapped, 0.95)

        # 3. 模糊匹配（基于编辑距离）
        best_score = 0.0
        best_match = ""
        for std_tag in self.tag_names:
            score = difflib.SequenceMatcher(None, tag, std_tag).ratio()
            if score > best_score:
                best_score = score
                best_match = std_tag

        if best_score >= self.tag_match_threshold:
            return (best_match, best_score)

        # 4. 子串匹配
        for std_tag in self.tag_names:
            if tag in std_tag or std_tag in tag:
                return (std_tag, 0.80)

        # 5. 无法匹配，保留原始标签
        logger.debug(f"标签无法映射到知识图谱: '{raw_tag}' (最佳匹配: '{best_match}' @ {best_score:.2f})")
        return (raw_tag, 0.0)

    def resolve_list(self, raw_tags: list[str]) -> tuple[list[str], float]:
        """批量解析标签列表，返回 (标准标签列表, 平均置信度)"""
        if not raw_tags:
            return (["综合"], 0.0)

        resolved = []
        confidences = []
        for tag in raw_tags:
            name, conf = self.resolve(tag)
            if name:
                resolved.append(name)
                confidences.append(conf)

        # 去重但保持顺序
        seen = set()
        unique = []
        for t in resolved:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return (unique if unique else ["综合"], avg_conf)


# ===================================================================
# 第三部分：地域推断引擎
# ===================================================================

# 地域关键词映射（关键词 → (region_code, 置信度)）
REGION_KEYWORDS: list[tuple[str, str, float]] = [
    # 直辖市
    ("北京", "beijing", 0.95),
    ("北京市", "beijing", 0.95),
    ("上海", "shanghai", 0.95),
    ("上海市", "shanghai", 0.95),
    # 广东
    ("广东", "guangdong", 0.95),
    ("广东省", "guangdong", 0.95),
    ("广州", "guangdong", 0.90),
    ("广州市", "guangdong", 0.90),
    ("深圳", "guangdong", 0.85),
    ("深圳市", "guangdong", 0.85),
    # 常见命题地
    ("海淀", "beijing", 0.80),
    ("西城", "beijing", 0.80),
    ("朝阳", "beijing", 0.80),
    ("东城", "beijing", 0.80),
    ("浦东", "shanghai", 0.75),
    ("徐汇", "shanghai", 0.75),
    # 考试类型 + 地域模式
    ("北京中考", "beijing", 0.98),
    ("北京高考", "beijing", 0.98),
    ("上海中考", "shanghai", 0.98),
    ("上海高考", "shanghai", 0.98),
    ("广东中考", "guangdong", 0.98),
    ("广东高考", "guangdong", 0.98),
    ("广州中考", "guangdong", 0.95),
    ("深圳中考", "guangdong", 0.95),
    # 教材版本推断地域
    ("人教版", "", 0.20),
    ("苏教版", "", 0.20),
    ("沪教版", "shanghai", 0.60),
    ("粤教版", "guangdong", 0.70),
    ("北师大版", "beijing", 0.50),
]


def infer_region(text: str, existing_region: str = "") -> tuple[str, str]:
    """
    根据题干文本推断地域。
    返回 (region_code, 推断来源: 'original' | 'inferred' | 'none')
    """
    if existing_region and existing_region in ("beijing", "shanghai", "guangdong"):
        return (existing_region, "original")

    best_region = ""
    best_conf = 0.0

    for keyword, region_code, confidence in REGION_KEYWORDS:
        if keyword in text and confidence > best_conf:
            best_region = region_code
            best_conf = confidence

    if best_region and best_conf >= 0.60:
        logger.debug(f"地域推断: '{best_region}' (关键词='{...}', 置信度={best_conf:.2f})")
        return (best_region, "inferred")

    return ("", "none")


# ===================================================================
# 第四部分：去重引擎
# ===================================================================

class Deduplicator:
    """基于 question_text 的去重器"""

    def __init__(self, threshold: float = DEDUP_SIMILARITY_THRESHOLD):
        self.threshold = threshold
        self.seen_hashes: set[str] = set()
        self.seen_texts: list[str] = []  # 用于模糊去重（已归一化的文本）

    def _normalize(self, text: str) -> str:
        """归一化：去空白、去标点多余空格"""
        text = re.sub(r"\s+", "", text)
        text = text.lower()
        return text

    def _text_hash(self, text: str) -> str:
        """计算归一化文本的 MD5 哈希"""
        return hashlib.md5(self._normalize(text).encode("utf-8")).hexdigest()

    def is_duplicate(self, text: str) -> bool:
        """
        判断文本是否与已有数据重复。
        返回 True 表示重复，应跳过。
        """
        norm = self._normalize(text)
        h = self._text_hash(text)

        # 1. 精确哈希去重
        if h in self.seen_hashes:
            return True

        # 2. 长度相近时才做模糊去重（优化性能）
        n_len = len(norm)
        for existing in self.seen_texts:
            e_len = len(existing)
            # 长度差异超过 30% 直接跳过
            if abs(n_len - e_len) / max(n_len, e_len) > 0.30:
                continue
            sim = difflib.SequenceMatcher(None, norm, existing).ratio()
            if sim >= self.threshold:
                logger.debug(f"模糊去重命中: sim={sim:.3f}, text={text[:60]}...")
                return True

        # 记录
        self.seen_hashes.add(h)
        self.seen_texts.append(norm)
        return False

    def count(self) -> int:
        return len(self.seen_texts)


# ===================================================================
# 第五部分：数据导入器
# ===================================================================

class JSONImporter:
    """JSON 格式导入器，支持数组格式和 JSONL 格式，自动识别 CMMaTH / EduAdapt 等模式"""

    def __init__(self, kg_index: KnowledgeGraphIndex):
        self.kg = kg_index

    def read_records(self, file_path: Path) -> list[dict]:
        """读取 JSON 文件，返回原始记录列表"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            return []

        # 尝试解析为 JSON 数组
        if content.startswith("["):
            return json.loads(content)

        # 尝试 JSONL（每行一条 JSON）
        if content.startswith("{"):
            records = []
            # 先尝试整个文件是否为单个 JSON 对象
            try:
                obj = json.loads(content)
                if isinstance(obj, list):
                    return obj
                if isinstance(obj, dict):
                    # 可能是带包装的数据集（如 CMMaTH 的完整数据文件）
                    return self._unwrap(obj)
            except json.JSONDecodeError:
                pass

            # JSONL 格式
            for line in content.split("\n"):
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning(f"跳过无效 JSON 行: {line[:80]}...")
            return records

        return []

    def _unwrap(self, obj: dict) -> list[dict]:
        """尝试从嵌套结构中提取记录列表"""
        # 常见键名
        for key in ("data", "records", "questions", "items", "problems", "entries"):
            if key in obj and isinstance(obj[key], list):
                return obj[key]
        return [obj]

    def detect_format(self, record: dict) -> str:
        """自动检测单条记录的格式（cmmath / eduadapt / edueval / generic）"""
        if "knowledge_point" in record or "grade_id" in record:
            return "cmmath"
        if "grade_label" in record and "subject" in record:
            return "eduadapt"
        if "cognitive_level" in record or "task_type" in record:
            return "edueval"
        return "generic"

    def convert(self, raw: dict, source: str, fmt: str = "auto") -> Optional[dict]:
        """将原始记录转换为统一格式"""
        if fmt == "auto":
            fmt = self.detect_format(raw)

        converter = getattr(self, f"_convert_{fmt}", None)
        if converter is None:
            converter = self._convert_generic

        try:
            return converter(raw, source)
        except Exception as e:
            logger.warning(f"转换失败 (source={source}, id={raw.get('id', '?')}): {e}")
            return None

    # ---- CMMaTH 格式转换 ----
    def _convert_cmmath(self, raw: dict, source: str) -> dict:
        """
        CMMaTH 格式示例：
        {
          "problem_id": "00014",
          "question": "如图，在矩形ABCD中...",
          "answer": "4√3-2-π",
          "answer_type": "free_form",
          "grade_id": 9,
          "knowledge_point": "圆(与圆有关的计算(弧长、扇形面积应用(...)))",
          "skill": "运算能力",
          "analysis": "..."
        }
        """
        grade_num = raw.get("grade_id", 7)
        grade = f"grade_{grade_num}"

        # 知识点层级 → 扁平化
        kp_raw = raw.get("knowledge_point", "")
        tags = self._flatten_cmmath_kp(kp_raw)

        question_type = QUESTION_TYPE_MAP.get(
            raw.get("answer_type", "free_form"), "short_answer"
        )

        return {
            "id": f"cmmath_{raw.get('problem_id', '')}",
            "subject": "math",
            "grade": grade,
            "grade_level": grade_to_level(grade),
            "region": infer_region(raw.get("question", ""))[0],
            "knowledge_tags": tags[0] if isinstance(tags, tuple) else tags,
            "difficulty": 3,  # CMMaTH 无难度标注，默认中等
            "question_type": question_type,
            "question_text": raw.get("question", ""),
            "options": self._extract_options_cmmath(raw),
            "answer": str(raw.get("answer", "")),
            "analysis": raw.get("analysis", ""),
            "source": source,
            "year": None,
            "_import_meta": {
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "original_id": raw.get("problem_id", ""),
                "original_tags": [kp_raw] if kp_raw else [],
                "original_difficulty": "",
                "tag_mapping_confidence": tags[1] if isinstance(tags, tuple) else 0.8,
                "region_source": "inferred" if infer_region(raw.get("question", ""))[0] else "none",
            },
        }

    def _flatten_cmmath_kp(self, kp_str: str) -> tuple[list[str], float]:
        """将 CMMaTH 层级知识点字符串（如 '圆(与圆有关的计算(...))'）扁平化"""
        if not kp_str:
            return (["综合"], 0.0)
        # 提取各级标签（括号外和括号内）
        parts = []
        # 分割：取最外层逗号 / 括号层级
        current = ""
        depth = 0
        for ch in kp_str:
            if ch == "(":
                if current.strip():
                    parts.append(current.strip())
                current = ""
                depth += 1
            elif ch == ")":
                if current.strip():
                    parts.append(current.strip())
                current = ""
                depth -= 1
            elif ch == "," and depth == 0:
                if current.strip():
                    parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())

        if not parts:
            return self.kg.resolve_list([kp_str])

        # 对每个部分做映射
        return self.kg.resolve_list(parts)

    def _extract_options_cmmath(self, raw: dict) -> list[dict]:
        """CMMaTH 的选择题选项"""
        if raw.get("answer_type") != "multiple_choice":
            return []
        choices = raw.get("choices", [])
        return [{"label": chr(65 + i), "text": c} for i, c in enumerate(choices)]

    # ---- EduAdapt 格式转换 ----
    def _convert_eduadapt(self, raw: dict, source: str) -> dict:
        """
        EduAdapt 格式（推测）：
        {
          "question": "...",
          "answer": "...",
          "grade_label": "6-8",
          "subject": "biology",
          "question_type": "open_ended" | "multiple_choice",
          "options": {"A": "...", "B": "...", "C": "...", "D": "..."}
        }
        """
        grade_label = raw.get("grade_label", "6-8")
        grade = self._map_eduadapt_grade(grade_label)
        subject = SUBJECT_MAP.get(raw.get("subject", "").lower(), "math")
        q_type = "choice" if raw.get("question_type") == "multiple_choice" else "short_answer"

        options = []
        if raw.get("options"):
            for label, text in raw["options"].items():
                options.append({"label": label, "text": text})

        return {
            "id": f"eduadapt_{raw.get('id', hashlib.md5(raw.get('question', '').encode()).hexdigest()[:8])}",
            "subject": subject,
            "grade": grade,
            "grade_level": grade_to_level(grade),
            "region": "",  # EduAdapt 无中国地域信息
            "knowledge_tags": self.kg.resolve_list(raw.get("knowledge_tags", [subject]))[0],
            "difficulty": 3,
            "question_type": q_type,
            "question_text": raw.get("question", ""),
            "options": options,
            "answer": str(raw.get("answer", "")),
            "analysis": raw.get("analysis", ""),
            "source": source,
            "year": raw.get("year"),
            "_import_meta": {
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "original_id": str(raw.get("id", "")),
                "original_tags": raw.get("knowledge_tags", []),
                "original_difficulty": "",
                "tag_mapping_confidence": 0.7,
                "region_source": "none",
            },
        }

    def _map_eduadapt_grade(self, label: str) -> str:
        """EduAdapt 年级标签映射"""
        mapping = {
            "1-2": "grade_2", "3-5": "grade_4", "6-8": "grade_7", "9-12": "grade_10",
            "1": "grade_1", "2": "grade_2", "3": "grade_3",
            "4": "grade_4", "5": "grade_5", "6": "grade_6",
            "7": "grade_7", "8": "grade_8", "9": "grade_9",
            "10": "grade_10", "11": "grade_11", "12": "grade_12",
        }
        return mapping.get(str(label), "grade_7")

    # ---- EduEval 格式转换 ----
    def _convert_edueval(self, raw: dict, source: str) -> dict:
        """
        EduEval 格式（推测）：
        {
          "question": "...",
          "answer": "...",
          "grade_level": "junior",
          "subject": "math",
          "task_type": "reasoning",
          "cognitive_level": "application"
        }
        """
        grade = raw.get("grade", "grade_7")
        subject = SUBJECT_MAP.get(raw.get("subject", "").lower(), "math")
        tags = [raw.get("task_type", ""), raw.get("cognitive_level", "")]
        tags = [t for t in tags if t]

        resolved_tags, tag_conf = self.kg.resolve_list(tags)

        return {
            "id": f"edueval_{raw.get('id', hashlib.md5(raw.get('question', '').encode()).hexdigest()[:8])}",
            "subject": subject,
            "grade": grade,
            "grade_level": grade_to_level(grade),
            "region": "",
            "knowledge_tags": resolved_tags,
            "difficulty": normalize_difficulty(raw.get("difficulty")),
            "question_type": QUESTION_TYPE_MAP.get(raw.get("question_type", "short_answer"), "short_answer"),
            "question_text": raw.get("question", ""),
            "options": [],
            "answer": str(raw.get("answer", "")),
            "analysis": raw.get("analysis", ""),
            "source": source,
            "year": raw.get("year"),
            "_import_meta": {
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "original_id": str(raw.get("id", "")),
                "original_tags": tags,
                "original_difficulty": str(raw.get("difficulty", "")),
                "tag_mapping_confidence": tag_conf,
                "region_source": "none",
            },
        }

    # ---- 通用格式转换 ----
    def _convert_generic(self, raw: dict, source: str) -> dict:
        """通用格式转换：尽力从原始字段中提取信息"""
        # ID
        rid = raw.get("id", raw.get("question_id", raw.get("problem_id", "")))
        uid = f"{source}_{rid}" if rid else f"{source}_{hashlib.md5(str(raw).encode()).hexdigest()[:8]}"

        # 学科
        subject_raw = raw.get("subject", raw.get("discipline", raw.get("学科", "")))
        subject = SUBJECT_MAP.get(str(subject_raw).lower(), "math")

        # 年级
        grade_raw = raw.get("grade", raw.get("grade_level", raw.get("年级", "grade_7")))
        grade = self._normalize_grade(grade_raw)

        # 地域
        region_raw = raw.get("region", raw.get("area", raw.get("省市", raw.get("province", ""))))
        # 中文名 → 代码映射
        if region_raw in REGION_NAME_MAP:
            region_raw = REGION_NAME_MAP[region_raw]
        elif region_raw not in ("beijing", "shanghai", "guangdong"):
            region_raw = ""

        # 知识点标签
        raw_tags = raw.get("knowledge_tags", raw.get("tags", raw.get("topics",
                  raw.get("knowledge_points", raw.get("知识点", [])))))
        if isinstance(raw_tags, str):
            raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        if not isinstance(raw_tags, list):
            raw_tags = []
        resolved_tags, tag_conf = self.kg.resolve_list(raw_tags)

        # 难度
        diff_raw = raw.get("difficulty", raw.get("level", raw.get("难度", 3)))
        difficulty = normalize_difficulty(diff_raw)

        # 题型
        qtype_raw = raw.get("question_type", raw.get("type", raw.get("题型", "short_answer")))
        qtype = QUESTION_TYPE_MAP.get(str(qtype_raw).lower(), "short_answer")

        # 选项
        options = []
        raw_options = raw.get("options", raw.get("choices", raw.get("选项", [])))
        if isinstance(raw_options, dict):
            for k, v in raw_options.items():
                options.append({"label": str(k), "text": str(v)})
        elif isinstance(raw_options, list):
            for i, opt in enumerate(raw_options):
                if isinstance(opt, dict):
                    options.append(opt)
                else:
                    options.append({"label": chr(65 + i), "text": str(opt)})

        # 题干
        question_text = raw.get("question_text", raw.get("question",
                       raw.get("stem", raw.get("题干", raw.get("text", "")))))

        # 推断地域
        inferred_region, region_source = infer_region(question_text, region_raw)

        return {
            "id": str(uid),
            "subject": subject,
            "grade": grade,
            "grade_level": grade_to_level(grade),
            "region": inferred_region,
            "knowledge_tags": resolved_tags,
            "difficulty": difficulty,
            "question_type": qtype,
            "question_text": question_text,
            "options": options,
            "answer": str(raw.get("answer", raw.get("答案", ""))),
            "analysis": raw.get("analysis", raw.get("解析", raw.get("explanation", ""))),
            "source": source,
            "year": raw.get("year", raw.get("年份")),
            "_import_meta": {
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "original_id": str(rid),
                "original_tags": raw_tags,
                "original_difficulty": str(diff_raw),
                "tag_mapping_confidence": tag_conf,
                "region_source": region_source,
            },
        }

    @staticmethod
    def _normalize_grade(raw) -> str:
        """将各种年级表示归一化为 grade_N 格式"""
        raw_str = str(raw).strip().lower()
        if raw_str.startswith("grade_"):
            return raw_str
        # 数字
        m = re.search(r"(\d+)", raw_str)
        if m:
            g = int(m.group(1))
            if 1 <= g <= 12:
                return f"grade_{g}"
            if 13 <= g <= 15:
                return f"grade_{g - 3}"
        # 中文
        cn_map = {
            "一年级": "grade_1", "二年级": "grade_2", "三年级": "grade_3",
            "四年级": "grade_4", "五年级": "grade_5", "六年级": "grade_6",
            "七年级": "grade_7", "八年级": "grade_8", "九年级": "grade_9",
            "高一": "grade_10", "高二": "grade_11", "高三": "grade_12",
            "初一": "grade_7", "初二": "grade_8", "初三": "grade_9",
        }
        return cn_map.get(raw_str, "grade_7")


class CSVImporter:
    """CSV 格式导入器，支持自定义列映射"""

    # 默认列名映射（CSV 列名 → 统一字段名，支持中英文）
    DEFAULT_COLUMN_MAP = {
        # 英文列名
        "id": "id", "question_id": "id", "problem_id": "id",
        "subject": "subject", "discipline": "subject",
        "grade": "grade", "grade_level": "grade",
        "region": "region", "area": "region", "province": "region",
        "knowledge_tags": "knowledge_tags", "tags": "knowledge_tags", "topics": "knowledge_tags",
        "difficulty": "difficulty", "level": "difficulty",
        "question_type": "question_type", "type": "question_type",
        "question_text": "question_text", "question": "question_text", "stem": "question_text",
        "answer": "answer",
        "analysis": "analysis", "explanation": "analysis",
        "year": "year",
        "options": "options",
        # 中文列名
        "编号": "id", "题目ID": "id",
        "学科": "subject",
        "年级": "grade",
        "地区": "region", "省份": "region", "省市": "region",
        "知识点": "knowledge_tags", "标签": "knowledge_tags",
        "难度": "difficulty", "难度等级": "difficulty",
        "题型": "question_type",
        "题干": "question_text", "题目": "question_text",
        "答案": "answer", "参考答案": "answer",
        "解析": "analysis", "题目解析": "analysis",
        "年份": "year", "来源年份": "year",
        "选项": "options",
    }

    def __init__(self, kg_index: KnowledgeGraphIndex):
        self.kg = kg_index
        self.json_importer = JSONImporter(kg_index)

    def read_records(self, file_path: Path, column_map: dict = None) -> list[dict]:
        """读取 CSV 文件，返回标准化后的原始记录列表"""
        if column_map is None:
            column_map = {}

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            raw_records = []
            for row in reader:
                mapped = self._apply_column_map(row, column_map)
                if mapped.get("question_text"):
                    raw_records.append(mapped)
                else:
                    logger.debug(f"跳过无题干行: {list(row.values())[:3]}")

        return raw_records

    def _apply_column_map(self, row: dict, user_map: dict) -> dict:
        """将 CSV 行映射到标准字段名"""
        merged_map = {**self.DEFAULT_COLUMN_MAP, **user_map}
        mapped = {}
        for csv_col, value in row.items():
            target = merged_map.get(csv_col, merged_map.get(csv_col.strip()))
            if target and value:
                if target == "knowledge_tags":
                    # 支持逗号/分号分隔
                    tags = [t.strip() for t in re.split(r"[,;，；]", str(value)) if t.strip()]
                    mapped[target] = tags
                elif target == "options":
                    try:
                        mapped[target] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        mapped[target] = []
                elif target == "difficulty":
                    mapped[target] = normalize_difficulty(value)
                elif target == "year":
                    try:
                        mapped[target] = int(value)
                    except (ValueError, TypeError):
                        mapped[target] = None
                else:
                    mapped[target] = str(value).strip()
        return mapped


class XKWAPIImporter:
    """学科网 API 导入器（需配置 API Key）"""

    def __init__(self, kg_index: KnowledgeGraphIndex, api_key: str = "", api_secret: str = ""):
        self.kg = kg_index
        self.api_key = api_key or XKW_API_KEY
        self.api_secret = api_secret or XKW_API_SECRET
        self.json_importer = JSONImporter(kg_index)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def fetch_questions(
        self,
        subject: str = "math",
        grade: int = 9,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> list[dict]:
        """
        从学科网 API 拉取试题。

        参数：
            subject: 学科（math/chinese/english/physics/chemistry/biology/history/geography/politics）
            grade: 年级编号（1-12）
            page_size: 每页数量
            max_pages: 最大页数

        返回：
            统一格式的试题列表
        """
        if not self.is_configured():
            logger.error("学科网 API 未配置。请设置环境变量 XKW_API_KEY 和 XKW_API_SECRET，"
                         "或修改脚本顶部 CONFIG 区域。")
            return []

        all_records = []
        for page in range(1, max_pages + 1):
            try:
                resp = self._call_api(subject, grade, page, page_size)
                records = self._parse_response(resp)
                all_records.extend(records)
                logger.info(f"学科网 API: 第 {page} 页，获取 {len(records)} 条")
                if len(records) < page_size:
                    break
            except Exception as e:
                logger.error(f"学科网 API 第 {page} 页请求失败: {e}")
                break

        return all_records

    def _call_api(self, subject: str, grade: int, page: int, page_size: int) -> dict:
        """
        调用学科网试题推送 API。

        注意：这是根据学科网开放平台文档编写的骨架代码。
        实际对接时需根据最新 API 文档调整 endpoint、参数名和签名方式。
        """
        import urllib.request
        import urllib.parse

        params = {
            "appKey": self.api_key,
            "subject": subject,
            "grade": str(grade),
            "pageNo": str(page),
            "pageSize": str(page_size),
            "timestamp": str(int(datetime.now().timestamp() * 1000)),
        }
        # 签名（示例，实际以文档为准）
        sign_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["sign"] = hashlib.md5(f"{sign_str}{self.api_secret}".encode()).hexdigest()

        url = f"{XKW_BASE_URL}/exam/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})

        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_response(self, resp: dict) -> list[dict]:
        """解析学科网 API 响应为统一格式"""
        records = []
        data = resp.get("data", resp.get("result", resp))

        items = data if isinstance(data, list) else data.get("list", data.get("records", []))
        for item in items:
            grade_num = item.get("grade", 7)
            grade = f"grade_{grade_num}"

            tags_raw = item.get("knowledgePoints", item.get("knowledge", []))
            if isinstance(tags_raw, str):
                tags_raw = [t.strip() for t in tags_raw.split(",")]

            resolved_tags, tag_conf = self.kg.resolve_list(tags_raw)

            region_raw = item.get("region", item.get("area", ""))
            qtype_raw = item.get("questionType", item.get("type", "short_answer"))
            qtype = QUESTION_TYPE_MAP.get(str(qtype_raw).lower(), "short_answer")

            # 选项
            options = []
            if qtype == "choice" and item.get("options"):
                for i, opt in enumerate(item["options"]):
                    if isinstance(opt, dict):
                        options.append(opt)
                    else:
                        options.append({"label": chr(65 + i), "text": str(opt)})

            record = {
                "id": f"xkw_{item.get('questionId', item.get('id', ''))}",
                "subject": SUBJECT_MAP.get(item.get("subject", "math").lower(), "math"),
                "grade": grade,
                "grade_level": grade_to_level(grade),
                "region": region_raw if region_raw in ("beijing", "shanghai", "guangdong") else infer_region(item.get("questionText", item.get("stem", "")), region_raw)[0],
                "knowledge_tags": resolved_tags,
                "difficulty": normalize_difficulty(item.get("difficulty", 3)),
                "question_type": qtype,
                "question_text": item.get("questionText", item.get("stem", "")),
                "options": options,
                "answer": str(item.get("answer", item.get("correctAnswer", ""))),
                "analysis": item.get("analysis", item.get("explanation", "")),
                "source": "xkw",
                "year": item.get("year"),
                "_import_meta": {
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "original_id": str(item.get("questionId", item.get("id", ""))),
                    "original_tags": tags_raw,
                    "original_difficulty": str(item.get("difficulty", "")),
                    "tag_mapping_confidence": tag_conf,
                    "region_source": "original" if region_raw else "inferred",
                },
            }
            records.append(record)

        return records


# ===================================================================
# 第六部分：清洗管线
# ===================================================================

class CleaningPipeline:
    """数据清洗管线：去重 → 标签规范化 → 地域推断 → 难度校准 → 输出"""

    def __init__(self, kg_index: KnowledgeGraphIndex):
        self.kg = kg_index
        self.deduplicator = Deduplicator()
        self.stats = {
            "total_input": 0,
            "filtered_no_text": 0,
            "filtered_invalid_subject": 0,
            "filtered_duplicate": 0,
            "tag_mapped": 0,
            "tag_unmapped": 0,
            "region_inferred": 0,
            "difficulty_calibrated": 0,
            "total_output": 0,
        }

    def clean(self, records: list[dict], source: str = "generic") -> list[dict]:
        """执行完整清洗管线"""
        self.stats["total_input"] = len(records)
        cleaned = []

        for record in records:
            # 1. 过滤空题干
            if not record.get("question_text", "").strip():
                self.stats["filtered_no_text"] += 1
                continue

            # 2. 校验学科
            valid_subjects = {"math", "chinese", "english", "physics", "chemistry",
                              "biology", "history", "geography", "politics"}
            if record.get("subject", "") not in valid_subjects:
                self.stats["filtered_invalid_subject"] += 1
                continue

            # 3. 去重
            if self.deduplicator.is_duplicate(record["question_text"]):
                self.stats["filtered_duplicate"] += 1
                continue

            # 4. 标签规范化
            if "_import_meta" in record and record["_import_meta"].get("tag_mapping_confidence", 1.0) < 1.0:
                self.stats["tag_mapped"] += 1
            # 确保至少有一个标签
            if not record.get("knowledge_tags"):
                record["knowledge_tags"] = ["综合"]

            # 5. 地域推断
            meta = record.get("_import_meta", {})
            if meta.get("region_source") == "inferred":
                self.stats["region_inferred"] += 1
            if not record.get("region"):
                inferred, rs = infer_region(record["question_text"])
                record["region"] = inferred
                if "_import_meta" not in record:
                    record["_import_meta"] = {}
                record["_import_meta"]["region_source"] = rs if inferred else "none"
                if inferred:
                    self.stats["region_inferred"] += 1

            # 6. 难度校准
            if not isinstance(record.get("difficulty"), int) or not (1 <= record["difficulty"] <= 5):
                record["difficulty"] = normalize_difficulty(record.get("difficulty"))
                self.stats["difficulty_calibrated"] += 1

            # 7. 清理（移除非标准字段的额外数据，仅保留 schema 定义字段 + _import_meta）
            cleaned.append(record)

        self.stats["total_output"] = len(cleaned)
        return cleaned

    def report(self):
        """输出清洗统计报告"""
        logger.info("=" * 60)
        logger.info("清洗统计报告")
        logger.info("=" * 60)
        logger.info(f"  输入总数:              {self.stats['total_input']:>6}")
        logger.info(f"  过滤 - 空题干:         {self.stats['filtered_no_text']:>6}")
        logger.info(f"  过滤 - 无效学科:       {self.stats['filtered_invalid_subject']:>6}")
        logger.info(f"  过滤 - 重复:           {self.stats['filtered_duplicate']:>6}")
        logger.info(f"  ─────────────────────────────")
        logger.info(f"  输出总数:              {self.stats['total_output']:>6}")
        logger.info(f"  标签已映射:            {self.stats['tag_mapped']:>6}")
        logger.info(f"  地域已推断:            {self.stats['region_inferred']:>6}")
        logger.info(f"  难度已校准:            {self.stats['difficulty_calibrated']:>6}")
        logger.info(f"  去重器已登记:          {self.deduplicator.count():>6}")
        logger.info("=" * 60)


# ===================================================================
# 第七部分：主入口
# ===================================================================

def import_from_json(
    file_path: Path,
    source: str,
    kg_index: KnowledgeGraphIndex,
    pipeline: CleaningPipeline,
) -> list[dict]:
    """从 JSON 文件导入"""
    importer = JSONImporter(kg_index)
    raw_records = importer.read_records(file_path)
    logger.info(f"JSON 导入: 读取 {len(raw_records)} 条原始记录 (source={source})")

    total_fmt = "generic"
    if source == "cmmath":
        total_fmt = "cmmath"
    elif source == "eduadapt":
        total_fmt = "eduadapt"
    elif source == "edueval":
        total_fmt = "edueval"

    converted = []
    for raw in raw_records:
        fmt = importer.detect_format(raw) if total_fmt == "generic" else total_fmt
        result = importer.convert(raw, source, fmt)
        if result:
            converted.append(result)

    logger.info(f"JSON 转换: {len(converted)} / {len(raw_records)} 条成功")
    return pipeline.clean(converted, source)


def import_from_csv(
    file_path: Path,
    source: str,
    kg_index: KnowledgeGraphIndex,
    pipeline: CleaningPipeline,
    column_map: dict = None,
) -> list[dict]:
    """从 CSV 文件导入"""
    importer = CSVImporter(kg_index)
    raw_records = importer.read_records(file_path, column_map)
    logger.info(f"CSV 导入: 读取 {len(raw_records)} 条原始记录 (source={source})")

    # CSV 记录已经是映射后的格式，直接使用通用转换器
    json_importer = JSONImporter(kg_index)
    converted = []
    for raw in raw_records:
        result = json_importer._convert_generic(raw, source)
        if result:
            converted.append(result)

    logger.info(f"CSV 转换: {len(converted)} / {len(raw_records)} 条成功")
    return pipeline.clean(converted, source)


def import_from_xkw(
    source: str,
    kg_index: KnowledgeGraphIndex,
    pipeline: CleaningPipeline,
    subject: str = "math",
    grade: int = 9,
    page_size: int = 100,
    max_pages: int = 10,
) -> list[dict]:
    """从学科网 API 导入"""
    importer = XKWAPIImporter(kg_index)

    if not importer.is_configured():
        logger.warning("学科网 API 未配置凭据。请通过以下方式之一配置：")
        logger.warning("  1. 环境变量: set XKW_API_KEY=your_key && set XKW_API_SECRET=your_secret")
        logger.warning("  2. 修改脚本顶部 CONFIG 区域的 XKW_API_KEY / XKW_API_SECRET")
        logger.warning("  3. 将凭据写入 .env 文件并加载")
        return []

    records = importer.fetch_questions(subject, grade, page_size, max_pages)
    logger.info(f"学科网 API: 获取 {len(records)} 条试题")
    return pipeline.clean(records, source)


def write_output(records: list[dict], output_path: Path):
    """将清洗后的数据写入 JSONL 文件"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(f"输出: {len(records)} 条试题 → {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="数据导入与清洗脚本 - 将外部数据源转换为系统统一格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/import_clean_data.py --input data/raw/cmmath.json --source cmmath
  python scripts/import_clean_data.py --input data/raw/export.csv --source 360tiku
  python scripts/import_clean_data.py --source xkw --api-subject math --api-grade 9
  python scripts/import_clean_data.py --input data/raw/batch.json --source cmmath --output data/processed/custom.jsonl
        """,
    )

    parser.add_argument("--input", "-i", type=str, help="输入文件路径（JSON 或 CSV）")
    parser.add_argument("--source", "-s", type=str, required=True,
                        help="数据来源标识（cmmath / eduadapt / edueval / xkw / nbsdc / 360tiku / mock 等）")
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
                        help=f"输出 JSONL 文件路径（默认: {DEFAULT_OUTPUT}）")
    parser.add_argument("--format", "-f", type=str, choices=["json", "csv", "xkw"],
                        help="输入格式（默认根据 --source 或文件扩展名自动推断）")
    parser.add_argument("--api-subject", type=str, default="math",
                        help="学科网 API: 学科（默认 math）")
    parser.add_argument("--api-grade", type=int, default=9,
                        help="学科网 API: 年级编号 1-12（默认 9）")
    parser.add_argument("--api-pages", type=int, default=10,
                        help="学科网 API: 最大拉取页数（默认 10）")
    parser.add_argument("--api-page-size", type=int, default=100,
                        help="学科网 API: 每页数量（默认 100）")
    parser.add_argument("--dedup-threshold", type=float, default=DEDUP_SIMILARITY_THRESHOLD,
                        help=f"去重相似度阈值 0-1（默认 {DEDUP_SIMILARITY_THRESHOLD}）")
    parser.add_argument("--tag-threshold", type=float, default=DEFAULT_TAG_MATCH_THRESHOLD,
                        help=f"标签匹配阈值 0-1（默认 {DEFAULT_TAG_MATCH_THRESHOLD}）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 推断格式
    fmt = args.format
    if not fmt:
        if args.source == "xkw":
            fmt = "xkw"
        elif args.input:
            ext = Path(args.input).suffix.lower()
            if ext == ".csv":
                fmt = "csv"
            else:
                fmt = "json"
        else:
            fmt = "xkw"

    # 初始化
    kg_index = KnowledgeGraphIndex(tag_match_threshold=args.tag_threshold)
    logger.info(f"知识图谱索引: 已加载 {len(kg_index.tag_names)} 个标准知识点 (标签匹配阈值={args.tag_threshold})")

    pipeline = CleaningPipeline(kg_index)
    pipeline.deduplicator.threshold = args.dedup_threshold

    # 执行导入
    records = []
    if fmt == "json":
        if not args.input:
            logger.error("JSON 导入需要指定 --input")
            sys.exit(1)
        records = import_from_json(Path(args.input), args.source, kg_index, pipeline)
    elif fmt == "csv":
        if not args.input:
            logger.error("CSV 导入需要指定 --input")
            sys.exit(1)
        records = import_from_csv(Path(args.input), args.source, kg_index, pipeline)
    elif fmt == "xkw":
        records = import_from_xkw(
            args.source, kg_index, pipeline,
            subject=args.api_subject,
            grade=args.api_grade,
            page_size=args.api_page_size,
            max_pages=args.api_pages,
        )

    # 输出
    if records:
        output_path = Path(args.output)
        write_output(records, output_path)

        # 同时更新输出路径记录
        latest_symlink = PROCESSED_DIR / "latest.jsonl"
        if latest_symlink.exists():
            latest_symlink.unlink()
        # Windows 不支持 symlink，改为写一个指针文件
        pointer_path = PROCESSED_DIR / "latest.txt"
        pointer_path.write_text(str(output_path.absolute()), encoding="utf-8")
        logger.info(f"最新输出指针: {pointer_path} → {output_path}")
    else:
        logger.warning("无有效数据输出，请检查输入数据或 API 配置")

    pipeline.report()


if __name__ == "__main__":
    main()
