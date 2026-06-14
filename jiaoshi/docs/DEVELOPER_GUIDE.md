# 智能试卷生成系统 - 开发者指南

> 面向开发者的技术文档。涵盖项目结构、Harness 框架、扩展机制等。

---

## 目录

1. [Harness 工程设计](#1-harness-工程设计)
2. [如何添加新地域](#2-如何添加新地域)
3. [如何扩展知识点](#3-如何扩展知识点)
4. [如何运行测试](#4-如何运行测试)
5. [如何运行评估](#5-如何运行评估)
6. [项目结构速查](#6-项目结构速查)

---

## 1. Harness 工程设计

系统采用 Harness Engineering 四重约束模式：

### 1.1 Agent 角色边界

7 个 Agent，每个只做一件事：

```
ParameterParser → QuestionRetriever → QAGenerator → PaperFormatter
      (解析)           (检索)            (生成)        (格式化)
```

| Agent | 文件 | 输入 | 输出 |
|-------|------|------|------|
| ParameterParser | `agents/parameter_parser_agent.py` | 用户请求 | `/tmp/paper_request.json` |
| QuestionRetriever | `agents/question_retriever_agent.py` | paper_request.json | `/tmp/retrieved_questions.json` |
| QAGenerator | `agents/qa_generator_agent.py` | retrieved + request | `/tmp/generated_questions.json` |
| PaperFormatter | `agents/paper_formatter_agent.py` | generated + request | `/tmp/paper_output.html` |

关键原则：Agent 间**只通过文件传递信息**，禁止依赖对话上下文。下游 Agent 禁止修改上游产物。

### 1.2 状态机

```
IDLE → PARSING → RETRIEVING → GENERATING → FORMATTING → COMPLETED
         │            │            │              │
         ▼            ▼            ▼              ▼
       FAILED       RETRY        RETRY          RETRY
                                   │
                              retry>3 → FAILED
```

实现：`backend/orchestration/state_machine.py`（24 条迁移规则 + JSONL 日志）

### 1.3 护栏规则

10 条护栏规则（`backend/guardrails/rules.yaml`）：

| 规则 | 级别 | 说明 |
|------|:---:|------|
| RULE-001 | BLOCKER | 禁止直接修改 RAG 知识库原始数据 |
| RULE-002 | BLOCKER | 禁止调用未授权外部 API |
| RULE-003 | BLOCKER | 禁止生成敏感内容 |
| RULE-004 | BLOCKER | 每道题必须含 answer + analysis |
| RULE-005 | BLOCKER | 试卷总分一致性 |
| RULE-006 | BLOCKER | 单次检索 ≤ 50 条 |

### 1.4 质量门禁

6 道门禁（`harness/QUALITY_GATES.md`）：

```
Gate 0 (数据就绪) → Gate 1 (完整性) → Gate 2 (合规) → Gate 3 (检索质量) → Gate 4 (生成质量) → Gate 5 (地域适配)
```

---

## 2. 如何添加新地域

### Step 1：注册地域代码

编辑 `data/schema.json`：
```json
"regions": {
  "items": [
    {"code": "beijing", "name": "北京", "syllabus": "人教版"},
    {"code": "shanghai", "name": "上海", "syllabus": "沪教版"},
    {"code": "guangdong", "name": "广东", "syllabus": "粤教版"},
    {"code": "zhejiang", "name": "浙江", "syllabus": "浙教版"}  // 新增
  ]
}
```

### Step 2：创建样式配置

编辑 `backend/paper_styles/style_registry.py`：
```python
ZHEJIANG_STYLE = {
    "template": "zhejiang_template.html",
    "font_family": '"SimSun", serif',
    "header_color": "#1a237e",
    "section_order": [...],
    "score_map": {...},
    "feature_tags": ["浙江", "浙教版", "创新教育"],
}
```

### Step 3：创建 HTML 模板

`backend/paper_styles/templates/zhejiang_template.html`

### Step 4：创建内容适配器

`backend/paper_styles/content_adapters/zhejiang_adapter.py`
```python
class ZhejiangAdapter(BaseContentAdapter):
    def __init__(self):
        super().__init__()
        self.region = "zhejiang"
        self.region_cn = "浙江"

    def adapt_question(self, question, request):
        # 浙江特色题目适配逻辑
        ...

    def get_additional_tags(self, request):
        return ["浙江卷", "浙教版", "创新教育"]
```

### Step 5：注册适配器

编辑 `backend/paper_styles/content_adapters/__init__.py`：
```python
_ADAPTERS = {
    "beijing": BeijingAdapter,
    "shanghai": ShanghaiAdapter,
    "guangdong": GuangdongAdapter,
    "zhejiang": ZhejiangAdapter,  # 新增
}
```

### Step 6：更新护栏规则

在 `backend/guardrails/rules.yaml` 中更新 RULE-010 的 `valid_regions` 列表。

---

## 3. 如何扩展知识点

### 添加新知识点

编辑 `data/knowledge_graph.json`，在当前学科的三级结构中找到合适的模块和子类，追加知识点名称。

### 添加新学科

在 `data/knowledge_graph.json` 中添加学科顶层键：

```json
"信息技术": {
  "description": "...",
  "初中": {
    "编程基础": {
      "子类": {
        "Python基础": ["变量与数据类型", "条件判断", "循环语句"]
      }
    }
  }
}
```

同步更新以下枚举：
- `data/schema.json` → `subjects.items`
- `data/schema/unified_question.json` → `subject.enum`
- `backend/agents/parameter_parser_agent.py` → `VALID_SUBJECTS`

### 知识图谱查询

```python
from backend.knowledge.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph()

# 前置知识点
kg.get_prerequisites("一元二次方程")  # → ["一元一次方程", ...]

# 关联知识点
kg.get_related("一次函数")           # → ["正比例函数", "二次函数", ...]

# 完整学习路径
kg.get_knowledge_path("一元二次方程")  # → 递归展开所有前置
```

---

## 4. 如何运行测试

### 4.1 完整测试套件

```bash
# 运行所有测试
pytest tests/ -v --tb=short

# 跳过网络依赖测试
pytest tests/ -v -k "not rag_ready and not flask_client"

# 仅运行集成测试
pytest tests/integration/ -v

# 运行覆盖度报告
pytest tests/ --cov=backend --cov-report=html
```

### 4.2 测试分类

| 目录 | 测试类型 | 用例数 |
|------|---------|:----:|
| `tests/test_api_endpoints.py` | API 端点 | 30+ |
| `tests/test_rag_search.py` | RAG 检索 | 10+ |
| `tests/integration/test_full_pipeline.py` | 全链路集成 | 30+ |
| `tests/integration/test_regional_adaptation.py` | 地域适配 | 25+ |
| `tests/validators/schema_validator.py` | Schema 校验 | 6 层 |
| `tests/validators/duplicate_detector.py` | 重复检测 | 双模式 |
| `tests/rag_evaluation/` | Ragas 评估 | 100 样本 |

### 4.3 CI 脚本

```bash
# 完整 CI 流程（含环境检查 + 测试 + RAG 评估 + 门禁）
bash scripts/ci/run_tests.sh

# 快速模式（首次失败即停止）
bash scripts/ci/run_tests.sh --quick
```

---

## 5. 如何运行评估

### RAG 检索与生成质量评估

```bash
# 构建 Golden Set + 评估 + HTML 报告
python tests/rag_evaluation/run_evals.py

# 使用已有 Golden Set + CI 模式
python tests/rag_evaluation/run_evals.py --golden-set tests/rag_evaluation/golden_set.json --ci

# Ragas 模式（需安装 ragas + 配置 LLM Key）
python tests/rag_evaluation/run_evals.py --use-ragas
```

评估指标：
- `context_recall` — 检索召回率（≥ 0.55）
- `context_precision` — 检索精确率（≥ 0.55）
- `faithfulness` — 答案忠实度（≥ 0.55）
- `answer_relevancy` — 答案相关性（≥ 0.50）

报告输出：
- HTML 仪表盘：`test_reports/rag_report.html`
- JSON 机器可读：`test_reports/rag_report.json`

---

## 6. 项目结构速查

```
backend/
├── server.py               # Flask API (端口 5000)
├── orchestrator.py          # 总控编排（状态机驱动）
├── rag_searcher.py          # RAG 检索（地域过滤 + @trace）
├── rag_indexer.py           # ChromaDB 索引构建
├── celery_app.py            # Celery 异步任务（Redis backend）
├── multi_cache.py           # 多级缓存 (Redis L1 + 内存 L2)
│
├── agents/                  # Sub-Agent
├── knowledge/               # 知识库 (向量存储/地域过滤/知识图谱)
├── paper_styles/            # 地域样式 (style_registry/templates/adapters)
├── orchestration/           # 编排引擎 (state_machine)
├── guardrails/               # 护栏系统 (rules.yaml + checker)
└── tracing/                 # LangSmith 追踪

harness/                     # Harness 工程规范
├── AGENTS.md                # Agent 角色定义
├── RULES.md                 # 护栏规则
├── SPECS.md                 # 数据契约
├── WORKFLOW.md              # 状态机流程
├── SKILLS.md                # Skill 模板
└── QUALITY_GATES.md         # 质量门禁

data/
├── schema.json              # 系统 Schema (科目/年级/地域/题型)
├── schema/unified_question.json  # 统一试题模型
├── knowledge_graph.json     # 三级知识点图谱 (435 节点)
├── processed/questions.jsonl     # 清洗后题库
└── chroma_db/               # ChromaDB 向量存储

tests/
├── conftest.py              # 30+ 共享 fixtures
├── test_api_endpoints.py    # API 端点测试
├── integration/             # 端到端集成测试
├── validators/              # Schema + 重复检测
└── rag_evaluation/          # Ragas 评估
```
