# -*- coding: utf-8 -*-
"""
内存缓存层
基于 TTL 的字典缓存，支持过期自动清理，无需外部依赖

用法:
    from cache import cache_result, get_cached, clear_cache

    cache_key = "search_math_grade1_beijing_abc123"
    cached = get_cached(cache_key)
    if cached is None:
        result = expensive_operation()
        cache_result(cache_key, result)
"""

import time
import hashlib
import json
import threading
from typing import Optional


class TTLCache:
    """带 TTL 的线程安全内存缓存"""

    def __init__(self, default_ttl: int = 3600, max_size: int = 1000):
        """
        参数:
            default_ttl: 默认过期时间（秒），默认 3600（1 小时）
            max_size:    最大缓存条目数，超过时淘汰最早条目
        """
        self._store: dict[str, tuple[float, any]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._max_size = max_size

    def get(self, key: str) -> Optional[any]:
        """获取缓存值，过期返回 None"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: any, ttl: int = None):
        """写入缓存"""
        if ttl is None:
            ttl = self._default_ttl
        expires_at = time.time() + ttl
        with self._lock:
            # 超出容量时淘汰最早条目
            while len(self._store) >= self._max_size:
                oldest_key = min(
                    self._store.keys(),
                    key=lambda k: self._store[k][0],
                )
                del self._store[oldest_key]
            self._store[key] = (expires_at, value)

    def delete(self, key: str):
        """删除指定缓存"""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """缓存统计"""
        with self._lock:
            now = time.time()
            total = len(self._store)
            expired = sum(1 for _, (exp, _) in self._store.items() if now > exp)
            return {
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
                "max_size": self._max_size,
            }


# 全局单例
_cache = TTLCache(default_ttl=3600, max_size=1000)


# ---------- 辅助函数 ----------

def make_cache_key(*parts) -> str:
    """
    生成缓存键

    用法:
        key = make_cache_key("search", subject, grade, region, knowledge_hash)
    """
    raw = "_".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def knowledge_hash(knowledge_points: list[str]) -> str:
    """知识点列表转简短哈希"""
    if not knowledge_points:
        return "empty"
    sorted_kps = sorted(knowledge_points)
    raw = ",".join(sorted_kps)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def cache_result(key: str, value: any, ttl: int = None):
    """存入缓存"""
    _cache.set(key, value, ttl)


def get_cached(key: str) -> Optional[any]:
    """读取缓存"""
    return _cache.get(key)


def clear_cache(key: str = None):
    """清除缓存（指定 key 或全部）"""
    if key:
        _cache.delete(key)
    else:
        _cache.clear()


def cache_stats() -> dict:
    """获取缓存统计"""
    return _cache.stats()


# ---------- 装饰器 ----------

def cached(ttl: int = None):
    """
    函数结果缓存装饰器

    用法:
        @cached(ttl=3600)
        def expensive_search(query, filters):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            key_parts = [func.__name__] + list(args) + sorted(kwargs.items())
            key = make_cache_key(*key_parts)
            result = get_cached(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache_result(key, result, ttl)
            return result
        return wrapper
    return decorator
