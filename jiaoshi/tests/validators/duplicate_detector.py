# -*- coding: utf-8 -*-
"""
重复检测器 (DuplicateDetector)
===============================
检测试卷内部和与 RAG 知识库之间的题目重复。

两种检测模式：
  1. 内部去重：检测同一份试卷内是否有高度相似的题目（文本相似度 > 0.85）
  2. 跨库去重：检测新题目与知识库已有题目的重复率

核心算法：
  - MD5 哈希精确去重（O(n)）
  - difflib.SequenceMatcher 编辑距离模糊去重（O(n²) 但仅比较长度相近的题目）

用法:
    detector = DuplicateDetector()
    # 内部去重
    result = detector.detect_intra_duplicates(questions)
    # 跨库去重
    result = detector.detect_cross_duplicates(new_questions, existing_questions)

返回格式:
    {
        "duplicate_found": True/False,
        "duplicate_count": N,
        "duplicate_pairs": [(idx1, idx2, similarity), ...],
        "unique_count": M,
        "max_similarity": 0.95,
    }
"""

import json
import os
import sys
import re
import hashlib
import difflib
import logging
from pathlib import Path
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("validator.duplicate")

# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------
EXACT_DUP_THRESHOLD = 1.0       # 精确重复
HIGH_SIMILARITY_THRESHOLD = 0.90  # 高度相似（几乎相同）
FUZZY_DUP_THRESHOLD = 0.85     # 模糊重复（判定阈值）
WARNING_THRESHOLD = 0.70       # 疑似重复（仅警告）


class DuplicateDetector:
    """
    题目重复检测器。

    用法:
        detector = DuplicateDetector(fuzzy_threshold=0.85)
        result = detector.detect_intra_duplicates(paper_questions)
        if result["duplicate_found"]:
            print(f"发现 {result['duplicate_count']} 对重复题目")
    """

    def __init__(self, fuzzy_threshold: float = FUZZY_DUP_THRESHOLD):
        self.fuzzy_threshold = fuzzy_threshold

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def detect_intra_duplicates(self, questions: list[dict]) -> dict:
        """
        检测试卷内部重复题目。

        参数:
            questions: 题目列表（dict 列表，每条需含 question_text 或 text 字段）

        返回:
            {
                "duplicate_found": bool,
                "duplicate_count": int,
                "duplicate_pairs": [(idx_a, idx_b, similarity, text_a[:50], text_b[:50]), ...],
                "exact_duplicates": [...],
                "fuzzy_duplicates": [...],
                "warnings": [...],
                "unique_count": int,
                "max_similarity": float,
            }
        """
        n = len(questions)
        if n < 2:
            return self._empty_result()

        texts = [self._extract_text(q) for q in questions]
        exact_dups = []
        fuzzy_dups = []
        warnings = []

        seen_hashes = {}
        all_pairs = []

        for i in range(n):
            ti = texts[i]
            hi = self._text_hash(ti)

            # 精确去重
            if hi in seen_hashes:
                exact_dups.append({
                    "index_a": seen_hashes[hi],
                    "index_b": i,
                    "text_a": texts[seen_hashes[hi]][:80],
                    "text_b": ti[:80],
                })
                all_pairs.append((seen_hashes[hi], i, 1.0, texts[seen_hashes[hi]][:50], ti[:50]))
                continue
            seen_hashes[hi] = i

            # 模糊去重：只比较长度相近且尚未被标记的
            for j in range(i):
                tj = texts[j]
                len_ratio = abs(len(ti) - len(tj)) / max(len(ti), len(tj), 1)
                if len_ratio > 0.35:
                    continue

                sim = difflib.SequenceMatcher(None, ti, tj).ratio()

                if sim >= EXACT_DUP_THRESHOLD:
                    continue  # 已被精确去重处理

                if sim >= self.fuzzy_threshold:
                    severity = "high" if sim >= HIGH_SIMILARITY_THRESHOLD else "medium"
                    fuzzy_dups.append({
                        "index_a": j,
                        "index_b": i,
                        "similarity": round(sim, 4),
                        "severity": severity,
                        "text_a": tj[:80],
                        "text_b": ti[:80],
                    })
                    all_pairs.append((j, i, round(sim, 4), tj[:50], ti[:50]))
                elif sim >= WARNING_THRESHOLD:
                    warnings.append({
                        "index_a": j,
                        "index_b": i,
                        "similarity": round(sim, 4),
                        "text_a": tj[:80],
                        "text_b": ti[:80],
                    })

        duplicate_count = len(exact_dups) + len(fuzzy_dups)
        max_sim = max([p[2] for p in all_pairs]) if all_pairs else 0.0

        return {
            "duplicate_found": duplicate_count > 0,
            "duplicate_count": duplicate_count,
            "duplicate_pairs": all_pairs,
            "exact_duplicates": exact_dups,
            "fuzzy_duplicates": fuzzy_dups,
            "warnings": warnings,
            "unique_count": n - len(exact_dups),
            "max_similarity": round(max_sim, 4),
        }

    def detect_cross_duplicates(
        self,
        new_questions: list[dict],
        existing_questions: list[dict],
    ) -> dict:
        """
        检测新题目与 RAG 知识库已有题目的重复率。

        参数:
            new_questions:      新题目列表
            existing_questions: 知识库已有题目列表

        返回:
            {
                "cross_duplicate_rate": 0.25,  # 新题中有多少比例与已有题目重复
                "cross_duplicate_count": 5,
                "cross_duplicate_pairs": [...],
                "total_new": 20,
                "total_existing": 5000,
            }
        """
        if not new_questions or not existing_questions:
            return self._empty_cross_result(len(new_questions), len(existing_questions))

        new_texts = [self._extract_text(q) for q in new_questions]
        existing_texts = [self._extract_text(q) for q in existing_questions]

        # 构建已有题目的哈希索引
        existing_hashes = {self._text_hash(t): i for i, t in enumerate(existing_texts)}

        cross_dups = []
        dup_count = 0

        for i, new_text in enumerate(new_texts):
            hi = self._text_hash(new_text)

            # 精确匹配
            if hi in existing_hashes:
                cross_dups.append({
                    "new_index": i,
                    "existing_index": existing_hashes[hi],
                    "similarity": 1.0,
                    "match_type": "exact",
                    "new_text": new_text[:80],
                })
                dup_count += 1
                continue

            # 模糊匹配（在已有文本中搜索）
            for j, existing_text in enumerate(existing_texts):
                len_ratio = abs(len(new_text) - len(existing_text)) / max(len(new_text), len(existing_text), 1)
                if len_ratio > 0.35:
                    continue

                sim = difflib.SequenceMatcher(None, new_text, existing_text).ratio()
                if sim >= self.fuzzy_threshold:
                    cross_dups.append({
                        "new_index": i,
                        "existing_index": j,
                        "similarity": round(sim, 4),
                        "match_type": "fuzzy",
                        "new_text": new_text[:80],
                        "existing_text": existing_text[:80],
                    })
                    dup_count += 1
                    break  # 找到一个重复即可

        return {
            "cross_duplicate_rate": round(dup_count / len(new_questions), 4) if new_questions else 0,
            "cross_duplicate_count": dup_count,
            "cross_duplicate_pairs": cross_dups,
            "total_new": len(new_questions),
            "total_existing": len(existing_questions),
        }

    # ------------------------------------------------------------------
    # 便捷检测
    # ------------------------------------------------------------------

    def check_paper_pass(self, questions: list[dict]) -> tuple[bool, str]:
        """
        检查试卷是否通过内部重复检测（pytest 友好）。

        返回:
            (passed, message)
        """
        result = self.detect_intra_duplicates(questions)
        if result["duplicate_found"]:
            return False, (
                f"发现 {result['duplicate_count']} 对重复题目 "
                f"(最高相似度 {result['max_similarity']:.2f})"
            )
        if result["warnings"]:
            return True, f"PASS (有 {len(result['warnings'])} 对疑似重复，相似度 0.70-0.85)"
        return True, "PASS (无重复)"

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(question: dict) -> str:
        """从题目中提取用于比较的文本"""
        # 支持多种格式
        text = question.get("question_text", question.get("text", question.get("question", "")))
        if isinstance(text, dict):
            text = text.get("question_text", str(text))
        return str(text)

    @staticmethod
    def _text_hash(text: str) -> str:
        """计算归一化文本的 MD5 哈希"""
        normalized = re.sub(r"\s+", "", str(text)).lower()
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _empty_result() -> dict:
        return {
            "duplicate_found": False,
            "duplicate_count": 0,
            "duplicate_pairs": [],
            "exact_duplicates": [],
            "fuzzy_duplicates": [],
            "warnings": [],
            "unique_count": 0,
            "max_similarity": 0.0,
        }

    @staticmethod
    def _empty_cross_result(new_count: int, existing_count: int) -> dict:
        return {
            "cross_duplicate_rate": 0.0,
            "cross_duplicate_count": 0,
            "cross_duplicate_pairs": [],
            "total_new": new_count,
            "total_existing": existing_count,
        }


# ---------------------------------------------------------------------------
# pytest 集成
# ---------------------------------------------------------------------------

def check_no_intra_duplicates(questions: list[dict]) -> None:
    """
    pytest 断言：试卷内不得有重复题目。

    用法:
        def test_paper_no_duplicates(sample_questions):
            check_no_intra_duplicates(sample_questions)
    """
    detector = DuplicateDetector()
    result = detector.detect_intra_duplicates(questions)
    if result["duplicate_found"]:
        detail = "; ".join(
            f"#{a}<->#{b} sim={s:.2f}"
            for a, b, s, _, _ in result["duplicate_pairs"]
        )
        raise AssertionError(f"试卷内发现 {result['duplicate_count']} 对重复题目: {detail}")


def check_cross_duplicate_rate(new_qs: list[dict], existing_qs: list[dict], max_rate: float = 0.3) -> None:
    """
    pytest 断言：新题目与已有题目的重复率不超过指定阈值。
    """
    detector = DuplicateDetector()
    result = detector.detect_cross_duplicates(new_qs, existing_qs)
    if result["cross_duplicate_rate"] > max_rate:
        raise AssertionError(
            f"跨库重复率 {result['cross_duplicate_rate']:.1%} > {max_rate:.0%} "
            f"({result['cross_duplicate_count']}/{result['total_new']} 题与已有题库重复)"
        )


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    detector = DuplicateDetector()

    # 测试1：内部重复检测
    print("=" * 60)
    print("内部重复检测")
    print("=" * 60)
    questions = [
        {"question_text": "解方程：x + 5 = 12"},
        {"question_text": "解方程：3x - 7 = 2x + 5"},
        {"question_text": "解方程：x + 5 = 12"},  # 精确重复
        {"question_text": "解方程：x + 6 = 13"},  # 模糊重复
        {"question_text": "三角形三个内角的和是多少度？"},
    ]
    result = detector.detect_intra_duplicates(questions)
    print(f"  重复: {result['duplicate_found']} (count={result['duplicate_count']})")
    for a, b, sim, ta, tb in result["duplicate_pairs"]:
        print(f"    #{a} <-> #{b} sim={sim:.3f}: {ta}... <-> {tb}...")
    for w in result["warnings"]:
        print(f"    [WARN] #{w['index_a']} <-> #{w['index_b']} sim={w['similarity']:.3f}")

    # 测试2：跨库重复检测
    print("\n跨库重复检测")
    print("=" * 60)
    existing = [
        {"question_text": "一元二次方程ax²+bx+c=0的求根公式"},
        {"question_text": "勾股定理：直角三角形的两直角边平方和等于斜边平方"},
        {"question_text": "计算三角形面积：底×高÷2"},
    ]
    new_qs = [
        {"question_text": "一元二次方程的求根公式是什么？"},       # 与 existing[0] 模糊匹配
        {"question_text": "勾股定理：a² + b² = c²"},                # 与 existing[1] 精确匹配
        {"question_text": "圆周率π约等于3.14159"},                 # 新题
    ]
    result = detector.detect_cross_duplicates(new_qs, existing)
    print(f"  跨库重复率: {result['cross_duplicate_rate']:.1%}")
    print(f"  重复数: {result['cross_duplicate_count']}/{result['total_new']}")
    for d in result["cross_duplicate_pairs"]:
        print(f"    新#{d['new_index']} <-> 已有#{d['existing_index']} [{d['match_type']}] sim={d['similarity']}")
