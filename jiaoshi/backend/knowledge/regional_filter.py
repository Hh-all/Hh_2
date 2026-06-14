# -*- coding: utf-8 -*-
"""
地域过滤模块
============
按地域筛选 RAG 检索结果，支持地域降级（fallback）策略。

核心能力：
  - filter_by_region:       按地域筛选检索结果
  - filter_by_region_strict: 严格地域过滤（无 fallback）
  - get_region_stats:        统计各区域题目分布

地域降级策略：
  1. 优先返回精确匹配指定地域的题目
  2. 如果某地域题目不足，降级到"全国通用"题目（region="" 的题目）
  3. 降级的题目在输出中标记 `region_fallback: true`
  4. 如果全域题目仍不足，按相似度顺序补充其他地域的题目（标记 `region_fallback: true`）
  5. 地域优先级链: 目标地域 > 全国通用 > 相邻地域 > 其他地域

用法:
    from backend.knowledge.regional_filter import RegionalFilter

    rf = RegionalFilter()
    filtered = rf.filter_by_region(search_results, region="beijing", min_count=5)
"""

import json
import os
import sys
import logging
from pathlib import Path
from collections import Counter
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("knowledge.regional_filter")

# ---------------------------------------------------------------------------
# 地域相邻关系映射（用于降级时的优先级排序）
# ---------------------------------------------------------------------------
# 当目标地域题目不足时，优先使用相邻地域的题目
REGION_NEIGHBORS = {
    "beijing":    ["beijing", "shanghai", "guangdong"],     # 北京 → 一线城市优先
    "shanghai":   ["shanghai", "beijing", "guangdong"],     # 上海 → 一线城市优先
    "guangdong":  ["guangdong", "beijing", "shanghai"],     # 广东 → 一线城市优先
}

# 目标地域在降级链中的优先级权重
FALLBACK_ORDER = [
    "exact_match",       # 精确匹配
    "national",          # 全国通用（region=""）
    "neighbor",          # 相邻地域
    "other",             # 其他地域
]


class RegionalFilter:
    """
    地域过滤器，对 RAG 检索结果按地域进行筛选和排序。

    地域策略:
      - 精确匹配: 题目 region == 目标地域，标记为 region_fallback=false
      - 全国通用: 题目 region == ""，标记为 region_fallback=true
      - 相邻地域: 题目 region 在相邻列表中，标记为 region_fallback=true
      - 其他地域: 剩余题目，标记为 region_fallback=true
    """

    def __init__(self, neighbor_map: dict = None):
        self.neighbor_map = neighbor_map or REGION_NEIGHBORS

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def filter_by_region(
        self,
        results: list[dict],
        region: str,
        min_count: int = 0,
        max_count: int = None,
        fallback: bool = True,
    ) -> list[dict]:
        """
        按地域筛选检索结果，自动启用降级策略。

        参数:
            results:   检索结果列表（来自 rag_searcher.search()）
            region:    目标地域代码（beijing / shanghai / guangdong / ""）
            min_count: 最少需要的题目数。不足时触发 fallback
            max_count: 最多返回的题目数（None 表示不限制）
            fallback:  是否启用降级策略（默认 True）

        返回:
            筛选后的结果列表，每项新增:
              - _region_match:   "exact" | "national" | "neighbor" | "other"
              - _region_fallback: bool（是否来自降级）
        """
        if not results:
            return []

        if not region:
            # 不限地域，所有结果标记为匹配
            for r in results:
                r["_region_match"] = "exact"
                r["_region_fallback"] = False
            return results[:max_count] if max_count else results

        # 按地域分组
        exact_match = []
        national = []       # region="" 的全国通用题
        neighbor_match = []
        other_match = []

        neighbors = self.neighbor_map.get(region, [])

        for r in results:
            r_region = r.get("region", "")
            if r_region == region:
                exact_match.append(r)
            elif r_region == "":
                national.append(r)
            elif r_region in neighbors:
                neighbor_match.append(r)
            else:
                other_match.append(r)

        # 组装结果
        if fallback:
            output = self._assemble_with_fallback(
                exact_match, national, neighbor_match, other_match, min_count, max_count
            )
        else:
            # 严格模式：只返回精确匹配
            output = exact_match
            for r in output:
                r["_region_match"] = "exact"
                r["_region_fallback"] = False
            if max_count:
                output = output[:max_count]

        # 统计日志
        stats = self._compute_group_stats(output)
        logger.info(
            f"地域过滤: region={region}, 精确={len(exact_match)}, "
            f"全国通用={len(national)}, 相邻={len(neighbor_match)}, "
            f"其他={len(other_match)}, 输出={len(output)}"
        )

        return output

    def filter_by_region_strict(
        self,
        results: list[dict],
        region: str,
        max_count: int = None,
    ) -> list[dict]:
        """
        严格地域过滤：只返回精确匹配的题目，不使用 fallback。

        适用场景：用户明确要求"只要北京卷"的情况。
        """
        return self.filter_by_region(results, region, fallback=False, max_count=max_count)

    def get_region_stats(self, results: list[dict]) -> dict:
        """
        统计检索结果中各地域的题目分布。

        返回:
            {
              "total": N,
              "by_region": {"beijing": 5, "shanghai": 3, ...},
              "national": 2,   # region="" 的题目数
              "top_region": "beijing"
            }
        """
        counter = Counter()
        national_count = 0
        for r in results:
            r_region = r.get("region", "")
            if r_region:
                counter[r_region] += 1
            else:
                national_count += 1

        return {
            "total": len(results),
            "by_region": dict(counter.most_common()),
            "national": national_count,
            "top_region": counter.most_common(1)[0][0] if counter else "",
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _assemble_with_fallback(
        self,
        exact: list[dict],
        national: list[dict],
        neighbor: list[dict],
        other: list[dict],
        min_count: int,
        max_count: int,
    ) -> list[dict]:
        """按降级优先级组装结果"""
        # 组内保持原有相似度排序，标记匹配类型
        for r in exact:
            r["_region_match"] = "exact"
            r["_region_fallback"] = False
        for r in national:
            r["_region_match"] = "national"
            r["_region_fallback"] = True
        for r in neighbor:
            r["_region_match"] = "neighbor"
            r["_region_fallback"] = True
        for r in other:
            r["_region_match"] = "other"
            r["_region_fallback"] = True

        # 第一阶段：精确匹配
        output = list(exact)

        # 第二阶段：不足时从全国通用题补充
        if len(output) < min_count and national:
            needed = min_count - len(output)
            output.extend(national[:needed])

        # 第三阶段：仍不足时从相邻地域补充
        if len(output) < min_count and neighbor:
            needed = min_count - len(output)
            output.extend(neighbor[:needed])

        # 第四阶段：仍不足时从其他地域补充
        if len(output) < min_count and other:
            needed = min_count - len(output)
            output.extend(other[:needed])

        # 如果仍然不足，记录警告
        if min_count > 0 and len(output) < min_count:
            logger.warning(
                f"地域过滤: 目标 {min_count} 题，实际可用 {len(output)} 题 "
                f"(精确={len(exact)}, 全国={len(national)}, 相邻={len(neighbor)}, 其他={len(other)})"
            )

        if max_count:
            output = output[:max_count]

        return output

    def _compute_group_stats(self, output: list[dict]) -> dict:
        """统计输出结果中各降级组的分布"""
        counter = Counter(r.get("_region_match", "unknown") for r in output)
        return dict(counter)


# ---------------------------------------------------------------------------
# 便捷函数（无状态调用）
# ---------------------------------------------------------------------------

def filter_by_region(results: list[dict], region: str, min_count: int = 5, fallback: bool = True) -> list[dict]:
    """
    便捷函数：按地域筛选检索结果。

    用法:
        from backend.knowledge.regional_filter import filter_by_region
        filtered = filter_by_region(search_results, region="beijing")
    """
    rf = RegionalFilter()
    return rf.filter_by_region(results, region, min_count=min_count, fallback=fallback)


def get_region_stats(results: list[dict]) -> dict:
    """便捷函数：统计地域分布"""
    rf = RegionalFilter()
    return rf.get_region_stats(results)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 模拟检索结果
    mock_results = [
        {"id": "1", "question_text": "北京数学题", "region": "beijing", "score": 0.95},
        {"id": "2", "question_text": "北京语文题", "region": "beijing", "score": 0.90},
        {"id": "3", "question_text": "全国数学题", "region": "", "score": 0.85},
        {"id": "4", "question_text": "全国英语题", "region": "", "score": 0.80},
        {"id": "5", "question_text": "上海数学题", "region": "shanghai", "score": 0.88},
        {"id": "6", "question_text": "广东英语题", "region": "guangdong", "score": 0.75},
        {"id": "7", "question_text": "北京数学题2", "region": "beijing", "score": 0.70},
        {"id": "8", "question_text": "全国语文题", "region": "", "score": 0.65},
    ]

    rf = RegionalFilter()

    # 测试1: 正常过滤（有足够精确匹配）
    print("=" * 50)
    print("测试1: 北京地域过滤 (min_count=3)")
    result = rf.filter_by_region(mock_results, region="beijing", min_count=3)
    for r in result:
        print(f"  {r['id']} region={r['region']} match={r['_region_match']} fallback={r['_region_fallback']} score={r['score']}")

    # 测试2: 触发 fallback（精确匹配不足）
    print("\n测试2: 上海地域过滤 (min_count=5, 触发fallback)")
    result = rf.filter_by_region(mock_results, region="shanghai", min_count=5)
    for r in result:
        print(f"  {r['id']} region={r['region']} match={r['_region_match']} fallback={r['_region_fallback']} score={r['score']}")

    # 测试3: 严格模式
    print("\n测试3: 北京严格过滤 (无fallback)")
    result = rf.filter_by_region_strict(mock_results, region="beijing")
    for r in result:
        print(f"  {r['id']} region={r['region']} match={r['_region_match']} fallback={r['_region_fallback']}")

    # 测试4: 地域统计
    print("\n测试4: 地域统计")
    stats = rf.get_region_stats(mock_results)
    print(f"  {json.dumps(stats, ensure_ascii=False, indent=2)}")
