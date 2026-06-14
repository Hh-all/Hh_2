# -*- coding: utf-8 -*-
"""
RAG 检索模块
基于向量相似度的题库检索，支持元数据过滤和地域降级策略
"""

import os
import sys
import time
import pickle
import logging
import numpy as np
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logger = logging.getLogger("rag_searcher")

# LangSmith 追踪（可选）
try:
    from backend.tracing.tracer import trace_rag_retrieval
    _TRACING_ENABLED = True
except ImportError:
    _TRACING_ENABLED = False
    def trace_rag_retrieval(func=None, **kwargs):
        if func is None:
            return lambda f: f
        return func

# ---------- 全局状态 ----------
_vector_store = None      # ChromaDB collection 或 InMemoryVectorStore
_embed_method = None      # "sentence_transformer" | "tfidf"
_embed_model = None       # 嵌入模型实例
_initialized = False


def init_searcher():
    """
    初始化检索器，自动检测可用的向量存储和嵌入模型。
    如果尚未构建索引，自动调用 rag_indexer.build_index()。
    """
    global _vector_store, _embed_method, _embed_model, _initialized
    if _initialized:
        return

    # 1) 尝试连 ChromaDB
    chroma_ok = False
    try:
        import chromadb
        persist_dir = os.path.join(ROOT, "data", "chroma_db")
        client = chromadb.PersistentClient(path=persist_dir)
        try:
            _vector_store = client.get_collection("exam_questions")
            count = _vector_store.count()
            print(f"[rag_searcher] ChromaDB 就绪，共 {count} 条")
            chroma_ok = True
        except Exception:
            print("[rag_searcher] ChromaDB 集合不存在，尝试构建索引...")
    except ImportError:
        print("[rag_searcher] chromadb 未安装")

    # 2) 回退到内存存储
    if not chroma_ok:
        mem_path = os.path.join(ROOT, "data", "vector_store.pkl")
        if os.path.exists(mem_path):
            print(f"[rag_searcher] 加载内存向量存储: {mem_path}")
            with open(mem_path, "rb") as f:
                data = pickle.load(f)
            _vector_store = data["store"]
            _embed_method = data.get("embed_method", "tfidf")
            _embed_model = data.get("embed_model")
            print(f"[rag_searcher] 内存存储就绪，共 {_vector_store.count()} 条")
        else:
            # 需要构建索引
            print("[rag_searcher] 未找到向量存储，正在构建索引...")
            from rag_indexer import build_index
            build_index()
            # 递归初始化
            return init_searcher()

    # 3) 加载嵌入模型
    if not chroma_ok and _embed_method is not None:
        # 内存模式已携带模型
        pass
    else:
        # 检查是否有本地缓存的 SentenceTransformer 模型
        _st_ok = False
        try:
            from sentence_transformers import SentenceTransformer
            import os as _os
            model_name = "paraphrase-multilingual-MiniLM-L12-v2"
            # 检查模型是否已缓存
            cache_dir = _os.path.join(_os.path.expanduser("~"), ".cache", "torch", "sentence_transformers")
            if _os.path.exists(cache_dir):
                print(f"[rag_searcher] 加载嵌入模型: {model_name}")
                _embed_model = SentenceTransformer(model_name)
                _embed_method = "sentence_transformer"
                _st_ok = True
        except Exception:
            pass
        if not _st_ok:
            # 直接使用 TF-IDF（跳过网络下载）
            print("[rag_searcher] 使用 TF-IDF 嵌入（SemanticTransformer 未缓存）")
            _embed_method = "tfidf"
            _embed_model = _load_or_build_tfidf()

    _initialized = True
    print("[rag_searcher] 初始化完成")


def _load_or_build_tfidf():
    """加载已有的 TF-IDF 模型或重新构建"""
    # 如果 vector_store 是 InMemoryVectorStore，其 embed_model 是 TF-IDF
    from rag_indexer import InMemoryVectorStore
    if isinstance(_vector_store, InMemoryVectorStore) and _embed_model is not None:
        return _embed_model

    # 重新从题库构建 TF-IDF（优先使用 questions.jsonl，回退到 training_data.json）
    from sklearn.feature_extraction.text import TfidfVectorizer
    import json
    data_path = os.path.join(ROOT, "data", "processed", "questions.jsonl")
    if not os.path.exists(data_path):
        data_path = os.path.join(ROOT, "data", "training_data.json")

    records = []
    with open(data_path, "r", encoding="utf-8") as f:
        content = f.read()
    if content.strip().startswith("["):
        records = json.loads(content)
    else:
        for line in content.split("\n"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    corpus = []
    for r in records:
        tags = " ".join(r.get("knowledge_tags", []))
        corpus.append(f"{r['question_text']} [{tags}]")
    return TfidfVectorizer(max_features=384).fit(corpus)


def _embed_query(query: str) -> np.ndarray:
    """对查询文本生成向量"""
    if _embed_method == "sentence_transformer":
        return _embed_model.encode([query], show_progress_bar=False, convert_to_numpy=True)[0]
    else:
        return _embed_model.transform([query]).toarray().astype(np.float32)[0]


def _build_where(filters: dict) -> Optional[dict]:
    """
    将用户友好的过滤条件转换为 ChromaDB where 语法。

    示例：
        {"subject": "数学"}                     → {"subject": "数学"}
        {"difficulty": {"$lte": 3}}             → {"difficulty": {"$lte": 3}}
        {"subject": "数学", "difficulty": {"$lte": 3}} → {"$and": [...]}
    """
    if not filters:
        return None

    chroma_cond = {}
    for key, cond in filters.items():
        if isinstance(cond, dict):
            chroma_cond[key] = {}
            for op, val in cond.items():
                chroma_cond[key][op] = val
        else:
            chroma_cond[key] = cond

    # ChromaDB 多个过滤条件需用 $and
    if len(chroma_cond) > 1:
        return {"$and": [{k: v} for k, v in chroma_cond.items()]}
    return chroma_cond


@trace_rag_retrieval
def search(
    query: str,
    filters: Optional[dict] = None,
    top_k: int = 5,
    region: str = "",
    enable_regional_filter: bool = False,
    regional_min_count: int = 0,
) -> list[dict]:
    """
    向量相似度检索（含地域过滤）

    参数：
        query:                  查询文本，如 "一元一次方程应用题"
        filters:                元数据过滤，如 {"subject": "数学", "difficulty": {"$lte": 3}}
        top_k:                  返回结果数，默认 5
        region:                 地域代码（beijing / shanghai / guangdong），空字符串表示不限地域
        enable_regional_filter: 是否启用地域过滤+降级策略（默认 False，向后兼容）
        regional_min_count:     地域过滤的最少题目数（不足时触发 fallback）

    返回：
        list[dict]，每条包含 id, question_text, answer, analysis, subject, grade,
                    region, difficulty, knowledge_tags, score（相似度分数）。
        当 enable_regional_filter=True 时，额外包含:
                    _region_match:   "exact" | "national" | "neighbor" | "other"
                    _region_fallback: bool
    """
    _start_time = time.time()
    if top_k <= 0:
        return []

    if not _initialized:
        init_searcher()

    query_vec = _embed_query(query)

    # 如果启用了地域过滤，可能需要检索更多结果以供后续筛选
    retrieval_k = top_k * 3 if (enable_regional_filter and region) else top_k

    where = _build_where(filters)

    # ChromaDB 查询
    try:
        import chromadb
        from rag_indexer import InMemoryVectorStore

        if isinstance(_vector_store, InMemoryVectorStore):
            result = _vector_store.query(query_vec, n_results=retrieval_k, where=where)
        else:
            result = _vector_store.query(
                query_embeddings=[query_vec.tolist()],
                n_results=retrieval_k,
                where=where,
            )
    except ImportError:
        result = _vector_store.query(query_vec, n_results=retrieval_k, where=where)

    # 统一输出格式
    items = []
    if not result["ids"] or not result["ids"][0]:
        return items

    for i in range(len(result["ids"][0])):
        meta = result["metadatas"][0][i] if result["metadatas"] else {}
        doc = result["documents"][0][i] if result["documents"] else ""
        distance = result["distances"][0][i] if result["distances"] else 0

        # 相似度分数：ChromaDB 返回距离，越小越好；内存返回余弦相似度，越大越好
        if isinstance(_vector_store, globals().get('InMemoryVectorStore', type(None))):
            score = float(distance)
        elif _embed_method == "sentence_transformer":
            score = 1.0 - float(distance)
        else:
            score = float(distance)

        items.append({
            "id": result["ids"][0][i],
            "question_text": meta.get("question_text", doc),
            "answer": meta.get("answer", ""),
            "analysis": meta.get("analysis", ""),
            "subject": meta.get("subject", ""),
            "grade": meta.get("grade", ""),
            "region": meta.get("region", ""),
            "difficulty": meta.get("difficulty", 0),
            "knowledge_tags": meta.get("knowledge_tags", ""),
            "score": round(score, 4),
        })

    # 地域过滤（后处理）
    if enable_regional_filter and region:
        from backend.knowledge.regional_filter import RegionalFilter
        rf = RegionalFilter()
        min_c = regional_min_count if regional_min_count > 0 else top_k
        items = rf.filter_by_region(items, region=region, min_count=min_c, max_count=top_k)

        # 后处理完成后只保留 top_k
        items = items[:top_k]
    else:
        items = items[:top_k]

    _elapsed = (time.time() - _start_time) * 1000
    logger.debug(f"RAG检索完成: query='{query[:40]}...' → {len(items)}条 ({_elapsed:.0f}ms)")
    return items


# ---------- 命令行入口 ----------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    init_searcher()

    print("\n" + "=" * 60)
    print("RAG 检索测试")
    print("=" * 60)

    # 测试 1：无过滤检索
    query1 = "一元一次方程"
    print(f"\n[查询] {query1}")
    results = search(query1, top_k=3)
    for r in results:
        print(f"  #{r['id']} [{r['subject']}] region={r['region']} 相似度={r['score']:.4f} 难度={r['difficulty']}")
        print(f"    题目: {r['question_text'][:60]}...")

    # 测试 2：带过滤器
    query2 = "函数图像"
    filters2 = {"subject": "math", "difficulty": {"$lte": 3}}
    print(f"\n[查询] {query2} 过滤={filters2}")
    results = search(query2, filters=filters2, top_k=3)
    for r in results:
        print(f"  #{r['id']} [{r['subject']}] region={r['region']} 相似度={r['score']:.4f} 难度={r['difficulty']}")
        print(f"    题目: {r['question_text'][:60]}...")

    # 测试 3：地域过滤
    query3 = "方程应用"
    print(f"\n[查询] {query3} (地域过滤: region=beijing)")
    results = search(query3, top_k=5, region="beijing", enable_regional_filter=True, regional_min_count=3)
    for r in results:
        fb = f" [fallback:{r.get('_region_match','?')}]" if r.get('_region_fallback') else ""
        print(f"  #{r['id']} [{r['subject']}] region={r['region']}{fb} score={r['score']:.4f}")
        print(f"    题目: {r['question_text'][:60]}...")
