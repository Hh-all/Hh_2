#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
缓存预热脚本
============
在服务启动后、接收请求前，预先计算热门查询结果并写入多级缓存。

预热策略:
  1. 热门知识点检索（Top 50 知识点 × 3 学科）
  2. 高频试卷请求（北京/上海/广东 × 3 年级）
  3. 知识图谱查询（前置关系 + 关联关系）

用法:
  python scripts/warmup_cache.py
  python scripts/warmup_cache.py --quiet     # 安静模式
  python scripts/warmup_cache.py --subjects math  # 仅预热指定学科
"""

import os
import sys
import time
import json
import logging
import argparse
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

logger = logging.getLogger("warmup_cache")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

# ---------------------------------------------------------------------------
# 热门知识点（按使用频率排序）
# ---------------------------------------------------------------------------

HOT_KNOWLEDGE_POINTS = {
    "math": [
        "一元一次方程", "二元一次方程组", "一元二次方程", "勾股定理",
        "一次函数表达式与图像", "二次函数", "全等三角形的判定",
        "相似三角形的判定", "概率的简单应用", "整数四则运算",
        "分数加减法", "小数乘除法", "正比例与反比例",
        "等差数列及其前n项和", "利用导数研究函数的单调性与极值",
    ],
    "chinese": [
        "古诗词背诵与鉴赏", "记叙文阅读与分析", "议论文写作",
        "文言文实词与虚词", "修辞方法", "成语与文化",
        "病句修改", "说明文阅读与分析", "现代诗歌鉴赏",
    ],
    "english": [
        "九大时态", "被动语态", "宾语从句与定语从句",
        "形容词与副词的比较等级", "1600个基础词汇及短语",
        "应用文（书信、通知、演讲）", "阅读理解", "状语从句",
    ],
}

# 高频请求组合
HOT_REQUESTS = [
    {"subject": "math", "grade": "grade_9", "region": "beijing"},
    {"subject": "math", "grade": "grade_8", "region": "shanghai"},
    {"subject": "math", "grade": "grade_7", "region": "guangdong"},
    {"subject": "chinese", "grade": "grade_9", "region": "beijing"},
    {"subject": "chinese", "grade": "grade_8", "region": "shanghai"},
    {"subject": "english", "grade": "grade_12", "region": "guangdong"},
    {"subject": "english", "grade": "grade_9", "region": "shanghai"},
    {"subject": "math", "grade": "grade_6", "region": "beijing"},
    {"subject": "chinese", "grade": "grade_6", "region": "beijing"},
]


def warmup_search_cache(subjects: list[str] = None):
    """预热 RAG 检索缓存"""
    from backend.multi_cache import get_cache
    from backend.rag_searcher import init_searcher, search as rag_search
    cache = get_cache()

    init_searcher()
    warmup_subjects = subjects or ["math", "chinese", "english"]

    count = 0
    for subject in warmup_subjects:
        kps = HOT_KNOWLEDGE_POINTS.get(subject, [])
        for kp in kps:
            for top_k in [3, 5, 10]:
                key = f"warmup:search:{subject}:{kp}:{top_k}"
                def factory(kp=kp, subject=subject, top_k=top_k):
                    return rag_search(kp, filters={"subject": subject}, top_k=top_k)
                cache.get_or_set(key, factory, ttl_l1=3600, ttl_l2=600)
                count += 1

    logger.info(f"检索缓存预热完成: {count} 个查询")
    return count


def warmup_knowledge_graph_cache():
    """预热知识图谱查询缓存"""
    from backend.multi_cache import get_cache
    from backend.knowledge.knowledge_graph import KnowledgeGraph
    cache = get_cache()
    kg = KnowledgeGraph()

    count = 0
    for subject, kps in HOT_KNOWLEDGE_POINTS.items():
        for kp in kps:
            # 前置关系
            key = f"warmup:kg:prereq:{kp}"
            cache.get_or_set(key, lambda k=kp: kg.get_prerequisites(k), ttl_l1=3600)
            count += 1
            # 关联关系
            key = f"warmup:kg:related:{kp}"
            cache.get_or_set(key, lambda k=kp: kg.get_related(k), ttl_l1=3600)
            count += 1

    logger.info(f"知识图谱缓存预热完成: {count} 个查询")
    return count


def warmup_paper_requests():
    """预热高频试卷请求"""
    from backend.multi_cache import get_cache
    cache = get_cache()

    count = 0
    for req in HOT_REQUESTS:
        key = f"warmup:paper:{req['subject']}:{req['grade']}:{req['region']}"
        cache.get_or_set(key, lambda r=req: r, ttl_l1=1800, ttl_l2=600)
        count += 1

    logger.info(f"试卷请求缓存预热完成: {count} 个")
    return count


def warmup_all(subjects: list[str] = None, quiet: bool = False):
    """执行全部预热任务"""
    if not quiet:
        logger.info("=" * 50)
        logger.info("缓存预热开始")
        logger.info("=" * 50)

    start = time.time()
    results = {}

    try:
        results["search"] = warmup_search_cache(subjects)
    except Exception as e:
        logger.warning(f"检索缓存预热跳过: {e}")
        results["search"] = 0

    try:
        results["knowledge_graph"] = warmup_knowledge_graph_cache()
    except Exception as e:
        logger.warning(f"知识图谱缓存预热跳过: {e}")
        results["knowledge_graph"] = 0

    try:
        results["paper_requests"] = warmup_paper_requests()
    except Exception as e:
        logger.warning(f"试卷请求缓存预热跳过: {e}")
        results["paper_requests"] = 0

    elapsed = time.time() - start
    total = sum(results.values())

    if not quiet:
        logger.info("=" * 50)
        logger.info(f"缓存预热完成: {total} 条 ({elapsed:.1f}s)")
        logger.info(f"  RAG 检索:  {results.get('search', 0)} 条")
        logger.info(f"  知识图谱:  {results.get('knowledge_graph', 0)} 条")
        logger.info(f"  试卷请求:  {results.get('paper_requests', 0)} 条")
        logger.info("=" * 50)

    return results


def main():
    parser = argparse.ArgumentParser(description="缓存预热脚本")
    parser.add_argument("--quiet", "-q", action="store_true", help="安静模式")
    parser.add_argument("--subjects", type=str, default="math,chinese,english",
                        help="学科列表（逗号分隔）")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    subjects = [s.strip() for s in args.subjects.split(",")]
    warmup_all(subjects, args.quiet)


if __name__ == "__main__":
    main()
