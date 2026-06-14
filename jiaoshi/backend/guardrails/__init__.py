# -*- coding: utf-8 -*-
"""
护栏规则系统 (Guardrails)
提供前置/后置校验，防止 Agent 越权操作
"""
from backend.guardrails.guardrail_checker import GuardrailChecker, Violation, check_before, check_after
