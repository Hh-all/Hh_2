# 智能试卷生成系统（RAG + LLM）

这是一个基于检索增强生成（RAG）与大模型（LLM）的智能试卷生成与渲染系统。系统支持向量检索（ChromaDB 回退到内存）、LLM 生成、工作流编排、异步任务与 HTML/PDF 导出，适用于教育场景的试卷/练习自动化生成。

**主要功能**
- 基于知识库的检索增强生成（RAG）
- 可扩展的 LLM 接入（Anthropic/OpenAI 兼容）
- 编排器（Orchestrator）+ Agent 模式，支持复杂工作流与重试
- 异步任务队列（Celery 或本地线程池）
- 模板渲染为 HTML，支持 PDF 导出
- Docker 化部署与监控支持（Prometheus/Grafana）

**技术栈**
- Python 3.11
- Flask（后端 API）
- ChromaDB / In-memory 向量存储
- Celery（可选）/ 本地线程池
- Jinja2 模板 + WeasyPrint（PDF 可选）
- Docker / Docker Compose

**仓库结构（简要）**
- [jiaoshi/](jiaoshi/): 应用代码、Docker 与部署脚本
  - [jiaoshi/backend/](jiaoshi/backend/): 后端实现（生成、检索、编排、渲染）
  - [jiaoshi/requirements.txt](jiaoshi/requirements.txt#L1): Python 依赖
- [docs/](docs/): 项目文档与设计说明
- [data/](data/): 示例与训练数据
- [frontend/](frontend/): 简单的渲染模板与静态页面
- [output/](output/): 生成的 HTML/PDF 输出

**快速开始（本地）**
1. 克隆仓库并进入项目根目录（已完成）。
2. 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r jiaoshi/requirements.txt
```

3. 配置环境变量（示例）：在 `jiaoshi/` 下创建 `.env`，填入 API key 等。参考 `docs/` 中的配置说明。
4. 启动后端（开发模式）：

```bash
python jiaoshi/backend/server.py
```

5. 打开浏览器查看前端页面：`frontend/paper_renderer.html`，或在 `output/` 中查看生成的 `paper.html`。

**使用（Docker / 生产）**
- 使用仓库内的 Dockerfile 与 docker-compose 进行容器化部署：

```bash
# 在 jiaoshi 目录下一键构建并启动
cd jiaoshi
./deploy.sh --docker
# 或直接使用 docker compose
docker compose up -d --build
```

- 监控：使用 `docker-compose.monitoring.yml` 启动 Prometheus/Grafana。

**开发者指南**
- 主要后端入口：[jiaoshi/backend/server.py](jiaoshi/backend/server.py#L1)
- 向量索引与检索：`rag_indexer.py`, `rag_searcher.py`（位于 [jiaoshi/backend/](jiaoshi/backend/)）
- 生成逻辑：`generate_paper.py`
- 渲染：`render_paper.py`（输出 HTML/PDF）
- 异步任务：`celery_app.py`（Celery）与 `async_tasks.py`（线程池）

**部署注意事项**
- 请确保 Secrets（API keys）通过环境变量或密钥管理工具注入，不要提交到仓库。
- ChromaDB 优先作为向量存储；若不可用，系统会回退到内存实现，但语义检索质量可能下降。
- 若使用 Celery，确保 Redis（或其他 broker）已正确配置并运行。

**文档与参考**
- 项目概览与架构：`docs/PROJECT_OVERVIEW.md`
- API 参考与开发指南：`docs/API_REFERENCE.md`、`docs/DEVELOPER_GUIDE.md`

**贡献**
欢迎提交 Issue 与 PR。请在贡献前说明变更目的并尽量包含可复现的测试用例。

**许可证 & 联系**
- 请在仓库根目录补充许可证文件（如 `LICENSE`）。
- 有问题请在 Issues 中留言或联系维护人。

---

README 已生成为项目根的 `README.md`，如需我将其补充更多示例、API 调用示例或添加徽章（CI / coverage），我可以继续完善。
