# -*- coding: utf-8 -*-
"""
参数解析 Agent (ParameterParserAgent)
=====================================
Harness 角色: Planner 的前置——解析并验证用户请求参数。

职责边界：
  只管解析和验证，不做规划、不检索、不出题。

输入：
  用户原始请求（dict / JSON 字符串 / 自然语言描述）

输出：
  /tmp/paper_request.json — 标准化参数文件

护栏：
  - 科目必须来自 schema.json 枚举值
  - 年级必须是 grade_1 ~ grade_12
  - 学段必须与年级匹配
  - 难度分布加起来必须 ≈ 1.0
  - 地域必须是已知地域代码
"""

import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

from backend.knowledge.knowledge_graph import KnowledgeGraph

logger = logging.getLogger("agent.parameter_parser")

# ---------------------------------------------------------------------------
# 契约文件路径
# ---------------------------------------------------------------------------
PAPER_REQUEST_PATH = ROOT / "tmp" / "paper_request.json"

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
VALID_SUBJECTS = {"math", "chinese", "english", "physics", "chemistry", "biology", "history", "geography", "politics"}
VALID_REGIONS = {"beijing", "shanghai", "guangdong", ""}
GRADE_TO_LEVEL = {
    **{f"grade_{i}": "primary" for i in range(1, 7)},
    **{f"grade_{i}": "junior" for i in range(7, 10)},
    **{f"grade_{i}": "senior" for i in range(10, 13)},
}
LEVEL_TO_GRADES = {
    "primary": [f"grade_{i}" for i in range(1, 7)],
    "junior": [f"grade_{i}" for i in range(7, 10)],
    "senior": [f"grade_{i}" for i in range(10, 13)],
}

# 学科与学段的兼容性检查（部分学科只在特定学段开设）
SUBJECT_STAGE_CONSTRAINTS = {
    "physics": {"junior", "senior"},       # 物理初二开始
    "chemistry": {"senior", "junior"},     # 化学初三开始
    "biology": {"primary", "junior", "senior"},
    "politics": {"primary", "junior", "senior"},
    "history": {"primary", "junior", "senior"},
    "geography": {"primary", "junior", "senior"},
}

DEFAULT_DIFFICULTY_DISTRIBUTION = {"1": 0.25, "2": 0.25, "3": 0.30, "4": 0.15, "5": 0.05}


class ParameterParserAgent:
    """
    参数解析 Agent。

    用法:
        agent = ParameterParserAgent()
        result = agent.parse({
            "subject": "math",
            "grade": "grade_9",
            "region": "beijing",
            "knowledge_points": ["一元二次方程"],
            "difficulty": 3,
        })
        # → 输出写入 /tmp/paper_request.json
    """

    def __init__(self):
        self._kg = None  # 延迟加载

    @property
    def kg(self) -> KnowledgeGraph:
        if self._kg is None:
            self._kg = KnowledgeGraph()
        return self._kg

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def parse(self, raw_input: dict) -> dict:
        """
        解析并验证用户请求参数。

        参数:
            raw_input: 用户原始请求 dict，可包含:
                - subject:           学科代码 (math/chinese/...)
                - grade:             年级 (grade_7 / "七年级" / "初三")
                - grade_level:       学段 (primary/junior/senior)，如不提供则从 grade 推导
                - region:            地域代码 (beijing/shanghai/guangdong)
                - knowledge_points:  知识点列表
                - difficulty:        整体难度 1-5 或难度分布 dict
                - question_count:    题目总数
                - question_types:    题型列表
                - title:             试卷标题

        返回:
            {"success": True, "request": {...}} 或 {"success": False, "errors": [...]}
        """
        errors = []
        normalized = {}

        # ---- 1. 解析学科 ----
        subject = raw_input.get("subject", raw_input.get("学科", ""))
        if not subject:
            errors.append("缺少必填参数: subject（学科）")
        else:
            subject = self._normalize_subject(subject)
            if subject not in VALID_SUBJECTS:
                errors.append(f"无效学科: '{subject}'，有效值: {sorted(VALID_SUBJECTS)}")
            normalized["subject"] = subject

        # ---- 2. 解析年级与学段 ----
        grade_raw = raw_input.get("grade", raw_input.get("年级", ""))
        grade_level_raw = raw_input.get("grade_level", raw_input.get("学段", ""))
        grade = self._normalize_grade(grade_raw)
        if grade:
            normalized["grade"] = grade
            normalized["grade_level"] = GRADE_TO_LEVEL.get(grade, "")
        if grade_level_raw:
            level = self._normalize_grade_level(grade_level_raw)
            if level:
                normalized["grade_level"] = level
                if not grade:
                    # 从学段推导一个默认年级
                    normalized["grade"] = LEVEL_TO_GRADES.get(level, ["grade_7"])[0]
        if not normalized.get("grade"):
            errors.append("缺少必填参数: grade（年级）或 grade_level（学段）")
        if not normalized.get("grade_level"):
            errors.append("缺少必填参数: grade_level（学段）或无法从年级推导")

        # ---- 2.1 学科与学段兼容性检查 ----
        if normalized.get("subject") and normalized.get("grade_level"):
            constraints = SUBJECT_STAGE_CONSTRAINTS.get(normalized["subject"])
            if constraints and normalized["grade_level"] not in constraints:
                errors.append(
                    f"学科 '{normalized['subject']}' 在 '{normalized['grade_level']}' 学段不开设。"
                    f"有效学段: {sorted(constraints)}"
                )

        # ---- 3. 解析地域 ----
        region = raw_input.get("region", raw_input.get("地域", ""))
        if region:
            region = self._normalize_region(region)
            if region not in VALID_REGIONS:
                errors.append(f"无效地域: '{region}'，有效值: {sorted(r for r in VALID_REGIONS if r)}")
            normalized["region"] = region
        else:
            normalized["region"] = ""

        # ---- 4. 解析知识点 ----
        kp_raw = raw_input.get("knowledge_points", raw_input.get("知识点", []))
        if isinstance(kp_raw, str):
            kp_raw = [k.strip() for k in kp_raw.split(",") if k.strip()]
        if not kp_raw:
            kp_raw = []
        # 验证知识点是否在知识图谱中存在
        validated_kps = []
        unmatched_kps = []
        for kp in kp_raw:
            info = self.kg.get_kp_info(kp)
            if info:
                validated_kps.append(info.get("matched_from", kp))
            else:
                unmatched_kps.append(kp)
        if unmatched_kps:
            logger.warning(f"知识点未在知识图谱中找到: {unmatched_kps}，将保留原始名称")
            validated_kps.extend(unmatched_kps)
        normalized["knowledge_points"] = validated_kps

        # ---- 5. 解析难度 ----
        difficulty_raw = raw_input.get("difficulty", raw_input.get("难度"))
        if isinstance(difficulty_raw, dict):
            # 难度分布
            dist = {}
            for level in ["1", "2", "3", "4", "5"]:
                dist[level] = float(difficulty_raw.get(level, difficulty_raw.get(int(level), 0.0)))
            total = sum(dist.values())
            if abs(total - 1.0) > 0.05:
                errors.append(f"难度分布总和应为 1.0，实际: {total:.2f}")
            normalized["difficulty_distribution"] = dist
            normalized["difficulty"] = None
        elif difficulty_raw is not None:
            diff = self._normalize_difficulty(difficulty_raw)
            if diff < 1 or diff > 5:
                errors.append(f"难度值需在 1-5 之间，实际: {difficulty_raw}")
            normalized["difficulty"] = diff
            normalized["difficulty_distribution"] = None
        else:
            normalized["difficulty"] = 3
            normalized["difficulty_distribution"] = None

        # ---- 6. 解析题量与题型 ----
        question_count = raw_input.get("question_count", raw_input.get("题量", 10))
        try:
            question_count = int(question_count)
            if question_count < 1 or question_count > 100:
                errors.append(f"题量需在 1-100 之间，实际: {question_count}")
        except (ValueError, TypeError):
            errors.append(f"题量需为整数，实际: {question_count}")
            question_count = 10
        normalized["question_count"] = question_count

        question_types = raw_input.get("question_types", raw_input.get("题型", []))
        if isinstance(question_types, str):
            question_types = [t.strip() for t in question_types.split(",")]
        if not question_types:
            question_types = ["choice", "fill_blank", "short_answer", "calculation"]
        valid_types = {"choice", "fill_blank", "true_false", "short_answer", "essay", "calculation"}
        normalized_types = []
        for qt in question_types:
            qt = self._normalize_question_type(qt)
            if qt in valid_types:
                normalized_types.append(qt)
        if not normalized_types:
            normalized_types = ["choice", "fill_blank", "short_answer"]
        normalized["question_types"] = normalized_types

        # ---- 7. 解析标题 ----
        title = raw_input.get("title", raw_input.get("标题", ""))
        if not title:
            title = self._generate_default_title(normalized)
        normalized["title"] = title

        # ---- 8. 输出 ----
        if errors:
            return {"success": False, "errors": errors, "request": normalized}

        # 写入产物契约
        request_obj = {
            "_meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": "ParameterParserAgent",
                "version": "1.0.0",
            },
            "request": normalized,
        }

        PAPER_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PAPER_REQUEST_PATH, "w", encoding="utf-8") as f:
            json.dump(request_obj, f, ensure_ascii=False, indent=2)
        logger.info(f"参数解析完成 → {PAPER_REQUEST_PATH}")

        return {"success": True, "request": normalized, "output_file": str(PAPER_REQUEST_PATH)}

    # ------------------------------------------------------------------
    # 规范化方法
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_subject(raw: str) -> str:
        """中文/英文 → 学科代码"""
        mapping = {
            "数学": "math", "语文": "chinese", "英语": "english",
            "物理": "physics", "化学": "chemistry", "生物": "biology",
            "历史": "history", "地理": "geography", "政治": "politics",
            "道德与法治": "politics",
            "math": "math", "chinese": "chinese", "english": "english",
            "physics": "physics", "chemistry": "chemistry", "biology": "biology",
            "history": "history", "geography": "geography", "politics": "politics",
        }
        return mapping.get(str(raw).strip().lower(), str(raw).strip().lower())

    @staticmethod
    def _normalize_grade(raw: str) -> str:
        """各种年级表示 → grade_N 格式"""
        if not raw:
            return ""
        raw = str(raw).strip()
        if raw.startswith("grade_"):
            return raw

        cn_map = {
            "一年级": "grade_1", "二年级": "grade_2", "三年级": "grade_3",
            "四年级": "grade_4", "五年级": "grade_5", "六年级": "grade_6",
            "七年级": "grade_7", "初一": "grade_7",
            "八年级": "grade_8", "初二": "grade_8",
            "九年级": "grade_9", "初三": "grade_9",
            "高一": "grade_10", "高二": "grade_11", "高三": "grade_12",
        }
        if raw in cn_map:
            return cn_map[raw]

        # 数字: "7" → grade_7
        import re
        m = re.search(r"(\d+)", raw)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 12:
                return f"grade_{n}"
        return ""

    @staticmethod
    def _normalize_grade_level(raw: str) -> str:
        mapping = {
            "小学": "primary", "primary": "primary", "小学阶段": "primary",
            "初中": "junior", "junior": "junior", "初中阶段": "junior",
            "高中": "senior", "senior": "senior", "高中阶段": "senior",
        }
        return mapping.get(str(raw).strip(), "")

    @staticmethod
    def _normalize_region(raw: str) -> str:
        mapping = {
            "北京": "beijing", "北京市": "beijing", "beijing": "beijing",
            "上海": "shanghai", "上海市": "shanghai", "shanghai": "shanghai",
            "广东": "guangdong", "广东省": "guangdong", "guangdong": "guangdong",
            "广州": "guangdong", "深圳": "guangdong",
        }
        return mapping.get(str(raw).strip(), str(raw).strip().lower())

    @staticmethod
    def _normalize_difficulty(raw) -> int:
        if isinstance(raw, (int, float)):
            return max(1, min(5, int(raw)))
        mapping = {"容易": 1, "简单": 1, "较易": 2, "中等": 3, "较难": 4, "困难": 5}
        return mapping.get(str(raw).strip(), 3)

    @staticmethod
    def _normalize_question_type(raw: str) -> str:
        mapping = {
            "选择": "choice", "选择题": "choice", "choice": "choice",
            "填空": "fill_blank", "填空题": "fill_blank", "fill_blank": "fill_blank",
            "判断": "true_false", "判断题": "true_false", "true_false": "true_false",
            "简答": "short_answer", "简答题": "short_answer", "short_answer": "short_answer",
            "论述": "essay", "论述题": "essay", "essay": "essay",
            "计算": "calculation", "计算题": "calculation", "calculation": "calculation",
        }
        return mapping.get(str(raw).strip().lower(), "short_answer")

    def _generate_default_title(self, normalized: dict) -> str:
        """当用户未提供标题时，自动生成"""
        subject_cn = {
            "math": "数学", "chinese": "语文", "english": "英语",
            "physics": "物理", "chemistry": "化学",
        }
        region_cn = {"beijing": "北京市", "shanghai": "上海市", "guangdong": "广东省"}
        subj = subject_cn.get(normalized.get("subject", ""), normalized.get("subject", ""))
        grade = normalized.get("grade", "").replace("grade_", "")
        region = region_cn.get(normalized.get("region", ""), "")
        region_prefix = f"{region}" if region else ""
        return f"{region_prefix}{grade}年级{subj}模拟试卷"


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def parse_request(raw_input: dict) -> dict:
    """便捷函数：解析用户请求"""
    agent = ParameterParserAgent()
    return agent.parse(raw_input)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    agent = ParameterParserAgent()

    # 测试1: 正常请求
    print("=" * 50)
    print("测试1: 正常请求")
    r = agent.parse({
        "subject": "数学",
        "grade": "初三",
        "region": "北京",
        "knowledge_points": ["一元二次方程", "二次函数"],
        "difficulty": 3,
        "question_count": 10,
    })
    print(f"结果: {json.dumps(r, ensure_ascii=False, indent=2)[:500]}")

    # 测试2: 无效参数
    print("\n测试2: 无效参数（学科与学段不匹配）")
    r = agent.parse({
        "subject": "physics",
        "grade": "grade_2",
        "region": "shanghai",
    })
    print(f"success={r['success']}")
    if not r["success"]:
        for e in r["errors"]:
            print(f"  错误: {e}")

    # 测试3: 只给学段
    print("\n测试3: 只给学段")
    r = agent.parse({
        "subject": "math",
        "grade_level": "junior",
    })
    print(f"success={r['success']}, grade={r.get('request', {}).get('grade')}")
