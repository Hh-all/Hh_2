#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# ==========================================================================
# LangSmith 追踪控制台启动脚本
# ==========================================================================
# 检查 LangSmith 配置状态，输出控制台访问链接和本地追踪摘要。
#
# 用法:
#   bash scripts/trace_dashboard.sh            # 查看追踪状态和控制台链接
#   bash scripts/trace_dashboard.sh --open     # 尝试在浏览器中打开控制台
#   bash scripts/trace_dashboard.sh --summary  # 输出本地追踪摘要
# ==========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

OPEN_BROWSER=false
SHOW_SUMMARY=false

for arg in "$@"; do
    case $arg in
        --open) OPEN_BROWSER=true ;;
        --summary) SHOW_SUMMARY=true ;;
    esac
done

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║         智能试卷生成系统 — LangSmith 追踪控制台            ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ---------------------------------------------------------------------------
# 1. 环境检查
# ---------------------------------------------------------------------------
echo -e "${BLUE}[1/4] 检查 LangSmith 配置...${NC}"

API_KEY="${LANGCHAIN_API_KEY:-${LANGSMITH_API_KEY:-}}"
PROJECT="${LANGCHAIN_PROJECT:-exam-paper-generator}"
ENDPOINT="${LANGSMITH_ENDPOINT:-https://api.smith.langchain.com}"

# 检查 .env
if [ -f "backend/.env" ]; then
    if grep -q "LANGCHAIN_API_KEY" backend/.env 2>/dev/null; then
        API_KEY="***configured***"
    fi
fi

if [ -n "$API_KEY" ]; then
    echo -e "  ${GREEN}✓${NC} API Key: 已配置"
    echo -e "  ${GREEN}✓${NC} 项目:   ${PROJECT}"
    echo -e "  ${GREEN}✓${NC} 端点:   ${ENDPOINT}"
    LANGCHAIN_READY=true
else
    echo -e "  ${YELLOW}⚠${NC} API Key: 未配置"
    echo -e "  ${YELLOW}⚠${NC} 追踪将以${CYAN}本地日志模式${NC}运行"
    echo ""
    echo -e "  ${YELLOW}配置方式:${NC}"
    echo "    export LANGCHAIN_API_KEY=ls__your_api_key"
    echo "    export LANGCHAIN_PROJECT=exam-paper-generator"
    echo ""
    echo "  API Key 获取地址:"
    echo -e "    ${CYAN}https://smith.langchain.com/settings${NC}"
    LANGCHAIN_READY=false
fi

# ---------------------------------------------------------------------------
# 2. Python 环境检查
# ---------------------------------------------------------------------------
echo ""
echo -e "${BLUE}[2/4] 检查 Python 环境...${NC}"

PYTHON=""
if command -v python &> /dev/null; then
    PYTHON=python
elif command -v python3 &> /dev/null; then
    PYTHON=python3
fi

if [ -n "$PYTHON" ]; then
    echo -e "  ${GREEN}✓${NC} Python: $($PYTHON --version 2>&1)"
else
    echo -e "  ${RED}✗${NC} Python 未找到"
fi

if $PYTHON -c "import langsmith" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} langsmith: 已安装"
    LS_VERSION=$($PYTHON -c "import langsmith; print(langsmith.__version__)" 2>/dev/null || echo "?")
    echo "         版本: $LS_VERSION"
else
    echo -e "  ${YELLOW}⚠${NC} langsmith: 未安装 — 执行: pip install langsmith"
fi

# ---------------------------------------------------------------------------
# 3. 追踪状态
# ---------------------------------------------------------------------------
echo ""
echo -e "${BLUE}[3/4] 追踪状态...${NC}"

TRACER_OUTPUT=$($PYTHON -c "
from backend.tracing.tracer import is_tracing_available, _LANGSMITH_AVAILABLE, _get_project_name
print(f'SDK={_LANGSMITH_AVAILABLE}')
print(f'Ready={is_tracing_available()}')
print(f'Project={_get_project_name()}')
" 2>/dev/null || echo "SDK=False
Ready=False
Project=exam-paper-generator")

echo "$TRACER_OUTPUT" | while IFS='=' read -r key value; do
    case "$key" in
        SDK)
            if [ "$value" = "True" ]; then
                echo -e "  ${GREEN}✓${NC} LangSmith SDK: 可用"
            else
                echo -e "  ${YELLOW}⚠${NC} LangSmith SDK: 不可用"
            fi
            ;;
        Ready)
            if [ "$value" = "True" ]; then
                echo -e "  ${GREEN}✓${NC} 追踪就绪: 是"
            else
                echo -e "  ${YELLOW}⚠${NC} 追踪就绪: 否 (本地日志回退)"
            fi
            ;;
        Project)
            echo -e "  ${CYAN}○${NC} 项目名称: ${value}"
            ;;
    esac
done

# 本地追踪文件统计
LOCAL_TRACES=$(find logs/traces -name "trace_*.jsonl" 2>/dev/null | wc -l || echo "0")
LOCAL_EVALS=$(find logs/evals -name "eval_*.json" 2>/dev/null | wc -l || echo "0")
echo -e "  ${CYAN}○${NC} 本地追踪文件: ${LOCAL_TRACES} 个"
echo -e "  ${CYAN}○${NC} 本地评估文件: ${LOCAL_EVALS} 个"

# ---------------------------------------------------------------------------
# 4. 控制台链接
# ---------------------------------------------------------------------------
echo ""
echo -e "${BLUE}[4/4] 控制台入口...${NC}"
echo ""

if [ "$LANGCHAIN_READY" = true ]; then
    # 尝试获取控制台 URL
    DASHBOARD_URL=$($PYTHON -c "
from backend.tracing.eval_tracker import get_dashboard_url
url = get_dashboard_url()
print(url if url else '')
" 2>/dev/null || echo "")

    if [ -n "$DASHBOARD_URL" ]; then
        echo -e "  ${GREEN}LangSmith 控制台:${NC}"
        echo -e "  ${CYAN}${DASHBOARD_URL}${NC}"
        echo ""
        echo "  功能:"
        echo "    - 查看所有 Traces（RAG 检索 + LLM 调用 + 试卷生成）"
        echo "    - 查看评估指标（Faithfulness / AnswerRelevancy / ContextRecall）"
        echo "    - 对比实验（baseline-v1 vs v2）"
        echo "    - 按标签过滤（rag / llm / paper-generation）"
        echo ""
    else
        echo -e "  ${CYAN}LangSmith 控制台:${NC}"
        echo "  https://smith.langchain.com"
        echo ""
        echo "  (登录后选择项目: ${PROJECT})"
        echo ""
    fi
else
    echo -e "  ${YELLOW}LangSmith 未配置，使用本地追踪:${NC}"
    echo ""
    echo -e "  ${CYAN}本地追踪日志:${NC} logs/traces/"
    echo -e "  ${CYAN}本地评估日志:${NC} logs/evals/"
    echo ""
    echo "  查看本地追踪:"
    echo "    cat logs/traces/trace_*.jsonl | python -m json.tool | head -50"
    echo ""
fi

# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------
if [ "$SHOW_SUMMARY" = true ]; then
    echo -e "${BLUE}────────────────────────────────────────────────────────────${NC}"
    echo -e "${BLUE}本地追踪摘要${NC}"
    echo -e "${BLUE}────────────────────────────────────────────────────────────${NC}"

    if [ -d "logs/traces" ] && [ "$(ls -A logs/traces/ 2>/dev/null)" ]; then
        echo ""
        echo "  最近的追踪事件:"
        find logs/traces -name "trace_*.jsonl" -type f -exec tail -5 {} \; 2>/dev/null | \
            $PYTHON -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        print(f'    [{d.get(\"name\",\"?\")}] {d.get(\"run_type\",\"?\")} — {d.get(\"elapsed_ms\",0):.0f}ms')
    except:
        pass
" 2>/dev/null || echo "    (无分析数据)"
    else
        echo "    (无本地追踪数据 — 运行一次试卷生成以产生追踪)"
    fi
fi

# ---------------------------------------------------------------------------
# 浏览器打开
# ---------------------------------------------------------------------------
if [ "$OPEN_BROWSER" = true ] && [ "$LANGCHAIN_READY" = true ]; then
    echo ""
    echo -e "  ${CYAN}正在打开浏览器...${NC}"
    if [ -n "$DASHBOARD_URL" ]; then
        if command -v start &> /dev/null; then
            start "$DASHBOARD_URL" 2>/dev/null || true
        elif command -v xdg-open &> /dev/null; then
            xdg-open "$DASHBOARD_URL" 2>/dev/null || true
        elif command -v open &> /dev/null; then
            open "$DASHBOARD_URL" 2>/dev/null || true
        fi
    fi
fi

echo ""
echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
echo ""
