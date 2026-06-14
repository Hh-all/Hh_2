# -*- coding: utf-8 -*-
"""
LangSmith 全流程追踪模块
提供 RAG 检索、LLM 调用、试卷生成的完整可观测性
"""
from backend.tracing.tracer import (
    Tracer,
    trace_rag_retrieval,
    trace_llm_call,
    trace_paper_generation,
    is_tracing_available,
    get_tracer,
)
