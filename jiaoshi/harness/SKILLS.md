# SKILLS.md —— 可复用 Skill 流程模板

> Harness 组件：Skill。把固定操作标准化，解决"这件事具体怎么做"。
>
> 设计原则：Skill 管步骤，Rule 管底线，Script 管结果——三者互补。
> Skill 的特征：步骤固定、每次必执行、搞砸代价大、不值得临场发挥。

---

## Skill 总览

| Skill | 触发场景 | 负责 Agent | 输入 | 输出 |
|-------|---------|-----------|------|------|
| `data-import` | 有新数据源需要导入 | DataImporter | 外部数据源路径 + 来源标识 | questions.jsonl + import_report.json |
| `rag-index` | 题库更新后需要重建索引 | RAGIndexer | questions.jsonl | ChromaDB 或 vector_store.pkl |
| `paper-plan` | 用户提交出卷需求 | PaperPlanner | 用户参数 | paper_plan.json |
| `question-select` | Paper Plan 就绪 | QuestionSelector | paper_plan.json | question_batch.json |
| `paper-assemble` | 题目检索完成 | PaperAssembler | paper_plan.json + question_batch.json | assembled_paper.json |
| `quality-check` | 试卷组装完成 | QualityChecker | assembled_paper.json + paper_plan.json | quality_report.json |
| `paper-render` | 质量检查通过 | PaperRenderer | assembled_paper.json + quality_report.json | PDF + HTML |
| `full-pipeline` | 用户要求端到端出卷 | Orchestrator | 用户参数 | 最终试卷 + 全链路中间产物 |

---

## Skill 1：data-import（数据导入）

### 触发条件

- 用户执行 `python scripts/import_clean_data.py --input <file> --source <name>`
- 或者 Orchestrator 调度 DataImporter

### 执行步骤

```
Step 1: 校验数据源
  ├── 检查文件存在且可读
  ├── 检查格式（JSON 数组 / JSONL / CSV）
  └── 不通过 → 返回 FORMAT_ERROR，终止

Step 2: 读取与解析
  ├── JSON: 自动检测 CMMaTH / EduAdapt / EduEval 格式
  ├── CSV: 应用中英文列名映射
  └── 每行解析失败 → 跳过并记录

Step 3: 转换与清洗
  ├── 学科映射（中文 → 代码）
  ├── 年级归一化（grade_N 格式）
  ├── 难度校准（外部表示 → 1-5）
  ├── 题型映射
  ├── 地域推断（题干关键词 → region code）
  └── 标签规范化（外部标签 → knowledge_graph 标准标签）

Step 4: 去重
  ├── MD5 哈希精确去重
  └── 编辑距离模糊去重（阈值 0.88）

Step 5: 输出
  ├── 写入 data/processed/questions.jsonl（追加模式）
  ├── 生成 data/processed/import_report.json
  └── 返回统计报告
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-1.1 | 输入文件存在且非空 | 终止，报告 FILE_NOT_FOUND |
| CP-1.2 | 至少 1 条有效记录 | 终止，报告 NO_VALID_RECORDS |
| CP-1.3 | 过滤率 < 90% | 警告，继续 |
| CP-1.4 | 学科映射成功率 > 80% | 警告，继续 |

### 回退方式

- 导入过程中断 → 不写入 `questions.jsonl`（事务性：先写临时文件，成功后再原子重命名）
- 临时文件：`data/processed/.questions_importing.jsonl`

---

## Skill 2：rag-index（向量索引构建）

### 触发条件

- `data/processed/questions.jsonl` 有更新
- 或用户执行 `python backend/rag_indexer.py --force-rebuild`
- 或 Orchestrator 在数据导入完成后自动触发

### 执行步骤

```
Step 1: 加载数据
  ├── 读取 data/processed/questions.jsonl
  ├── 解析每条 JSON
  └── 构建文档文本：question_text + " " + knowledge_tags 拼接

Step 2: 初始化嵌入模型
  ├── 尝试加载 SentenceTransformer (paraphrase-multilingual-MiniLM-L12-v2)
  ├── 成功 → embed_method = "sentence_transformer"
  └── 失败 → 回退到 TF-IDF，embed_method = "tfidf_fallback"

Step 3: 生成向量
  ├── 批量编码（batch_size=32）
  ├── 失败率 > 5% → 终止，报告 EMBED_FAILURE
  └── 记录向量维度

Step 4: 存储向量
  ├── 尝试 ChromaDB (PersistentClient)
  ├── 可用 → 写入 chroma_db/，HNSW 索引 (cosine, ef=200, M=32)
  └── 不可用 → 回退到 InMemoryVectorStore → 序列化到 vector_store.pkl

Step 5: 生成报告
  └── 写入 data/processed/index_report.json
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-2.1 | questions.jsonl 非空 | 终止，报告 EMPTY_DATA |
| CP-2.2 | 嵌入完成率 > 95% | 终止，报告 EMBED_FAILURE |
| CP-2.3 | 向量维度 > 0 | 终止，报告 INVALID_DIMENSION |
| CP-2.4 | 存储写入成功 | 回退到内存方案 |

---

## Skill 3：paper-plan（试卷规划）

### 触发条件

- 用户提交出卷请求（参数：科目、年级、地域、难度分布、题型偏好、知识点范围）

### 执行步骤

```
Step 1: 解析请求参数
  ├── 验证科目在 schema.json 枚举中
  ├── 验证年级合法
  ├── 验证难度分布和为 1.0（±0.02）
  └── 验证题型在枚举中

Step 2: 查询知识点覆盖
  ├── 从 knowledge_graph.json 提取指定科目/学段的知识点
  ├── 按用户指定的知识点范围过滤
  └── 检查 RAG 索引中是否有该知识点的题目

Step 3: 规划试卷结构
  ├── 确定大题数量（section_order）
  ├── 分配每个 section 的题型、题数、分值
  ├── 分配知识点到每个 section
  └── 确保难度分布符合用户需求

Step 4: 验证 Plan
  ├── 总分 = sum(section.count * section.score_per_question)
  ├── 知识点覆盖 = 用户指定的知识点 ⊆ Plan 中的知识点
  └── 题型顺序：choice → fill_blank → true_false → short_answer → calculation → essay

Step 5: 输出
  └── 写入 harness/contracts/paper_plan.json
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-3.1 | 科目在枚举中 | 返回 INVALID_SUBJECT |
| CP-3.2 | 年级合法 | 返回 INVALID_GRADE |
| CP-3.3 | 难度分布和 = 1.0 | 返回 INVALID_DIFFICULTY |
| CP-3.4 | 知识点在 knowledge_graph 中 | 标记为 missing_knowledge（警告） |
| CP-3.5 | 总分 > 0 | 返回 INVALID_SCORE |

---

## Skill 4：question-select（题目检索）

### 触发条件

- `harness/contracts/paper_plan.json` 就绪

### 执行步骤

```
Step 1: 加载 Paper Plan
  ├── 读取 paper_plan.json
  └── 提取每个 section 的检索条件

Step 2: 构建检索 Query
  ├── 对每个 section.knowledge_focus[index] 构建 query
  ├── Query = f"{knowledge_tag} {question_type} 难度{difficulty_range}"
  └── 附加 metadata filter: subject, grade, region

Step 3: 执行 RAG 检索
  ├── 调用 backend/rag_searcher.py
  ├── similarity_threshold: 默认 0.70
  └── top_k: section.count * 3（留出筛选余量）

Step 4: 后处理
  ├── 过滤难度偏差 > 2 的题目
  ├── 过滤 excluded_knowledge_tags
  ├── 检查 source 分布（同一 source < 60%）
  └── 按 similarity_score 降序排列

Step 5: 不足时降级重试
  ├── 降低 similarity_threshold 0.05
  ├── 最多 3 次（最低 0.55）
  └── 仍不足 → 标记 coverage_gap

Step 6: 输出
  └── 写入 harness/contracts/question_batch.json
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-4.1 | paper_plan.json 存在且版本匹配 | 等待 Planner 完成 |
| CP-4.2 | 每个 section 至少检索到 1 道题 | 标记 coverage_gap |
| CP-4.3 | 检索结果无排除知识点 | 过滤后重新排名 |
| CP-4.4 | source 分布合理 | 多样性过滤 |

---

## Skill 5：paper-assemble（试卷组装）

### 触发条件

- `harness/contracts/paper_plan.json` 和 `harness/contracts/question_batch.json` 就绪

### 执行步骤

```
Step 1: 加载输入
  ├── 读取 paper_plan.json
  └── 读取 question_batch.json

Step 2: 选题
  ├── 对每个 section，从 batch 中取 top section.count 道题
  ├── 确保 section 内无重复
  └── 确保 section 内知识点不重复（去重标准：相同 knowledge_tags 只保留一道）

Step 3: 排序
  ├── 大题按 section_order 排列
  ├── 大题内按 difficulty 递增排列
  └── 相同难度按 similarity_score 降序排列

Step 4: 编号
  ├── 全局 sequence_number: 1, 2, 3, ...
  └── section_sequence: 1, 2, 3, ...（每个 section 内独立编号）

Step 5: 生成答案
  ├── 逐题提取 answer 和 analysis
  └── 生成 answer_key 数组

Step 6: 验证
  ├── 总分 = sum(assigned_score)
  └── 总分与 plan 偏差 ≤ 2

Step 7: 输出
  └── 写入 harness/contracts/assembled_paper.json
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-5.1 | 两个输入文件均存在 | 等待上游完成 |
| CP-5.2 | section 题目数 >= plan 定义 | 标记 unfilled_slots |
| CP-5.3 | 全局 sequence_number 连续 | 重新编号 |
| CP-5.4 | 总分偏差 ≤ 2 | 调整最后一题分值 |

---

## Skill 6：quality-check（质量校验）

### 触发条件

- `harness/contracts/assembled_paper.json` 就绪
- 由 Orchestrator 在组装完成后自动触发

### 执行步骤

```
Step 1: 加载输入
  ├── 读取 assembled_paper.json
  └── 读取 paper_plan.json（对照）

Step 2: 覆盖度检查
  ├── 统计试卷中出现的知识点
  ├── 与 plan.knowledge_focus 对比
  └── 计算覆盖率

Step 3: 难度检查
  ├── 统计试卷中每道题的 difficulty
  ├── 计算实际分布
  └── 与 plan.difficulty_distribution 对比（偏差 ≤ 0.5）

Step 4: 题型检查
  ├── 逐 section 检查 question_type
  └── 必须与 plan 完全匹配

Step 5: 地域检查
  ├── 逐题检查 region 字段
  └── 与 plan.regions 对比（匹配率 ≥ 80%）

Step 6: 内部去重检查
  ├── 计算试卷内任意两题的相似度
  └── 最高相似度 > 0.85 → FAIL

Step 7: 答案完整度检查
  └── 逐题检查 answer 非空

Step 8: 判定
  ├── 全部 PASS → 输出 PASS
  ├── 仅 coverage FAIL + 缺口 ≤ 2 → PASS_WITH_WARNING
  └── 其他 FAIL → REJECT

Step 9: 输出
  └── 写入 harness/contracts/quality_report.json
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-6.1 | assembled_paper.json 存在 | 等待 Assembler 完成 |
| CP-6.2 | 所有 6 维度检查已执行 | 不能跳过任何维度 |
| CP-6.3 | 判定符合判定矩阵 | 按 WORKFLOW.md 判定矩阵执行 |

---

## Skill 7：paper-render（试卷渲染）

### 触发条件

- `quality_report.json` status = PASS 或 PASS_WITH_WARNING

### 执行步骤

```
Step 1: 前置检查
  ├── 读取 quality_report.json
  └── 检查 status ∈ {PASS, PASS_WITH_WARNING}

Step 2: 加载试卷
  └── 读取 assembled_paper.json

Step 3: 渲染试卷
  ├── 调用 backend/render_paper.py
  ├── 生成 PDF（含密封线、准考证号区、试卷头）
  ├── 生成 HTML（网页预览版）
  └── 生成参考答案 PDF（单独文件）

Step 4: 输出
  ├── output/paper_{YYYYMMDD_HHmmss}.pdf
  ├── output/paper_{YYYYMMDD_HHmmss}.html
  └── output/paper_{YYYYMMDD_HHmmss}_answer.pdf
```

### 验证检查点

| 检查点 | 检查内容 | 不通过动作 |
|--------|---------|-----------|
| CP-7.1 | QA status = PASS 或 PASS_WITH_WARNING | 拒绝渲染，返回 QA_NOT_PASSED |
| CP-7.2 | 输出文件写入成功 | 重试最多 2 次 |
| CP-7.3 | 输出文件 > 0 字节 | 标记 RENDER_FAILED |

---

## Skill 8：full-pipeline（端到端出卷）

### 触发条件

- 用户执行 `python scripts/generate_paper.py --subject math --grade grade_9 --region beijing`

### 编排策略

```
full-pipeline 不是简单的顺序调用，而是一个状态机编排：

1. Orchestrator 检查数据就绪状态
2. 按序调用 Skill 3 → Skill 4 → Skill 5 → Skill 6 → Skill 7
3. 每步读取上一步的产出物（契约文件）
4. 异常时根据 WORKFLOW.md 的状态机决定：
   - 回退到上一步
   - 降低约束重试
   - 标记 FATAL 等待人工介入
```

### 执行步骤

```
Step 0: 前置检查
  ├── 检查 data/processed/questions.jsonl 存在且非空
  ├── 检查 ChromaDB 或 vector_store.pkl 可用
  └── 未就绪 → 提示先执行 data-import 和 rag-index

Step 1: 规划 (Skill 3)
  └── 等待产出 paper_plan.json

Step 2: 检索 (Skill 4)
  └── 等待产出 question_batch.json

Step 3: 组装 (Skill 5)
  └── 等待产出 assembled_paper.json

Step 4: 校验 (Skill 6)
  ├── PASS → 进入 Step 5
  ├── PASS_WITH_WARNING → 记录警告，进入 Step 5
  └── REJECT → 回到 Step 3（最多 3 次回路）

Step 5: 渲染 (Skill 7)
  └── 输出最终文件

Step 6: 清理
  └── 可选：保留本次所有中间产物，或只保留最终输出
```

---

## Skill 依赖关系

```
data-import ──→ rag-index
                    │
                    ├──→ paper-plan ──→ question-select ──→ paper-assemble
                    │                                              │
                    │                                        ┌─────┘
                    │                                        ▼
                    │                                   quality-check
                    │                                        │
                    │                                   ┌────┴────┐
                    │                                   │         │
                    │                                REJECT     PASS
                    │                                   │         │
                    │                              [回路最多3次]    ▼
                    │                                   │    paper-render
                    │                                   │         │
                    │                                   └────┬────┘
                    │                                        │
                    └────────────────────────────────────────┘
                                                     DONE
```

> 虚线表示异步依赖：`data-import` 和 `rag-index` 可以在 `full-pipeline` 启动前独立执行。
