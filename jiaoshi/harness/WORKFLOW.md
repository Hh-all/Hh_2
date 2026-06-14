# WORKFLOW.md —— 试卷生成状态机流程

> Harness 第四重约束：状态机。定义流程走向和所有异常路径，防止跳步、死循环和静默失败。
>
> 设计原则：每个状态有明确的进入条件、离开条件和异常出口。不存在"隐式"状态转移。

---

## 一、顶层状态机

```
                              ┌─────────┐
                              │  IDLE   │
                              └────┬────┘
                                   │ 用户发起请求
                                   ▼
                         ┌─────────────────┐
                         │  VALIDATING     │──▶ INVALID_REQUEST ──▶ 返回错误
                         │  _REQUEST       │
                         └────────┬────────┘
                                  │ 参数合法
                                  ▼
                         ┌─────────────────┐
                         │  PLANNING       │──▶ PLAN_FAILED ──▶ retry≤3?
                         │  (PaperPlanner) │        │              │
                         └────────┬────────┘        │         ┌────┴────┐
                                  │ Plan 就绪        └────────▶│  FATAL  │
                                  ▼                            └─────────┘
                         ┌─────────────────┐
                         │  SELECTING      │──▶ COVERAGE_GAP ──▶ retry≤3?
                         │ (QuestionSelect)│        │               │
                         └────────┬────────┘        │          ┌────┴────┐
                                  │ 题目就绪          └─────────▶│  FATAL  │
                                  ▼                             └─────────┘
                         ┌─────────────────┐
                         │  ASSEMBLING     │──▶ ASSEMBLY_FAILED ──▶ retry≤3?
                         │ (PaperAssembler)│        │                 │
                         └────────┬────────┘        │            ┌────┴────┐
                                  │ 组装完成         └───────────▶│  FATAL  │
                                  ▼                               └─────────┘
                         ┌─────────────────┐
                         │  CHECKING       │──▶ REJECT ──▶ 退回 ASSEMBLING
                         │(QualityChecker) │        │        (retry_count++)
                         └────────┬────────┘        │
                                  │                 ├── retry≥3 → FATAL
                         ┌────────┴────────┐        │
                         │  PASS / PASS    │        ▼
                         │  _WITH_WARNING  │    ┌─────────┐
                         └────────┬────────┘    │  FATAL  │──▶ 人工介入
                                  │             └─────────┘
                                  ▼
                         ┌─────────────────┐
                         │  RENDERING      │──▶ RENDER_FAILED ──▶ retry≤2?
                         │ (PaperRenderer) │        │               │
                         └────────┬────────┘        │          ┌────┴────┐
                                  │                  └─────────▶│  报错   │
                                  ▼                             └─────────┘
                         ┌─────────────────┐
                         │  DONE           │
                         └─────────────────┘
```

---

## 二、状态详解

### S1：IDLE

| 属性 | 内容 |
|------|------|
| **描述** | 系统空闲，等待用户请求 |
| **进入条件** | 系统启动 / 上一次流程完成 |
| **触发事件** | 用户通过 API / CLI 提交试卷生成请求 |
| **离开动作** | 解析请求参数 → 进入 VALIDATING_REQUEST |

### S2：VALIDATING_REQUEST

| 属性 | 内容 |
|------|------|
| **描述** | 验证用户请求参数的合法性 |
| **负责 Agent** | Orchestrator（无独立 Agent，由工作流引擎执行） |
| **检查项** | 科目是否在 `schema.json` 枚举中、年级范围是否合法、难度分布和是否为 1.0、题型是否在枚举中 |
| **正常出口** | 所有参数合法 → PLANNING |
| **异常出口** | 参数不合法 → INVALID_REQUEST → 返回 `{ "error": "INVALID_PARAMS", "detail": [...] }` |

```
VALIDATING_REQUEST:
  check_subject(subjects)           → valid / INVALID_SUBJECT
  check_grade(grade)                → valid / INVALID_GRADE
  check_difficulty(distribution)    → valid / INVALID_DIFFICULTY (sum must = 1.0)
  check_question_types(types)       → valid / INVALID_TYPE
  all_checks_passed                 → → → PLANNING
  any_check_failed                  → → → INVALID_REQUEST
```

### S3：PLANNING

| 属性 | 内容 |
|------|------|
| **描述** | PaperPlanner 根据用户请求生成试卷蓝图 |
| **负责 Agent** | PaperPlanner |
| **输入** | 已验证的用户请求参数 + `data/schema.json` + `data/knowledge_graph.json` |
| **输出** | `harness/contracts/paper_plan.json` |
| **正常出口** | Plan 写入成功 → SELECTING |
| **异常出口** | 知识点覆盖不足 → 标记 `missing_coverage` → 仍可进入 SELECTING（非阻断） |
| **异常出口** | 致命错误（3次重试均失败） → FATAL |
| **重试策略** | 每次重试降低一个约束维度（如放宽地域限制 → 放宽难度范围 → 放宽题型） |

```
PLANNING:
  generate_plan(request_params)     → paper_plan
  validate_plan_against_schema()    → valid / INVALID_PLAN
  check_knowledge_coverage()        → sufficient / insufficient (warn, continue)
  write_plan(paper_plan)            → success / FAILED
  success                           → → → SELECTING
  FAILED, retry_count < 3           → retry PLANNING
  FAILED, retry_count >= 3          → → → FATAL
```

### S4：SELECTING

| 属性 | 内容 |
|------|------|
| **描述** | QuestionSelector 根据 Paper Plan 从 RAG 检索题目 |
| **负责 Agent** | QuestionSelector |
| **输入** | `harness/contracts/paper_plan.json` + 向量数据库 |
| **输出** | `harness/contracts/question_batch.json` |
| **正常出口** | 所有 section 的题目全部检索到 → ASSEMBLING |
| **异常出口** | 部分知识点无匹配题目 → 标记 `coverage_gaps` → 仍进入 ASSEMBLING（由 Checker 最终判定） |
| **异常出口** | 3次降低阈值重试后检索结果仍为空 → FATAL |
| **重试策略** | 每次降低 `similarity_threshold` 0.05（从默认 0.70 开始，最低降至 0.55） |

```
SELECTING:
  read_plan()                        → plan loaded / PLAN_NOT_FOUND
  for each section in plan.sections:
    query_rag(section.knowledge_focus, threshold) → results
    if len(results) < section.count:
      mark_coverage_gap()
  dedup_across_sections()            → filtered_results
  write_batch(filtered_results)      → success / WRITE_FAILED
  success                            → → → ASSEMBLING
  PLAN_NOT_FOUND                     → retry (wait for planner)
  total_results == 0, retry < 3      → lower threshold, retry SELECTING
  total_results == 0, retry >= 3     → → → FATAL
```

### S5：ASSEMBLING

| 属性 | 内容 |
|------|------|
| **描述** | PaperAssembler 将检索结果按 Plan 结构组装成完整试卷 |
| **负责 Agent** | PaperAssembler |
| **输入** | `harness/contracts/paper_plan.json` + `harness/contracts/question_batch.json` |
| **输出** | `harness/contracts/assembled_paper.json` |
| **正常出口** | 组装完成 → CHECKING |
| **异常出口** | 题目不足 → 标记 `unfilled_slots` → 仍进入 CHECKING |
| **异常出口** | 3次组装失败 → FATAL |

```
ASSEMBLING:
  load_plan()                        → plan loaded / PLAN_MISSING
  load_batch()                       → batch loaded / BATCH_MISSING
  for each section in plan.sections:
    select_top_k(batch, section)     → assigned_questions
    if len(assigned) < section.count:
      mark_unfilled_slot()
  order_by_section()                 → ordered_questions
  build_answer_key()                 → answer_key
  validate_total_score()             → score matches plan (±2 tolerance)
  write_assembly()                   → success / WRITE_FAILED
  success                            → → → CHECKING
  PLAN_MISSING or BATCH_MISSING      → retry (wait for upstream)
  WRITE_FAILED, retry < 3            → retry ASSEMBLING
  WRITE_FAILED, retry >= 3           → → → FATAL
```

### S6：CHECKING

| 属性 | 内容 |
|------|------|
| **描述** | QualityChecker 对组装完成的试卷进行多维度校验 |
| **负责 Agent** | QualityChecker |
| **输入** | `harness/contracts/assembled_paper.json` + `harness/contracts/paper_plan.json` |
| **输出** | `harness/contracts/quality_report.json` |
| **判定逻辑** | 见下方判定表 |
| **回路机制** | REJECT 时返回 ASSEMBLING，retry_count++ |

```
CHECKING:
  load_assembly()                    → assembly loaded / NO_ASSEMBLY
  load_plan()                        → plan loaded
  check_coverage()                   → PASS / FAIL (threshold: 85%)
  check_difficulty()                 → PASS / FAIL (deviation ≤ 0.5)
  check_question_types()             → PASS / FAIL (must match exactly)
  check_region()                     → PASS / FAIL (match_rate ≥ 80%)
  check_intra_dedup()                → PASS / FAIL (sim ≤ 0.85)
  check_answer_completeness()        → PASS / FAIL (100% non-empty)
  evaluate_verdict()                 → PASS / PASS_WITH_WARNING / REJECT
  write_report()                     → success
```

### 判定矩阵

| 覆盖度 | 难度 | 题型 | 地域 | 去重 | 答案 | 判定 |
|:---:|:---:|:---:|:---:|:---:|:---:|------|
| PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| FAIL | PASS | PASS | PASS | PASS | PASS | **PASS_WITH_WARNING**（仅缺口 ≤ 2 知识点） |
| * | * | FAIL | * | * | * | **REJECT**（题型是硬性约束） |
| * | * | * | * | FAIL | * | **REJECT**（内部重复不可接受） |
| * | * | * | * | * | FAIL | **REJECT**（答案缺失不可接受） |
| FAIL | FAIL | PASS | PASS | PASS | PASS | **REJECT**（≥3个知识点缺口且难度偏差） |

### S7：RENDERING

| 属性 | 内容 |
|------|------|
| **描述** | PaperRenderer 将通过的试卷渲染为最终格式 |
| **负责 Agent** | PaperRenderer |
| **前置条件** | `quality_report.json` 的 `status` 必须为 `PASS` 或 `PASS_WITH_WARNING` |
| **正常出口** | 渲染成功 → DONE |
| **异常出口** | 渲染失败 2 次 → 报错退出 |

### S8：DONE

| 属性 | 内容 |
|------|------|
| **描述** | 流程完成，输出文件就绪 |
| **输出物** | `output/paper_{timestamp}.pdf` + `output/paper_{timestamp}_answer.pdf` + `output/paper_{timestamp}.html` |
| **下一状态** | IDLE（等待下一次请求） |

---

## 三、异常路径总览

```
         ┌─────────┐
         │  IDLE   │
         └────┬────┘
              │
    ┌─────────┼─────────────┐
    │         │             │
    ▼         ▼             ▼
INVALID    FATAL       PARTIAL_DONE
_REQUEST   (人工介入)   (含warning输出)
```

### 异常分类

| 异常码 | 含义 | 触发条件 | 处理方式 |
|--------|------|---------|---------|
| `INVALID_REQUEST` | 请求参数不合法 | 科目/年级/难度不在枚举中 | 返回错误说明，流程终止 |
| `PLAN_FAILED` | 规划失败 | 3 次重试均无法生成合法 Plan | FATAL，人工介入 |
| `COVERAGE_GAP` | 知识点覆盖不足 | 检索结果少于需求 | 标记缺口继续，Checker 最终判定 |
| `ASSEMBLY_FAILED` | 组装失败 | 3 次重试均无法完成组装 | FATAL，人工介入 |
| `REJECT` | 质量不通过 | Checker 硬性项失败 | 退回上一步，最多 3 次回路 |
| `FATAL` | 不可恢复 | 连续 3 次 REJECT 同一流程 | 人工介入，保留所有中间产物供排查 |
| `RENDER_FAILED` | 渲染失败 | 输出格式生成异常 | 重试 2 次，仍失败则报错 |

---

## 四、回路计数规则

```
retry_count 的作用域：每个 Paper Plan 的完整生命周期内

PLANNING retry_count:     0 → 1 → 2 → FATAL
SELECTING retry_count:    0 → 1 → 2 → FATAL  
ASSEMBLING retry_count:   0 → 1 → 2 → FATAL
CHECKING loop_count:      0 → 1 → 2 → FATAL (每次 REJECT 后累加)

区别：
- retry_count: Agent 内部重试（如降低阈值、放宽约束）
- loop_count:  QualityChecker REJECT 后，整个 ASSEMBLING→CHECKING 的外层循环
```

---

## 五、数据准备流程（独立于主流程）

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│  IDLE   │────▶│IMPORTING│────▶│INDEXING │────▶ READY
└─────────┘     │(DataImp)│     │(RAGIdx) │     │
                └────┬────┘     └────┬────┘     │
                     │               │          │
                     ▼               ▼          │
                IMPORT_FAILED   INDEX_FAILED    │
                (报告错误)      (回退方案)      │
                                               │
                ┌──────────────────────────────┘
                │ 外部数据就绪 + 索引就绪
                ▼
          ┌─────────┐
          │  READY  │──── 可接收出卷请求
          └─────────┘
```

> 数据准备流程和试卷生成流程是**异步解耦**的。试卷生成流程启动前，Orchestrator 检查 `READY` 状态。未就绪则返回 `{ "error": "DATA_NOT_READY" }`。

---

## 六、状态持久化

每个状态转移时，工作流引擎写入 `harness/contracts/.workflow_state.json`：

```json
{
  "workflow_id": "wf_20260614_103000_abc123",
  "current_state": "CHECKING",
  "previous_state": "ASSEMBLING",
  "entered_at": "2026-06-14T10:35:00+08:00",
  "retry_counts": {
    "planning": 0,
    "selecting": 1,
    "assembling": 0,
    "checking_loop": 0
  },
  "artifacts": {
    "paper_plan": "harness/contracts/paper_plan.json",
    "question_batch": "harness/contracts/question_batch.json",
    "assembled_paper": "harness/contracts/assembled_paper.json",
    "quality_report": null
  },
  "errors": []
}
```

> 状态文件的作用：
> - 支持流程恢复：系统重启后可从中断处继续
> - 支持审计追溯：每次状态转移都有时间戳记录
> - 支持调试：排查问题时可知完整状态转移链
