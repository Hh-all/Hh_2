# -*- coding: utf-8 -*-
"""
测试数据集构建器 (TestDatasetBuilder)
=====================================
从已有题库数据中构建 Golden Set（标准测试集），用于 RAG 系统评估。

每条测试数据包含：
  - query:            用户查询（基于知识点的自然语言问题）
  - retrieved_docs:   预期的检索结果（以题干为文档文本）
  - ground_truth_docs: 标准答案文档（含答案解析的知识文档）
  - answer:           预期答案
  - subject:          学科
  - grade:            年级
  - knowledge_tags:   知识点标签

构建策略：
  1. 从 data/processed/questions.jsonl 读取所有题目
  2. 按学科和难度分层抽样，保证覆盖度
  3. 为每道题生成自然语言查询（基于 knowledge_tags）
  4. 同一知识点的其他题目作为 ground_truth_docs

输出：
  tests/rag_evaluation/golden_set.json — 100 条标准测试数据
"""

import json
import os
import sys
import random
import hashlib
import logging
from pathlib import Path
from collections import defaultdict
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("rag_evaluation.test_dataset_builder")

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
QUESTIONS_PATH = ROOT / "data" / "processed" / "questions.jsonl"
GOLDEN_SET_PATH = ROOT / "tests" / "rag_evaluation" / "golden_set.json"
TARGET_SIZE = 100

# 分层抽样比例
STRATUM_WEIGHTS = {
    "math": 0.35,
    "chinese": 0.25,
    "english": 0.25,
    "physics": 0.05,
    "chemistry": 0.05,
    "biology": 0.05,
}

# 查询模板（将知识点转换为自然语言问题）
QUERY_TEMPLATES = {
    "math": [
        "请解释{tags}的概念，并给出例题。",
        "{tags}的常见题型有哪些？请举例说明。",
        "如何解决{tags}类的问题？",
        "{tags}在考试中通常以什么形式出现？",
        "请出一道{tags}的题目并给出答案。",
    ],
    "chinese": [
        "关于{tags}，有哪些常考的知识点？",
        "请分析{tags}类题目的解题思路。",
        "{tags}的考查重点是什么？",
        "请举一个{tags}的考题例子。",
    ],
    "english": [
        "What are the key points of {tags}?",
        "How to master {tags} in English learning?",
        "请解释英语中{tags}的用法。",
        "{tags}有哪些常见的考点？",
    ],
    "default": [
        "请解释{tags}的知识要点。",
        "{tags}的典型例题有哪些？",
        "如何理解和掌握{tags}？",
    ],
}


class TestDatasetBuilder:
    """
    测试数据集构建器。

    用法:
        builder = TestDatasetBuilder()
        dataset = builder.build(target_size=100)
        builder.save(dataset)
        # → 写入 tests/rag_evaluation/golden_set.json
    """

    def __init__(self, questions_path: Path = None, seed: int = 42):
        self.questions_path = questions_path or QUESTIONS_PATH
        self.rng = random.Random(seed)
        self._questions = []
        self._by_subject = defaultdict(list)
        self._by_tag = defaultdict(list)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def load_questions(self) -> list[dict]:
        """加载题库数据"""
        if self._questions:
            return self._questions

        if not self.questions_path.exists():
            logger.warning(f"题库文件不存在: {self.questions_path}")
            return self._generate_mock_questions()

        questions = []
        with open(self.questions_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        q = json.loads(line)
                        if q.get("question_text") and q.get("answer"):
                            questions.append(q)
                    except json.JSONDecodeError:
                        continue

        if len(questions) < TARGET_SIZE:
            logger.warning(f"题库仅有 {len(questions)} 题，补充模拟数据至 {TARGET_SIZE}")
            mock_questions = self._generate_mock_questions()
            questions.extend(mock_questions[:TARGET_SIZE - len(questions)])

        self._questions = questions
        self._build_indexes()
        logger.info(f"加载 {len(questions)} 道题目")
        return questions

    def _build_indexes(self):
        """构建学科和知识点索引"""
        for q in self._questions:
            subj = q.get("subject", "unknown")
            self._by_subject[subj].append(q)
            tags = q.get("knowledge_tags", [])
            for tag in tags:
                self._by_tag[tag].append(q)

    def build(self, target_size: int = TARGET_SIZE) -> list[dict]:
        """
        构建 Golden Set。

        参数:
            target_size: 目标样本数（默认 100）

        返回:
            [
                {
                    "id": "golden_001",
                    "query": "请解释一元二次方程的概念，并给出例题。",
                    "answer": "使用求根公式...",
                    "retrieved_docs": ["一元二次方程ax²+bx+c=0...", ...],
                    "ground_truth_docs": ["求根公式：x=(-b±√(b²-4ac))/(2a)", ...],
                    "subject": "math",
                    "grade": "grade_9",
                    "knowledge_tags": ["一元二次方程"],
                },
                ...
            ]
        """
        questions = self.load_questions()
        if not questions:
            logger.error("无可用题目")
            return []

        # ---- 分层抽样 ----
        stratified = self._stratified_sample(questions, target_size)

        # ---- 构建测试样本 ----
        dataset = []
        for i, q in enumerate(stratified):
            sample = self._build_sample(q, i)
            dataset.append(sample)

        # 去重（基于 query 哈希）
        seen_queries = set()
        unique_dataset = []
        for s in dataset:
            h = hashlib.md5(s["query"].encode("utf-8")).hexdigest()
            if h not in seen_queries:
                seen_queries.add(h)
                unique_dataset.append(s)

        # 补齐到目标数量
        while len(unique_dataset) < target_size:
            src = self.rng.choice(dataset)
            variant = self._build_sample(
                self.rng.choice(stratified), len(unique_dataset)
            )
            h = hashlib.md5(variant["query"].encode("utf-8")).hexdigest()
            if h not in seen_queries:
                seen_queries.add(h)
                unique_dataset.append(variant)

        logger.info(f"Golden Set: {len(unique_dataset)} 条 (目标 {target_size})")
        return unique_dataset[:target_size]

    def save(self, dataset: list[dict], output_path: Path = None):
        """保存 Golden Set 到文件"""
        if output_path is None:
            output_path = GOLDEN_SET_PATH
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "_meta": {
                    "generated_at": __import__('datetime').datetime.now().isoformat(),
                    "total_samples": len(dataset),
                    "description": "RAG 评估 Golden Set — 用于 RAG 检索和生成质量评估",
                },
                "samples": dataset,
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Golden Set 已保存: {output_path}")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _stratified_sample(self, questions: list[dict], target: int) -> list[dict]:
        """按学科分层抽样"""
        sampled = []
        remaining = target

        for subject, weight in STRATUM_WEIGHTS.items():
            pool = self._by_subject.get(subject, [])
            if not pool:
                continue
            n = max(1, int(target * weight))
            n = min(n, len(pool), remaining)
            sampled.extend(self.rng.sample(pool, n))
            remaining -= n

        # 剩余配额均匀分配给所有学科
        if remaining > 0 and questions:
            extra = self.rng.sample(questions, min(remaining, len(questions)))
            sampled.extend(extra)

        return sampled[:target]

    def _build_sample(self, q: dict, index: int) -> dict:
        """从单道题构建一条测试样本"""
        subject = q.get("subject", "math")
        tags = q.get("knowledge_tags", ["综合"])
        main_tag = tags[0] if tags else "综合"

        # 生成自然语言查询
        templates = QUERY_TEMPLATES.get(subject, QUERY_TEMPLATES["default"])
        query = self.rng.choice(templates).replace("{tags}", main_tag)

        # 题目本身作为答案
        answer = q.get("answer", "")
        question_text = q.get("question_text", "")
        analysis = q.get("analysis", "")

        # retrieved_docs: 用题目文本和解析构建模拟检索结果
        retrieved_docs = self._build_retrieved_docs(q)

        # ground_truth_docs: 同一知识点的其他题目作为 ground truth
        ground_truth_docs = self._build_ground_truth_docs(q)

        return {
            "id": f"golden_{index:04d}",
            "query": query,
            "answer": answer,
            "retrieved_docs": retrieved_docs,
            "ground_truth_docs": ground_truth_docs,
            "subject": subject,
            "grade": q.get("grade", "grade_7"),
            "knowledge_tags": tags,
            "question_type": q.get("question_type", "short_answer"),
            "difficulty": q.get("difficulty", 3),
            "source_question_id": q.get("id", ""),
        }

    def _build_retrieved_docs(self, q: dict) -> list[str]:
        """
        为题目构建模拟的检索结果文档。
        包含：题干的扩展版本 + 知识点相关的上下文。
        """
        docs = []
        question_text = q.get("question_text", "")
        analysis = q.get("analysis", "")
        tags = q.get("knowledge_tags", [])
        tag_str = "、".join(tags) if tags else "综合知识"

        # 文档1: 知识点概述
        docs.append(f"知识点概述：{tag_str}。这是{q.get('subject', '')}学科{q.get('grade', '')}的重要内容。")

        # 文档2: 题目本身（作为检索返回的核心文档）
        if question_text:
            docs.append(f"题目：{question_text}")

        # 文档3: 解析（如果有）
        if analysis:
            docs.append(f"解析：{analysis}")

        # 文档4: 同知识点补充
        related = self._by_tag.get(tags[0] if tags else "", [])
        if len(related) > 1:
            other = self.rng.choice([r for r in related if r.get("id") != q.get("id")])
            docs.append(f"相关例题：{other.get('question_text', '')}")

        return docs

    def _build_ground_truth_docs(self, q: dict) -> list[str]:
        """
        构建 ground truth 文档集。
        包含：标准答案解析 + 同一知识点相关题目。
        """
        docs = []
        analysis = q.get("analysis", "")
        answer = q.get("answer", "")
        tags = q.get("knowledge_tags", [])

        if analysis:
            docs.append(f"标准解析：{analysis}")
        if answer:
            docs.append(f"标准答案：{answer}")

        # 添加同一知识点的其他题目作为 ground truth
        tag = tags[0] if tags else ""
        related = self._by_tag.get(tag, [])
        candidates = [r for r in related if r.get("id") != q.get("id")]
        for r in self.rng.sample(candidates, min(3, len(candidates))):
            gt_doc = f"相关知识点题目：{r.get('question_text', '')}"
            if r.get("analysis"):
                gt_doc += f" 解析：{r['analysis']}"
            docs.append(gt_doc)

        return docs

    def _generate_mock_questions(self) -> list[dict]:
        """生成模拟题目（题库不足时的回退方案）"""
        mock = []
        subjects = ["math", "math", "math", "chinese", "chinese", "english", "english"]
        grades = ["grade_5", "grade_6", "grade_7", "grade_8", "grade_9", "grade_10", "grade_11"]
        knowledge_pools = {
            "math": [
                ("一元一次方程", "解方程：2x + 5 = 15", "x = 5", "移项：2x = 10，x = 5"),
                ("勾股定理", "直角三角形的两直角边分别为3和4，求斜边长", "5", "3² + 4² = 5²，c = 5"),
                ("二次函数", "y = x² - 4x + 3 的顶点坐标", "(2, -1)", "配方得 y = (x-2)² - 1"),
            ],
            "chinese": [
                ("古诗词鉴赏", "分析《静夜思》中'疑是地上霜'的意象", "表达了思乡之情", "将月光比喻为霜，渲染冷清氛围"),
                ("文言文阅读", "解释'之'的用法", "结构助词，相当于'的'", "'之'连接定语和中心语"),
            ],
            "english": [
                ("一般过去时", "She ____ (go) to school yesterday.", "went", "go的过去式是went"),
                ("定语从句", "The boy ____ is reading is Tom.", "who", "先行词是人，关系代词用who"),
            ],
        }
        for i in range(TARGET_SIZE):
            subj = self.rng.choice(subjects)
            pool = knowledge_pools.get(subj, knowledge_pools["math"])
            tag, question, answer, analysis = self.rng.choice(pool)
            g = self.rng.choice(grades)
            mock.append({
                "id": f"mock_{i:04d}",
                "subject": subj,
                "grade": g,
                "knowledge_tags": [tag],
                "difficulty": self.rng.randint(1, 5),
                "question_type": self.rng.choice(["choice", "short_answer", "calculation"]),
                "question_text": question,
                "answer": answer,
                "analysis": analysis,
                "source": "mock",
            })
        return mock


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def build_golden_set(target_size: int = TARGET_SIZE, output_path: Path = None) -> list[dict]:
    """便捷函数：构建并保存 Golden Set"""
    builder = TestDatasetBuilder()
    dataset = builder.build(target_size)
    builder.save(dataset, output_path)
    return dataset


def load_golden_set(path: Path = None) -> list[dict]:
    """加载 Golden Set"""
    if path is None:
        path = GOLDEN_SET_PATH
    if not path.exists():
        logger.error(f"Golden Set 不存在: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("samples", [])


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    dataset = build_golden_set(target_size=TARGET_SIZE)

    print(f"\nGolden Set 统计:")
    print(f"  总样本数: {len(dataset)}")
    subjects = defaultdict(int)
    grades = defaultdict(int)
    for s in dataset:
        subjects[s.get("subject", "?")] += 1
        grades[s.get("grade", "?")] += 1
    print(f"  学科分布: {dict(subjects)}")
    print(f"  年级分布: {dict(sorted(grades.items()))}")

    # 预览前3条
    print(f"\n预览 (前3条):")
    for s in dataset[:3]:
        print(f"  [{s['id']}] {s['subject']}/{s['grade']}")
        print(f"    Query: {s['query'][:60]}...")
        print(f"    Answer: {str(s['answer'])[:60]}...")
        print(f"    Retrieved Docs: {len(s['retrieved_docs'])} 条")
        print(f"    Ground Truth Docs: {len(s['ground_truth_docs'])} 条")
