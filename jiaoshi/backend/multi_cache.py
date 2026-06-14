# -*- coding: utf-8 -*-
"""
多级缓存策略
============
L1: Redis 缓存（热门查询，TTL 1小时）
L2: 内存缓存（高频知识点检索结果，TTL 10分钟）
L3: 本地文件缓存（向量嵌入，持久化）

回退链: Redis → 内存 → 文件 → 重新计算

用法:
    from backend.multi_cache import MultiCache
    cache = MultiCache()
    result = cache.get_or_set("search:一元一次方程", lambda: do_search(...))
"""

import os
import sys
import json
import time
import pickle
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional, Callable

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("multi_cache")

# ---------------------------------------------------------------------------
# L2 内存缓存（与现有 cache.py 兼容）
# ---------------------------------------------------------------------------

from backend.cache import TTLCache

# ---------------------------------------------------------------------------
# L1 Redis 缓存
# ---------------------------------------------------------------------------

class RedisCache:
    """Redis 缓存层，自动检测 Redis 可用性"""

    def __init__(self, redis_url: str = None):
        self._redis = None
        self._available = False
        redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/2")
        self._redis_url = redis_url
        self._init_redis()

    def _init_redis(self):
        try:
            import redis
            self._redis = redis.from_url(self._redis_url, socket_connect_timeout=2)
            self._redis.ping()
            self._available = True
            logger.info(f"Redis L1 缓存就绪: {self._redis_url}")
        except Exception:
            self._available = False
            logger.info("Redis 不可用，L1 缓存跳过")

    @property
    def available(self) -> bool:
        return self._available

    def get(self, key: str) -> Optional[any]:
        if not self._available:
            return None
        try:
            value = self._redis.get(key)
            if value:
                return json.loads(value)
        except Exception:
            pass
        return None

    def set(self, key: str, value: any, ttl: int = 3600):
        if not self._available:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        except Exception:
            pass

    def delete(self, key: str):
        if not self._available:
            return
        try:
            self._redis.delete(key)
        except Exception:
            pass

    def keys(self, pattern: str = "*") -> list[str]:
        if not self._available:
            return []
        try:
            return [k.decode() for k in self._redis.keys(pattern)]
        except Exception:
            return []

    def flush_pattern(self, pattern: str):
        """清除匹配 pattern 的缓存"""
        if not self._available:
            return
        keys = self.keys(pattern)
        if keys:
            try:
                self._redis.delete(*keys)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 多级缓存引擎
# ---------------------------------------------------------------------------

class MultiCache:
    """
    三级缓存引擎：Redis (L1) → 内存 (L2) → 文件 (L3) → 重算。

    用法:
        cache = MultiCache()
        result = cache.get_or_set(
            "search:math:一元一次方程",
            lambda: expensive_search(),
            ttl_l1=3600, ttl_l2=600,
        )
    """

    def __init__(self, redis_url: str = None):
        self._l1 = RedisCache(redis_url)
        self._l2 = TTLCache(default_ttl=600, max_size=2000)  # 2000 条内存缓存
        self._l3_dir = ROOT / "data" / "cache"
        self._l3_dir.mkdir(parents=True, exist_ok=True)
        self._stats = {"hits_l1": 0, "hits_l2": 0, "hits_l3": 0, "misses": 0, "sets": 0}

    def get_or_set(self, key: str, factory: Callable, ttl_l1: int = 3600,
                   ttl_l2: int = 600, use_l3: bool = False) -> any:
        """多级缓存读取，未命中则计算并回填"""
        # L1: Redis
        value = self._l1.get(key)
        if value is not None:
            self._stats["hits_l1"] += 1
            self._l2.set(key, value, ttl_l2)  # 回填 L2
            return value

        # L2: 内存
        value = self._l2.get(key)
        if value is not None:
            self._stats["hits_l2"] += 1
            self._l1.set(key, value, ttl_l1)  # 回填 L1
            return value

        # L3: 文件
        if use_l3:
            value = self._l3_get(key)
            if value is not None:
                self._stats["hits_l3"] += 1
                self._l2.set(key, value, ttl_l2)
                self._l1.set(key, value, ttl_l1)
                return value

        # 未命中：计算
        self._stats["misses"] += 1
        value = factory()

        if value is not None:
            self._stats["sets"] += 1
            self._l2.set(key, value, ttl_l2)
            self._l1.set(key, value, ttl_l1)
            if use_l3:
                self._l3_set(key, value)

        return value

    def invalidate(self, key: str):
        """使缓存失效"""
        self._l1.delete(key)
        self._l2.delete(key)

    def invalidate_pattern(self, pattern: str):
        """按模式使缓存失效"""
        self._l1.flush_pattern(pattern)

    def warmup(self, keys_and_factories: list[tuple[str, Callable]]):
        """预热缓存"""
        for key, factory in keys_and_factories:
            self.get_or_set(key, factory)

    def stats(self) -> dict:
        total = sum(self._stats.values())
        hit_rate = (self._stats["hits_l1"] + self._stats["hits_l2"] +
                    self._stats["hits_l3"]) / max(total, 1)
        return {
            **self._stats,
            "total_ops": total,
            "hit_rate": round(hit_rate, 4),
            "l2_size": len(self._l2._store),
        }

    # ------------------------------------------------------------------
    # L3 文件缓存
    # ------------------------------------------------------------------

    def _l3_key_to_path(self, key: str) -> Path:
        h = hashlib.md5(key.encode()).hexdigest()
        return self._l3_dir / f"{h[:2]}" / f"{h}.pkl"

    def _l3_get(self, key: str) -> Optional[any]:
        path = self._l3_key_to_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                entry = pickle.load(f)
            if time.time() - entry.get("ts", 0) > entry.get("ttl", 86400):
                path.unlink(missing_ok=True)
                return None
            return entry.get("value")
        except Exception:
            return None

    def _l3_set(self, key: str, value: any, ttl: int = 86400):
        path = self._l3_key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "wb") as f:
                pickle.dump({"value": value, "ts": time.time(), "ttl": ttl}, f)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_cache = MultiCache()


def get_cache() -> MultiCache:
    return _cache


def cached_search(query: str, subject: str = "", top_k: int = 5) -> list[dict]:
    """带缓存的 RAG 检索"""
    key = f"search:{subject}:{hashlib.md5(query.encode()).hexdigest()[:12]}:{top_k}"
    return _cache.get_or_set(
        key,
        lambda: _do_search(query, subject, top_k),
        ttl_l1=3600, ttl_l2=600,
    )


def _do_search(query: str, subject: str, top_k: int) -> list[dict]:
    from backend.rag_searcher import init_searcher, search
    init_searcher()
    filters = {"subject": subject} if subject else None
    return search(query, filters=filters, top_k=top_k)
