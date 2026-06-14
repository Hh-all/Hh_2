# RULES.md —— 开发约束与护栏规则

> Harness 第二重约束：护栏规则。明确的禁止清单 + 硬性编码规范 + 红线机制。
>
> 设计原则（Ratchet Principle）：每条 Rule 都对应一次真实踩坑。不凭空预设，不堆砌无效约束。
> 规则只增不减——发现一个新的失败模式，就加一条规则。

---

## 一、全局硬约束（所有 Agent 必须遵守）

### 1.1 产物契约不可绕过

| 规则编号 | 规则内容 | 违反后果 |
|---------|---------|---------|
| **G-001** | Agent 间通信必须通过 `harness/contracts/` 下的固定格式文件，**禁止依赖对话上下文传递关键信息** | 下游 Agent 拒绝执行，要求上游重新产出 |
| **G-002** | 每个契约文件必须包含 `_meta.generated_at`（ISO 8601 时间戳）和 `_meta.generated_by`（Agent 名称） | QualityChecker 直接标记 `REJECT` |
| **G-003** | 下游 Agent **不得修改上游产出的契约文件**。有问题必须提 Blocker 走回滚流程 | 违反者立即终止，记录到 audit log |

### 1.2 数据完整性

| 规则编号 | 规则内容 | 违反后果 |
|---------|---------|---------|
| **G-004** | 题目一旦写入 `data/processed/questions.jsonl`，**禁止修改或删除**。如需修正，新增一条并标记 `_import_meta.correction_of` | DataImporter 终止操作 |
| **G-005** | `data/knowledge_graph.json` 的修改**必须经过架构评审**，且附带修改说明和影响分析 | 拒绝合并 |
| **G-006** | 数据 Schema 变更（`data/schema/*.json`）必须同步更新 `harness/SPECS.md` 中的契约定义 | QualityChecker 发现不一致时报 `SCHEMA_DRIFT` |

### 1.3 代码规范

| 规则编号 | 规则内容 | 违反后果 |
|---------|---------|---------|
| **G-007** | 所有代码注释使用**中文**，文件编码使用 **UTF-8** | Code Review 退回 |
| **G-008** | 代码中**禁止使用 emoji** | Code Review 退回 |
| **G-009** | **禁止写 fallback / 兜底逻辑**。处理思路是消除触发场景，而不是加更多兜底 | Code Review 退回 |
| **G-010** | 迁移代码时**先 copy 原文件，再在副本上改写**。禁止直接重写 | Code Review 退回 |

### 1.4 禁止操作（系统级护栏）

| 规则编号 | 规则内容 |
|---------|---------|
| **G-011** | **禁止** `rm -rf`、`git push --force`、`DROP TABLE` |
| **G-012** | **禁止** 在生产环境直接修改 `data/chroma_db/` |
| **G-013** | **禁止** 注释掉失败的测试来让测试通过 |
| **G-014** | **禁止** 在代码中硬编码密钥、Token 或密码。统一使用环境变量或 `.env` 文件 |
| **G-015** | **禁止** 引入项目 `requirements.txt` 中未声明的外部依赖 |

---

## 二、Agent 专属护栏

### 2.1 DataImporter 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **DI-001** | 导入前必须验证数据源格式。不匹配则**拒绝导入**，不允许"尽力而为" |
| **DI-002** | 标签映射置信度 < 0.7 的知识点**必须标记**在 `_import_meta.tag_mapping_confidence`，不能静默映射 |
| **DI-003** | 单次导入超过 10% 的记录被过滤时，**必须触发警告**并写入 import_report |
| **DI-004** | 禁止在答题干为空的记录（`question_text` 为 null 或空字符串） |

### 2.2 RAGIndexer 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **RI-001** | 索引构建前必须检查 `data/processed/questions.jsonl` 非空 |
| **RI-002** | 嵌入失败率 > 5% 时必须终止，不得用零向量填充 |
| **RI-003** | 回退到 TF-IDF 必须在 `index_report.json` 中明确标注 `embed_method: "tfidf_fallback"` |

### 2.3 PaperPlanner 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **PP-001** | 生成的 Paper Plan 中**每项配置必须可从 `data/schema.json` 枚举值中找到** |
| **PP-002** | 知识点列表必须来自 `data/knowledge_graph.json` 的叶子节点 |
| **PP-003** | 难度分布在 1-5 区间内，比例总和必须 = 1.0（±0.02） |
| **PP-004** | 禁止生成 `difficulty: 0` 或 `difficulty: >5` 的规划 |

### 2.4 QuestionSelector 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **QS-001** | **禁止**返回与 Paper Plan `excluded_knowledge_tags` 匹配的题目 |
| **QS-002** | 同一试卷中同一 source 的题目占比不得超过 60%（防止过度依赖单一数据源） |
| **QS-003** | 检索结果中难度偏差 > 2（5 级标度）的题目**自动过滤** |
| **QS-004** | 禁止绕过 Paper Plan 中的 `question_type` 约束检索其他题型 |

### 2.5 PaperAssembler 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **PA-001** | 组装时必须严格按 Paper Plan 的 `section_order` 排列 |
| **PA-002** | 组装后的试卷总分必须 = Paper Plan 定义的总分（±2 分容忍度） |
| **PA-003** | 每道题在试卷中只出现一次（基于 `id` 去重） |
| **PA-004** | 相邻题目之间知识点标签重复率不得超过 40% |

### 2.6 QualityChecker 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **QC-001** | **必须逐项检查**所有 6 个维度，不得跳过任何一项 |
| **QC-002** | 判定为 `PASS` 的试卷必须所有硬性检查通过 |
| **QC-003** | 连续 3 次 `REJECT` 同一份试卷 → 标记 `FATAL`，**强制人工介入** |
| **QC-004** | 禁止在 QualityChecker 中"修复"试卷——只报告，不修改 |

### 2.7 PaperRenderer 护栏

| 规则编号 | 规则内容 |
|---------|---------|
| **PR-001** | 渲染前**必须检查** `quality_report.json` 的 `status` 字段为 `PASS` 或 `PASS_WITH_WARNING` |
| **PR-002** | 输出文件名必须包含时间戳（格式 `YYYYMMDD_HHmmss`），防止覆盖 |
| **PR-003** | 参考答案和解析**必须单独输出**，不与试卷合并在同一文件中 |

---

## 三、回滚与重试规则

### 3.1 重试上限

| Agent | 最大重试次数 | 超限动作 |
|-------|:---------:|---------|
| DataImporter | 1 | **终止**，报告格式错误 |
| RAGIndexer | 2 | 回退到内存方案 |
| PaperPlanner | 3 | **FATAL**，人工介入 |
| QuestionSelector | 3 | 标记 `coverage_gap` |
| PaperAssembler | 3 | **FATAL**，人工介入 |
| QualityChecker | 不适用 | 每次都是全新检查 |
| PaperRenderer | 2 | 报告渲染失败 |

### 3.2 回滚触发条件

| 触发条件 | 回滚目标 | 回滚方式 |
|---------|---------|---------|
| QualityChecker `REJECT` | PaperAssembler | 重新选题或重新排序 |
| 题目检索不满足覆盖率 | QuestionSelector → RAGIndexer | 先检查索引是否过期 |
| Paper Plan 非法参数 | PaperPlanner | 重新解析用户需求 |
| 嵌入模型不可用 | RAGIndexer | 自动回退到 TF-IDF（不视为回滚） |

### 3.3 回路熔断

```
          ┌─────────┐
          │ REJECT  │
          └────┬────┘
               │
          ┌────▼────┐      ┌─────────┐
          │ 重试 N  │─────▶│   PASS  │
          └────┬────┘      └─────────┘
               │
          ┌────▼────┐
          │ N >= 3  │
          └────┬────┘
               │
          ┌────▼────┐
          │  FATAL  │──────▶ 人工介入
          └─────────┘
```

---

## 四、文件系统约束

### 4.1 目录权限矩阵

| 目录 | DataImporter | RAGIndexer | Planner | Selector | Assembler | Checker | Renderer |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `data/raw/` | RW | - | - | - | - | - | - |
| `data/processed/` | RW | R | - | - | - | - | - |
| `data/chroma_db/` | - | RW | - | R | - | - | - |
| `data/schema/` | R | R | R | R | R | R | - |
| `harness/contracts/` | - | - | W | RW | RW | RW | R |
| `output/` | - | - | - | - | - | - | RW |

### 4.2 文件命名规范

```
data/processed/
  questions.jsonl              # 清洗后的题库
  import_report.json           # 导入统计报告
  index_report.json            # 索引构建报告

harness/contracts/
  paper_plan.json              # 试卷蓝图
  question_batch.json          # 检索到的候选题目池
  assembled_paper.json         # 组装完成的试卷
  quality_report.json          # 质量检查报告

output/
  paper_{YYYYMMDD_HHmmss}.pdf        # 最终试卷
  paper_{YYYYMMDD_HHmmss}_answer.pdf # 参考答案
```

---

## 五、被明确排除的操作

以下行为在系统任何阶段都**不被允许**，属于硬性红线：

1. **不写测试**：除非用户明确要求，否则不主动编写测试脚本（项目偏好）
2. **不主动生成文档**：不创建 README、CHANGELOG 等项目说明文档（项目偏好）
3. **不兼容旧版本**：不考虑 fallback，不兼容旧数据格式，假设没有老用户（项目偏好）
4. **不重写已有实现**：如果项目中已有类似实现，必须先阅读原逻辑再修改（项目偏好）
5. **不追求折中方案**：方案要清晰合理，不过度优化。Bug 的折中方案不等于最终方案（项目偏好）

> 项目偏好的 5 条来自全局 `CLAUDE.md`，在此作为 Harness 层面的正式约束纳入。
