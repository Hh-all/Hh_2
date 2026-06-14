# QUALITY_GATES.md —— 试卷生成质量门禁标准

> Harness 反馈回路：每个质量门禁都是一个可执行的校验节点。不合格的产物不得进入下一阶段。
>
> 设计原则：门禁不通过 → 回路重试 → 3次不通过 → FATAL 人工介入。

---

## 一、质量门禁总览

```
┌─────────────────────────────────────────────────────────────────┐
│                      QUALITY GATES                              │
│                                                                 │
│  Gate 0: 数据就绪        Gate 3: 检索质量                       │
│  ┌──────────┐            ┌──────────┐                          │
│  │ 题库非空  │            │ 召回率   │                          │
│  │ 索引已建  │            │ 精确率   │                          │
│  │ Schema正确│            │ 覆盖缺口 │                          │
│  └────┬─────┘            └────┬─────┘                          │
│       │                       │                                │
│       ▼                       ▼                                │
│  Gate 1: 试卷完整性      Gate 4: 生成质量                       │
│  ┌──────────┐            ┌──────────┐                          │
│  │ 题量达标  │            │ 忠实度   │                          │
│  │ 题型分布  │            │ 答案相关 │                          │
│  │ 分值正确  │            │ 解析覆盖 │                          │
│  └────┬─────┘            └────┬─────┘                          │
│       │                       │                                │
│       ▼                       ▼                                │
│  Gate 2: 内容合规        Gate 5: 地域适配                       │
│  ┌──────────┐            ┌──────────┐                          │
│  │ 无内部重复│            │ 地域标签  │                          │
│  │ 答案100% │            │ 特色题目  │                          │
│  │ 难度分布  │            │ 情景匹配  │                          │
│  └──────────┘            └──────────┘                          │
│                                                                 │
│  全部通过 → RELEASE      任一失败 → REJECT → 回路               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、门禁详述

### Gate 0: 数据就绪

| 检查项 | 标准 | 校验方式 | 不通过动作 |
|--------|------|---------|-----------|
| G0.1 题库非空 | `data/processed/questions.jsonl` 存在且 ≥ 10 题 | `os.path.getsize() > 0` + 行数检查 | 阻断，提示运行 `data-import` |
| G0.2 索引已建 | ChromaDB 或 `vector_store.pkl` 可用 | `rag_searcher.init_searcher()` | 阻断，提示运行 `rag-index` |
| G0.3 Schema 正确 | 题库中 ≥ 95% 题目通过 Schema Validator | `SchemaValidator.validate_questions_batch()` | 阻断，标记不合规题目 |

### Gate 1: 试卷完整性

| 检查项 | 标准 | 阈值 | 校验方式 |
|--------|------|:---:|---------|
| **G1.1 题量达标** | 实际题量 ≥ 请求题量的 80% | ≥ 80% | `actual / requested >= 0.80` |
| **G1.2 题型分布正确** | 试卷中出现的题型在请求的题型列表中 | 100% | `set(actual_types) ⊆ set(requested_types)` |
| **G1.3 分值正确** | 分值和 = paper_meta.total_score（容差 ±2 分） | ±2 | `abs(sum(scores) - total) <= 2` |
| G1.4 题号连续 | sequence_number 1..N 无跳号 | 连续 | 逐项检查连续性 |
| G1.5 大题结构完整 | 每个 section 有 section_title + question_count | 完整 | SchemaValidator L1 |

**判定规则**：
- G1.1 + G1.2 + G1.3 全部通过 → Gate 1 **PASS**
- 任一项失败 → Gate 1 **REJECT**，退回 PaperAssembler 重试

### Gate 2: 内容合规

| 检查项 | 标准 | 阈值 | 校验方式 |
|--------|------|:---:|---------|
| **G2.1 答案覆盖率** | 每道题必须有非空 answer | **100%** | `SchemaValidator L2` |
| **G2.2 解析覆盖率** | ≥ 90% 题目包含非空 analysis | **≥ 90%** | 逐题统计 `analysis` 非空比例 |
| **G2.3 无内部重复** | 试卷内任意两题文本相似度 ≤ 0.85 | **≤ 0.85** | `DuplicateDetector.detect_intra_duplicates()` |
| G2.4 难度分布合理 | 实际难度分布与请求偏差 ≤ 0.5（5级标度） | ≤ 0.5 | 统计实际难度与请求分布的各等级偏差 |
| G2.5 必填字段完整 | 每道题含 id/subject/grade/knowledge_tags/difficulty/question_text/answer/source | **100%** | `SchemaValidator L2` |
| G2.6 题型约束合规 | 选择题有 options、填空题有空白标记 | 合规 | `SchemaValidator L3` |

**判定规则**：
- G2.1 + G2.3 + G2.5 全部通过（**硬性**）→ 继续检查
- G2.2 ≥ 90% + G2.4 + G2.6 → Gate 2 **PASS**
- G2.1 失败 → **FATAL**（答案缺失不可接受）
- G2.3 失败 → **REJECT**（退回去重处理）
- G2.2 不足 90% 但 ≥ 70% → **PASS_WITH_WARNING**

### Gate 3: 检索质量

| 检查项 | 标准 | 阈值 | 校验方式 |
|--------|------|:---:|---------|
| G3.1 上下文召回率 | 检索结果覆盖 ground truth 的比例 | ≥ 0.55 | `RAGEvaluator` context_recall |
| G3.2 上下文精确率 | 检索结果中相关文档占比 | ≥ 0.55 | `RAGEvaluator` context_precision |
| G3.3 覆盖缺口 | 缺失的知识点数量 | ≤ 2 | `QuestionRetrieverAgent` coverage_gaps |

**判定规则**：
- G3.1 + G3.2 通过 + G3.3 达标 → Gate 3 **PASS**
- 仅 G3.3 不达标 → Gate 3 **PASS_WITH_WARNING**
- G3.1 或 G3.2 不达标 → 降低阈值重试，3次仍不达标 → **REJECT**

### Gate 4: 生成质量

| 检查项 | 标准 | 阈值 | 校验方式 |
|--------|------|:---:|---------|
| G4.1 忠实度 | 答案声明被上下文支持的比例 | ≥ 0.55 | `RAGEvaluator` faithfulness |
| G4.2 答案相关性 | 答案与问题的关联程度 | ≥ 0.50 | `RAGEvaluator` answer_relevancy |
| G4.3 综合得分 | 检索(40%) + 生成(60%) 加权 | ≥ 0.55 | `RAGEvaluator` composite_score |

**判定规则**：
- G4.1 + G4.2 + G4.3 全部通过 → Gate 4 **PASS**
- 任一项不达标 → Gate 4 **REJECT**，退回 QAGeneratorAgent 重新生成

### Gate 5: 地域适配

| 检查项 | 标准 | 阈值 | 校验方式 |
|--------|------|:---:|---------|
| **G5.1 地域标签** | 试卷中 ≥ 30% 题目 region 与请求匹配 | **≥ 30%** | 统计 region 匹配比例 |
| **G5.2 特色题目** | 至少 1 道题目包含地域特色标记（`_is_regional_extra` 或 `_has_scenario`） | **≥ 1** | 扫描题目元数据 |
| G5.3 地域情境匹配 | 题干中出现地域关键词（如"北京""上海""广东"） | ≥ 1 | 正则匹配 |
| G5.4 模板正确 | 使用了正确的地域样式模板 | 匹配 | `style_registry.get_template_name()` |

**判定规则**：
- G5.1 + G5.2 通过 → Gate 5 **PASS**
- G5.1 通过 + G5.2 不通过 → Gate 5 **PASS_WITH_WARNING**（已降级到全国通用题）
- G5.1 不通过 → Gate 5 **REJECT**，提示"地域题目不足，请补充地域特色数据"

---

## 三、门禁判定矩阵

```
          Gate 0  Gate 1  Gate 2  Gate 3  Gate 4  Gate 5  │  最终判定
          ──────  ──────  ──────  ──────  ──────  ──────  │  ────────
          PASS    PASS    PASS    PASS    PASS    PASS     │  RELEASE
          PASS    PASS    PASS    PASS    PASS    WARN     │  RELEASE (含警告)
          PASS    PASS    WARN    PASS    PASS    PASS     │  RELEASE (含警告)
          PASS    PASS    PASS    WARN    PASS    PASS     │  RELEASE (含警告)
          PASS    FAIL    *       *       *       *        │  REJECT
          PASS    PASS    FAIL    *       *       *        │  REJECT
          PASS    PASS    PASS    FAIL    *       *        │  REJECT (重试3次 → FATAL)
          PASS    PASS    PASS    PASS    FAIL    *        │  REJECT
          FAIL    *       *       *       *       *        │  FATAL
```

> 图例：PASS=通过 WARN=PASS_WITH_WARNING FAIL=不通过 REJECT=退回重试 FATAL=人工介入 *=不限

---

## 四、CI 集成映射

| CI Step | 对应 Gate | 失败含义 |
|---------|:-------:|---------|
| Step 2: Schema 校验 | Gate 1 + Gate 2 | 试卷结构不合法 |
| Step 3: pytest | Gate 0 + Gate 2 | 核心功能回归 |
| Step 4: RAG 评估 | Gate 3 + Gate 4 | 检索/生成质量不达标 |
| Step 5: 质量门禁汇总 | Gate 5 + 代码规范 | 地域/规范不合规 |

---

## 五、回路协议

当任一 Gate 返回 REJECT 时，触发以下回路：

```
┌──────────┐     REJECT      ┌──────────────┐
│   Gate   │────────────────▶│  Orchestrator │
└──────────┘                 └──────┬───────┘
                                    │
                         ┌──────────┼──────────┐
                         │          │          │
                    retry ≤ 2  retry = 3  FATAL
                         │          │          │
                    ┌────▼────┐ ┌──▼───┐ ┌────▼────┐
                    │ 退回上一步│ │ 3次  │ │ 人工介入 │
                    │ Agent重试│ │ 回路 │ │ 保留现场 │
                    └─────────┘ └──────┘ └─────────┘
```

- **retry_count** 在整个 Paper Plan 生命周期内累计
- 每个 Gate 失败递增 retry_count
- retry_count ≥ 3 → **FATAL**，停止自动流程

---

## 六、质量标准速查卡

| 维度 | 指标 | 最低阈值 | 目标值 |
|------|------|:------:|:-----:|
| 完整性 | 题量达标率 | 80% | 100% |
| 完整性 | 答案覆盖率 | 100% | 100% |
| 完整性 | 解析覆盖率 | 90% | 100% |
| 合规性 | 内部重复 | 0对 | 0对 |
| 合规性 | 必填字段 | 100% | 100% |
| 检索 | 上下文召回率 | 0.55 | 0.80 |
| 检索 | 上下文精确率 | 0.55 | 0.80 |
| 生成 | 忠实度 | 0.55 | 0.85 |
| 生成 | 答案相关性 | 0.50 | 0.80 |
| 地域 | 地域标签匹配 | 30% | 80% |
| 地域 | 特色题目 | 1道 | 3道 |
