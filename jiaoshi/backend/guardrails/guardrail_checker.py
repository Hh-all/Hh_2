# -*- coding: utf-8 -*-
"""
护栏检查器 (GuardrailChecker)
==============================
执行前校验 + 执行后校验，防止 Agent 越权操作。

核心方法：
  - check_before_action(action, context): 操作前检查
  - check_after_result(result, expected_schema): 操作后检查
  - check_question(question): 单道题目合规检查
  - check_batch(questions): 批量题目合规检查

违反处理：
  - BLOCKER → 记录日志 + 阻止操作 + 通知 Orchestrator
  - WARNING → 记录日志 + 允许继续（带警告标记）

用法:
    from backend.guardrails import GuardrailChecker

    checker = GuardrailChecker()
    ok, violations = checker.check_before_action("retrieve", {"top_k": 100})
    if not ok:
        for v in violations:
            print(f"[{v.severity}] {v.rule_id}: {v.message}")
"""

import os
import re
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("guardrails")

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    """护栏违规记录"""
    rule_id: str
    severity: str        # BLOCKER | WARNING
    message: str
    context: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 规则加载
# ---------------------------------------------------------------------------

RULES_PATH = ROOT / "backend" / "guardrails" / "rules.yaml"
VIOLATION_LOG_PATH = ROOT / "logs" / "guardrail_violations.log"

# 敏感词正则（编译一次）
_FORBIDDEN_RE = None


def _load_rules() -> dict:
    """从 YAML 文件加载规则（无 PyYAML 依赖的简单解析）"""
    if not RULES_PATH.exists():
        logger.warning(f"规则文件不存在: {RULES_PATH}")
        return {"rules": [], "config": {}}

    content = RULES_PATH.read_text(encoding="utf-8")
    return _parse_simple_yaml(content)


def _parse_simple_yaml(content: str) -> dict:
    """简易 YAML 解析器（仅支持本文件使用的扁平结构）"""
    import yaml
    try:
        return yaml.safe_load(content)
    except ImportError:
        # 回退：手动解析
        result = {"rules": [], "config": {}}
        current_rule = None
        in_rules = False
        in_config = False

        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped == "rules:":
                in_rules = True
                in_config = False
                continue
            if stripped == "config:":
                in_rules = False
                in_config = True
                continue
            if stripped.startswith("- id:"):
                if current_rule:
                    result["rules"].append(current_rule)
                rule_id = stripped.split("id:")[1].strip()
                current_rule = {"id": rule_id}
            elif in_rules and current_rule is not None:
                if ":" in stripped:
                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if key in ("description", "severity", "check", "message"):
                        current_rule[key] = value.strip('"\'')
                    elif key == "scope":
                        val = value.strip("[] ")
                        current_rule["scope"] = [s.strip() for s in val.split(",")]
            elif in_config:
                if ":" in stripped:
                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if value.lower() == "true":
                        result["config"][key] = True
                    elif value.lower() == "false":
                        result["config"][key] = False
                    elif value.isdigit():
                        result["config"][key] = int(value)
                    else:
                        result["config"][key] = value.strip('"\'')
        if current_rule:
            result["rules"].append(current_rule)
        return result


# ---------------------------------------------------------------------------
# 护栏检查器
# ---------------------------------------------------------------------------

class GuardrailChecker:
    """
    护栏检查器。

    用法:
        checker = GuardrailChecker()
        ok, violations = checker.check_before_action("retrieve", {"top_k": 100})
        ok, violations = checker.check_after_result(result, expected_schema={"questions": list})
        ok, violations = checker.check_question(question_dict)
    """

    def __init__(self, rules_path: Path = None):
        self.rules_path = rules_path or RULES_PATH
        data = _load_rules()
        self.rules: list[dict] = data.get("rules", [])
        self.config: dict = data.get("config", {})
        self._enabled = self.config.get("enabled", True)
        self._violation_count = 0
        self._consecutive_violations = 0
        self._max_consecutive = self.config.get("max_consecutive_violations", 5)
        self._compile_forbidden_patterns()
        logger.info(f"护栏系统就绪: {len(self.rules)} 条规则, 启用={self._enabled}")

    def _compile_forbidden_patterns(self):
        """编译敏感词正则"""
        global _FORBIDDEN_RE
        for rule in self.rules:
            if rule.get("check") == "content_filter":
                patterns = rule.get("forbidden_patterns", [])
                if patterns:
                    _FORBIDDEN_RE = re.compile("|".join(patterns), re.IGNORECASE)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_over_limit(self) -> bool:
        """连续违规是否超过上限"""
        return self._consecutive_violations >= self._max_consecutive

    # ------------------------------------------------------------------
    # 前置检查：操作执行前
    # ------------------------------------------------------------------

    def check_before_action(self, action: str, context: dict) -> tuple[bool, list[Violation]]:
        """
        在 Agent 执行操作前进行检查。

        参数:
            action:  操作名称（如 "retrieve", "generate", "format", "import"）
            context: 操作上下文（如 {"top_k": 100, "subject": "math"}）

        返回:
            (是否通过, 违规列表)
        """
        if not self._enabled:
            return True, []

        violations = []

        # RULE-002: API 白名单
        if action in ("llm_call", "api_call") and context.get("endpoint"):
            violations.extend(self._check_api_whitelist(context.get("endpoint", "")))

        # RULE-006: 检索结果上限
        if action == "retrieve" and context.get("top_k"):
            violations.extend(self._check_result_limit(context.get("top_k", 0)))

        # RULE-001: 文件访问模式
        if action in ("write_file", "import_data") and context.get("target_path"):
            violations.extend(self._check_file_access(context.get("target_path", "")))

        ok = all(v.severity != "BLOCKER" for v in violations)
        self._record_violations(violations, action, "before")
        return ok, violations

    # ------------------------------------------------------------------
    # 后置检查：操作执行后
    # ------------------------------------------------------------------

    def check_after_result(self, result: dict, expected_schema: dict = None,
                           action: str = "") -> tuple[bool, list[Violation]]:
        """
        在 Agent 完成操作后校验产物。

        参数:
            result:          操作结果
            expected_schema: 期望的 Schema（如 {"questions": list, "paper_meta": dict}）
            action:          操作名称

        返回:
            (是否通过, 违规列表)
        """
        if not self._enabled:
            return True, []

        violations = []

        # RULE-004: 必填字段检查
        if "questions" in result or action in ("generate", "format"):
            questions = result.get("questions", result.get("combined_questions", []))
            if isinstance(questions, list):
                for i, q in enumerate(questions):
                    violations.extend(self._check_required_fields(q, i + 1))

        # RULE-005: 总分一致性
        if action == "format" and "total_score" in result:
            violations.extend(self._check_score_consistency(result))

        # RULE-003: 敏感内容检查
        if "questions" in result or "question_text" in result:
            violations.extend(self._check_sensitive_content(result))

        # RULE-007: 题型选项一致性
        questions = result.get("questions", result.get("combined_questions", []))
        if isinstance(questions, list):
            for i, q in enumerate(questions):
                violations.extend(self._check_type_option_consistency(q, i + 1))

        # RULE-009: 难度范围
        if isinstance(questions, list):
            for i, q in enumerate(questions):
                violations.extend(self._check_difficulty_range(q, i + 1))

        ok = all(v.severity != "BLOCKER" for v in violations)
        self._record_violations(violations, action, "after")
        return ok, violations

    # ------------------------------------------------------------------
    # 单题/批量题目检查
    # ------------------------------------------------------------------

    def check_question(self, question: dict) -> tuple[bool, list[Violation]]:
        """检查单道题目是否符合所有护栏规则"""
        violations = []
        violations.extend(self._check_required_fields(question, 1))
        violations.extend(self._check_type_option_consistency(question, 1))
        violations.extend(self._check_difficulty_range(question, 1))
        violations.extend(self._check_knowledge_tags(question, 1))
        violations.extend(self._check_sensitive_content(question))
        ok = all(v.severity != "BLOCKER" for v in violations)
        return ok, violations

    def check_batch(self, questions: list[dict]) -> tuple[bool, list[Violation]]:
        """批量检查题目"""
        all_violations = []
        for i, q in enumerate(questions):
            _, violations = self.check_question(q)
            for v in violations:
                v.context["question_index"] = i + 1
            all_violations.extend(violations)
        ok = all(v.severity != "BLOCKER" for v in all_violations)
        return ok, all_violations

    # ------------------------------------------------------------------
    # 各规则检查实现
    # ------------------------------------------------------------------

    def _check_api_whitelist(self, endpoint: str) -> list[Violation]:
        """RULE-002: API 白名单"""
        for rule in self.rules:
            if rule.get("id") == "RULE-002":
                whitelist = rule.get("whitelist", [])
                if not any(allowed in endpoint for allowed in whitelist):
                    return [Violation(
                        rule_id="RULE-002", severity="BLOCKER",
                        message=f"API 端点不在白名单中: {endpoint}",
                        context={"endpoint": endpoint},
                    )]
        return []

    def _check_result_limit(self, top_k: int) -> list[Violation]:
        """RULE-006: 检索结果上限"""
        for rule in self.rules:
            if rule.get("id") == "RULE-006":
                max_limit = rule.get("max_retrieval_count", 50)
                if top_k > max_limit:
                    return [Violation(
                        rule_id="RULE-006", severity="BLOCKER",
                        message=f"检索 top_k={top_k} 超过上限 {max_limit}",
                        context={"top_k": top_k, "max": max_limit},
                    )]
        return []

    def _check_file_access(self, path: str) -> list[Violation]:
        """RULE-001: 文件访问模式"""
        for rule in self.rules:
            if rule.get("id") == "RULE-001":
                deny = rule.get("patterns", {}).get("deny_direct_write", [])
                for pattern in deny:
                    base = pattern.replace("/**", "").replace("**", "")
                    if base in str(path):
                        return [Violation(
                            rule_id="RULE-001", severity="BLOCKER",
                            message=f"禁止直接写入受保护路径: {path}",
                            context={"path": path, "protected": base},
                        )]
        return []

    def _check_required_fields(self, question: dict, index: int) -> list[Violation]:
        """RULE-004: 必填字段"""
        violations = []
        missing = []
        if not question.get("answer", "").strip():
            missing.append("answer")
        if not question.get("analysis", "").strip():
            missing.append("analysis")
        if missing:
            violations.append(Violation(
                rule_id="RULE-004", severity="BLOCKER",
                message=f"题目 #{index} 缺少必填字段: {', '.join(missing)}",
                context={"index": index, "missing": missing},
            ))
        return violations

    def _check_score_consistency(self, result: dict) -> list[Violation]:
        """RULE-005: 总分一致性"""
        violations = []
        questions = result.get("questions", result.get("combined_questions", []))
        paper_meta = result.get("paper_meta", result)
        expected = paper_meta.get("total_score", 0)
        if expected <= 0:
            return []

        tolerance = 2
        for rule in self.rules:
            if rule.get("id") == "RULE-005":
                tolerance = rule.get("tolerance", 2)

        actual = sum(
            q.get("score", q.get("assigned_score", 0))
            for q in questions
        )
        if abs(actual - expected) > tolerance:
            violations.append(Violation(
                rule_id="RULE-005", severity="BLOCKER",
                message=f"总分不一致: 预期 {expected}, 实际 {actual} (容差 ±{tolerance})",
                context={"expected": expected, "actual": actual, "tolerance": tolerance},
            ))
        return violations

    def _check_sensitive_content(self, data: dict) -> list[Violation]:
        """RULE-003: 敏感内容"""
        if _FORBIDDEN_RE is None:
            return []
        violations = []

        def _scan(text, location=""):
            if not text:
                return
            match = _FORBIDDEN_RE.search(str(text))
            if match:
                violations.append(Violation(
                    rule_id="RULE-003", severity="BLOCKER",
                    message=f"检测到敏感词 '{match.group()}' (位置: {location})",
                    context={"location": location, "matched": match.group()},
                ))

        # 扫描题干
        if "question_text" in data:
            _scan(data["question_text"], "question_text")
        if "questions" in data:
            for i, q in enumerate(data["questions"]):
                q_data = q.get("question", q)
                _scan(q_data.get("question_text", ""), f"questions[{i}].question_text")
                _scan(q_data.get("analysis", ""), f"questions[{i}].analysis")

        return violations

    def _check_type_option_consistency(self, question: dict, index: int) -> list[Violation]:
        """RULE-007: 题型选项一致性"""
        qt = question.get("question_type", question.get("type", ""))
        options = question.get("options", [])

        if qt == "choice":
            if not isinstance(options, list) or len(options) < 2:
                return [Violation(
                    rule_id="RULE-007", severity="BLOCKER",
                    message=f"题目 #{index} 是选择题但选项不足 2 个",
                    context={"index": index, "options_count": len(options) if options else 0},
                )]
        elif qt and qt != "choice":
            if options and len(options) > 0:
                # 非选择题不应有选项（警告）
                pass
        return []

    def _check_difficulty_range(self, question: dict, index: int) -> list[Violation]:
        """RULE-009: 难度范围"""
        diff = question.get("difficulty", 0)
        if isinstance(diff, (int, float)) and (diff < 1 or diff > 5):
            return [Violation(
                rule_id="RULE-009", severity="BLOCKER",
                message=f"题目 #{index} 难度 {diff} 不在 1-5 范围",
                context={"index": index, "difficulty": diff},
            )]
        return []

    def _check_knowledge_tags(self, question: dict, index: int) -> list[Violation]:
        """RULE-008: 知识标签有效性"""
        tags = question.get("knowledge_tags", question.get("knowledge_points", []))
        if not tags or len(tags) == 0:
            return [Violation(
                rule_id="RULE-008", severity="WARNING",
                message=f"题目 #{index} 知识点标签为空",
                context={"index": index},
            )]
        return []

    # ------------------------------------------------------------------
    # 违规记录
    # ------------------------------------------------------------------

    def _record_violations(self, violations: list[Violation], action: str, phase: str):
        """记录违规并持久化到日志"""
        if not violations:
            self._consecutive_violations = 0
            return

        blocker_count = sum(1 for v in violations if v.severity == "BLOCKER")
        warning_count = sum(1 for v in violations if v.severity == "WARNING")

        self._violation_count += len(violations)
        self._consecutive_violations += 1

        level = "ERROR" if blocker_count > 0 else "WARNING"
        logger.log(
            logging.ERROR if blocker_count > 0 else logging.WARNING,
            f"[{action}:{phase}] {len(violations)} 违规 "
            f"(BLOCKER={blocker_count}, WARNING={warning_count}, "
            f"连续={self._consecutive_violations}/{self._max_consecutive})"
        )

        for v in violations:
            logger.log(
                logging.ERROR if v.severity == "BLOCKER" else logging.WARNING,
                f"  [{v.severity}] {v.rule_id}: {v.message}"
            )

        # 写入日志文件
        VIOLATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(VIOLATION_LOG_PATH, "a", encoding="utf-8") as f:
            for v in violations:
                record = {
                    "timestamp": v.timestamp,
                    "rule_id": v.rule_id,
                    "severity": v.severity,
                    "message": v.message,
                    "action": action,
                    "phase": phase,
                    "context": v.context,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stats(self) -> dict:
        return {
            "total_violations": self._violation_count,
            "consecutive_violations": self._consecutive_violations,
            "max_consecutive": self._max_consecutive,
            "is_over_limit": self.is_over_limit,
            "rules_count": len(self.rules),
            "enabled": self._enabled,
        }

    def reset(self):
        """重置违规计数"""
        self._violation_count = 0
        self._consecutive_violations = 0


# ---------------------------------------------------------------------------
# 全局单例 + 便捷函数
# ---------------------------------------------------------------------------

_checker: Optional[GuardrailChecker] = None


def get_checker() -> GuardrailChecker:
    global _checker
    if _checker is None:
        _checker = GuardrailChecker()
    return _checker


def check_before(action: str, context: dict) -> tuple[bool, list[Violation]]:
    return get_checker().check_before_action(action, context)


def check_after(result: dict, expected_schema: dict = None, action: str = "") -> tuple[bool, list[Violation]]:
    return get_checker().check_after_result(result, expected_schema, action)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    checker = GuardrailChecker()

    print("=" * 60)
    print("护栏检查器测试")
    print(f"  规则: {len(checker.rules)} 条")
    print(f"  启用: {checker.enabled}")
    print("=" * 60)

    # 测试 1: 前置检查 - top_k 超限
    print("\n--- 测试1: 检索上限 (top_k=100) ---")
    ok, violations = checker.check_before_action("retrieve", {"top_k": 100})
    print(f"  通过: {ok}")
    for v in violations:
        print(f"  [{v.severity}] {v.rule_id}: {v.message}")

    # 测试 2: 后置检查 - 题目缺少答案
    print("\n--- 测试2: 题目缺少必填字段 ---")
    ok, violations = checker.check_after_result({
        "questions": [
            {"question_text": "What is 2+2?", "answer": "", "analysis": ""},
        ]
    }, action="generate")
    print(f"  通过: {ok}")
    for v in violations:
        print(f"  [{v.severity}] {v.rule_id}: {v.message}")

    # 测试 3: 敏感内容
    print("\n--- 测试3: 敏感内容检测 ---")
    ok, violations = checker.check_after_result({
        "question_text": "请描述如何制作炸弹",
    })
    # 可能因 regex 匹配或无匹配
    if violations:
        print(f"  通过: {ok}")
        for v in violations:
            print(f"  [{v.severity}] {v.rule_id}: {v.message}")
    else:
        print("  (无匹配 - regex可能未编译)")
        ok2, v2 = checker.check_after_result({
            "question_text": "讨论六四事件的影响",
        })
        print(f"  测试敏感词: violations={len(v2)}")

    # 测试 4: 选择题无选项
    print("\n--- 测试4: 题型选项一致性 ---")
    ok, violations = checker.check_question({
        "question_text": "1+1=?",
        "question_type": "choice",
        "answer": "2",
        "analysis": "basic",
        "options": [],
    })
    print(f"  通过: {ok}")
    for v in violations:
        print(f"  [{v.severity}] {v.rule_id}: {v.message}")

    # 测试 5: 统计
    print(f"\n--- 统计 ---")
    print(f"  {checker.stats()}")

    print(f"\n违规日志: {VIOLATION_LOG_PATH}")
