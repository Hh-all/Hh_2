#!/usr/bin/env bash
# ============================================================
# 智能试卷生成系统 - 一键部署脚本
# ============================================================
# 用法:
#   bash deploy.sh              # 交互式部署
#   bash deploy.sh --docker     # 使用 Docker 部署
#   bash deploy.sh --local      # 本地直接部署
#   bash deploy.sh --help       # 查看帮助
# ============================================================

set -euo pipefail

# -------------------- 颜色输出 --------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${BLUE}[STEP]${NC} $*"; }

# -------------------- 项目根路径 --------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PROJECT_NAME="智能试卷生成系统"

# -------------------- Logo --------------------
show_banner() {
    echo ""
    echo "  ================================================"
    echo "    ${PROJECT_NAME} - 部署工具"
    echo "  ================================================"
    echo ""
}

# -------------------- 帮助 --------------------
show_help() {
    echo "用法: bash deploy.sh [选项]"
    echo ""
    echo "选项:"
    echo "  --docker      使用 Docker Compose 部署"
    echo "  --local       本地直接部署（不依赖 Docker）"
    echo "  --help        显示此帮助信息"
    echo ""
    echo "环境变量（可选）:"
    echo "  ANTHROPIC_API_KEY   Claude API Key（不填则使用本地回退方案）"
    echo "  SERVER_PORT         后端端口，默认 5000"
    echo "  FRONTEND_PORT       前端端口，默认 8080"
    exit 0
}

# -------------------- 检查依赖 --------------------
check_deps_local() {
    step "检查本地依赖"

    # Python 版本
    if command -v python3 &>/dev/null; then
        PYTHON=python3
    elif command -v python &>/dev/null; then
        PYTHON=python
    else
        error "未找到 Python，请安装 Python 3.11+"
        exit 1
    fi

    PY_VER=$($PYTHON --version 2>&1 | awk '{print $2}')
    info "Python 版本: $PY_VER"

    # pip
    if ! $PYTHON -m pip --version &>/dev/null; then
        error "未找到 pip，请安装 pip"
        exit 1
    fi
}

check_deps_docker() {
    step "检查 Docker 依赖"

    if ! command -v docker &>/dev/null; then
        error "未找到 Docker，请先安装 Docker"
        exit 1
    fi
    info "Docker 版本: $(docker --version)"

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        error "未找到 docker-compose"
        exit 1
    fi
    info "Docker Compose 可用"
}

# -------------------- 环境配置 --------------------
setup_env() {
    step "配置环境变量"

    # 创建 .env 文件（如果不存在）
    if [ ! -f "backend/.env" ]; then
        info "创建 backend/.env 配置文件..."
        cp backend/.env.example backend/.env

        echo ""
        warn "=============================================="
        warn "  注意: Claude API Key 尚未配置"
        warn "  系统将使用 RAG 检索 + 本地改写方案"
        warn "  如需 LLM 生成，请编辑 backend/.env 填入 API Key"
        warn "=============================================="
        echo ""

        # 交互式询问 API Key
        read -r -p "是否现在输入 Anthropic API Key? (y/n): " SET_KEY
        if [[ "$SET_KEY" =~ ^[Yy]$ ]]; then
            read -r -p "API Key (sk-ant-...): " API_KEY
            if [ -n "$API_KEY" ]; then
                # macOS sed compatibility
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$API_KEY/" backend/.env
                else
                    sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$API_KEY/" backend/.env
                fi
                info "API Key 已保存到 backend/.env"
            fi
        fi
    else
        info "backend/.env 已存在，跳过"
    fi

    # 确保数据目录存在
    mkdir -p data/chroma_db output
}

# -------------------- 本地部署 --------------------
deploy_local() {
    check_deps_local
    setup_env

    step "安装 Python 依赖"
    info "正在安装依赖（这可能需要几分钟）..."
    $PYTHON -m pip install --upgrade pip -q
    $PYTHON -m pip install -r requirements.txt -q

    # 安装 Sentence-Transformers（较大的模型包）
    info "安装 Sentence-Transformers..."
    $PYTHON -m pip install sentence-transformers -q

    # 安装可选依赖
    info "安装 jinja2 / weasyprint..."
    $PYTHON -m pip install jinja2 weasyprint -q 2>/dev/null || warn "weasyprint 安装失败，PDF 导出将使用浏览器打印"

    step "构建向量索引"
    info "正在构建知识库向量索引..."
    $PYTHON -c "
import sys
sys.path.insert(0, 'backend')
from rag_indexer import build_index
build_index(force_rebuild=False)
" || warn "向量索引构建失败，将在首次启动时自动构建"

    step "运行测试（可选）"
    read -r -p "是否运行测试套件? (y/n, 默认 n): " RUN_TEST
    if [[ "$RUN_TEST" =~ ^[Yy]$ ]]; then
        info "运行测试..."
        $PYTHON -m pip install pytest -q
        $PYTHON -m pytest tests/ -v --tb=short || warn "部分测试未通过，不影响启动"
    fi

    step "启动服务"
    echo ""
    info "=============================================="
    info "  启动命令:"
    info "    python backend/server.py"
    info ""
    info "  前端页面: 在浏览器中打开 frontend/index.html"
    info "  API 健康检查: http://127.0.0.1:5000/api/health"
    info "=============================================="
    echo ""

    read -r -p "是否现在启动服务? (y/n, 默认 y): " START_NOW
    if [[ -z "$START_NOW" || "$START_NOW" =~ ^[Yy]$ ]]; then
        info "启动 Flask 服务..."
        SERVER_PORT=${SERVER_PORT:-5000} $PYTHON backend/server.py
    fi
}

# -------------------- Docker 部署 --------------------
deploy_docker() {
    check_deps_docker
    setup_env

    step "构建 Docker 镜像"
    info "正在构建镜像（首次构建可能需要 5-10 分钟）..."
    docker compose build

    step "启动服务"
    FRONTEND_PORT=${FRONTEND_PORT:-8080} \
    SERVER_PORT=${SERVER_PORT:-5000} \
    docker compose up -d

    # 等待就绪
    info "等待服务就绪..."
    for i in $(seq 1 30); do
        if curl -sf http://127.0.0.1:${SERVER_PORT:-5000}/api/health > /dev/null 2>&1; then
            info "后端服务就绪"
            break
        fi
        sleep 2
    done

    echo ""
    info "=============================================="
    info "  部署完成!"
    info "  前端页面: http://127.0.0.1:${FRONTEND_PORT:-8080}"
    info "  API 接口: http://127.0.0.1:${SERVER_PORT:-5000}"
    info ""
    info "  查看日志:    docker compose logs -f backend"
    info "  停止服务:    docker compose down"
    info "  启用 Redis:   docker compose --profile redis up -d"
    info "=============================================="
    echo ""
}

# -------------------- 主入口 --------------------
main() {
    show_banner

    MODE="${1:-}"

    case "$MODE" in
        --help|-h)
            show_help
            ;;
        --docker)
            deploy_docker
            ;;
        --local)
            deploy_local
            ;;
        "")
            # 交互式选择
            echo "请选择部署方式:"
            echo "  1) Docker 部署（推荐，一键启动前后端）"
            echo "  2) 本地部署（直接安装依赖运行）"
            echo ""
            read -r -p "输入 1 或 2 (默认 1): " CHOICE

            if [[ "$CHOICE" == "2" ]]; then
                deploy_local
            else
                deploy_docker
            fi
            ;;
        *)
            error "未知选项: $MODE"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
