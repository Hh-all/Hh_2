# -*- coding: utf-8 -*-
"""
RAG 向量索引模块
将题库数据向量化并存入向量数据库（ChromaDB 或内存回退方案）
"""

import json
import os
import sys
import pickle
import numpy as np
from typing import Optional

# 项目根路径
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# -------------------- 嵌入模型选择 --------------------
_EMBED_MODEL = None
_EMBED_METHOD = None  # "sentence_transformer" | "tfidf"


def _init_sentence_transformer():
    """尝试加载 sentence-transformers 模型"""
    global _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        model_name = "paraphrase-multilingual-MiniLM-L12-v2"
        print(f"[rag_indexer] 正在加载 SentenceTransformer 模型: {model_name}")
        _EMBED_MODEL = SentenceTransformer(model_name)
        return True
    except ImportError:
        print("[rag_indexer] sentence-transformers 未安装")
        return False
    except Exception as e:
        print(f"[rag_indexer] 加载 SentenceTransformer 失败: {e}")
        return False


def _init_tfidf(corpus: list[str]):
    """使用 TF-IDF 构建向量化器"""
    global _EMBED_MODEL
    from sklearn.feature_extraction.text import TfidfVectorizer
    print("[rag_indexer] 使用 TF-IDF 作为嵌入方案（注意：这不是语义向量，仅用于演示）")
    vectorizer = TfidfVectorizer(max_features=384)
    _EMBED_MODEL = vectorizer.fit(corpus)
    return True


def embed_texts(texts: list[str]) -> np.ndarray:
    """对一批文本生成向量"""
    global _EMBED_METHOD
    if _EMBED_METHOD == "sentence_transformer":
        return _EMBED_MODEL.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    else:
        return _EMBED_MODEL.transform(texts).toarray().astype(np.float32)


def embed_query(query: str) -> np.ndarray:
    """对单条查询生成向量"""
    global _EMBED_METHOD
    if _EMBED_METHOD == "sentence_transformer":
        return _EMBED_MODEL.encode([query], show_progress_bar=False, convert_to_numpy=True)[0]
    else:
        return _EMBED_MODEL.transform([query]).toarray().astype(np.float32)[0]


def _get_embedding_dim() -> int:
    if _EMBED_METHOD == "sentence_transformer":
        return _EMBED_MODEL.get_embedding_dimension()
    else:
        return len(_EMBED_MODEL.get_feature_names_out())


# -------------------- ChromaDB 向量存储 --------------------

def _init_chromadb(collection_name: str, persist_dir: str):
    """初始化 ChromaDB 集合"""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=persist_dir)

        # 尝试获取已有集合，若不存在则创建
        try:
            collection = client.get_collection(collection_name)
            print(f"[rag_indexer] ChromaDB 集合 '{collection_name}' 已存在，共 {collection.count()} 条")
        except Exception:
            collection = client.create_collection(
                collection_name,
                metadata={"hnsw:space": "cosine", "hnsw:construction_ef": 200, "hnsw:M": 32},
            )
            print(f"[rag_indexer] 创建 ChromaDB 集合 '{collection_name}' (HNSW 索引)")

        return client, collection
    except ImportError:
        print("[rag_indexer] chromadb 未安装")
        return None, None
    except Exception as e:
        print(f"[rag_indexer] ChromaDB 初始化失败: {e}")
        return None, None


# -------------------- 内存向量存储（回退方案） --------------------

class InMemoryVectorStore:
    """纯内存向量存储，不依赖任何外部数据库"""

    def __init__(self):
        self.vectors: list[np.ndarray] = []
        self.metadata: list[dict] = []
        self.documents: list[str] = []

    def add(self, ids, embeddings, metadatas, documents):
        for i, emb, meta, doc in zip(ids, embeddings, metadatas, documents):
            self.vectors.append(np.array(emb).flatten())
            self.metadata.append(meta)
            self.documents.append(doc)

    def query(self, query_embedding, n_results, where=None):
        query_vec = np.array(query_embedding).flatten()
        stack = np.stack(self.vectors)
        # 余弦相似度
        sims = np.dot(stack, query_vec) / (np.linalg.norm(stack, axis=1) * np.linalg.norm(query_vec) + 1e-8)

        # 过滤
        kept = []
        for idx in range(len(self.vectors)):
            if where and not self._match_filter(self.metadata[idx], where):
                continue
            kept.append((idx, sims[idx]))

        kept.sort(key=lambda x: x[1], reverse=True)
        top = kept[:n_results]

        ids_out = [str(i) for i, _ in top]
        docs_out = [self.documents[i] for i, _ in top]
        metas_out = [self.metadata[i] for i, _ in top]
        dists_out = [float(s) for _, s in top]

        return {"ids": [ids_out], "documents": [docs_out], "metadatas": [metas_out], "distances": [dists_out]}

    def count(self):
        return len(self.vectors)

    @staticmethod
    def _match_filter(meta: dict, where: dict) -> bool:
        """简单过滤逻辑，支持 $eq, $lte, $gte, $in"""
        for key, cond in where.items():
            val = meta.get(key)
            if isinstance(cond, dict):
                if "$eq" in cond and val != cond["$eq"]:
                    return False
                if "$lte" in cond and (val is None or val > cond["$lte"]):
                    return False
                if "$gte" in cond and (val is None or val < cond["$gte"]):
                    return False
                if "$in" in cond and val not in cond["$in"]:
                    return False
            else:
                if val != cond:
                    return False
        return True


# -------------------- 主流程 --------------------

def build_index(
    data_path: str = None,
    collection_name: str = "exam_questions",
    persist_dir: str = None,
    force_rebuild: bool = False
):
    """
    构建向量索引

    参数：
        data_path: training_data.json 路径，默认 data/training_data.json
        collection_name: ChromaDB 集合名称
        persist_dir: ChromaDB 持久化目录，默认 data/chroma_db
        force_rebuild: 是否强制重建（删除旧集合）
    """
    global _EMBED_METHOD

    if data_path is None:
        data_path = os.path.join(ROOT, "data", "training_data.json")
    if persist_dir is None:
        persist_dir = os.path.join(ROOT, "data", "chroma_db")

    # 加载数据（支持 JSON 数组和 JSONL 两种格式）
    print(f"[rag_indexer] 读取数据: {data_path}")
    with open(data_path, "r", encoding="utf-8") as f:
        content = f.read()
    if content.strip().startswith("["):
        records = json.loads(content)
    else:
        # JSONL 格式：每行一条 JSON
        records = []
        for line in content.split("\n"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    print(f"[rag_indexer] 加载 {len(records)} 条题目")

    # 构建文档文本：question_text + knowledge_tags
    documents = []
    for r in records:
        tags = " ".join(r.get("knowledge_tags", []))
        doc = f"{r['question_text']} [{tags}]"
        documents.append(doc)

    # 初始化嵌入模型
    if _init_sentence_transformer():
        _EMBED_METHOD = "sentence_transformer"
        print(f"[rag_indexer] 嵌入方案: SentenceTransformer")
    else:
        _EMBED_METHOD = "tfidf"
        _init_tfidf(documents)
        print(f"[rag_indexer] 嵌入方案: TF-IDF（回退）")

    dim = _get_embedding_dim()
    print(f"[rag_indexer] 向量维度: {dim}")

    # 生成向量
    print("[rag_indexer] 正在生成向量...")
    embeddings = embed_texts(documents)

    # 元数据（ChromaDB 要求元数据值为 str/int/float/bool）
    metadatas = []
    for r in records:
        metadatas.append({
            "subject": r["subject"],
            "grade": r["grade"],
            "region": r["region"],
            "difficulty": int(r["difficulty"]),
            "knowledge_tags": ", ".join(r["knowledge_tags"]),
            "answer": r.get("answer", ""),
            "analysis": r.get("analysis", ""),
            "question_text": r.get("question_text", ""),
        })

    ids = [str(r["id"]) for r in records]

    # 存入向量数据库
    chroma_client, chroma_collection = _init_chromadb(collection_name, persist_dir)

    if chroma_collection is not None:
        if force_rebuild and chroma_collection.count() > 0:
            chroma_client.delete_collection(collection_name)
            chroma_collection = chroma_client.create_collection(collection_name)
            print("[rag_indexer] 已删除旧集合，重新创建")

        if chroma_collection.count() > 0:
            print(f"[rag_indexer] ChromaDB 已有 {chroma_collection.count()} 条数据，跳过索引（使用 force_rebuild=True 强制重建）")
            return

        # 批量写入（分批避免过大请求）
        batch_size = 100
        for start in range(0, len(ids), batch_size):
            end = min(start + batch_size, len(ids))
            chroma_collection.add(
                ids=ids[start:end],
                embeddings=embeddings[start:end].tolist(),
                metadatas=metadatas[start:end],
                documents=documents[start:end],
            )
        print(f"[rag_indexer] 已写入 ChromaDB: {chroma_collection.count()} 条")

    else:
        # 回退到内存存储，序列化保存
        print("[rag_indexer] ChromaDB 不可用，使用内存向量存储")
        store = InMemoryVectorStore()
        store.add(ids, embeddings, metadatas, documents)

        mem_path = os.path.join(ROOT, "data", "vector_store.pkl")
        with open(mem_path, "wb") as f:
            pickle.dump({
                "store": store,
                "embed_method": _EMBED_METHOD,
                "embed_model": _EMBED_MODEL if _EMBED_METHOD == "tfidf" else None,
                "dim": dim,
            }, f)
        print(f"[rag_indexer] 已保存内存向量存储: {mem_path} ({store.count()} 条)")


if __name__ == "__main__":
    build_index(force_rebuild=True)
