#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# ==========================================================================
# CI 自动化测试脚本
# ==========================================================================
# 在代码提交前自动运行所有测试和质量校验。
# 任何失败都会导致 CI 标记为 FAIL，禁止合入。
#
# 用法:
#   bash scripts/ci/run_tests.sh            # 完整测试套件
#   bash scripts/ci/run_tests.sh --quick    # 快速模式（仅核心测试）
#   bash scripts/ci/run_tests.sh --pytest-only  # 仅 pytest
#
# CI 集成（GitHub Actions / GitLab CI）:
#   - name: Run tests
#     run: bash scripts/ci/run_tests.sh --ci
# ==========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 报告目录
REPORT_DIR="$PROJECT_ROOT/test_reports"
mkdir -p "$REPORT_DIR"

# 退出码
EXIT_CODE=0
FAILED_STEPS=()
PASSED_STEPS=()

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

log_section() {
    echo ""
    echo -e "${BLUE}==========================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}==========================================================================${NC}"
}

log_step() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"
}

log_pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
    PASSED_STEPS+=("$1")
}

log_fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    FAILED_STEPS+=("$1")
    EXIT_CODE=1
}

log_warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
}

check_python() {
    if command -v python &> /dev/null; then
        PYTHON=python
    elif command -v python3 &> /dev/null; then
        PYTHON=python3
    else
        log_fail "Python not found"
        exit 1
    fi
    echo "  Python: $($PYTHON --version)"
}

run_python() {
    $PYTHON "$@" 2>&1 | while IFS= read -r line; do
        echo "    $line"
    done
    return ${PIPESTATUS[0]}
}

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
QUICK_MODE=false
CI_MODE=false
PYTEST_ONLY=false

for arg in "$@"; do
    case $arg in
        --quick) QUICK_MODE=true ;;
        --ci) CI_MODE=true ;;
        --pytest-only) PYTEST_ONLY=true ;;
    esac
done

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

log_section "智能试卷生成系统 — CI 自动化测试"
echo "  项目路径: $PROJECT_ROOT"
echo "  快速模式: $QUICK_MODE"
echo "  CI 模式:  $CI_MODE"

check_python

# ==========================================================================
# Step 1: 环境检查
# ==========================================================================
log_section "Step 1: 环境检查"

log_step "1.1 Python 依赖检查"
if $PYTHON -c "import pytest" 2>/dev/null; then
    log_pass "pytest 可用"
else
    log_fail "pytest 未安装 — 请执行: pip install pytest"
fi

if $PYTHON -c "import jinja2" 2>/dev/null; then
    log_pass "jinja2 可用"
else
    log_warn "jinja2 未安装 — 试卷渲染将不可用"
fi

log_step "1.2 关键文件检查"
REQUIRED_FILES=(
    "backend/rag_searcher.py"
    "backend/rag_indexer.py"
    "data/schema.json"
    "data/schema/unified_question.json"
    "data/knowledge_graph.json"
    "scripts/import_clean_data.py"
    "harness/SPECS.md"
    "harness/RULES.md"
)
all_files_ok=true
for f in "${REQUIRED_FILES[@]}"; do
    if [ -f "$f" ]; then
        log_pass "$f"
    else
        log_fail "$f 不存在"
        all_files_ok=false
    fi
done

if [ "$all_files_ok" = false ] && [ "$CI_MODE" = true ]; then
    log_fail "关键文件缺失，CI 终止"
    exit 1
fi

if [ "$PYTEST_ONLY" = true ]; then
    log_section "跳至 pytest 步骤"
fi

# ==========================================================================
# Step 2: Schema 校验
# ==========================================================================
if [ "$PYTEST_ONLY" = false ]; then
log_section "Step 2: Schema 校验（数据模型契约验证）"

log_step "2.1 Schema Validator"
if run_python tests/validators/schema_validator.py 2>/dev/null; then
    log_pass "Schema Validator 通过"
else
    log_fail "Schema Validator 失败"
fi

log_step "2.2 Duplicate Detector"
if run_python tests/validators/duplicate_detector.py 2>/dev/null; then
    log_pass "Duplicate Detector 通过"
else
    log_fail "Duplicate Detector 失败"
fi
fi  # PYTEST_ONLY

# ==========================================================================
# Step 3: pytest 单元测试
# ==========================================================================
log_section "Step 3: pytest 单元测试"

PYTEST_ARGS=(
    "-v"
    "--tb=short"
    "--color=yes"
    "--timeout=60"
    "--junitxml=$REPORT_DIR/pytest_results.xml"
    "--html=$REPORT_DIR/pytest_report.html"
    "--self-contained-html"
)

if [ "$QUICK_MODE" = true ]; then
    PYTEST_ARGS+=("-x")  # 首次失败即停止
    PYTEST_ARGS+=("--ignore=tests/test_api_endpoints.py")  # 跳过慢速测试
fi

# 发现并运行所有测试
log_step "3.1 运行 pytest"
if $PYTHON -m pytest tests/ "${PYTEST_ARGS[@]}" 2>&1 | tee "$REPORT_DIR/pytest_output.log"; then
    log_pass "pytest 全部通过"
else
    PYT_EXIT=${PIPESTATUS[0]}
    log_fail "pytest 有失败项 (exit=$PYT_EXIT)"
fi

# ==========================================================================
# Step 4: RAG 评估
# ==========================================================================
if [ "$QUICK_MODE" = false ] && [ "$PYTEST_ONLY" = false ]; then
log_section "Step 4: RAG 评估"

log_step "4.1 RAG 检索与生成质量评估"
if run_python tests/rag_evaluation/run_evals.py --samples 50 --ci; then
    log_pass "RAG 评估通过"
else
    log_fail "RAG 评估未达标"
fi
fi  # !QUICK_MODE

# ==========================================================================
# Step 5: 质量门禁汇总
# ==========================================================================
log_section "Step 5: 质量门禁汇总"

# 5.1 代码规范检查
log_step "5.1 UTF-8 编码检查"
non_utf8=$(find . -name "*.py" -type f -exec file {} \; 2>/dev/null | grep -v "UTF-8" | grep -v "ASCII" || true)
if [ -z "$non_utf8" ]; then
    log_pass "所有 Python 文件编码为 UTF-8"
else
    log_warn "部分文件可能非 UTF-8 编码"
fi

# 5.2 禁止 emoji
log_step "5.2 Emoji 使用检查"
emoji_count=$(grep -rP '[\x{1F300}-\x{1F9FF}]' --include="*.py" . 2>/dev/null | wc -l || echo "0")
if [ "$emoji_count" -eq 0 ]; then
    log_pass "代码中无 emoji"
else
    log_warn "检测到 $emoji_count 处 emoji 使用（应避免）"
fi

# 5.3 硬编码密钥检查
log_step "5.3 硬编码密钥检查"
leaked_keys=$(grep -rE '(api_key|API_KEY|secret|SECRET|password|PASSWORD)\s*=\s*["'"'"'][^$]' --include="*.py" . 2>/dev/null | grep -v "os.environ" | grep -v "\.env" | grep -v "test_" | grep -v "#" || true)
if [ -z "$leaked_keys" ]; then
    log_pass "无硬编码密钥"
else
    log_fail "发现疑似硬编码密钥"
    echo "$leaked_keys"
fi

# ==========================================================================
# 汇总
# ==========================================================================
log_section "CI 测试汇总"

PASSED_COUNT=${#PASSED_STEPS[@]}
FAILED_COUNT=${#FAILED_STEPS[@]}
TOTAL=$((PASSED_COUNT + FAILED_COUNT))

echo ""
echo -e "  通过: ${GREEN}${PASSED_COUNT}${NC} / ${TOTAL}"
echo -e "  失败: ${RED}${FAILED_COUNT}${NC} / ${TOTAL}"

if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}  失败项:${NC}"
    for step in "${FAILED_STEPS[@]}"; do
        echo -e "    ${RED}✗${NC} $step"
    done
fi

if [ ${#PASSED_STEPS[@]} -gt 0 ]; then
    echo ""
    echo -e "${GREEN}  通过项:${NC}"
    for step in "${PASSED_STEPS[@]}"; do
        echo -e "    ${GREEN}✓${NC} $step"
    done
fi

# 写入 CI 摘要
SUMMARY_FILE="$REPORT_DIR/ci_summary.txt"
{
    echo "CI Test Summary — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=============================="
    echo "Passed: $PASSED_COUNT / $TOTAL"
    echo "Failed: $FAILED_COUNT / $TOTAL"
    echo ""
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Overall: PASS"
    else
        echo "Overall: FAIL"
    fi
} > "$SUMMARY_FILE"

echo ""
echo -e "  报告目录: ${BLUE}$REPORT_DIR${NC}"
echo -e "  摘要文件: ${BLUE}$SUMMARY_FILE${NC}"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}  CI 通过 — 所有质量门禁达标${NC}"
else
    echo -e "${RED}  CI 失败 — 请检查上述失败项${NC}"
fi

exit $EXIT_CODE
