# -*- coding: utf-8 -*-
"""
智能试卷生成系统 - Flask API 服务
提供试卷生成、题目替换等 RESTful 接口
"""

import os
import sys
import json
import random
import copy
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from flask import Flask, request, jsonify
from flask_cors import CORS

# 初始化 RAG 检索器（只加载一次）
from rag_searcher import init_searcher, search as rag_search
init_searcher()

# 导入生成模块
from generate_paper import _call_claude, _build_system_prompt, _build_user_prompt, _fallback_generate

# 导入缓存和异步任务模块
from cache import make_cache_key, knowledge_hash, get_cached, cache_result, cache_stats as get_cache_stats
from async_tasks import submit_task, get_task_status, get_task_result, task_stats as get_task_stats

app = Flask(__name__)
CORS(app)


# ---------- 辅助函数 ----------

def _random_question_from_rag(subject: str, knowledge_points: list, difficulty: int) -> dict:
    """从 RAG 检索结果中随机取一条，稍作改写"""
    query = " ".join(knowledge_points)
    results = rag_search(query, filters={"subject": subject, "difficulty": {"$lte": difficulty}}, top_k=10)

    if not results:
        results = rag_search(query, filters=None, top_k=10)
    if not results:
        return {
            "type": "short_answer",
            "text": "（暂无可用的参考题目）",
            "options": None,
            "answer": "",
            "analysis": ""
        }

    r = random.choice(results)
    text = r.get("question_text", "")
    # 轻度改写
    import re
    numbers = re.findall(r"\d+", text)
    for n in numbers:
        try:
            delta = random.choice([-2, -1, 1, 2])
            new_n = int(n) + delta
            if new_n > 0 and delta != 0:
                text = text.replace(n, str(new_n), 1)
                break
        except ValueError:
            pass

    q = {
        "type": r.get("type", "short_answer"),
        "text": text + "（改编）",
        "options": None,
        "answer": r.get("answer", "") + "（注：数值可能已调整）",
        "analysis": r.get("analysis", ""),
        "source": "rag_rewrite",
    }

    # 如果是选择题，构造干扰项
    if q["type"] == "choice":
        ans = r.get("answer", "").strip()
        q["options"] = [
            f"A. {ans}",
            f"B. （请根据题干计算）",
            f"C. （请根据题干计算）",
            f"D. （请根据题干计算）",
        ]

    return q


def _generate_single_question(subject: str, knowledge_points: list, difficulty: int) -> dict:
    """生成单道题目：优先 LLM，回退 RAG"""
    # 先尝试 LLM
    system = _build_system_prompt(subject, "grade_8", "", difficulty)
    user_parts = [
        f"请为知识点「{'、'.join(knowledge_points)}」生成 1 道难度为 {difficulty}/5 的新题目。",
        "随机选择题型（选择题/填空题/解答题）。",
        "输出格式如下 JSON：",
        '{"type": "choice|fill_blank|short_answer", "text": "题干", "options": ["A. ...", ...] 或 null, "answer": "答案", "analysis": "解析"}',
        "只输出 JSON，不要输出其他内容。",
    ]
    user = "\n".join(user_parts)

    result = _call_claude(system, user)
    if result and "type" in result:
        result.setdefault("options", None)
        result.setdefault("analysis", "")
        result["source"] = "claude"
        return result

    # 回退
    return _random_question_from_rag(subject, knowledge_points, difficulty)


def _generate_full_paper(params: dict) -> list[dict]:
    """生成整份试卷"""
    subject = params.get("subject", "math")
    kps = params.get("knowledge_points", [])
    difficulty = params.get("difficulty", 3)
    num = params.get("num_questions", 5)
    grade = params.get("grade", "grade_8")
    region = params.get("region", "北京")

    # 题型分布
    q_types = []
    type_pool = ["choice", "fill_blank", "short_answer"]
    for i in range(num):
        q_types.append(type_pool[i % len(type_pool)])

    # RAG 检索参考
    query = " ".join(kps)
    references = rag_search(query, filters={"subject": subject}, top_k=5)

    # LLM 生成
    system = _build_system_prompt(subject, grade, region, difficulty)
    user = _build_user_prompt(kps, num, difficulty, references, q_types)
    result = _call_claude(system, user)

    if result and "questions" in result:
        for q in result["questions"]:
            q.setdefault("options", None)
            q.setdefault("analysis", "")
            q["source"] = "claude"
        return result["questions"][:num]

    # 回退
    questions = []
    for i in range(num):
        target_type = q_types[i] if i < len(q_types) else "short_answer"
        r = _random_question_from_rag(subject, kps, difficulty)
        r["type"] = target_type
        questions.append(r)
    return questions


# ============================================================
# API 端点
# ============================================================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/generate_paper", methods=["POST"])
def generate_paper():
    """
    生成整份试卷

    Request body:
    {
        "subject": "math",
        "grade": "grade_8",
        "region": "北京",
        "knowledge_points": ["代数", "方程"],
        "difficulty": 3,
        "num_questions": 5
    }
    """
    data = request.get_json(silent=True) or {}

    print(f"[API] 生成试卷: subject={data.get('subject')}, kps={data.get('knowledge_points')}, diff={data.get('difficulty')}")

    try:
        questions = _generate_full_paper({
            "subject": data.get("subject", "math"),
            "grade": data.get("grade", "grade_8"),
            "region": data.get("region", "北京"),
            "knowledge_points": data.get("knowledge_points", []),
            "difficulty": int(data.get("difficulty", 3)),
            "num_questions": int(data.get("num_questions", 5)),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

    return jsonify({"questions": questions})


@app.route("/api/replace_question", methods=["POST"])
def replace_question():
    """
    替换单道题目

    Request body:
    {
        "subject": "math",
        "knowledge_points": ["代数", "方程"],
        "difficulty": 3,
        "question_index": 2,       # 当前题目在列表中的位置
        "current_type": "choice"   # 可选，保持原题型
    }
    """
    data = request.get_json(silent=True) or {}

    subject = data.get("subject", "math")
    kps = data.get("knowledge_points", [])
    difficulty = int(data.get("difficulty", 3))
    current_type = data.get("current_type", None)

    print(f"[API] 替换题目: subject={subject}, kps={kps}, diff={difficulty}, index={data.get('question_index')}")

    question = _generate_single_question(subject, kps, difficulty)

    # 如果请求指定了题型，覆盖
    if current_type and current_type in ("choice", "fill_blank", "short_answer"):
        # 如果生成的题型不匹配，从 RAG 找一题匹配的
        if question.get("type") != current_type:
            fallback = _random_question_from_rag(subject, kps, difficulty)
            fallback["type"] = current_type
            question = fallback
        else:
            question["type"] = current_type

    return jsonify({"question": question})


@app.route("/api/search_similar", methods=["POST"])
def search_similar():
    """
    搜索相似题目（用于展示参考）

    Request body:
    {
        "query": "一元一次方程",
        "subject": "math",
        "top_k": 3
    }
    """
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")
    subject = data.get("subject", "math")
    top_k = int(data.get("top_k", 3))

    # 缓存检查
    cache_key = make_cache_key("search", query, subject, top_k)
    cached = get_cached(cache_key)
    if cached is not None:
        return jsonify({"results": cached, "cached": True})

    filters = {"subject": subject} if subject else None

    results = rag_search(query, filters=filters, top_k=top_k)
    # 只返回轻量信息
    simplified = []
    for r in results:
        simplified.append({
            "id": r["id"],
            "text": r["question_text"],
            "subject": r["subject"],
            "difficulty": r["difficulty"],
            "score": r["score"],
        })

    # 缓存结果
    cache_result(cache_key, simplified)

    return jsonify({"results": simplified})


@app.route("/api/generate_paper_async", methods=["POST"])
def generate_paper_async():
    """
    生成整份试卷（异步模式）

    返回:
        {"task_id": "abc123def456"}
    前端用 GET /api/task_result/<task_id> 轮询结果
    """
    data = request.get_json(silent=True) or {}

    params = {
        "subject": data.get("subject", "math"),
        "grade": data.get("grade", "grade_8"),
        "region": data.get("region", ""),
        "knowledge_points": data.get("knowledge_points", []),
        "difficulty": int(data.get("difficulty", 3)),
        "num_questions": int(data.get("num_questions", 5)),
    }

    # 缓存检查
    ks_hash = knowledge_hash(params["knowledge_points"])
    cache_key = make_cache_key("paper", params["subject"], params["grade"],
                               params["region"], ks_hash, params["difficulty"], params["num_questions"])
    cached = get_cached(cache_key)
    if cached is not None:
        print(f"[API] async: 缓存命中 {cache_key[:16]}...")
        return jsonify({"task_id": "__cached__", "status": "done", "result": cached})

    print(f"[API] async: 提交任务 subject={params['subject']}, diff={params['difficulty']}")

    task_id = submit_task(_cached_generate_paper, params, cache_key)
    return jsonify({"task_id": task_id, "status": "pending"})


def _cached_generate_paper(params: dict, cache_key: str) -> dict:
    """带缓存的试卷生成（供异步任务调用）"""
    questions = _generate_full_paper(params)
    # 缓存结果
    result = {
        "questions": questions,
        "cached": True,
    }
    cache_result(cache_key, result)
    return result


@app.route("/api/task_result/<task_id>", methods=["GET"])
def task_result(task_id: str):
    """
    查询异步任务状态和结果

    返回:
        pending/running: {"status": "running", "elapsed": 2.5}
        done:            {"status": "done", "result": {...}, "elapsed": 5.2}
        error:           {"status": "error", "error": "..."}
    """
    # 处理缓存直接返回的情况
    if task_id == "__cached__":
        return jsonify({"status": "done", "elapsed": 0})

    status = get_task_status(task_id)
    if status is None:
        return jsonify({"status": "not_found", "error": "任务不存在或已过期"}), 404

    # 如果已完成，附上结果
    if status["status"] in ("done", "error"):
        result = get_task_result(task_id, timeout=0.1)
        if result:
            status["result"] = result.get("result")
            status["error"] = result.get("error")

    return jsonify(status)


@app.route("/api/system/stats", methods=["GET"])
def system_stats():
    """系统统计信息（缓存 + 任务队列）"""
    return jsonify({
        "cache": get_cache_stats(),
        "tasks": get_task_stats(),
        "server_time": time.time(),
    })


# ============================================================
# ============================================================
# Prometheus 指标（可选集成）
# ============================================================
try:
    from prometheus_flask_exporter import PrometheusMetrics
    metrics = PrometheusMetrics(app)
    metrics.info("exam_paper_generator", "智能试卷生成系统", version="1.0.0")
    print("[metrics] Prometheus 指标已启用: /metrics")
except ImportError:
    print("[metrics] prometheus_flask_exporter 未安装，跳过指标收集")
    @app.route("/metrics")
    def metrics_stub():
        return "prometheus_flask_exporter not installed", 501


# 请求计数中间件
import functools as _functools
_request_counts = {"generate_paper": 0, "search_similar": 0, "replace_question": 0,
                   "health": 0, "async_generate": 0, "task_status": 0}
_request_lock = __import__('threading').Lock()

@app.before_request
def _count_request():
    from flask import request as _req
    with _request_lock:
        for key in _request_counts:
            if key in (_req.path or ""):
                _request_counts[key] += 1
                break

@app.route("/api/stats")
def get_stats():
    """返回服务统计信息"""
    with _request_lock:
        return jsonify({
            "request_counts": dict(_request_counts),
            "cache": get_cache_stats(),
            "tasks": get_task_stats(),
        })


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", 5000))
    debug = os.getenv("SERVER_DEBUG", "true").lower() == "true"

    print(f"""
╔══════════════════════════════════════════════╗
║  智能试卷生成系统 API Server                 ║
║  地址: http://127.0.0.1:{port}               ║
║  文档: http://127.0.0.1:{port}/api/health    ║
╚══════════════════════════════════════════════╝
""")
    app.run(host="127.0.0.1", port=port, debug=debug)
