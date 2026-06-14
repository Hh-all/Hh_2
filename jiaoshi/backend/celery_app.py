# -*- coding: utf-8 -*-
"""
Celery 异步任务队列
===================
使用 Redis 作为消息代理，将试卷生成等耗时操作异步化。

架构:
  Flask App ──▶ Redis (broker) ──▶ Celery Worker ──▶ Redis (backend)
       │                                                      │
       └──── 轮询 GET /api/task_result/{id} ◀─────────────────┘

环境变量:
  CELERY_BROKER_URL   — Redis 消息代理地址（默认: redis://localhost:6379/0）
  CELERY_RESULT_BACKEND — Redis 结果后端地址（默认: redis://localhost:6379/1）
  CELERY_TASK_TIMEOUT  — 任务超时秒数（默认: 120）

用法:
  # 启动 Worker
  celery -A backend.celery_app worker --loglevel=info --concurrency=4

  # 提交任务
  from backend.celery_app import generate_paper_async
  result = generate_paper_async.delay(subject="math", grade="grade_9", ...)
  print(result.id)  # task_id
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env")
except ImportError:
    pass

from celery import Celery

logger = logging.getLogger("celery")

# ---------------------------------------------------------------------------
# Celery 应用配置
# ---------------------------------------------------------------------------

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
TASK_TIMEOUT = int(os.environ.get("CELERY_TASK_TIMEOUT", "120"))

celery_app = Celery(
    "exam_paper_generator",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=TASK_TIMEOUT,
    task_soft_time_limit=TASK_TIMEOUT - 10,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    result_expires=1800,  # 结果过期 30 分钟
)

# ---------------------------------------------------------------------------
# 异步任务定义
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="generate_paper", max_retries=2)
def generate_paper_task(self, params: dict) -> dict:
    """
    异步生成整份试卷。

    参数:
        params: {"subject": "math", "grade": "grade_9", "region": "beijing",
                 "knowledge_points": [...], "difficulty": 3, "num_questions": 5}

    返回:
        {"status": "done", "questions": [...], "paper_meta": {...}}
    """
    logger.info(f"[task {self.request.id}] 开始生成试卷: {params.get('subject')}")
    self.update_state(state="PROGRESS", meta={"phase": "retrieving"})

    try:
        from backend.orchestrator import Orchestrator
        orch = Orchestrator()
        result = orch.run(params)

        if result.get("success"):
            self.update_state(state="PROGRESS", meta={"phase": "formatting"})
            return {
                "status": "done",
                "paper_meta": result.get("paper_meta", {}),
                "output_file": result.get("output_file", ""),
                "workflow_id": result.get("workflow_id", ""),
                "elapsed_seconds": result.get("elapsed_seconds", 0),
            }
        else:
            return {
                "status": "error",
                "error": result.get("errors", ["未知错误"]),
                "state": result.get("state", "UNKNOWN"),
            }
    except Exception as e:
        logger.error(f"[task {self.request.id}] 生成失败: {e}", exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=5 * (self.request.retries + 1))
        return {"status": "error", "error": str(e)}


@celery_app.task(bind=True, name="search_similar", max_retries=1)
def search_similar_task(self, query: str, subject: str = None,
                        top_k: int = 5, region: str = "") -> list[dict]:
    """异步检索相似题目"""
    try:
        from backend.rag_searcher import init_searcher, search as rag_search
        init_searcher()
        filters = {}
        if subject:
            filters["subject"] = subject
        results = rag_search(
            query=query, filters=filters, top_k=min(top_k, 50),
            region=region, enable_regional_filter=bool(region),
        )
        return results
    except Exception as e:
        logger.error(f"检索失败: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=3)
        return []


@celery_app.task(bind=True, name="warmup_cache")
def warmup_cache_task(self):
    """预热缓存（定时任务）"""
    logger.info(f"[task {self.request.id}] 开始缓存预热")
    try:
        from scripts.warmup_cache import warmup_all
        warmup_all()
        return {"status": "done", "message": "缓存预热完成"}
    except Exception as e:
        logger.error(f"缓存预热失败: {e}")
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# 定期任务调度（可选）
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "warmup-cache-every-30-minutes": {
        "task": "warmup_cache",
        "schedule": 1800.0,  # 30 分钟
    },
}
