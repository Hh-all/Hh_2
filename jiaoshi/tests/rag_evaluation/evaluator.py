# -*- coding: utf-8 -*-
"""
RAG 评估器 (RAGEvaluator)
=========================
基于 Ragas 框架的检索与生成双维度评估。

评估指标：
  检索维度:
    - context_recall:    上下文召回率（检索是否全面）
    - context_precision: 上下文精确率（检索是否精准）

  生成维度:
    - faithfulness:       忠实度（答案是否忠实于检索上下文）
    - answer_relevancy:   答案相关性（答案与问题的相关程度）

两种模式：
  1. Ragas 模式（安装了 ragas + 配置了 LLM）—— 使用 LLM-as-a-judge 精确评估
  2. 启发式回退模式（无 ragas / 无 LLM）—— 基于文本相似度的近似评估

用法:
    evaluator = RAGEvaluator()
    # 检索评估
    retrieval_scores = evaluator.evaluate_retrieval(
        query="一元二次方程求解",
        retrieved_docs=["一元二次方程求根公式...", "二次函数图像..."],
        ground_truth_docs=["一元二次方程的定义...", "一元二次方程求根公式..."]
    )
    # 生成评估
    gen_scores = evaluator.evaluate_generation(
        question="如何求解一元二次方程？",
        answer="使用求根公式 x=(-b±√(b²-4ac))/2a",
        contexts=["一元二次方程求根公式为 x=(-b±√(b²-4ac))/2a"]
    )
"""

import os
import sys
import re
import math
import hashlib
import logging
from pathlib import Path
from typing import Optional
from collections import Counter

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("rag_evaluation.evaluator")

# ---------------------------------------------------------------------------
# Ragas 可用性检测
# ---------------------------------------------------------------------------

_RAGAS_AVAILABLE = False
_RAGAS_LLM_AVAILABLE = False

try:
    from ragas import SingleTurnSample, EvaluationDataset, evaluate as ragas_evaluate
    from ragas.metrics import Faithfulness, AnswerRelevancy
    _RAGAS_AVAILABLE = True
    logger.info("Ragas 框架可用")
except ImportError:
    logger.warning("Ragas 未安装。使用启发式回退评估模式。")


def _check_llm_available() -> bool:
    """检测是否有可用的评估 LLM"""
    global _RAGAS_LLM_AVAILABLE
    if _RAGAS_LLM_AVAILABLE:
        return True
    # 检查 OpenAI / Anthropic API Key
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        _RAGAS_LLM_AVAILABLE = True
        return True
    # 检查 .env 文件
    env_path = ROOT / "backend" / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "OPENAI_API_KEY" in content or "ANTHROPIC_API_KEY" in content:
            _RAGAS_LLM_AVAILABLE = True
            return True
    return False


# ---------------------------------------------------------------------------
# 启发式评估引擎（无 Ragas 时的回退方案）
# ---------------------------------------------------------------------------

class HeuristicEvaluator:
    """基于文本相似度的启发式评估器，无需外部 LLM"""

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """中文文本分词（简单 bigram 分词）"""
        text = re.sub(r"[^一-龥a-zA-Z0-9]", "", str(text))
        tokens = set()
        for i in range(len(text) - 1):
            tokens.add(text[i:i+2])
        tokens.update(text)  # 单字也作为特征
        return tokens

    @staticmethod
    def _jaccard(set1: set, set2: set) -> float:
        """Jaccard 相似度"""
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)

    @staticmethod
    def _overlap_coverage(subset: set, superset: set) -> float:
        """subset 中有多少比例被 superset 覆盖"""
        if not subset:
            return 1.0
        return len(subset & superset) / len(subset)

    def context_recall(self, ground_truth_docs: list[str], retrieved_docs: list[str]) -> float:
        """
        上下文召回率：ground_truth 中的关键信息在 retrieved 中被覆盖了多少。
        启发式：将 ground_truth 的所有 token 作为目标，计算 retrieved 的覆盖率。
        """
        if not ground_truth_docs:
            return 1.0
        gt_tokens = set()
        for doc in ground_truth_docs:
            gt_tokens.update(self._tokenize(doc))

        retrieved_tokens = set()
        for doc in retrieved_docs:
            retrieved_tokens.update(self._tokenize(doc))

        return self._overlap_coverage(gt_tokens, retrieved_tokens)

    def context_precision(self, ground_truth_docs: list[str], retrieved_docs: list[str]) -> float:
        """
        上下文精确率：retrieved 中有多少比例与 ground_truth 相关。
        启发式：每条 retrieved doc 与 ground_truth 的 Jaccard 相似度加权平均。
        """
        if not retrieved_docs:
            return 0.0
        gt_text = " ".join(str(d) for d in ground_truth_docs)
        gt_tokens = self._tokenize(gt_text)

        scores = []
        for doc in retrieved_docs:
            doc_tokens = self._tokenize(str(doc))
            if doc_tokens:
                scores.append(self._jaccard(doc_tokens, gt_tokens))

        return sum(scores) / len(scores) if scores else 0.0

    def faithfulness(self, answer: str, contexts: list[str]) -> float:
        """
        忠实度：答案中的声明有多少被上下文支持。
        启发式：将答案拆分为句子，检查每个句子与上下文的 Jaccard 相似度。
        """
        if not contexts:
            return 0.0
        ctx_text = " ".join(str(c) for c in contexts)
        ctx_tokens = self._tokenize(ctx_text)

        # 将答案拆分为句子
        sentences = re.split(r"[。！？\.!\?]+", str(answer))
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 2]

        if not sentences:
            # 答案太短，直接计算整体相似度
            ans_tokens = self._tokenize(str(answer))
            return self._overlap_coverage(ans_tokens, ctx_tokens)

        supported = 0
        for sent in sentences:
            sent_tokens = self._tokenize(sent)
            ratio = self._overlap_coverage(sent_tokens, ctx_tokens)
            if ratio >= 0.3:
                supported += 1

        return supported / len(sentences)

    def answer_relevancy(self, question: str, answer: str) -> float:
        """
        答案相关性：答案与问题的关联程度。
        启发式：答案+问题的 token 重叠度，并惩罚过短/过长的答案。
        """
        q_tokens = self._tokenize(str(question))
        a_tokens = self._tokenize(str(answer))

        if not a_tokens:
            return 0.0

        # 核心相关性 = Jaccard 相似度
        relevance = self._jaccard(q_tokens, a_tokens)

        # 惩罚过短的答案（< 10 个 token 但问题有 > 5 个 token）
        if len(a_tokens) < 10 and len(q_tokens) > 5:
            relevance *= 0.7

        # 惩罚过长的答案（可能是冗余信息）
        if len(a_tokens) > len(q_tokens) * 10:
            relevance *= 0.8

        return min(relevance, 1.0)


# ---------------------------------------------------------------------------
# 主评估器
# ---------------------------------------------------------------------------

class RAGEvaluator:
    """
    RAG 检索与生成质量评估器。

    用法:
        evaluator = RAGEvaluator(use_ragas=True)
        retrieval_scores = evaluator.evaluate_retrieval(query, retrieved_docs, ground_truth_docs)
        generation_scores = evaluator.evaluate_generation(question, answer, contexts)
        report = evaluator.evaluate_full(query, answer, retrieved_docs, ground_truth_docs, contexts)
    """

    def __init__(self, use_ragas: bool = True):
        """
        参数:
            use_ragas: 是否尝试使用 Ragas 框架（需安装 ragas + 配置 LLM）
                       设为 False 则强制使用启发式评估
        """
        self.use_ragas = use_ragas and _RAGAS_AVAILABLE and _check_llm_available()
        self._heuristic = HeuristicEvaluator()
        self._ragas_llm = None
        self._ragas_embeddings = None

        if self.use_ragas:
            self._init_ragas()

    def _init_ragas(self):
        """初始化 Ragas 评估 LLM"""
        try:
            # 尝试 OpenAI
            if os.environ.get("OPENAI_API_KEY"):
                from ragas.llms import LangchainLLMWrapper
                from langchain_openai import ChatOpenAI
                self._ragas_llm = LangchainLLMWrapper(ChatOpenAI(
                    model=os.environ.get("RAGAS_LLM_MODEL", "gpt-4o-mini"),
                    temperature=0,
                ))
                logger.info("Ragas LLM: OpenAI")
                return

            # 尝试 Anthropic
            if os.environ.get("ANTHROPIC_API_KEY"):
                from ragas.llms import LangchainLLMWrapper
                from langchain_anthropic import ChatAnthropic
                self._ragas_llm = LangchainLLMWrapper(ChatAnthropic(
                    model=os.environ.get("RAGAS_LLM_MODEL", "claude-haiku-4-5-20251001"),
                    temperature=0,
                ))
                logger.info("Ragas LLM: Anthropic")
                return
        except ImportError as e:
            logger.warning(f"Ragas LLM 初始化失败 ({e})，回退到启发式评估")
            self.use_ragas = False
        except Exception as e:
            logger.warning(f"Ragas LLM 初始化失败 ({e})，回退到启发式评估")
            self.use_ragas = False

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def evaluate_retrieval(
        self,
        query: str,
        retrieved_docs: list[str],
        ground_truth_docs: list[str],
    ) -> dict:
        """
        评估检索质量。

        参数:
            query:             用户查询文本
            retrieved_docs:    检索返回的文档列表（contexts）
            ground_truth_docs: 预期应返回的文档列表（ground truth）

        返回:
            {
                "context_recall": 0.0-1.0,
                "context_precision": 0.0-1.0,
                "mode": "ragas" | "heuristic"
            }
        """
        if self.use_ragas:
            try:
                return self._evaluate_retrieval_ragas(query, retrieved_docs, ground_truth_docs)
            except Exception as e:
                logger.warning(f"Ragas 检索评估失败 ({e})，回退到启发式评估")

        # 启发式回退
        recall = self._heuristic.context_recall(ground_truth_docs, retrieved_docs)
        precision = self._heuristic.context_precision(ground_truth_docs, retrieved_docs)
        return {
            "context_recall": round(recall, 4),
            "context_precision": round(precision, 4),
            "mode": "heuristic",
        }

    def evaluate_generation(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> dict:
        """
        评估生成质量。

        参数:
            question: 用户问题
            answer:   模型生成的答案
            contexts: 检索返回的上下文列表

        返回:
            {
                "faithfulness": 0.0-1.0,
                "answer_relevancy": 0.0-1.0,
                "mode": "ragas" | "heuristic"
            }
        """
        if self.use_ragas:
            try:
                return self._evaluate_generation_ragas(question, answer, contexts)
            except Exception as e:
                logger.warning(f"Ragas 生成评估失败 ({e})，回退到启发式评估")

        faithfulness = self._heuristic.faithfulness(answer, contexts)
        relevancy = self._heuristic.answer_relevancy(question, answer)
        return {
            "faithfulness": round(faithfulness, 4),
            "answer_relevancy": round(relevancy, 4),
            "mode": "heuristic",
        }

    def evaluate_full(
        self,
        query: str,
        answer: str,
        retrieved_docs: list[str],
        ground_truth_docs: list[str],
        contexts: list[str] = None,
    ) -> dict:
        """
        完整评估：同时评估检索和生成。

        返回:
            {
                "retrieval": {...},
                "generation": {...},
                "composite_score": 0.0-1.0
            }
        """
        if contexts is None:
            contexts = retrieved_docs

        retrieval = self.evaluate_retrieval(query, retrieved_docs, ground_truth_docs)
        generation = self.evaluate_generation(query, answer, contexts)

        # 综合得分：检索 40% + 生成 60%
        r_score = (retrieval.get("context_recall", 0) + retrieval.get("context_precision", 0)) / 2
        g_score = (generation.get("faithfulness", 0) + generation.get("answer_relevancy", 0)) / 2
        composite = round(r_score * 0.4 + g_score * 0.6, 4)

        return {
            "retrieval": retrieval,
            "generation": generation,
            "composite_score": composite,
        }

    def evaluate_batch(self, samples: list[dict]) -> dict:
        """
        批量评估，返回聚合统计。

        参数:
            samples: [
                {
                    "query": "...",
                    "answer": "...",
                    "retrieved_docs": ["...", "..."],
                    "ground_truth_docs": ["...", "..."],
                    "contexts": ["...", "..."]  # 可选
                },
                ...
            ]

        返回:
            {
                "samples": N,
                "aggregate": { metric_name: avg_score, ... },
                "pass_rate": { metric_name: (count >= threshold) / N, ... },
                "details": [...]
            }
        """
        all_metrics = []
        details = []

        for i, sample in enumerate(samples):
            result = self.evaluate_full(
                query=sample["query"],
                answer=sample.get("answer", ""),
                retrieved_docs=sample.get("retrieved_docs", []),
                ground_truth_docs=sample.get("ground_truth_docs", []),
                contexts=sample.get("contexts"),
            )
            result["index"] = i
            result["query"] = sample["query"][:60]
            details.append(result)

            all_metrics.append({
                "context_recall": result["retrieval"]["context_recall"],
                "context_precision": result["retrieval"]["context_precision"],
                "faithfulness": result["generation"]["faithfulness"],
                "answer_relevancy": result["generation"]["answer_relevancy"],
                "composite_score": result["composite_score"],
            })

        # 聚合统计
        n = len(all_metrics)
        aggregate = {}
        for metric in all_metrics[0].keys():
            values = [m[metric] for m in all_metrics]
            aggregate[metric] = round(sum(values) / n, 4)

        # 达标率（阈值 0.6）
        pass_rate = {}
        thresholds = {"context_recall": 0.6, "context_precision": 0.6,
                       "faithfulness": 0.6, "answer_relevancy": 0.5,
                       "composite_score": 0.6}
        for metric, threshold in thresholds.items():
            values = [m[metric] for m in all_metrics]
            pass_rate[metric] = round(sum(1 for v in values if v >= threshold) / n, 4)

        return {
            "samples": n,
            "aggregate": aggregate,
            "pass_rate": pass_rate,
            "mode": details[0]["retrieval"]["mode"] if details else "unknown",
            "details": details,
        }

    # ------------------------------------------------------------------
    # Ragas 实现
    # ------------------------------------------------------------------

    def _evaluate_retrieval_ragas(
        self, query: str, retrieved_docs: list[str], ground_truth_docs: list[str]
    ) -> dict:
        """使用 Ragas 评估检索质量"""
        # Context Precision
        from ragas.metrics import ContextPrecision
        cp_metric = ContextPrecision(llm=self._ragas_llm)
        cp_sample = SingleTurnSample(
            user_input=query,
            retrieved_contexts=retrieved_docs,
            reference="\n".join(ground_truth_docs),
        )
        cp_score = cp_metric.single_turn_score(cp_sample)

        # LLMContextRecall
        from ragas.metrics import LLMContextRecall
        cr_metric = LLMContextRecall(llm=self._ragas_llm)
        cr_sample = SingleTurnSample(
            user_input=query,
            retrieved_contexts=retrieved_docs,
            reference="\n".join(ground_truth_docs),
        )
        cr_score = cr_metric.single_turn_score(cr_sample)

        return {
            "context_recall": round(float(cr_score), 4),
            "context_precision": round(float(cp_score), 4),
            "mode": "ragas",
        }

    def _evaluate_generation_ragas(
        self, question: str, answer: str, contexts: list[str]
    ) -> dict:
        """使用 Ragas 评估生成质量"""
        from ragas.metrics import Faithfulness, AnswerRelevancy

        faith_metric = Faithfulness(llm=self._ragas_llm)
        faith_sample = SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
        )
        faith_score = faith_metric.single_turn_score(faith_sample)

        ar_metric = AnswerRelevancy(llm=self._ragas_llm)
        ar_sample = SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
        )
        ar_score = ar_metric.single_turn_score(ar_sample)

        return {
            "faithfulness": round(float(faith_score), 4),
            "answer_relevancy": round(float(ar_score), 4),
            "mode": "ragas",
        }


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def evaluate_retrieval(query: str, retrieved_docs: list[str], ground_truth_docs: list[str]) -> dict:
    """便捷函数：评估检索质量"""
    evaluator = RAGEvaluator()
    return evaluator.evaluate_retrieval(query, retrieved_docs, ground_truth_docs)


def evaluate_generation(question: str, answer: str, contexts: list[str]) -> dict:
    """便捷函数：评估生成质量"""
    evaluator = RAGEvaluator()
    return evaluator.evaluate_generation(question, answer, contexts)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    evaluator = RAGEvaluator(use_ragas=False)

    print("=" * 60)
    print("RAG 评估器测试")
    print(f"模式: {'Ragas' if evaluator.use_ragas else '启发式回退'}")
    print("=" * 60)

    # 测试检索评估
    print("\n--- 检索评估 ---")
    r_score = evaluator.evaluate_retrieval(
        query="一元二次方程求根公式",
        retrieved_docs=[
            "一元二次方程ax²+bx+c=0的求根公式为x=(-b±√(b²-4ac))/2a",
            "二次函数y=ax²+bx+c的图像是一条抛物线",
        ],
        ground_truth_docs=[
            "一元二次方程ax²+bx+c=0(a≠0)的求根公式：x=(-b±√(b²-4ac))/(2a)",
            "判别式Δ=b²-4ac决定了方程的根的情况",
        ],
    )
    print(f"  context_recall: {r_score['context_recall']:.4f}")
    print(f"  context_precision: {r_score['context_precision']:.4f}")

    # 测试生成评估
    print("\n--- 生成评估 ---")
    g_score = evaluator.evaluate_generation(
        question="如何求解一元二次方程？",
        answer="使用求根公式 x = (-b ± √(b²-4ac)) / (2a)，先计算判别式 Δ = b²-4ac，若 Δ>0 有两个不等实数根，Δ=0 有一个实数根，Δ<0 无实数根。",
        contexts=[
            "一元二次方程ax²+bx+c=0(a≠0)的求根公式：x=(-b±√(b²-4ac))/(2a)",
            "判别式Δ=b²-4ac决定了方程根的情况：Δ>0两个不等实根，Δ=0两个相等实根，Δ<0无实根",
        ],
    )
    print(f"  faithfulness: {g_score['faithfulness']:.4f}")
    print(f"  answer_relevancy: {g_score['answer_relevancy']:.4f}")

    # 综合测试
    print("\n--- 综合评估 ---")
    full = evaluator.evaluate_full(
        query="一元二次方程求根公式",
        answer="x=(-b±√(b²-4ac))/2a",
        retrieved_docs=["一元二次方程ax²+bx+c=0的求根公式为x=(-b±√(b²-4ac))/2a"],
        ground_truth_docs=["求根公式：x=(-b±√(b²-4ac))/(2a)", "判别式Δ=b²-4ac"],
    )
    print(f"  composite_score: {full['composite_score']:.4f}")
