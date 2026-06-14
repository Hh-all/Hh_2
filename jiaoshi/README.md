# 智能试卷生成系统 (Smart Exam Generator)

## 项目目标

基于 RAG（检索增强生成）的多地域智能试卷生成系统。系统能够根据用户指定的地域、学科、难度等参数，从知识库中检索相关内容，结合大语言模型自动生成高质量、符合当地教学大纲的试卷。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.10+ / Flask（推荐）或 FastAPI |
| 向量数据库 | ChromaDB |
| LLM 集成 | OpenAI API / 兼容接口 |
| 前端 | HTML + JavaScript（原生或 Vue.js） |
| 配置管理 | YAML |
| 数据存储 | JSON / SQLite（本地开发） |

## 目录结构

```
.
├── backend/          # 后端服务（API、RAG 引擎、LLM 调用）
├── frontend/         # 前端页面（用户交互界面）
├── data/             # 知识库文档、题库、向量存储
├── config/           # 配置文件
├── tests/            # 测试脚本
├── requirements.txt  # Python 依赖
├── config/config.yaml # 主配置文件
└── README.md
```

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 编辑配置文件
cp config/config.yaml config/config.local.yaml
# 按需修改 config/config.local.yaml 中的 API Key、地域等配置

# 3. 启动后端服务
cd backend && python app.py
```
