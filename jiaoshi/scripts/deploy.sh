#!/usr/bin/env bash
# ==========================================================================
# 一键部署脚本
# ==========================================================================
# 检查环境 → 构建索引 → 运行迁移 → 启动所有服务
#
# 用法:
#   bash scripts/deploy.sh                  # 全栈部署
#   bash scripts/deploy.sh --prod           # 生产模式
#   bash scripts/deploy.sh --check-only     # 仅检查环境
#   bash scripts/deploy.sh --no-monitoring  # 跳过监控栈
# ==========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

PROD_MODE=false
CHECK_ONLY=false
SKIP_MONITORING=false

for arg in "$@"; do
    case $arg in
        --prod) PROD_MODE=true ;;
        --check-only) CHECK_ONLY=true ;;
        --no-monitoring) SKIP_MONITORING=true ;;
    esac
done

EXIT_CODE=0

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
log()  { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"; }
pass() { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; EXIT_CODE=1; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; EXIT_CODE=1; }

check_python() {
    if command -v python &> /dev/null; then
        PYTHON=python
    elif command -v python3 &> /dev/null; then
        PYTHON=python3
    else
        fail "Python 未安装"
        return 1
    fi
    pass "Python: $($PYTHON --version 2>&1)"
    return 0
}

# ==========================================================================
# Step 1: 环境检查
# ==========================================================================
log "${CYAN}========================================${NC}"
log "${CYAN}  Step 1/5: 环境检查${NC}"
log "${CYAN}========================================${NC}"

# Python
check_python

# Docker
if command -v docker &> /dev/null; then
    pass "Docker: $(docker --version 2>&1 | head -1)"
else
    warn "Docker 未安装（非容器部署可忽略）"
fi

if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    DC="docker compose"
    pass "Docker Compose: 可用"
else
    DC=""
    warn "Docker Compose 未安装"
fi

# API Keys
KEYS_OK=true
ENV_FILE="backend/.env"
if [ -f "$ENV_FILE" ]; then
    for key in ANTHROPIC_API_KEY OPENAI_API_KEY; do
        if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null && ! grep -q "^${key}=\s*$" "$ENV_FILE" 2>/dev/null; then
            pass "API Key: $key 已配置"
        fi
    done
    if grep -q "LANGCHAIN_API_KEY" "$ENV_FILE" 2>/dev/null; then
        pass "LangSmith: 已配置"
    else
        warn "LangSmith: 未配置（追踪功能不可用）"
    fi
else
    warn ".env 文件不存在: $ENV_FILE"
    KEYS_OK=false
fi

# 关键文件
for f in data/knowledge_graph.json data/schema.json data/schema/unified_question.json; do
    if [ -f "$f" ]; then
        pass "文件: $f"
    else
        fail "文件缺失: $f"
    fi
done

if [ "$CHECK_ONLY" = true ]; then
    log "环境检查完成 (exit=$EXIT_CODE)"
    exit $EXIT_CODE
fi

# ==========================================================================
# Step 2: 构建向量数据库索引
# ==========================================================================
log ""
log "${CYAN}========================================${NC}"
log "${CYAN}  Step 2/5: 构建向量索引${NC}"
log "${CYAN}========================================${NC}"

if [ -f "data/processed/questions.jsonl" ]; then
    QUESTION_COUNT=$(wc -l < "data/processed/questions.jsonl" 2>/dev/null || echo "0")
    pass "题库就绪: ${QUESTION_COUNT} 题"

    if [ -d "data/chroma_db" ] && [ "$(ls -A data/chroma_db 2>/dev/null)" ]; then
        pass "ChromaDB 索引已存在"
    else
        log "正在构建 ChromaDB 索引..."
        if $PYTHON -c "from backend.rag_indexer import build_index; build_index(force_rebuild=True)" 2>/dev/null; then
            pass "索引构建完成"
        else
            warn "索引构建失败（将使用内存回退方案）"
        fi
    fi
else
    warn "题库为空: data/processed/questions.jsonl 不存在"
    log "  提示: 运行 python scripts/import_clean_data.py 导入数据后重试"
fi

# ==========================================================================
# Step 3: 运行迁移脚本
# ==========================================================================
log ""
log "${CYAN}========================================${NC}"
log "${CYAN}  Step 3/5: 运行迁移${NC}"
log "${CYAN}========================================${NC}"

# 创建必要目录
for d in logs tmp output test_reports data/knowledge; do
    mkdir -p "$d"
done
pass "目录结构就绪"

# 知识图谱索引
if $PYTHON -c "from backend.knowledge.knowledge_graph import KnowledgeGraph; KnowledgeGraph()" 2>/dev/null; then
    pass "知识图谱索引就绪"
else
    warn "知识图谱加载失败"
fi

# ==========================================================================
# Step 4: 缓存预热（可选）
# ==========================================================================
log ""
log "${CYAN}========================================${NC}"
log "${CYAN}  Step 4/5: 缓存预热${NC}"
log "${CYAN}========================================${NC}"

if $PYTHON scripts/warmup_cache.py --quiet 2>/dev/null; then
    pass "缓存预热完成"
else
    warn "缓存预热跳过（在服务启动后自动预热）"
fi

# ==========================================================================
# Step 5: 启动服务
# ==========================================================================
log ""
log "${CYAN}========================================${NC}"
log "${CYAN}  Step 5/5: 启动服务${NC}"
log "${CYAN}========================================${NC}"

if [ "$PROD_MODE" = true ] && [ -n "$DC" ]; then
    log "生产模式: Docker Compose 启动"
    $DC up -d --build
    $DC ps
    echo ""
    log "服务已启动:"
    log "  API:      http://localhost:5000"
    log "  Grafana:  http://localhost:3000 (admin/admin)"
    log "  Prometheus: http://localhost:9090"
elif [ -n "$DC" ]; then
    log "开发模式: Docker Compose 启动"
    $DC up -d
    $DC ps
else
    log "本地模式: 直接启动 Flask"
    log "  启动命令: python backend/server.py"
fi

# ==========================================================================
# 部署摘要
# ==========================================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署完成${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

if [ -n "$DC" ]; then
    echo "  查看日志:  docker compose logs -f api"
    echo "  停止服务:  docker compose down"
else
    echo "  启动服务:  python backend/server.py"
    echo "  启动 Celery: celery -A backend.celery_app worker --loglevel=info"
fi

echo "  健康检查:  curl http://localhost:5000/api/health"
echo "  API 文档:  http://localhost:5000/api/health"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "  ${GREEN}所有检查通过${NC}"
else
    echo -e "  ${YELLOW}部分检查有警告，请检查上述 [WARN] 项${NC}"
fi

exit $EXIT_CODE
