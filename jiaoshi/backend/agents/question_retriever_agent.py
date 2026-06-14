# -*- coding: utf-8 -*-
"""
题目检索 Agent (QuestionRetrieverAgent)
======================================
Harness 角色: QuestionSelector — 根据 Paper Plan 从向量数据库检索题目。

职责边界：
  只管从 RAG 检索题目，不做规划、不生成新题、不排序格式化。

输入：
  /tmp/paper_request.json（由 ParameterParserAgent 产出）

输出：
  /tmp/retrieved_questions.json

护栏：
  - 最多检索 top_k=20 条
  - 必须启用地域过滤
  - 检索结果需去重（同知识点只保留 top-3）
  - 空结果时返回 coverage_gap 标记
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

from backend.rag_searcher import init_searcher, search as rag_search
from backend.knowledge.knowledge_graph import KnowledgeGraph
from backend.knowledge.regional_filter import RegionalFilter

logger = logging.getLogger("agent.question_retriever")

# ---------------------------------------------------------------------------
# 契约文件路径
# ---------------------------------------------------------------------------
PAPER_REQUEST_PATH = ROOT / "tmp" / "paper_request.json"
RETRIEVED_QUESTIONS_PATH = ROOT / "tmp" / "retrieved_questions.json"

# ---------------------------------------------------------------------------
# 护栏常量
# ---------------------------------------------------------------------------
MAX_TOP_K = 20          # 单次检索最多返回数
MAX_PER_KNOWLEDGE = 3   # 每个知识点最多保留题数
RETRIEVAL_PER_QUERY = 8 # 每次 query 检索数
MIN_RESULTS_WARNING = 3 # 低于此数触发警告


class QuestionRetrieverAgent:
    """
    题目检索 Agent。

    用法:
        agent = QuestionRetrieverAgent()
        result = agent.retrieve()
        # → 读取 /tmp/paper_request.json，检索并写入 /tmp/retrieved_questions.json
    """

    def __init__(self):
        self._kg = None
        self._rf = None
        self._searcher_ready = False

    @property
    def kg(self) -> KnowledgeGraph:
        if self._kg is None:
            self._kg = KnowledgeGraph()
        return self._kg

    @property
    def rf(self) -> RegionalFilter:
        if self._rf is None:
            self._rf = RegionalFilter()
        return self._rf

    def _ensure_searcher(self):
        """确保 RAG 检索器已初始化"""
        if not self._searcher_ready:
            init_searcher()
            self._searcher_ready = True

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def retrieve(self, request_path: Path = None) -> dict:
        """
        从 RAG 知识库检索题目。

        参数:
            request_path: paper_request.json 路径，默认 /tmp/paper_request.json

        返回:
            {
                "success": True/False,
                "total_retrieved": N,
                "coverage_gaps": [...],
                "output_file": "..."
            }
        """
        if request_path is None:
            request_path = PAPER_REQUEST_PATH

        # ---- 读取请求 ----
        if not request_path.exists():
            return {"success": False, "error": f"请求文件不存在: {request_path}", "output_file": str(RETRIEVED_QUESTIONS_PATH)}

        with open(request_path, "r", encoding="utf-8") as f:
            request_obj = json.load(f)

        request = request_obj.get("request", request_obj)
        subject = request.get("subject", "")
        grade = request.get("grade", "grade_7")
        region = request.get("region", "")
        knowledge_points = request.get("knowledge_points", [])
        difficulty = request.get("difficulty")
        question_count = request.get("question_count", 10)
        question_types = request.get("question_types", [])

        self._ensure_searcher()

        # ---- 检索策略：每个知识点 + 每种题型组合分别检索 ----
        all_results = []
        seen_ids = set()
        coverage_gaps = []

        for kp in knowledge_points[:10]:  # 最多检索10个知识点
            for qt in (question_types or ["short_answer"]):
                query = f"{kp} {qt}"
                filters = {"subject": subject}

                if difficulty:
                    filters["difficulty"] = {"$lte": min(difficulty + 1, 5)}

                try:
                    results = rag_search(
                        query=query,
                        filters=filters,
                        top_k=RETRIEVAL_PER_QUERY,
                        region=region,
                        enable_regional_filter=bool(region),
                        regional_min_count=3,
                    )
                except Exception as e:
                    logger.warning(f"检索失败 (query={query}): {e}")
                    continue

                # 去重并记录
                added = 0
                for r in results:
                    rid = r.get("id", "")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        r["_retrieved_for_kp"] = kp
                        r["_retrieved_for_type"] = qt
                        all_results.append(r)
                        added += 1

                if added == 0:
                    # 扩大检索范围：去掉题型限制重试
                    retry_results = rag_search(
                        query=kp,
                        filters=filters,
                        top_k=RETRIEVAL_PER_QUERY,
                        region=region,
                        enable_regional_filter=bool(region),
                        regional_min_count=2,
                    )
                    for r in retry_results:
                        rid = r.get("id", "")
                        if rid and rid not in seen_ids:
                            seen_ids.add(rid)
                            r["_retrieved_for_kp"] = kp
                            r["_retrieved_for_type"] = "fallback"
                            all_results.append(r)

                    if len(all_results) == 0:
                        coverage_gaps.append({
                            "knowledge_point": kp,
                            "question_type": qt,
                            "retrieved": 0,
                        })

        # ---- 同知识点去重（每个知识点最多保留 MAX_PER_KNOWLEDGE 题）----
        kp_counter = {}
        deduped = []
        for r in all_results:
            kp = r.get("_retrieved_for_kp", "")
            kp_counter.setdefault(kp, 0)
            if kp_counter[kp] < MAX_PER_KNOWLEDGE:
                kp_counter[kp] += 1
                deduped.append(r)
            else:
                logger.debug(f"知识点 '{kp}' 已满 {MAX_PER_KNOWLEDGE} 题，跳过")

        # ---- 按 question_count 截断 ----
        target_count = min(question_count * 2, MAX_TOP_K)  # 检索 2x 目标量以供后续筛选
        deduped = deduped[:target_count]

        # ---- 输出 ----
        output_obj = {
            "_meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": "QuestionRetrieverAgent",
                "request_ref": str(request_path),
            },
            "total_retrieved": len(deduped),
            "coverage_gaps": coverage_gaps,
            "gap_count": len(coverage_gaps),
            "questions": deduped,
        }

        RETRIEVED_QUESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RETRIEVED_QUESTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(output_obj, f, ensure_ascii=False, indent=2)

        status = "WARNING" if coverage_gaps else "OK"
        logger.info(
            f"检索完成: {len(deduped)} 题 (覆盖缺口: {len(coverage_gaps)}) → {RETRIEVED_QUESTIONS_PATH}"
        )

        return {
            "success": True,
            "total_retrieved": len(deduped),
            "coverage_gaps": coverage_gaps,
            "has_gaps": len(coverage_gaps) > 0,
            "status": status,
            "output_file": str(RETRIEVED_QUESTIONS_PATH),
        }

    def retrieve_with_custom_query(self, queries: list[str], subject: str, top_k: int = 10) -> list[dict]:
        """
        使用自定义查询列表检索（用于重试场景）。

        参数:
            queries: 查询文本列表
            subject: 学科过滤
            top_k:   每查询返回数

        返回:
            检索结果列表
        """
        self._ensure_searcher()
        all_results = []
        seen = set()

        for q in queries:
            try:
                results = rag_search(query=q, filters={"subject": subject}, top_k=top_k)
            except Exception as e:
                logger.warning(f"自定义查询失败: {e}")
                continue
            for r in results:
                rid = r.get("id", "")
                if rid not in seen:
                    seen.add(rid)
                    all_results.append(r)

        return all_results[:MAX_TOP_K]


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def retrieve_questions(request_path: str = None) -> dict:
    """便捷函数：检索题目"""
    agent = QuestionRetrieverAgent()
    return agent.retrieve(Path(request_path) if request_path else None)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 先确保有 paper_request.json
    from backend.agents.parameter_parser_agent import ParameterParserAgent
    parser = ParameterParserAgent()
    parse_result = parser.parse({"subject": "数学", "grade": "初三", "region": "beijing",
                                  "knowledge_points": ["一元一次方程"], "question_count": 5})
    if not parse_result["success"]:
        print(f"参数解析失败: {parse_result['errors']}")
        sys.exit(1)

    agent = QuestionRetrieverAgent()
    result = agent.retrieve()
    print(f"\n检索结果: total={result['total_retrieved']}, gaps={result['has_gaps']}")
    print(f"输出: {result['output_file']}")
