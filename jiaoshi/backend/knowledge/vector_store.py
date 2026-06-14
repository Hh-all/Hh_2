# -*- coding: utf-8 -*-
"""
向量存储管理器
==============
管理 ChromaDB 中试题文档的增量增删改操作，并维护版本历史。

核心能力：
  - add_documents:    批量添加新题目（带去重检测）
  - update_document:  更新已有题目的内容和元数据
  - delete_document:  按 ID 删除题目
  - get_version_info: 查看变更历史
  - rollback:         回退到指定版本

版本文件 (data/knowledge/version.json) 格式：
{
  "store_version": 5,
  "total_documents": 1250,
  "last_updated": "2026-06-14T15:30:00+08:00",
  "history": [
    {
      "version": 5,
      "action": "add_documents",
      "count": 100,
      "timestamp": "2026-06-14T15:30:00+08:00",
      "summary": "从 CMMaTH 导入 100 道数学题"
    },
    ...
  ]
}
"""

import json
import os
import sys
import time
import shutil
import hashlib
import difflib
import logging
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("knowledge.vector_store")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CHROMA_DIR = ROOT / "data" / "chroma_db"
VERSION_FILE = ROOT / "data" / "knowledge" / "version.json"
BACKUP_DIR = ROOT / "data" / "knowledge" / "backups"
COLLECTION_NAME = "exam_questions"
DEDUP_SIMILARITY_THRESHOLD = 0.88


class VectorStoreManager:
    """
    向量存储管理器，封装 ChromaDB 的增量 CRUD 操作和版本管理。

    用法:
        manager = VectorStoreManager()
        manager.add_documents(docs, metadatas, ids)
        manager.update_document("doc_123", new_content, new_metadata)
        manager.delete_document("doc_123")
        manager.rollback(3)  # 回退到版本 3
    """

    def __init__(self, collection_name: str = COLLECTION_NAME):
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._embed_model = None
        self._embed_method = None
        self._init_chroma()
        self._init_version_file()
        self._init_embed_model()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _init_chroma(self):
        """初始化 ChromaDB 客户端和集合"""
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            try:
                self._collection = self._client.get_collection(self.collection_name)
                logger.info(f"ChromaDB 集合 '{self.collection_name}' 已存在，{self._collection.count()} 条")
            except Exception:
                self._collection = self._client.create_collection(
                    self.collection_name,
                    metadata={"hnsw:space": "cosine", "hnsw:construction_ef": 200, "hnsw:M": 32},
                )
                logger.info(f"创建 ChromaDB 集合 '{self.collection_name}'")
            return True
        except ImportError:
            logger.warning("chromadb 未安装，回退到内存模式")
            self._client = None
            self._collection = _InMemoryCollection()
            return False
        except Exception as e:
            logger.error(f"ChromaDB 初始化失败: {e}")
            self._client = None
            self._collection = _InMemoryCollection()
            return False

    def _init_version_file(self):
        """初始化或加载版本文件"""
        VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        if VERSION_FILE.exists():
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                self._version_data = json.load(f)
        else:
            self._version_data = {
                "store_version": 0,
                "total_documents": 0,
                "last_updated": "",
                "history": [],
            }
            self._save_version()

    def _init_embed_model(self):
        """初始化嵌入模型"""
        try:
            from sentence_transformers import SentenceTransformer
            model_name = "paraphrase-multilingual-MiniLM-L12-v2"
            self._embed_model = SentenceTransformer(model_name)
            self._embed_method = "sentence_transformer"
            logger.info(f"嵌入模型: {model_name}")
        except (ImportError, Exception) as e:
            logger.warning(f"SentenceTransformer 不可用 ({e})，使用 TF-IDF")
            self._embed_method = "tfidf"
            self._embed_model = None

    # ------------------------------------------------------------------
    # 版本管理
    # ------------------------------------------------------------------

    def _save_version(self):
        """持久化版本文件"""
        self._version_data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(self._version_data, f, ensure_ascii=False, indent=2)

    def _record_action(self, action: str, count: int, summary: str = ""):
        """记录一次变更到版本历史"""
        self._version_data["store_version"] += 1
        self._version_data["total_documents"] = self._collection.count()
        self._version_data["history"].append({
            "version": self._version_data["store_version"],
            "action": action,
            "count": count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        })
        # 最多保留 50 条历史
        if len(self._version_data["history"]) > 50:
            self._version_data["history"] = self._version_data["history"][-50:]
        self._save_version()

    def _backup_collection(self, version: int):
        """在关键操作前备份当前集合状态"""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"backup_v{version}_{int(time.time())}.json"
        docs = self._get_all_documents()
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False)
        logger.info(f"备份已保存: {backup_path} ({len(docs)} 条)")
        # 最多保留 10 个备份
        backups = sorted(BACKUP_DIR.glob("backup_*.json"), key=lambda p: p.stat().st_mtime)
        for old in backups[:-10]:
            old.unlink()

    def get_version_info(self) -> dict:
        """获取当前版本信息"""
        return {
            "store_version": self._version_data["store_version"],
            "total_documents": self._collection.count(),
            "last_updated": self._version_data["last_updated"],
            "history_count": len(self._version_data["history"]),
            "recent_changes": self._version_data["history"][-5:],
        }

    def rollback(self, target_version: int) -> bool:
        """
        回退到指定版本。
        通过加载对应备份文件恢复集合状态。

        注意：ChromaDB 不支持直接回退，采用"清空 + 从备份重建"策略。
        """
        # 查找最近的备份文件（版本号 ≤ target_version）
        backups = sorted(BACKUP_DIR.glob("backup_*.json"), key=lambda p: p.stat().st_mtime)
        candidates = [b for b in backups if int(b.stem.split("_")[1][1:]) <= target_version]
        if not candidates:
            logger.error(f"无可用备份 (目标版本: {target_version})")
            return False

        backup_path = candidates[-1]
        logger.warning(f"回退到版本 {target_version}，使用备份: {backup_path}")

        with open(backup_path, "r", encoding="utf-8") as f:
            docs = json.load(f)

        # 清空当前集合
        self._clear_collection()

        # 从备份重建
        ids = [d["id"] for d in docs]
        documents = [d.get("document", "") for d in docs]
        metadatas = [d.get("metadata", {}) for d in docs]
        embeddings = [d.get("embedding") for d in docs]

        if embeddings and embeddings[0] is not None:
            self._collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        else:
            self._collection.add(ids=ids, documents=documents, metadatas=metadatas)

        # 裁剪版本历史
        self._version_data["history"] = [
            h for h in self._version_data["history"] if h["version"] <= target_version
        ]
        self._version_data["store_version"] = target_version
        self._version_data["total_documents"] = len(docs)
        self._record_action("rollback", len(docs), f"回退到版本 {target_version}")
        logger.info(f"回退完成: {len(docs)} 条文档")
        return True

    def _clear_collection(self):
        """清空集合中的所有文档"""
        if isinstance(self._collection, _InMemoryCollection):
            self._collection.clear()
        else:
            all_ids = self._collection.get()["ids"]
            if all_ids:
                self._collection.delete(ids=all_ids)

    def _get_all_documents(self) -> list[dict]:
        """获取集合中所有文档（用于备份）"""
        if isinstance(self._collection, _InMemoryCollection):
            return self._collection.get_all()
        else:
            result = self._collection.get(include=["embeddings", "documents", "metadatas"])
            docs = []
            for i in range(len(result["ids"])):
                doc = {
                    "id": result["ids"][i],
                    "document": result["documents"][i] if result["documents"] else "",
                    "metadata": result["metadatas"][i] if result["metadatas"] else {},
                }
                if result.get("embeddings") and result["embeddings"][i]:
                    doc["embedding"] = result["embeddings"][i]
                docs.append(doc)
            return docs

    # ------------------------------------------------------------------
    # 嵌入辅助
    # ------------------------------------------------------------------

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """对一批文本生成向量"""
        if self._embed_method == "sentence_transformer":
            vectors = self._embed_model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
            return vectors.tolist()
        else:
            # TF-IDF 模式：使用简单哈希向量（实际应使用训练好的 TF-IDF）
            dim = 384
            vectors = []
            for text in texts:
                h = hashlib.md5(text.encode("utf-8")).digest()
                vec = [((h[i % 16] / 255.0) * 2 - 1) for i in range(dim)]
                vectors.append(vec)
            return vectors

    # ------------------------------------------------------------------
    # 去重检测
    # ------------------------------------------------------------------

    def _detect_duplicates(self, texts: list[str]) -> tuple[list[int], list[int]]:
        """
        检测与已有文档重复的文本。
        返回 (去重后的索引列表, 重复的索引列表)。
        """
        all_docs = self._get_all_documents()
        existing_texts = [d.get("metadata", {}).get("question_text", d.get("document", "")) for d in all_docs]

        keep_indices = []
        dup_indices = []

        for i, text in enumerate(texts):
            is_dup = False
            norm_text = self._normalize(text)

            for existing in existing_texts:
                norm_existing = self._normalize(existing)
                if len(norm_text) == 0 or len(norm_existing) == 0:
                    continue
                # 长度差异超过 30% 跳过
                len_ratio = abs(len(norm_text) - len(norm_existing)) / max(len(norm_text), len(norm_existing))
                if len_ratio > 0.30:
                    continue
                sim = difflib.SequenceMatcher(None, norm_text, norm_existing).ratio()
                if sim >= DEDUP_SIMILARITY_THRESHOLD:
                    is_dup = True
                    logger.debug(f"去重命中: sim={sim:.3f}, text={text[:50]}...")
                    break

            if is_dup:
                dup_indices.append(i)
            else:
                keep_indices.append(i)

        return keep_indices, dup_indices

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化文本用于去重比较"""
        import re
        text = re.sub(r"\s+", "", text)
        return text.lower()

    # ------------------------------------------------------------------
    # 公开 API：增删改
    # ------------------------------------------------------------------

    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict],
        ids: list[str] = None,
        skip_dedup: bool = False,
        source_summary: str = "",
    ) -> dict:
        """
        批量添加新题目。

        参数:
            documents: 文档文本列表（question_text + knowledge_tags 拼接）
            metadatas: 元数据列表，每个 dict 需包含 question_text/answer/analysis/subject/grade/region/difficulty/knowledge_tags
            ids:       文档 ID 列表（可选，不提供则自动生成）
            skip_dedup: 跳过去重检测（默认 False）
            source_summary: 来源说明（记录在版本历史中）

        返回:
            {"added": N, "duplicates_skipped": M, "version": V}
        """
        if not documents:
            return {"added": 0, "duplicates_skipped": 0, "version": self._version_data["store_version"]}

        # 生成 ID（如未提供）
        if ids is None:
            ids = [f"doc_{hashlib.md5(d.encode()).hexdigest()[:12]}" for d in documents]

        # 备份当前状态
        self._backup_collection(self._version_data["store_version"])

        # 去重
        if not skip_dedup:
            question_texts = [m.get("question_text", "") for m in metadatas]
            keep_idx, dup_idx = self._detect_duplicates(question_texts)
            if dup_idx:
                logger.info(f"去重: {len(dup_idx)}/{len(documents)} 条重复")
        else:
            keep_idx = list(range(len(documents)))
            dup_idx = []

        if not keep_idx:
            return {"added": 0, "duplicates_skipped": len(dup_idx), "version": self._version_data["store_version"]}

        # 筛选
        filtered_docs = [documents[i] for i in keep_idx]
        filtered_metas = [metadatas[i] for i in keep_idx]
        filtered_ids = [ids[i] for i in keep_idx]

        # 生成嵌入向量
        embeddings = self._embed_texts(filtered_docs)

        # 写入 ChromaDB
        self._collection.add(
            ids=filtered_ids,
            embeddings=embeddings,
            metadatas=filtered_metas,
            documents=filtered_docs,
        )

        added = len(filtered_ids)
        summary = source_summary or f"添加 {added} 条文档"
        self._record_action("add_documents", added, summary)

        logger.info(f"已添加 {added} 条文档，跳过 {len(dup_idx)} 条重复 (版本 {self._version_data['store_version']})")
        return {
            "added": added,
            "duplicates_skipped": len(dup_idx),
            "version": self._version_data["store_version"],
            "ids": filtered_ids,
        }

    def update_document(self, doc_id: str, new_content: str, new_metadata: dict = None) -> bool:
        """
        更新已有题目。

        参数:
            doc_id:      文档 ID
            new_content: 新的文档文本
            new_metadata: 新的元数据（可选，不提供则保留原有元数据）

        返回:
            是否成功
        """
        # 检查文档是否存在
        try:
            existing = self._collection.get(ids=[doc_id])
            if not existing["ids"]:
                logger.warning(f"文档不存在: {doc_id}")
                return False
        except Exception:
            logger.warning(f"文档不存在: {doc_id}")
            return False

        # 备份
        self._backup_collection(self._version_data["store_version"])

        # ChromaDB 不支持直接更新，采用"删除 + 重新添加"策略
        self._collection.delete(ids=[doc_id])

        new_embedding = self._embed_texts([new_content])[0]
        meta = new_metadata if new_metadata else existing["metadatas"][0] if existing["metadatas"] else {}

        self._collection.add(
            ids=[doc_id],
            embeddings=[new_embedding],
            metadatas=[meta],
            documents=[new_content],
        )

        self._record_action("update_document", 1, f"更新文档 {doc_id}")
        logger.info(f"已更新文档: {doc_id}")
        return True

    def delete_document(self, doc_id: str) -> bool:
        """
        删除指定题目。

        参数:
            doc_id: 文档 ID

        返回:
            是否成功
        """
        try:
            existing = self._collection.get(ids=[doc_id])
            if not existing["ids"]:
                logger.warning(f"文档不存在: {doc_id}")
                return False
        except Exception:
            logger.warning(f"文档不存在: {doc_id}")
            return False

        self._backup_collection(self._version_data["store_version"])
        self._collection.delete(ids=[doc_id])

        self._record_action("delete_document", 1, f"删除文档 {doc_id}")
        logger.info(f"已删除文档: {doc_id}")
        return True

    def delete_documents(self, doc_ids: list[str]) -> dict:
        """
        批量删除题目。

        返回:
            {"deleted": N, "not_found": M}
        """
        deleted = 0
        not_found = 0
        for doc_id in doc_ids:
            if self.delete_document(doc_id):
                deleted += 1
            else:
                not_found += 1
        return {"deleted": deleted, "not_found": not_found}

    def count(self) -> int:
        """返回集合中文档总数"""
        return self._collection.count()

    def get_document(self, doc_id: str) -> Optional[dict]:
        """获取单条文档"""
        try:
            result = self._collection.get(
                ids=[doc_id],
                include=["documents", "metadatas"],
            )
            if result["ids"]:
                return {
                    "id": result["ids"][0],
                    "document": result["documents"][0] if result["documents"] else "",
                    "metadata": result["metadatas"][0] if result["metadatas"] else {},
                }
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# 内存回退集合（ChromiaDB 不可用时的备选方案）
# ---------------------------------------------------------------------------

class _InMemoryCollection:
    """纯内存的 ChromaDB 兼容集合，用于无 ChromaDB 环境"""

    def __init__(self):
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self._embeddings: list[list[float]] = []

    def add(self, ids, embeddings=None, metadatas=None, documents=None):
        self._ids.extend(ids)
        self._documents.extend(documents or [""] * len(ids))
        self._metadatas.extend(metadatas or [{}] * len(ids))
        if embeddings:
            self._embeddings.extend(embeddings)

    def get(self, ids=None, include=None):
        if ids:
            indices = [self._ids.index(i) for i in ids if i in self._ids]
        else:
            indices = list(range(len(self._ids)))

        result = {"ids": [self._ids[i] for i in indices]}
        if include:
            if "documents" in include:
                result["documents"] = [self._documents[i] for i in indices]
            if "metadatas" in include:
                result["metadatas"] = [self._metadatas[i] for i in indices]
            if "embeddings" in include:
                result["embeddings"] = [self._embeddings[i] if i < len(self._embeddings) else None for i in indices]
        return result

    def delete(self, ids):
        indices_to_delete = {i for i, id_ in enumerate(self._ids) if id_ in set(ids)}
        self._ids = [v for i, v in enumerate(self._ids) if i not in indices_to_delete]
        self._documents = [v for i, v in enumerate(self._documents) if i not in indices_to_delete]
        self._metadatas = [v for i, v in enumerate(self._metadatas) if i not in indices_to_delete]
        self._embeddings = [v for i, v in enumerate(self._embeddings) if i not in indices_to_delete]

    def count(self) -> int:
        return len(self._ids)

    def clear(self):
        self._ids.clear()
        self._documents.clear()
        self._metadatas.clear()
        self._embeddings.clear()

    def get_all(self) -> list[dict]:
        return [
            {
                "id": self._ids[i],
                "document": self._documents[i],
                "metadata": self._metadatas[i],
            }
            for i in range(len(self._ids))
        ]


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    manager = VectorStoreManager()

    # 展示版本信息
    info = manager.get_version_info()
    print(f"\n当前版本: v{info['store_version']}")
    print(f"文档总数: {info['total_documents']}")
    print(f"最近变更:")
    for h in info["recent_changes"]:
        print(f"  v{h['version']} {h['action']}: {h['summary']} ({h['timestamp'][:19]})")

    # 测试增删改
    print("\n--- 测试 add_documents ---")
    test_docs = [f"测试题目：{i} + {i+1} = ?" for i in range(3)]
    test_metas = [
        {"question_text": d, "subject": "math", "grade": "grade_7", "region": "beijing",
         "difficulty": 2, "knowledge_tags": "一元一次方程", "answer": str(2*i+1), "analysis": "测试解析"}
        for i, d in enumerate(test_docs)
    ]
    result = manager.add_documents(test_docs, test_metas, source_summary="测试添加")
    print(f"添加结果: {result}")

    print("\n--- 测试 update_document ---")
    if result.get("ids"):
        doc_id = result["ids"][0]
        ok = manager.update_document(doc_id, "更新后的题目：100 + 200 = ?",
                                     {"question_text": "更新后的题目：100 + 200 = ?", "subject": "math",
                                      "grade": "grade_7", "region": "shanghai", "difficulty": 3,
                                      "knowledge_tags": "整数四则运算", "answer": "300", "analysis": "更新解析"})
        print(f"更新结果: {'成功' if ok else '失败'}")

    print("\n--- 测试 delete_document ---")
    if result.get("ids") and len(result["ids"]) > 1:
        doc_id = result["ids"][1]
        ok = manager.delete_document(doc_id)
        print(f"删除结果: {'成功' if ok else '失败'}")

    print(f"\n最终文档数: {manager.count()}")
    print(f"最终版本: v{manager.get_version_info()['store_version']}")
