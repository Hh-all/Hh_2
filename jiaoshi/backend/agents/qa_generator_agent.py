# -*- coding: utf-8 -*-
"""
题目生成 Agent (QAGeneratorAgent)
=================================
Harness 角色: QuestionSelector 的补充 — 当检索结果不足时，调用 LLM 生成新题。

职责边界：
  只管基于检索结果 + 请求参数调用 LLM 生成题目，不做检索、不排版、不输出最终试卷。

输入：
  - /tmp/retrieved_questions.json（由 QuestionRetrieverAgent 产出）
  - /tmp/paper_request.json（由 ParameterParserAgent 产出）

输出：
  /tmp/generated_questions.json

护栏：
  - 每道题必须包含 question_text, answer, analysis 三个字段，缺一不可
  - 每道题必须标注 source = "llm_generated"
  - 生成题目必须去重（与检索结果中的题目做模糊去重）
  - 单次最多生成 20 题
"""

import json
import os
import sys
import time
import hashlib
import difflib
import logging
import copy
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("agent.qa_generator")

# LangSmith 追踪（可选）
try:
    from backend.tracing.tracer import trace_llm_call, get_tracer
    _TRACING_ENABLED = True
except ImportError:
    _TRACING_ENABLED = False
    def trace_llm_call(func=None, **kwargs):
        if func is None:
            return lambda f: f
        return func
    def get_tracer():
        return None

# 护栏系统（可选）
try:
    from backend.guardrails.guardrail_checker import get_checker
    _GUARDRAIL_ENABLED = True
except ImportError:
    _GUARDRAIL_ENABLED = False
    def get_checker():
        return None

# ---------------------------------------------------------------------------
# 契约文件路径
# ---------------------------------------------------------------------------
RETRIEVED_QUESTIONS_PATH = ROOT / "tmp" / "retrieved_questions.json"
PAPER_REQUEST_PATH = ROOT / "tmp" / "paper_request.json"
GENERATED_QUESTIONS_PATH = ROOT / "tmp" / "generated_questions.json"

# ---------------------------------------------------------------------------
# 护栏常量
# ---------------------------------------------------------------------------
MAX_GENERATED = 20
REQUIRED_FIELDS = {"question_text", "answer", "analysis"}
DEDUP_SIMILARITY_THRESHOLD = 0.85


class QAGeneratorAgent:
    """
    题目生成 Agent。
    支持两种模式：
      - LLM 模式：调用外部 LLM（Claude API）生成
      - 回退模式：基于检索结果中的模板进行参数变换生成

    用法:
        agent = QAGeneratorAgent()
        result = agent.generate()
        # → 读取检索结果和请求参数，生成新题，写入 /tmp/generated_questions.json
    """

    def __init__(self, llm_client=None):
        """
        参数:
            llm_client: LLM 客户端（可选），如 anthropic.Client。
                        不提供则使用回退模式（基于检索结果变换）。
        """
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def generate(self, retrieved_path: Path = None, request_path: Path = None) -> dict:
        """
        基于检索结果生成新题目。

        返回:
            {
                "success": True/False,
                "generated_count": N,
                "skipped_duplicates": M,
                "mode": "llm" | "fallback_template",
                "output_file": "..."
            }
        """
        _start_time = time.time()
        if retrieved_path is None:
            retrieved_path = RETRIEVED_QUESTIONS_PATH
        if request_path is None:
            request_path = PAPER_REQUEST_PATH

        # ---- 读取输入 ----
        if not retrieved_path.exists():
            return self._error("retrieved_questions.json 不存在，请先运行 QuestionRetrieverAgent")
        if not request_path.exists():
            return self._error("paper_request.json 不存在，请先运行 ParameterParserAgent")

        with open(retrieved_path, "r", encoding="utf-8") as f:
            retrieved_obj = json.load(f)
        with open(request_path, "r", encoding="utf-8") as f:
            request_obj = json.load(f)

        retrieved_questions = retrieved_obj.get("questions", [])
        request = request_obj.get("request", request_obj)

        # ---- 计算需要生成的数量 ----
        target_count = request.get("question_count", 10)
        existing_count = len(retrieved_questions)
        needed = max(0, target_count - existing_count)
        needed = min(needed, MAX_GENERATED)

        if needed <= 0:
            logger.info(f"检索结果已足够 ({existing_count} >= {target_count})，无需生成")
            return self._write_output([], retrieved_questions, "skip")

        # ---- 生成题目 ----
        if self._llm_client:
            generated = self._generate_via_llm(request, retrieved_questions, needed)
            mode = "llm"
        else:
            generated = self._generate_via_fallback(request, retrieved_questions, needed)
            mode = "fallback_template"

        # ---- 验证生成结果 ----
        valid, invalid = self._validate_questions(generated)
        if invalid:
            logger.warning(f"{len(invalid)} 题验证不通过（缺少必填字段），已丢弃")

        # ---- 去重 ----
        existing_texts = [q.get("question_text", "") for q in retrieved_questions]
        deduped, duplicates = self._dedup_against_existing(valid, existing_texts)

        # ---- 输出 ----
        return self._write_output(deduped, retrieved_questions, mode, len(duplicates), len(invalid))

    # ------------------------------------------------------------------
    # 生成模式
    # ------------------------------------------------------------------

    @trace_llm_call
    def _generate_via_llm(self, request: dict, examples: list[dict], count: int) -> list[dict]:
        """
        调用 LLM 生成题目。
        需要 self._llm_client 已设置。
        """
        subject = request.get("subject", "math")
        grade = request.get("grade", "grade_7")
        region = request.get("region", "")
        knowledge_points = request.get("knowledge_points", [])
        difficulty = request.get("difficulty", 3)

        # 构建 prompt
        examples_text = ""
        for i, ex in enumerate(examples[:5]):
            qt = ex.get("question_text", "")
            ans = ex.get("answer", "")
            al = ex.get("analysis", "")
            examples_text += f"\n示例{i+1}: 题干='{qt[:120]}' 答案='{ans[:80]}' 解析='{al[:80]}'"

        system_prompt = (
            f"你是一位经验丰富的中国{self._subject_cn(subject)}教师，为{grade.replace('grade_','')}年级学生出题。"
            f"{'所在地区为' + self._region_cn(region) if region else ''}"
            f"知识点范围: {', '.join(knowledge_points[:5])}。难度: {self._difficulty_label(difficulty)}。"
            f"参考以下示例风格，生成{count}道新的、不重复的题目。"
            f"每道题必须包含 question_text(题干), answer(答案), analysis(解析)。"
            f"以 JSON 数组格式输出: [{{\"question_text\":\"...\", \"answer\":\"...\", \"analysis\":\"...\", \"question_type\":\"...\", \"difficulty\":{difficulty}}}, ...]"
            f"不要输出其他内容。"
        )

        user_prompt = f"参考示例:{examples_text}\n\n请生成{count}道新题目。"

        try:
            response = self._llm_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text
            # 提取 JSON 数组
            import re
            match = re.search(r"\[[\s\S]*\]", text)
            if match:
                return json.loads(match.group())
            return []
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}，回退到模板生成")
            return self._generate_via_fallback(request, examples, count)

    def _generate_via_fallback(self, request: dict, examples: list[dict], count: int) -> list[dict]:
        """
        回退模式：基于检索结果中的题目模板进行参数变换生成新题。
        不需要 LLM，纯规则驱动。
        """
        import random
        rng = random.Random(hash(str(request)) + count)

        difficulty = request.get("difficulty", 3)
        knowledge_points = request.get("knowledge_points", ["综合"])
        subject = request.get("subject", "math")
        grade = request.get("grade", "grade_7")

        generated = []
        attempts = 0
        max_attempts = count * 3

        while len(generated) < count and attempts < max_attempts:
            attempts += 1

            if examples:
                template = rng.choice(examples)
                qt = template.get("question_text", "")
                ans = template.get("answer", "")
                al = template.get("analysis", "")

                # 参数变换：数字替换
                import re as _re
                new_qt = _re.sub(
                    r"(?<!\d)\d+(?!\d)",
                    lambda m: str(int(m.group()) + rng.randint(-5, 5)),
                    qt
                )
                new_ans = _re.sub(
                    r"(?<!\d)\d+(?!\d)",
                    lambda m: str(int(m.group()) + rng.randint(-5, 5)),
                    ans
                )
                new_al = _re.sub(
                    r"(?<!\d)\d+(?!\d)",
                    lambda m: str(int(m.group()) + rng.randint(-5, 5)),
                    al
                )

                if new_qt == qt:
                    continue
            else:
                # 无检索结果时，生成简单的基础题
                kp = rng.choice(knowledge_points) if knowledge_points else "综合"
                a, b = rng.randint(1, 100), rng.randint(1, 50)
                new_qt = f"（{kp}）计算：{a} + {b} = ?"
                new_ans = str(a + b)
                new_al = f"{a} + {b} = {a + b}"

            generated.append({
                "id": f"gen_{hashlib.md5(new_qt.encode()).hexdigest()[:10]}",
                "subject": subject,
                "grade": grade,
                "region": request.get("region", ""),
                "knowledge_tags": knowledge_points[:3],
                "difficulty": difficulty,
                "question_type": rng.choice(["calculation", "short_answer", "fill_blank"]),
                "question_text": new_qt,
                "answer": new_ans,
                "analysis": new_al,
                "source": "llm_generated",
                "_generated_by": "QAGeneratorAgent (fallback template)",
            })

        logger.info(f"回退生成: {len(generated)} 题 (尝试 {attempts} 次)")
        return generated

    # ------------------------------------------------------------------
    # 验证与去重
    # ------------------------------------------------------------------

    def _validate_questions(self, questions: list[dict]) -> tuple[list[dict], list[dict]]:
        """验证每道题包含必填字段"""
        valid = []
        invalid = []
        for i, q in enumerate(questions):
            missing = REQUIRED_FIELDS - set(q.keys())
            if missing:
                logger.warning(f"题目 #{i} 缺少字段: {missing}")
                invalid.append(q)
            else:
                if not q["question_text"].strip() or not q["answer"].strip():
                    invalid.append(q)
                else:
                    # 补齐可选字段
                    q.setdefault("source", "llm_generated")
                    q.setdefault("id", f"gen_{hashlib.md5(q['question_text'].encode()).hexdigest()[:10]}")
                    q.setdefault("_generated_by", "QAGeneratorAgent")
                    valid.append(q)
        return valid, invalid

    def _dedup_against_existing(self, new_questions: list[dict], existing_texts: list[str]) -> tuple[list[dict], list[dict]]:
        """将新题目与已有题目做模糊去重"""
        unique = []
        duplicates = []

        for q in new_questions:
            new_text = q.get("question_text", "")
            norm_new = self._normalize(new_text)
            is_dup = False

            for existing in existing_texts:
                norm_ex = self._normalize(existing)
                if len(norm_new) < 5 or len(norm_ex) < 5:
                    continue
                if abs(len(norm_new) - len(norm_ex)) / max(len(norm_new), len(norm_ex)) > 0.30:
                    continue
                sim = difflib.SequenceMatcher(None, norm_new, norm_ex).ratio()
                if sim >= DEDUP_SIMILARITY_THRESHOLD:
                    is_dup = True
                    logger.debug(f"去重命中: sim={sim:.3f}")
                    break

            if is_dup:
                duplicates.append(q)
            else:
                unique.append(q)
                existing_texts.append(new_text)

        return unique, duplicates

    @staticmethod
    def _normalize(text: str) -> str:
        import re
        return re.sub(r"\s+", "", str(text)).lower()

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def _write_output(self, generated: list[dict], retrieved: list[dict],
                      mode: str, skipped_dup: int = 0, skipped_invalid: int = 0) -> dict:
        """写入 generated_questions.json"""
        output_obj = {
            "_meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": "QAGeneratorAgent",
                "mode": mode,
            },
            "total_retrieved": len(retrieved),
            "total_generated": len(generated),
            "total_combined": len(retrieved) + len(generated),
            "skipped_duplicates": skipped_dup,
            "skipped_invalid": skipped_invalid,
            "combined_questions": retrieved + generated,
        }

        GENERATED_QUESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(GENERATED_QUESTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(output_obj, f, ensure_ascii=False, indent=2)

        # 护栏后置检查：验证生成的题目
        if _GUARDRAIL_ENABLED:
            checker = get_checker()
            if checker and checker.enabled:
                ok, violations = checker.check_after_result(
                    output_obj, action="generate"
                )
                if not ok:
                    logger.warning(f"[护栏] 生成后检查: {len(violations)} 项违规")

        _elapsed = (time.time() - _start_time) * 1000
        logger.info(
            f"生成完成: {len(generated)} 新题 + {len(retrieved)} 检索题 = "
            f"{len(retrieved) + len(generated)} 总题 ({_elapsed:.0f}ms) → {GENERATED_QUESTIONS_PATH}"
        )

        return {
            "success": True,
            "generated_count": len(generated),
            "retrieved_count": len(retrieved),
            "total_combined": len(retrieved) + len(generated),
            "skipped_duplicates": skipped_dup,
            "skipped_invalid": skipped_invalid,
            "mode": mode,
            "output_file": str(GENERATED_QUESTIONS_PATH),
        }

    def _error(self, msg: str) -> dict:
        logger.error(msg)
        return {"success": False, "error": msg, "output_file": str(GENERATED_QUESTIONS_PATH)}

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _subject_cn(subject: str) -> str:
        m = {"math": "数学", "chinese": "语文", "english": "英语",
             "physics": "物理", "chemistry": "化学", "biology": "生物",
             "history": "历史", "geography": "地理", "politics": "政治"}
        return m.get(subject, subject)

    @staticmethod
    def _region_cn(region: str) -> str:
        m = {"beijing": "北京", "shanghai": "上海", "guangdong": "广东"}
        return m.get(region, region)

    @staticmethod
    def _difficulty_label(d: int) -> str:
        return {1: "非常容易", 2: "容易", 3: "中等", 4: "较难", 5: "困难"}.get(d, "中等")


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def generate_questions(retrieved_path: str = None, request_path: str = None) -> dict:
    """便捷函数：生成题目"""
    agent = QAGeneratorAgent()
    return agent.generate(
        Path(retrieved_path) if retrieved_path else None,
        Path(request_path) if request_path else None,
    )


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 确保前置条件
    from backend.agents.parameter_parser_agent import ParameterParserAgent
    parser = ParameterParserAgent()
    parse_result = parser.parse({"subject": "数学", "grade": "初三", "region": "beijing",
                                  "knowledge_points": ["一元一次方程"], "question_count": 5})
    if not parse_result["success"]:
        print(f"参数解析失败: {parse_result['errors']}")
        sys.exit(1)

    from backend.agents.question_retriever_agent import QuestionRetrieverAgent
    retriever = QuestionRetrieverAgent()
    retriever.retrieve()

    agent = QAGeneratorAgent()
    result = agent.generate()
    print(f"\n生成结果: generated={result.get('generated_count', 0)}, "
          f"mode={result.get('mode', '?')}")
