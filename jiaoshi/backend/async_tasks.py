# -*- coding: utf-8 -*-
"""
异步任务管理器
使用线程池处理耗时操作（试卷生成），支持状态查询和结果获取

用法:
    from async_tasks import submit_task, get_task_status, get_task_result

    task_id = submit_task(generate_func, arg1, arg2)
    # 轮询
    status = get_task_status(task_id)  # "pending" | "running" | "done" | "error"
    result = get_task_result(task_id)  # 阻塞直到完成
"""

import uuid
import time
import threading
from typing import Callable, Optional


class TaskManager:
    """线程池任务管理器"""

    def __init__(self, max_workers: int = 4, ttl_seconds: int = 1800):
        """
        参数:
            max_workers: 最大并发任务数
            ttl_seconds: 任务结果过期时间
        """
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max_workers)
        self._ttl = ttl_seconds

    def submit(self, func: Callable, *args, **kwargs) -> str:
        """
        提交异步任务

        返回:
            task_id: 唯一任务标识符
        """
        task_id = str(uuid.uuid4())[:12]

        with self._lock:
            self._tasks[task_id] = {
                "status": "pending",
                "created_at": time.time(),
                "result": None,
                "error": None,
            }

        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, func, args, kwargs),
            daemon=True,
        )
        thread.start()

        self._cleanup_expired()
        return task_id

    def _run_task(self, task_id: str, func: Callable, args: tuple, kwargs: dict):
        """在线程中执行任务"""
        self._semaphore.acquire()
        try:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "running"

            result = func(*args, **kwargs)

            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "done"
                    self._tasks[task_id]["result"] = result
                    self._tasks[task_id]["completed_at"] = time.time()

        except Exception as e:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "error"
                    self._tasks[task_id]["error"] = str(e)
        finally:
            self._semaphore.release()

    def get_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return {
                "task_id": task_id,
                "status": task["status"],
                "created_at": task["created_at"],
                "elapsed": round(time.time() - task["created_at"], 2),
            }

    def get_result(self, task_id: str, timeout: float = None) -> Optional[dict]:
        """
        获取任务结果（阻塞直到完成或超时）

        返回:
            {"status": "done", "result": ...} 或 {"status": "error", "error": "..."}
        """
        start = time.time()
        while True:
            status = self.get_status(task_id)
            if status is None:
                return {"status": "not_found", "error": "任务不存在"}

            if status["status"] in ("done", "error"):
                with self._lock:
                    task = self._tasks[task_id]
                return {
                    "status": task["status"],
                    "result": task.get("result"),
                    "error": task.get("error"),
                }

            if timeout and (time.time() - start) > timeout:
                return {"status": "timeout", "error": "任务超时"}

            time.sleep(0.5)

    def _cleanup_expired(self):
        """清理过期任务"""
        now = time.time()
        expired = []
        for task_id, task in self._tasks.items():
            created = task.get("created_at", 0)
            if task["status"] in ("done", "error") and (now - created) > self._ttl:
                expired.append(task_id)
        for task_id in expired:
            del self._tasks[task_id]

    def stats(self) -> dict:
        """任务统计"""
        with self._lock:
            counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
            for task in self._tasks.values():
                s = task.get("status", "unknown")
                counts[s] = counts.get(s, 0) + 1
            return {
                "total_tasks": len(self._tasks),
                "by_status": counts,
                "max_workers": self._semaphore._initial_value,
            }


# 全局单例
_task_manager = TaskManager(max_workers=4, ttl_seconds=1800)


def submit_task(func: Callable, *args, **kwargs) -> str:
    """提交异步任务，返回 task_id"""
    return _task_manager.submit(func, *args, **kwargs)


def get_task_status(task_id: str) -> Optional[dict]:
    """查询任务状态"""
    return _task_manager.get_status(task_id)


def get_task_result(task_id: str, timeout: float = 60) -> Optional[dict]:
    """获取任务结果（阻塞）"""
    return _task_manager.get_result(task_id, timeout=timeout)


def task_stats() -> dict:
    """任务统计"""
    return _task_manager.stats()
