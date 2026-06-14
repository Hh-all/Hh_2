# -*- coding: utf-8 -*-
"""
试卷生成核心流水线
RAG 检索 + Claude API 生成 + 本地回退
"""

import json
import os
import sys
import random
import copy
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from rag_searcher import init_searcher, search as rag_search


# ---------- 默认题型分布 ----------
DEFAULT_QUESTION_TYPES = ["choice", "choice", "fill_blank", "fill_blank", "short_answer"]


def _build_system_prompt(subject: str, grade: str, region: str, difficulty: int) -> str:
    """构建系统提示"""
    difficulty_labels = {1: "非常简单", 2: "简单", 3: "中等", 4: "较难", 5: "困难"}
    diff_label = difficulty_labels.get(difficulty, "中等")

    return (
        f"你是一位经验丰富的中国{subject}教师，为{grade}学生出题。"
        f"所在地区为{region}，难度为{diff_label}。"
        f"请严格按照要求的JSON格式输出，不要输出任何其他内容。"
    )


def _build_user_prompt(
    knowledge_points: list[str],
    num_questions: int,
    difficulty: int,
    references: list[dict],
    question_types: list[str],
) -> str:
    """构建用户提示，包含参考题目"""
    kp_text = "、".join(knowledge_points)

    # 题型描述
    type_desc = []
    for i, qt in enumerate(question_types):
        labels = {"choice": "选择题", "fill_blank": "填空题", "short_answer": "解答题"}
        type_desc.append(f"第{i+1}题: {labels.get(qt, qt)}")

    # 参考题目
    ref_text = ""
    if references:
        ref_lines = []
        for i, ref in enumerate(references):
            ref_lines.append(
                f"参考{i+1}: {ref.get('question_text', '')}\n"
                f"  答案: {ref.get('answer', '')}\n"
                f"  解析: {ref.get('analysis', '')}"
            )
        ref_text = "\n".join(ref_lines)

    prompt = f"""请为以下知识点生成一份练习题。

知识点范围: {kp_text}
题目数量: {num_questions} 道
难度: {difficulty}/5
题型分布:
{chr(10).join(type_desc)}

{"参考题目（请参考以下题目的风格和难度，但生成全新的题目）:" if references else ""}
{ref_text}

请以JSON格式输出，结构如下:
{{
  "questions": [
    {{
      "type": "choice",
      "text": "题干内容",
      "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
      "answer": "C",
      "analysis": "解题思路和详细解析"
    }},
    {{
      "type": "fill_blank",
      "text": "填空题题干，用______表示填空位置",
      "answer": "填空答案",
      "analysis": "解题思路和详细解析"
    }},
    {{
      "type": "short_answer",
      "text": "解答题题干",
      "answer": "完整的解答过程和最终答案",
      "analysis": "评分标准和关键得分点"
    }}
  ]
}}

要求:
- 题型分布严格按上述顺序: {", ".join(type_desc)}
- 选择题只能有4个选项(A/B/C/D)
- 所有题目必须为原创，不能与参考题目完全相同
- 难度为 {difficulty}/5，答题者应为本年级中等水平学生
- 答案必须准确无误
- 解析要详细，包含推导过程"""
    return prompt


def _call_claude(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """
    调用 LLM API 生成题目（支持多供应商）
    返回解析后的 JSON 字典，失败返回 None

    供应商选择:
        LLM_PROVIDER=anthropic  -> Anthropic Claude API
        LLM_PROVIDER=deepseek   -> DeepSeek API (OpenAI 兼容)
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider == "deepseek":
        return _call_deepseek(system_prompt, user_prompt)
    else:
        return _call_anthropic(system_prompt, user_prompt)


def _call_anthropic(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """通过 Anthropic SDK 调用 Claude API"""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))

    if not api_key:
        print("[generate] ANTHROPIC_API_KEY 未设置，跳过 API 调用")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        print(f"[generate] 初始化 Anthropic 客户端失败: {e}")
        return None

    try:
        print(f"[generate] 调用 Claude API (model={model})...")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = response.content[0].text.strip()
        print(f"[generate] API 返回 {len(text)} 字符")

        # 提取 JSON（可能被包裹在 ```json 中）
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"[generate] JSON 解析失败: {e}")
        print(f"[generate] 原始返回: {text[:500]}")
        return None
    except Exception as e:
        print(f"[generate] API 调用失败: {e}")
        return None


def _call_deepseek(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """通过 OpenAI 兼容 SDK 调用 DeepSeek API"""
    from openai import OpenAI

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    max_tokens = int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096"))
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    if not api_key:
        print("[generate] DEEPSEEK_API_KEY 未设置，跳过 API 调用")
        return None

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
    except Exception as e:
        print(f"[generate] 初始化 DeepSeek 客户端失败: {e}")
        return None

    try:
        print(f"[generate] 调用 DeepSeek API (model={model})...")
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.7,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        text = response.choices[0].message.content.strip()
        print(f"[generate] API 返回 {len(text)} 字符")

        # 提取 JSON（DeepSeek 可能用 ```json 包裹）
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"[generate] JSON 解析失败: {e}")
        print(f"[generate] 原始返回: {text[:500]}")
        return None
    except Exception as e:
        print(f"[generate] DeepSeek API 调用失败: {e}")
        return None


def _rewrite_question(original: dict, target_type: str, difficulty: int) -> dict:
    """
    基于检索结果改写题目（本地回退方案）
    对原题做轻度变换：修改数字、替换表述、调整题型结构
    """
    q = copy.deepcopy(original)

    # 简单改写题干文本
    text = q.get("question_text", "")
    # 替换数字
    import re
    numbers = re.findall(r"\d+", text)
    for n in numbers:
        # 小幅度修改数字，避免变得无意义
        try:
            delta = random.choice([-2, -1, 1, 2])
            new_n = int(n) + delta
            if new_n > 0 and delta != 0:
                text = text.replace(n, str(new_n), 1)
        except ValueError:
            pass
    q["text"] = text + "（改编）"

    # 答案和解析保留（已改写题干）
    q["answer"] = q.get("answer", "") + "（注：数值已根据题干调整）"
    q["analysis"] = q.get("analysis", "（本解析对应原题，改编后题目仅供参考）")

    # 根据目标类型添加 options
    q["type"] = target_type
    if target_type == "choice":
        # 尝试从原答案生成干扰项
        ans = q.get("answer", "").strip()
        q["options"] = [
            f"A. {ans}",
            f"B. （干扰项1，请以实际题目为准）",
            f"C. （干扰项2，请以实际题目为准）",
            f"D. （干扰项3，请以实际题目为准）",
        ]

    return {
        "type": q["type"],
        "text": q.get("text", q.get("question_text", "")),
        "options": q.get("options"),
        "answer": q.get("answer", ""),
        "analysis": q.get("analysis", ""),
    }


def _fallback_generate(subject: str, knowledge_points: list[str], difficulty: int,
                       num_questions: int, question_types: list[str]) -> list[dict]:
    """
    回退方案：从 RAG 检索结果中选取题目并改写
    """
    print("[generate] 使用本地回退方案（从题库改写）")
    query = " ".join(knowledge_points)
    results = rag_search(query, filters={"subject": subject}, top_k=15)

    if len(results) < num_questions:
        # 放宽过滤
        results = rag_search(" ".join(knowledge_points), filters=None, top_k=20)

    if not results:
        print("[generate] 回退方案也未找到可用题目")
        return _empty_result()

    random.shuffle(results)
    questions = []
    used = set()

    for i in range(num_questions):
        target_type = question_types[i] if i < len(question_types) else "short_answer"

        # 选一条未用过的
        for r in results:
            rid = r.get("id")
            if rid not in used:
                used.add(rid)
                questions.append(_rewrite_question(r, target_type, difficulty))
                break
        else:
            # 全部用过了，随机复用
            r = random.choice(results)
            questions.append(_rewrite_question(r, target_type, difficulty))

    return questions


def _empty_result() -> list[dict]:
    return [
        {
            "type": "short_answer",
            "text": "（未能生成题目，请检查知识库和配置）",
            "options": None,
            "answer": "",
            "analysis": "",
        }
    ]


# -------------------- 主入口 --------------------

def generate_questions(parameters: dict) -> dict:
    """
    生成试卷题目

    参数：
        parameters = {
            "subject":          "数学",              # 学科
            "grade":            "grade_8",           # 年级
            "region":           "北京",              # 地域
            "knowledge_points": ["一元一次方程"],     # 知识点列表
            "difficulty":       3,                   # 难度 1-5
            "num_questions":    5,                   # 题目数量
        }

    返回：
        {
            "questions": [
                {"type": "choice", "text": "...", "options": [...], "answer": "...", "analysis": "..."},
                ...
            ],
            "source": "claude" | "fallback"   # 来源标记
        }
    """
    # 初始化检索器
    init_searcher()

    subject = parameters.get("subject", "数学")
    grade = parameters.get("grade", "grade_8")
    region = parameters.get("region", "北京")
    knowledge_points = parameters.get("knowledge_points", [])
    difficulty = parameters.get("difficulty", 3)
    num_questions = parameters.get("num_questions", 5)

    # 确定题型分布
    if num_questions == len(DEFAULT_QUESTION_TYPES):
        question_types = list(DEFAULT_QUESTION_TYPES)
    else:
        # 按比例分配
        question_types = []
        type_pool = ["choice", "fill_blank", "short_answer"]
        for i in range(num_questions):
            question_types.append(type_pool[i % len(type_pool)])

    print(f"\n[generate] 参数: subject={subject}, grade={grade}, "
          f"region={region}, difficulty={difficulty}")
    print(f"[generate] 知识点: {knowledge_points}")
    print(f"[generate] 题型分布: {question_types}")

    # ----- Step 1: RAG 检索 -----▌
    query = " ".join(knowledge_points)
    print(f"\n[generate] Step 1: RAG 检索 \"{query}\"...")
    references = rag_search(
        query,
        filters={"subject": subject, "difficulty": {"$lte": difficulty}},
        top_k=5,
    )
    print(f"[generate] 检索到 {len(references)} 条参考题目")

    # ----- Step 2: 调用 LLM -----
    print(f"\n[generate] Step 2: 调用 LLM 生成题目...")
    system_prompt = _build_system_prompt(subject, grade, region, difficulty)
    user_prompt = _build_user_prompt(knowledge_points, num_questions, difficulty, references, question_types)

    result = _call_claude(system_prompt, user_prompt)

    if result and "questions" in result:
        questions = result["questions"]
        # 补全缺失字段
        for q in questions:
            q.setdefault("options", None)
            q.setdefault("analysis", "")
        print(f"[generate] LLM 生成成功: {len(questions)} 道题")
        return {"questions": questions[:num_questions], "source": "claude"}

    # ----- Step 3: 回退 -----
    print(f"\n[generate] Step 3: LLM 失败，启用回退方案...")
    questions = _fallback_generate(subject, knowledge_points, difficulty, num_questions, question_types)
    return {"questions": questions, "source": "fallback"}


# -------------------- 命令行测试 --------------------

if __name__ == "__main__":
    params = {
        "subject": "数学",
        "grade": "grade_8",
        "region": "北京",
        "knowledge_points": ["一元一次方程", "二元一次方程组"],
        "difficulty": 3,
        "num_questions": 5,
    }

    result = generate_questions(params)

    print(f"\n{'=' * 60}")
    print(f"生成结果 (来源: {result['source']})")
    print(f"{'=' * 60}")

    for i, q in enumerate(result["questions"]):
        type_map = {"choice": "选择题", "fill_blank": "填空题", "short_answer": "解答题"}
        print(f"\n第{i+1}题 [{type_map.get(q['type'], q['type'])}]")
        print(f"  题干: {q['text']}")
        if q.get("options"):
            for opt in q["options"]:
                print(f"    {opt}")
        print(f"  答案: {q.get('answer', '')}")
        if q.get("analysis"):
            print(f"  解析: {q['analysis'][:80]}...")
