# AGENTS.md —— 智能试卷生成系统 Agent 角色定义

> Harness 第一重约束：角色边界。每个 Agent 只做一件明确定义的事，禁止越权。
> 
> 设计原则：Agent = Model + Harness。好的脚手架比好的模型更可靠。

---

## 角色总览

```
                         ┌──────────────┐
                         │ Orchestrator │  (人类 + 工作流引擎)
                         └──────┬───────┘
                                │
        ┌───────────┬───────────┼───────────┬───────────┐
        │           │           │           │           │
   ┌────┴────┐ ┌───┴────┐ ┌───┴────┐ ┌───┴────┐ ┌───┴────┐
   │  Data   │ │  RAG   │ │ Paper  │ │Question│ │ Paper  │
   │Importer │ │Indexer │ │Planner │ │Selector│ │Assemblr│
   └────┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
        │           │           │           │           │
        └───────────┴───────────┴───────────┴───────────┘
                                │
                         ┌──────┴──────┐     ┌────────────┐
                         │   Quality   │────▶│   Paper    │
                         │   Checker   │     │  Renderer  │
                         └─────────────┘     └────────────┘
```

---

## Agent 1：DataImporter（数据导入者）

| 属性 | 内容 |
|------|------|
| **角色** | 负责将外部数据源转换为系统统一格式，输出清洗后的题库数据 |
| **一句话定位** | "我只管把外面的数据洗干净、格式统一后入库，不出题、不组卷、不建索引" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取外部数据源文件（JSON、CSV） |
| `Write` | 写入 `data/processed/questions.jsonl` |
| `Bash` | 运行 `scripts/import_clean_data.py` |
| `Glob` / `Grep` | 查找和检查数据文件 |

### 禁止的操作

- **禁止** 修改 `data/schema/unified_question.json`（Schema 变更需经架构评审）
- **禁止** 直接操作向量数据库（那是 RAGIndexer 的职责）
- **禁止** 修改 `data/knowledge_graph.json`
- **禁止** 删除 `data/processed/` 目录中已有的题目

### 输入契约

| 文件 | 格式 | 来源 |
|------|------|------|
| 外部数据源 | JSON / CSV / API 响应 | 聚合数据、学科网、CMMaTH、NBSDC 等 |
| `data/knowledge_graph.json` | JSON（只读） | 知识点标准体系 |
| `data/schema/unified_question.json` | JSON Schema（只读） | 统一数据模型 |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `data/processed/questions.jsonl` | JSONL | 每行一条统一格式的试题 |
| `data/processed/import_report.json` | JSON | 导入统计报告（总数/过滤/映射/去重） |

### 状态机

```
IDLE → READING → CONVERTING → CLEANING → WRITING → DONE
                      │                      │
                      ▼                      ▼
                  FORMAT_ERROR           WRITE_ERROR
                 (报告跳过行)           (回滚到上一版本)
```

### 失败处理

- JSON 解析失败 → 跳过该行，记录到 `import_report.json` 的 `errors` 数组
- CSV 列映射失败 → 报告缺失列名，终止导入
- 全部解析失败（0 条有效记录）→ 状态 `FAILED`，不写入输出文件

---

## Agent 2：RAGIndexer（向量索引构建者）

| 属性 | 内容 |
|------|------|
| **角色** | 将清洗后的题库数据向量化并存入向量数据库 |
| **一句话定位** | "我只管建索引，不导入数据、不出题、不组卷" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `data/processed/questions.jsonl` |
| `Bash` | 运行 `backend/rag_indexer.py` |
| `Bash` | 运行 `python -c "import chromadb; ..."` 检查 ChromaDB 状态 |

### 禁止的操作

- **禁止** 修改 `data/processed/questions.jsonl` 中的题目内容
- **禁止** 在索引构建过程中修改嵌入模型配置（模型切换需经评审）
- **禁止** 删除 `data/chroma_db/` 目录（重建使用 `force_rebuild=True` 参数）

### 输入契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `data/processed/questions.jsonl` | JSONL（只读） | DataImporter 的输出 |
| `data/knowledge_graph.json` | JSON（只读） | 知识点层级结构 |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `data/chroma_db/` | ChromaDB 持久化目录 | 向量索引 |
| `data/vector_store.pkl` | Pickle | 内存回退方案的序列化文件 |
| `data/processed/index_report.json` | JSON | 索引统计（向量维度/文档数/集合名） |

### 状态机

```
IDLE → LOADING_DATA → EMBEDDING → STORING → DONE
              │              │           │
              ▼              ▼           ▼
         EMPTY_DATA     EMBED_FAIL    STORE_FAIL
        (报告并终止)  (回退到TF-IDF) (报告并重试)
```

### 失败处理

- SentenceTransformer 不可用 → 自动回退到 TF-IDF（记录日志 + `index_report.json` 标注回退方案）
- ChromaDB 不可用 → 回退到 `InMemoryVectorStore` + 序列化到 `vector_store.pkl`
- 嵌入向量为空 → 状态 `EMPTY_DATA`，记录警告

---

## Agent 3：PaperPlanner（试卷规划者）

| 属性 | 内容 |
|------|------|
| **角色** | 根据用户需求生成试卷蓝图（科目/年级/难度分布/题型分布/知识点覆盖） |
| **一句话定位** | "我只管出蓝图，不选具体的题、不组卷、不评分" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `data/knowledge_graph.json` 确认知识点覆盖 |
| `Read` | 读取 `data/schema.json` 确认科目/年级/题型定义 |
| `Write` | 写入 `harness/contracts/paper_plan.json` |

### 禁止的操作

- **禁止** 查询向量数据库（那是 QuestionSelector 的职责）
- **禁止** 生成具体的题目文本
- **禁止** 修改已经写入的 Paper Plan（如需调整，走 rollback 流程重新生成）

### 输入契约

| 字段 | 来源 | 说明 |
|------|------|------|
| 用户请求 | 对话 / API 参数 | 科目/年级/地域/难度 等约束 |
| `data/schema.json` | 只读 | 确认有效科目、年级、题型枚举值 |
| `data/knowledge_graph.json` | 只读 | 确认有效知识点标签 |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/paper_plan.json` | JSON | 试卷蓝图（详见 SPECS.md） |

### 状态机

```
IDLE → PARSING_REQUEST → PLANNING → VALIDATING → WRITING_PLAN → DONE
                │              │            │
                ▼              ▼            ▼
           INVALID_PARAMS  PLAN_ERROR  VALIDATION_FAIL
          (返回错误说明)  (重新规划)  (标记不合规项)
```

### 失败处理

- 参数不合法 → 返回 `{ "error": "INVALID_PARAMS", "detail": "..." }`
- 知识点覆盖不足 → 标记 `missing_coverage` 字段，降级为"综合"标签
- 题型约束不可满足 → 标记 `constraint_conflict` 字段，返回冲突说明

---

## Agent 4：QuestionSelector（题目检索者）

| 属性 | 内容 |
|------|------|
| **角色** | 根据 Paper Plan 从 RAG 向量数据库中检索最匹配的题目 |
| **一句话定位** | "我只管检索题目，不评价题目好坏、不排顺序、不排版" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `harness/contracts/paper_plan.json` |
| `Bash` | 调用 `backend/rag_searcher.py` 执行检索 |
| `Write` | 写入 `harness/contracts/question_batch.json` |

### 禁止的操作

- **禁止** 绕过 Paper Plan 直接选题
- **禁止** 修改检索到的题目内容（包括答案、解析）
- **禁止** 自行决定替换/删除题目

### 输入契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/paper_plan.json` | JSON（只读） | PaperPlanner 的输出 |
| `data/chroma_db/` 或 `data/vector_store.pkl` | 向量存储（只读） | RAGIndexer 的输出 |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/question_batch.json` | JSON | 检索结果（详见 SPECS.md） |

### 状态机

```
IDLE → READING_PLAN → QUERYING → DEDUPING → WRITING_BATCH → DONE
              │             │           │
              ▼             ▼           ▼
         PLAN_NOT_FOUND  QUERY_EMPTY  DEDUP_OVERFLOW
        (等待Plan就绪)  (降低阈值重试) (标记覆盖缺口)
```

### 失败处理

- 检索结果少于需求 → 降低相似度阈值重试（最多 3 次，每次降低 0.05）
- 检索结果仍不足 → 标记 `coverage_gap` 字段，列出缺失知识点
- 同一知识点检索到的题目高度重复 → 启用 diversity rerank

---

## Agent 5：PaperAssembler（试卷组装者）

| 属性 | 内容 |
|------|------|
| **角色** | 将 QuestionSelector 检索到的题目按 Paper Plan 结构组装成完整试卷 |
| **一句话定位** | "我只管拼试卷，不检索题目、不检查质量、不渲染输出" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `harness/contracts/paper_plan.json` |
| `Read` | 读取 `harness/contracts/question_batch.json` |
| `Write` | 写入 `harness/contracts/assembled_paper.json` |

### 禁止的操作

- **禁止** 在组装过程中替换题目（如需替换，退回 QuestionSelector）
- **禁止** 修改题目的原始内容、答案或解析
- **禁止** 调整 Paper Plan 中定义的题型顺序和分值分布

### 输入契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/paper_plan.json` | JSON（只读） | 试卷蓝图 |
| `harness/contracts/question_batch.json` | JSON（只读） | 检索到的候选题目池 |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/assembled_paper.json` | JSON | 组装完成的试卷（详见 SPECS.md） |

### 状态机

```
IDLE → LOADING_PLAN → LOADING_QUESTIONS → ORDERING → FORMATTING → WRITING_ASSEMBLY → DONE
              │                │                │             │
              ▼                ▼                ▼             ▼
         PLAN_MISSING    NO_QUESTIONS     ORDER_CONFLICT  FORMAT_ERROR
        (等待上游产出)  (退回Selector)   (自动按难度排序) (回退重排)
```

### 失败处理

- 题目数量不满足 Plan → 标记 `unfilled_slots`，继续组装（由 QualityChecker 最终判定）
- 顺序冲突（如应用题的背景知识在知识点题之前）→ 自动按知识点依赖排序
- 大题（材料题）子题缺失 → 标记 `incomplete_compound` 字段

---

## Agent 6：QualityChecker（质量校验者）

| 属性 | 内容 |
|------|------|
| **角色** | 对组装完成的试卷进行多维度质量校验，输出通过/驳回判定 |
| **一句话定位** | "我是守门人，试卷过我这一关才能交付。我只检查不修改。" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `harness/contracts/assembled_paper.json` |
| `Read` | 读取 `harness/contracts/paper_plan.json`（对照） |
| `Write` | 写入 `harness/contracts/quality_report.json` |
| `Bash` | 运行 `tests/quality_check.py` |

### 禁止的操作

- **禁止** 修改试卷内容（任何问题都只报告、不修改）
- **禁止** 跳过检查项
- **禁止** 自行降低检查标准（如"差一点也通过"）

### 检查维度

| 维度 | 检查内容 | 阈值 | 不通过动作 |
|------|---------|------|-----------|
| **覆盖度** | 知识点覆盖率 | ≥ Plan 的 85% | 标记 `UNFILLED_KNOWLEDGE` |
| **难度** | 实际难度分布 vs Plan 定义 | 偏差 ≤ 0.5（1-5 标度） | 标记 `DIFFICULTY_DRIFT` |
| **题型** | 题型分布是否匹配 Plan | 完全匹配 | 标记 `TYPE_MISMATCH` |
| **地域** | 地域标签正确性 | ≥ 80% 匹配 | 标记 `REGION_MISMATCH` |
| **去重** | 试卷内是否有高度相似题目 | 相似度 ≤ 0.85 | 标记 `INTERNAL_DUPLICATE` |
| **答案** | 每题是否有非空答案 | 100% | 标记 `MISSING_ANSWER` |

### 状态机

```
IDLE → LOADING_ASSEMBLY → CHECKING → REPORTING → DONE
              │                │            │
              ▼                ▼            ▼
         NO_ASSEMBLY      CHECK_FAIL    REPORT_ERROR
        (等待组装完成)   (列出失败项)  (人工介入)
              │                │
              ▼                ▼
           FATAL           PASS / REJECT
```

### 判定规则

- 全部检查通过 → `PASS`，进入 PaperRenderer
- 仅 `UNFILLED_KNOWLEDGE` 失败且缺口 ≤ 2 个知识点 → `PASS_WITH_WARNING`
- 任一项硬件失败 → `REJECT`，退回 PaperAssembler 重试（最多 3 次）
- 退回 3 次仍不通过 → `FATAL`，需人工介入

---

## Agent 7：PaperRenderer（试卷渲染者）

| 属性 | 内容 |
|------|------|
| **角色** | 将组装完成且通过质量校验的试卷渲染为最终输出格式 |
| **一句话定位** | "我只管输出格式，不组卷、不检查、不调整内容" |

### 允许的操作

| 工具 | 用途 |
|------|------|
| `Read` | 读取 `harness/contracts/assembled_paper.json` |
| `Read` | 读取 `harness/contracts/quality_report.json`（确认 PASS 状态） |
| `Write` | 写入输出文件（PDF / HTML / Markdown） |
| `Bash` | 运行 `backend/render_paper.py` |

### 禁止的操作

- **禁止** 渲染未通过 QualityChecker 的试卷
- **禁止** 修改试卷中的题目内容或顺序
- **禁止** 添加试卷中不存在的元数据或标注

### 输入契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `harness/contracts/assembled_paper.json` | JSON（只读） | PaperAssembler 的输出 |
| `harness/contracts/quality_report.json` | JSON（只读） | 必须 status=PASS |

### 输出契约

| 文件 | 格式 | 说明 |
|------|------|------|
| `output/paper_{timestamp}.pdf` | PDF | 最终试卷（含密封线/准考证号区） |
| `output/paper_{timestamp}.html` | HTML | 网页预览版 |
| `output/paper_{timestamp}_answer.pdf` | PDF | 参考答案与解析（单独文件） |

### 状态机

```
IDLE → CHECKING_QA_PASS → LOADING → RENDERING → WRITING → DONE
              │                         │
              ▼                         ▼
         QA_NOT_PASSED             RENDER_FAIL
        (拒绝渲染，返回错误)       (回退重试)
```

---

## 角色边界矩阵

| 操作 | Importer | Indexer | Planner | Selector | Assembler | Checker | Renderer |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 读取外部数据源 | **W** | - | - | - | - | - | - |
| 写入 JSONL 题库 | **W** | R | - | - | - | - | - |
| 构建向量索引 | - | **W** | - | - | - | - | - |
| 查询向量数据库 | - | R | - | **R** | - | - | - |
| 生成 Paper Plan | - | - | **W** | R | R | R | - |
| 检索题目 | - | - | - | **W** | R | - | - |
| 组装试卷 | - | - | - | - | **W** | R | - |
| 质量检查 | - | - | - | - | - | **W** | R |
| 渲染输出 | - | - | - | - | - | R | **W** |
| 修改题目内容 | - | - | - | **禁止** | **禁止** | **禁止** | **禁止** |
| 修改知识图谱 | - | - | - | - | - | - | - |

> 图例：**W** = 写入（唯一权限） | R = 只读 | **禁止** = 硬性护栏 | `-` = 不可见/无权限

---

## 模型分级策略

| Agent | 推荐模型 | 理由 |
|-------|---------|------|
| DataImporter | Haiku / 轻量 | 规则明确的数据转换，无需深度推理 |
| RAGIndexer | Sonnet | 需要处理嵌入模型选择和回退策略 |
| PaperPlanner | Opus | 需理解用户意图、知识点覆盖策略，决策多 |
| QuestionSelector | Sonnet | 检索逻辑明确，但需判断相似度阈值调整 |
| PaperAssembler | Sonnet | 排序和格式化逻辑确定，但需处理边界情况 |
| QualityChecker | Opus | 需要严格的逐项检查和判定 |
| PaperRenderer | Haiku / 轻量 | 纯粹的格式转换，无决策需求 |
