# -*- coding: utf-8 -*-
"""
知识点图谱查询引擎
==================
加载 task 2.2 生成的三级知识点图谱，提供前置知识点和关联知识点的查询能力。

图谱结构（来自 data/knowledge_graph.json）:
  学科 → 学段(小学/初中/高中) → 一级模块 → 子类 → 三级知识点(叶子节点)

查询能力：
  - get_prerequisites(kp):  返回目标知识点的前置知识点列表
  - get_related(kp):        返回与目标知识点相关联的知识点列表
  - get_kp_info(kp):        返回知识点的详细信息（所属模块、学段、年级范围等）
  - get_grade_range(kp):    返回知识点对应的年级范围
  - search_kp(keyword):     按关键词搜索知识点

前置关系推导规则：
  1. 同一模块内，排在目标知识点前面的知识点是前置
  2. 同一学科中，低学段的同名(或相似)知识点是前置
  3. 父级分类(子类/模块)中的所有前置子类是当前子类的前置

关联关系推导规则：
  1. 同一子类下的其他知识点
  2. 同一模块下其他子类的知识点
  3. 跨学段的同名模块下的知识点

用法:
    from backend.knowledge.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    prereqs = kg.get_prerequisites("一元二次方程")
    related = kg.get_related("一次函数")
"""

import json
import os
import sys
import logging
import difflib
from pathlib import Path
from typing import Optional
from collections import Counter

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

KG_PATH = ROOT / "data" / "knowledge_graph.json"

logger = logging.getLogger("knowledge.knowledge_graph")


class KnowledgeGraph:
    """
    知识点图谱查询引擎。

    在初始化时解析 knowledge_graph.json，构建:
      - _leaf_index:    叶子知识点 → (subject, stage, module, subcategory) 的映射
      - _prereq_index:  叶子知识点 → [前置知识点列表] 的映射 (预计算)
      - _related_index: 叶子知识点 → [关联知识点列表] 的映射 (预计算)
      - _flat_list:     所有叶子知识点的扁平列表（用于模糊搜索）
    """

    # 学段顺序（用于跨学段前置关系推导）
    STAGE_ORDER = {"小学": 0, "初中": 1, "高中": 2}

    # 学科间关联（用于跨学科关联查询）
    CROSS_SUBJECT_LINKS = {
        "数学": ["物理", "化学"],     # 数学是理科基础
        "物理": ["数学", "化学"],
        "化学": ["数学", "物理", "生物"],
        "生物": ["化学"],
        "地理": ["历史", "政治"],     # 文科关联
        "历史": ["地理", "政治"],
        "政治": ["历史", "地理"],
        "语文": ["英语", "历史"],     # 语言文化关联
        "英语": ["语文"],
    }

    def __init__(self, kg_path: Path = KG_PATH):
        with open(kg_path, "r", encoding="utf-8") as f:
            self._raw = json.load(f)

        # 数据结构预计算
        self._leaf_index: dict[str, dict] = {}      # kp_name → meta
        self._prereq_index: dict[str, list[str]] = {}  # kp_name → [prereq names]
        self._related_index: dict[str, list[str]] = {} # kp_name → [related names]
        self._flat_list: list[str] = []                # 所有叶子知识点名
        self._module_order: dict[str, list[str]] = {}  # (subject,stage)→module顺序

        self._build_indexes()

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------

    def _build_indexes(self):
        """遍历图谱，构建所有查询索引"""
        # 第一步：收集所有叶子知识点及层级信息
        for subject_name, subject_data in self._raw.items():
            if subject_name.startswith("_"):
                continue

            for stage_name, stage_data in subject_data.items():
                if stage_name in ("description",):
                    continue

                module_order_for_stage = []
                for module_name, module_data in stage_data.items():
                    if module_name in ("description",):
                        continue
                    module_order_for_stage.append(module_name)

                    subcats = module_data.get("子类", {})
                    subcat_order = list(subcats.keys())

                    for subcat_idx, (subcat_name, points) in enumerate(subcats.items()):
                        for pt_idx, point_name in enumerate(points):
                            # 记录叶子节点元信息
                            self._leaf_index[point_name] = {
                                "subject": subject_name,
                                "stage": stage_name,
                                "module": module_name,
                                "subcategory": subcat_name,
                                "subcategory_index": subcat_idx,
                                "point_index": pt_idx,
                                "subcategory_points": points,
                                "sibling_count": len(points),
                            }
                            self._flat_list.append(point_name)

                # 记录模块顺序
                stage_key = f"{subject_name}|{stage_name}"
                self._module_order[stage_key] = module_order_for_stage

        # 去重扁平列表
        self._flat_list = list(dict.fromkeys(self._flat_list))
        logger.info(f"知识图谱索引: {len(self._leaf_index)} 个叶子知识点")

        # 第二步：预计算前置关系
        self._build_prereq_index()

        # 第三步：预计算关联关系
        self._build_related_index()

    def _build_prereq_index(self):
        """为每个知识点预计算前置知识点列表"""
        for kp_name, meta in self._leaf_index.items():
            prereqs = []

            # 规则1：同一子类中排在目标前面的知识点是前置
            siblings = meta["subcategory_points"]
            pt_idx = meta["point_index"]
            for i in range(pt_idx):
                prereqs.append(siblings[i])

            # 规则2：同一模块内，排在目标子类前面的子类的所有知识点是前置
            subject = meta["subject"]
            stage = meta["stage"]
            module = meta["module"]
            stage_data = self._raw.get(subject, {}).get(stage, {})
            module_data = stage_data.get(module, {})
            subcats = module_data.get("子类", {})

            subcat_idx = meta["subcategory_index"]
            subcat_names = list(subcats.keys())
            for i in range(subcat_idx):
                prev_subcat = subcat_names[i]
                prev_points = subcats.get(prev_subcat, [])
                prereqs.extend(prev_points)

            # 规则3：同一学科低学段的同级/同名知识点是前置
            current_stage_order = self.STAGE_ORDER.get(stage, 1)
            for lower_stage_name, lower_stage_order in self.STAGE_ORDER.items():
                if lower_stage_order >= current_stage_order:
                    continue
                lower_stage_data = self._raw.get(subject, {}).get(lower_stage_name, {})
                # 在低学段中查找同名模块
                for lm_name, lm_data in lower_stage_data.items():
                    if lm_name in ("description",):
                        continue
                    # 同模块名或模块间有概念上的进阶关系
                    if self._modules_related(module, lm_name):
                        lm_subcats = lm_data.get("子类", {})
                        for lsc_name, lsc_points in lm_subcats.items():
                            if self._subcategories_related(subcat_names[subcat_idx] if subcat_idx < len(subcat_names) else "", lsc_name):
                                prereqs.extend(lsc_points)

            # 去重（保持顺序）
            seen = set()
            unique = []
            for p in prereqs:
                if p != kp_name and p not in seen:
                    seen.add(p)
                    unique.append(p)
            self._prereq_index[kp_name] = unique

    def _build_related_index(self):
        """为每个知识点预计算关联知识点列表"""
        for kp_name, meta in self._leaf_index.items():
            related = []

            # 规则1：同一子类下的其他知识点
            siblings = meta["subcategory_points"]
            for s in siblings:
                if s != kp_name:
                    related.append(s)

            # 规则2：同一模块下其他子类的知识点
            subject = meta["subject"]
            stage = meta["stage"]
            module = meta["module"]
            stage_data = self._raw.get(subject, {}).get(stage, {})
            module_data = stage_data.get(module, {})
            subcats = module_data.get("子类", {})
            for sc_name, sc_points in subcats.items():
                if sc_name != meta["subcategory"]:
                    related.extend(sc_points)

            # 规则3：跨学段同名/相似模块下的知识点
            current_so = self.STAGE_ORDER.get(stage, 1)
            for other_stage, other_so in self.STAGE_ORDER.items():
                if other_stage == stage:
                    continue
                other_stage_data = self._raw.get(subject, {}).get(other_stage, {})
                for om_name, om_data in other_stage_data.items():
                    if om_name in ("description",):
                        continue
                    if self._modules_related(module, om_name):
                        om_subcats = om_data.get("子类", {})
                        for osc_points in om_subcats.values():
                            related.extend(osc_points)

            # 去重
            seen = set()
            unique = []
            for r in related:
                if r != kp_name and r not in seen:
                    seen.add(r)
                    unique.append(r)
            self._related_index[kp_name] = unique

    # ------------------------------------------------------------------
    # 模块/子类相似性判断
    # ------------------------------------------------------------------

    @staticmethod
    def _modules_related(mod1: str, mod2: str) -> bool:
        """判断两个模块名是否表示概念上的进阶关系"""
        # 共享关键词
        common_keywords = ["数", "式", "方程", "函数", "图形", "几何", "统计", "概率",
                           "阅读", "表达", "写作", "语言", "文化", "语法", "词汇", "技能"]
        for kw in common_keywords:
            if kw in mod1 and kw in mod2:
                return True
        # 编辑距离
        sim = difflib.SequenceMatcher(None, mod1, mod2).ratio()
        return sim >= 0.3

    @staticmethod
    def _subcategories_related(sc1: str, sc2: str) -> bool:
        """判断两个子类名是否相关"""
        sim = difflib.SequenceMatcher(None, sc1, sc2).ratio()
        return sim >= 0.3

    # ------------------------------------------------------------------
    # 公开查询 API
    # ------------------------------------------------------------------

    def get_prerequisites(self, knowledge_point: str) -> list[str]:
        """
        返回该知识点的前置知识点列表。

        前置关系: 必须先掌握前置知识，才能学习目标知识。

        返回:
            [前置知识点名称列表]，空列表表示无前置知识点

        示例:
            kg.get_prerequisites("一元二次方程")
            → ["一元一次方程", "整式的加减", "整式的乘除与乘法公式"]
        """
        # 精确匹配
        if knowledge_point in self._prereq_index:
            return list(self._prereq_index[knowledge_point])

        # 模糊匹配
        best_match, score = self._fuzzy_match(knowledge_point)
        if best_match and score >= 0.80:
            logger.debug(f"前置查询时模糊匹配: '{knowledge_point}' → '{best_match}' (score={score:.2f})")
            return list(self._prereq_index.get(best_match, []))

        logger.warning(f"知识点未找到: '{knowledge_point}'")
        return []

    def get_related(self, knowledge_point: str) -> list[str]:
        """
        返回与该知识点相关联的知识点列表。

        关联关系: 可以同时学习或互相参照的知识点（非前置但相关）。

        返回:
            [关联知识点列表]，空列表表示无关联知识点

        示例:
            kg.get_related("一次函数")
            → ["正比例函数", "二次函数", "反比例函数", "一次函数应用题"]
        """
        if knowledge_point in self._related_index:
            return list(self._related_index[knowledge_point])

        best_match, score = self._fuzzy_match(knowledge_point)
        if best_match and score >= 0.80:
            logger.debug(f"关联查询时模糊匹配: '{knowledge_point}' → '{best_match}' (score={score:.2f})")
            return list(self._related_index.get(best_match, []))

        logger.warning(f"知识点未找到: '{knowledge_point}'")
        return []

    def get_kp_info(self, knowledge_point: str) -> Optional[dict]:
        """
        返回知识点的详细信息。

        返回:
            {
              "name": "一元二次方程",
              "subject": "数学",
              "stage": "初中",
              "module": "方程与不等式",
              "subcategory": "方程",
              "sibling_count": 4,
              "prerequisites": ["一元一次方程", ...],
              "related": ["二次函数", ...]
            }
        """
        if knowledge_point in self._leaf_index:
            meta = dict(self._leaf_index[knowledge_point])
            meta["name"] = knowledge_point
            meta["prerequisites"] = self.get_prerequisites(knowledge_point)
            meta["related"] = self.get_related(knowledge_point)
            meta.pop("subcategory_points", None)
            return meta

        best_match, score = self._fuzzy_match(knowledge_point)
        if best_match and score >= 0.80:
            meta = dict(self._leaf_index[best_match])
            meta["name"] = best_match
            meta["matched_from"] = knowledge_point
            meta["match_score"] = score
            meta["prerequisites"] = self.get_prerequisites(best_match)
            meta["related"] = self.get_related(best_match)
            meta.pop("subcategory_points", None)
            return meta

        return None

    def get_grade_range(self, knowledge_point: str) -> tuple[int, int]:
        """
        返回知识点对应的年级范围。

        基于学段推断:
          - 小学: 1-6 年级
          - 初中: 7-9 年级
          - 高中: 10-12 年级

        返回:
            (最低年级, 最高年级)，未知返回 (1, 12)

        示例:
            kg.get_grade_range("一元二次方程") → (7, 9)
        """
        if knowledge_point in self._leaf_index:
            stage = self._leaf_index[knowledge_point]["stage"]
            grade_map = {"小学": (1, 6), "初中": (7, 9), "高中": (10, 12)}
            return grade_map.get(stage, (1, 12))

        best_match, _ = self._fuzzy_match(knowledge_point)
        if best_match and best_match in self._leaf_index:
            stage = self._leaf_index[best_match]["stage"]
            grade_map = {"小学": (1, 6), "初中": (7, 9), "高中": (10, 12)}
            return grade_map.get(stage, (1, 12))

        return (1, 12)

    def search_kp(self, keyword: str, top_k: int = 5, subject_filter: str = None) -> list[dict]:
        """
        按关键词搜索知识点。

        参数:
            keyword:        搜索关键词
            top_k:          返回结果数
            subject_filter: 学科过滤（可选）

        返回:
            [
              {"name": "...", "subject": "...", "stage": "...", "score": 0.95},
              ...
            ]
        """
        scored = []
        for kp_name in self._flat_list:
            meta = self._leaf_index.get(kp_name, {})
            if subject_filter and meta.get("subject") != subject_filter:
                continue
            # 相似度评分
            score = difflib.SequenceMatcher(None, keyword, kp_name).ratio()
            # 子串匹配加分
            if keyword in kp_name:
                score = max(score, 0.90)
            scored.append((score, kp_name, meta))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "name": name,
                "subject": meta.get("subject", ""),
                "stage": meta.get("stage", ""),
                "module": meta.get("module", ""),
                "score": round(score, 4),
            }
            for score, name, meta in scored[:top_k] if score > 0.3
        ]

    def get_knowledge_path(self, knowledge_point: str) -> list[str]:
        """
        返回从零基础到该知识点的完整学习路径（包括所有前置知识的递归展开）。

        返回:
            [k1, k2, ..., kp]，按学习顺序排列
        """
        visited = set()
        path = []

        def _dfs(kp):
            if kp in visited:
                return
            visited.add(kp)
            for prereq in self.get_prerequisites(kp):
                _dfs(prereq)
            path.append(kp)

        _dfs(knowledge_point)
        return path

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _fuzzy_match(self, name: str) -> tuple[str, float]:
        """模糊匹配知识点名称，返回 (最佳匹配, 分数)"""
        best_score = 0.0
        best_match = ""

        # 先去精确查找
        if name in self._leaf_index:
            return (name, 1.0)

        for kp_name in self._flat_list:
            score = difflib.SequenceMatcher(None, name, kp_name).ratio()
            if name in kp_name:
                score = max(score, 0.85)
            if kp_name in name:
                score = max(score, 0.80)
            if score > best_score:
                best_score = score
                best_match = kp_name

        return (best_match, best_score)

    def get_all_by_stage(self, subject: str, stage: str) -> list[str]:
        """返回指定学科和学段的所有叶子知识点"""
        result = []
        stage_data = self._raw.get(subject, {}).get(stage, {})
        for module_data in stage_data.values():
            if isinstance(module_data, str):
                continue
            for subcat_data in module_data.get("子类", {}).values():
                result.extend(subcat_data)
        return result

    def stats(self) -> dict:
        """返回图谱统计信息"""
        subjects = Counter()
        stages = Counter()
        for meta in self._leaf_index.values():
            subjects[meta["subject"]] += 1
            stages[meta["stage"]] += 1
        return {
            "total_leaf_nodes": len(self._leaf_index),
            "unique_leaf_names": len(self._flat_list),
            "by_subject": dict(subjects),
            "by_stage": dict(stages),
            "avg_prerequisites": round(
                sum(len(v) for v in self._prereq_index.values()) / max(len(self._prereq_index), 1), 1
            ),
            "avg_related": round(
                sum(len(v) for v in self._related_index.values()) / max(len(self._related_index), 1), 1
            ),
        }


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    kg = KnowledgeGraph()

    # 统计信息
    print("知识图谱统计:")
    stats = kg.stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # 测试前置查询
    print("\n--- 前置知识点查询 ---")
    test_kps = ["一元二次方程", "一次函数表达式与图像", "议论文写作", "九大时态"]
    for kp in test_kps:
        prereqs = kg.get_prerequisites(kp)
        related = kg.get_related(kp)
        print(f"\n【{kp}】")
        print(f"  前置 ({len(prereqs)}): {prereqs[:5]}{'...' if len(prereqs) > 5 else ''}")
        print(f"  关联 ({len(related)}): {related[:5]}{'...' if len(related) > 5 else ''}")

    # 测试信息查询
    print("\n--- 知识点详情 ---")
    info = kg.get_kp_info("勾股定理")
    if info:
        print(f"  名称: {info['name']}")
        print(f"  学科: {info['subject']}, 学段: {info['stage']}")
        print(f"  模块: {info['module']}, 子类: {info['subcategory']}")
        print(f"  前置: {info.get('prerequisites', [])[:5]}")
        print(f"  关联: {info.get('related', [])[:5]}")

    # 测试搜索
    print("\n--- 知识点搜索 ---")
    for kw in ["方程", "函数", "阅读", "时态"]:
        results = kg.search_kp(kw, top_k=3)
        print(f"\n  搜索 '{kw}':")
        for r in results:
            print(f"    {r['name']} [{r['subject']}·{r['stage']}] score={r['score']:.3f}")

    # 测试完整学习路径
    print("\n--- 学习路径 ---")
    path = kg.get_knowledge_path("一元二次方程")
    print(f"  一元二次方程的完整学习路径 ({len(path)} 步):")
    for i, kp in enumerate(path[-6:], 1):  # 只显示最后6步
        print(f"    {i}. {kp}")
