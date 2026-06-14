# 项目实现概述：智能试卷生成系统

> 本文档由自动化代码阅读生成，概述项目的架构、实现步骤、关键模块和常见面试问题与参考答案。

**目录**
- 项目概述
- 技术栈
- 系统架构与数据流
- 逐步实现说明（按模块）
- 部署与运行（包含 Docker 说明）
- 常见面试问答（假设问题 + 参考答案）

---

**项目概述**

这是一个基于 RAG（检索增强生成）与 LLM 的智能试卷生成系统，功能包括：题目检索（向量检索）、基于 LLM 的题目生成、对生成结果的编排与格式化、异步任务支持与渲染为 HTML/PDF。

---

**技术栈**

- Python 3.11
- Flask（后端 API） — [jiaoshi/backend/server.py](jiaoshi/backend/server.py)
- Celery / 本地线程池（异步任务） — [jiaoshi/backend/celery_app.py](jiaoshi/backend/celery_app.py)、[jiaoshi/backend/async_tasks.py](jiaoshi/backend/async_tasks.py)
- 向量检索：ChromaDB（优先）或 TF-IDF + 内存回退 — [jiaoshi/backend/rag_indexer.py](jiaoshi/backend/rag_indexer.py)、[jiaoshi/backend/rag_searcher.py](jiaoshi/backend/rag_searcher.py)
- LLM 集成：Anthropic Claude / OpenAI 兼容（DeepSeek） — [jiaoshi/backend/generate_paper.py](jiaoshi/backend/generate_paper.py)
- 模板渲染与导出：Jinja2 + WeasyPrint（PDF 可选） — [jiaoshi/backend/render_paper.py](jiaoshi/backend/render_paper.py)
- 缓存：内存 TTL 缓存（简单实现） — [jiaoshi/backend/cache.py](jiaoshi/backend/cache.py)
- 编排器（状态机）与 Agent 模式 — [jiaoshi/backend/orchestrator.py](jiaoshi/backend/orchestrator.py) 与 `backend/agents/` 目录
- 容器化与编排：Dockerfile / docker-compose — [jiaoshi/Dockerfile](jiaoshi/Dockerfile)、[jiaoshi/Dockerfile.production](jiaoshi/Dockerfile.production)、[jiaoshi/docker-compose.yml](jiaoshi/docker-compose.yml)

---

**系统架构与数据流（高层）**

1. 前端（静态页面）由 Nginx 提供，调用后端 REST API。[jiaoshi/docker-compose.yml](jiaoshi/docker-compose.yml)
2. 后端 Flask 接受请求，优先同步调用 LLM 生成；对于耗时或批量请求可提交到异步队列（Celery 或本地线程池）。[jiaoshi/backend/server.py](jiaoshi/backend/server.py)
3. 生成流程：RAG 检索（ChromaDB 或内存）→ 将检索到的参考作为 prompt 传给 LLM → 解析并返回 JSON 题目 → 若 LLM 失败则回退为 RAG 改写方案。
   - 向量索引构建：[jiaoshi/backend/rag_indexer.py](jiaoshi/backend/rag_indexer.py)
   - 检索逻辑与地域过滤：[jiaoshi/backend/rag_searcher.py](jiaoshi/backend/rag_searcher.py)
   - 生成逻辑（prompt 构建、LLM 调用、回退）：[jiaoshi/backend/generate_paper.py](jiaoshi/backend/generate_paper.py)
4. 编排器 `Orchestrator` 使用一系列 Agent（参数解析、检索、生成、格式化）严格控制执行流程与重试；记录 trace 与日志。[jiaoshi/backend/orchestrator.py](jiaoshi/backend/orchestrator.py)
5. 渲染模块将题目渲染为 HTML 并可导出为 PDF。[jiaoshi/backend/render_paper.py](jiaoshi/backend/render_paper.py)
6. 持久化与卷：使用 Docker volumes 保存数据、日志、输出与 Redis 数据以保证容器重建后的持久性。[jiaoshi/docker-compose.yml](jiaoshi/docker-compose.yml)

---

**逐步实现说明（按代码模块）**

1) 启动与配置
- 脚本入口：`jiaoshi/deploy.sh` 提供本地或 Docker 一键部署，并负责创建 `backend/.env`、安装依赖或执行 `docker compose up`。
- Docker 镜像：`jiaoshi/Dockerfile`（开发/构建）与 `jiaoshi/Dockerfile.production`（生产优化，多阶段构建、非 root 用户、健康检查）。

2) 向量索引（构建与存储）
- 实现：`rag_indexer.build_index()` 读取 `data/training_data.json`（或 JSONL），构建文本表示并尝试加载 `sentence-transformers` 模型生成嵌入。
- 存储：优先写入 ChromaDB；若不可用，则使用 `InMemoryVectorStore` 并序列化到 `data/vector_store.pkl`。（实现见 [jiaoshi/backend/rag_indexer.py](jiaoshi/backend/rag_indexer.py)）

3) 检索（RAG）
- `rag_searcher.init_searcher()` 会尝试连接 ChromaDB 或加载内存回退的向量存储；并初始化嵌入方法（SentenceTransformer 或 TF-IDF）。
- `rag_searcher.search()` 根据 query 向量计算相似度，支持元数据过滤、地域过滤与后置降级策略（regional filter）。（见 [jiaoshi/backend/rag_searcher.py](jiaoshi/backend/rag_searcher.py)）

4) 生成（LLM 集成 + 回退）
- 构建 Prompt：`generate_paper._build_system_prompt` 与 `_build_user_prompt` 将知识点、题型分布、参考题目合并成 LLM prompt。
- LLM 调用：支持 Anthropic Claude（env: `ANTHROPIC_API_KEY`）或 DeepSeek/OpenAI 兼容接口。
- 回退策略：若 LLM 未返回有效 JSON，使用 `_fallback_generate` 从 RAG 检索结果改写题目。
- 入口函数：`generate_questions(parameters)` 返回题目列表与来源标记。

5) 编排器 & Agents
- `Orchestrator` (状态机) 负责整个工作流：参数验证 → 检索 → 生成 → 格式化；包括重试策略、trace 写入与护栏（Guardrails）检查。
- Agents 负责各子任务（见 `backend/agents/`）：参数解析、检索整理、QA 生成、格式化输出。

6) 异步任务与缓存
- 两种异步方案：生产使用 Celery（`celery_app.py`），本地/轻量使用线程池（`async_tasks.py`）。API 提供同步 `/api/generate_paper` 与异步 `/api/generate_paper_async`。
- 缓存：内存 TTL 缓存 `cache.py` 用于缓存检索或试卷结果，避免重复计算。

7) 渲染与导出
- 使用 Jinja2 将题目数据渲染为 HTML（模板位于 `frontend/`），并尝试用 WeasyPrint 导出 PDF（若未安装则仅生成 HTML）。实现见 [jiaoshi/backend/render_paper.py](jiaoshi/backend/render_paper.py)。

8) 监控与观测
- `docker-compose.monitoring.yml` 提供 Prometheus 与 Grafana，用于指标与仪表盘展示。

---

**部署简要**

- 推荐：使用 Docker Compose（项目根 `jiaoshi/deploy.sh --docker`）一键构建与启动。
- 本地调试：`python backend/server.py` 或使用脚本 `deploy.sh --local` 安装依赖并运行。详见 [jiaoshi/deploy.sh](jiaoshi/deploy.sh)。

---

**假设的面试官问题与参考答案**

Q1: 请描述本系统的整体架构与主要组件，它们如何协同工作？

A1: 系统由前端静态页面（Nginx）、后端 Flask API、检索（RAG）、生成（LLM）、编排器（Orchestrator）、异步任务队列（Celery/线程池）与持久存储（命名卷、Redis）组成。请求进入 Flask，由 Orchestrator/生成模块驱动 RAG 检索参考题并调用 LLM 生成；对于耗时任务可走异步队列，最后使用 Jinja2 渲染为 HTML/PDF。向量索引使用 ChromaDB（优先），回退为内存实现。

Q2: RAG 的作用是什么？为什么要把检索和生成结合？

A2: RAG（检索增强生成）先从题库检索相关参考题目，再把这些参考作为 LLM 的上下文，能提高生成的准确性与域内一致性，减少 hallucination，并能在 LLM 不能生成时提供回退改写方案。

Q3: 如何保证异步任务与结果可以被前端查询？

A3: 系统提供异步任务接口 `/api/generate_paper_async`，后端提交到线程池或 Celery，并返回 `task_id`。前端轮询 `/api/task_result/<task_id>` 来查询状态和结果。Celery 方案使用 Redis 作为 broker/result 后端；本地线程池用内存管理任务状态。

Q4: 如果 ChromaDB 不可用，系统如何运行？

A4: `rag_indexer` 与 `rag_searcher` 支持回退机制：会构建 TF-IDF 向量或使用 `InMemoryVectorStore`，并将数据序列化到 `data/vector_store.pkl`，保证检索功能仍可用（但语义质量较低）。

Q5: 如何扩展 worker 的并发能力？

A5: 使用 `docker compose up -d --scale celery_worker=4` 或调整 `celery` 启动参数中的 `--concurrency`。在生产可用 Kubernetes/Swarm 扩展副本并配合水平扩展 Redis。

Q6: LLM 返回的 JSON 解析失败怎么办？

A6: 系统在 `_call_anthropic`/`_call_deepseek` 中尝试提取三引号包裹的 JSON，并捕获 `json.JSONDecodeError`。若解析失败或未返回，`generate_paper` 会启动本地回退 `_fallback_generate`，从检索结果改写题目保证可用输出。

Q7: 如何保证生成题目的原创性和版权问题？

A7: 当前策略主要是基于 prompt 要求“原创”与回退改写（对检索题目做数值/表述修改）。要更严格可加入相似度检测模块，或限制直接引用原题并在回退时更强烈改写。

Q8: 安全/护栏（guardrails）如何实现？

A8: 项目包含 `backend/guardrails/guardrail_checker.py` 和规则 `rules.yaml`，Orchestrator 在前置与后置阶段调用护栏检查器，拦截不合规输入或对生成结果进行策略检测（如敏感内容、超出配额等）。

---

**下一步建议**

- 在 `README.md` 或 `docs/` 中加入快速启动示例（带 `.env` 样例），以及生产部署注意事项（Secrets 管理、资源限制）。
- 添加端到端测试与集成测试覆盖关键路径（生成 → 渲染 → 导出）。
- 若迁移到 Kubernetes，提供 `k8s/` 部署清单和持久化卷说明。

---

文档生成结束。已基于代码逐文件读取与分析生成该说明，如需更详细的代码级流程图或补充面试题，请告知要侧重的方向（算法、架构、安全或部署）。
