# SPECS.md —— 输入输出数据结构契约

> Harness 第三重约束：产物契约。Agent 间通过固定格式的文件传递信息，不依赖对话上下文。
>
> 设计原则：每个契约文件有明确的 JSON Schema 定义，字段不可随意增减。下游 Agent 按契约读取，不猜测意图。

---

## 一、Paper Plan 契约

**文件**：`harness/contracts/paper_plan.json`
**生产者**：PaperPlanner
**消费者**：QuestionSelector、PaperAssembler、QualityChecker

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PaperPlan",
  "type": "object",
  "required": ["_meta", "paper_config", "sections"],
  "properties": {
    "_meta": {
      "type": "object",
      "required": ["generated_at", "generated_by", "plan_version"],
      "properties": {
        "generated_at": { "type": "string", "format": "date-time", "description": "ISO 8601 时间戳" },
        "generated_by": { "const": "PaperPlanner", "description": "生产者标识" },
        "plan_version": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$", "description": "语义版本号" },
        "retry_count": { "type": "integer", "minimum": 0, "default": 0, "description": "重试次数（首次为0）" }
      }
    },
    "paper_config": {
      "type": "object",
      "required": ["title", "total_score", "time_limit_minutes", "subjects", "grade_level", "regions", "difficulty_distribution"],
      "properties": {
        "title": { "type": "string", "minLength": 1, "description": "试卷标题，如'2026年北京市中考数学模拟试卷'" },
        "total_score": { "type": "integer", "minimum": 1, "maximum": 300, "description": "试卷总分" },
        "time_limit_minutes": { "type": "integer", "minimum": 30, "maximum": 300, "description": "考试时长（分钟）" },
        "subjects": {
          "type": "array",
          "items": { "type": "string", "enum": ["math", "chinese", "english", "physics", "chemistry", "biology", "history", "geography", "politics"] },
          "minItems": 1,
          "description": "包含的学科"
        },
        "grade_level": { "type": "string", "enum": ["primary", "junior", "senior"], "description": "学段" },
        "grade": { "type": "string", "pattern": "^grade_(1[0-2]|[1-9])$", "description": "具体年级，如 grade_9" },
        "regions": {
          "type": "array",
          "items": { "type": "string" },
          "description": "地域偏好列表。空数组表示不限地域"
        },
        "difficulty_distribution": {
          "type": "object",
          "required": ["1", "2", "3", "4", "5"],
          "properties": {
            "1": { "type": "number", "minimum": 0, "maximum": 1, "description": "难度1(容易)的比例" },
            "2": { "type": "number", "minimum": 0, "maximum": 1 },
            "3": { "type": "number", "minimum": 0, "maximum": 1 },
            "4": { "type": "number", "minimum": 0, "maximum": 1 },
            "5": { "type": "number", "minimum": 0, "maximum": 1, "description": "难度5(困难)的比例" }
          },
          "description": "5个难度级别的题目占比，总和必须=1.0(±0.02)"
        }
      }
    },
    "sections": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["section_order", "section_title", "question_type", "count", "score_per_question", "knowledge_focus"],
        "properties": {
          "section_order": { "type": "integer", "minimum": 1, "description": "大题序号（一、二、三...）" },
          "section_title": { "type": "string", "description": "大题标题，如'一、选择题'" },
          "question_type": { "type": "string", "enum": ["choice", "fill_blank", "true_false", "short_answer", "essay", "calculation"], "description": "题型" },
          "count": { "type": "integer", "minimum": 1, "description": "该大题包含的题目数" },
          "score_per_question": { "type": "integer", "minimum": 1, "description": "每题分值" },
          "knowledge_focus": {
            "type": "array",
            "items": { "type": "string" },
            "description": "重点考察的知识点标签（来自 knowledge_graph.json 叶子节点）"
          },
          "excluded_knowledge_tags": {
            "type": "array",
            "items": { "type": "string" },
            "description": "排除的知识点标签（如避免重复考察）"
          },
          "difficulty_range": {
            "type": "array",
            "items": { "type": "integer", "minimum": 1, "maximum": 5 },
            "minItems": 2,
            "maxItems": 2,
            "description": "该大题允许的难度范围 [min, max]"
          }
        }
      }
    }
  }
}
```

### 示例

```json
{
  "_meta": {
    "generated_at": "2026-06-14T10:30:00+08:00",
    "generated_by": "PaperPlanner",
    "plan_version": "1.0.0",
    "retry_count": 0
  },
  "paper_config": {
    "title": "2026年北京市中考数学模拟试卷",
    "total_score": 120,
    "time_limit_minutes": 120,
    "subjects": ["math"],
    "grade_level": "junior",
    "grade": "grade_9",
    "regions": ["beijing"],
    "difficulty_distribution": { "1": 0.30, "2": 0.20, "3": 0.30, "4": 0.15, "5": 0.05 }
  },
  "sections": [
    {
      "section_order": 1,
      "section_title": "一、选择题",
      "question_type": "choice",
      "count": 10,
      "score_per_question": 3,
      "knowledge_focus": ["一元二次方程", "一次函数表达式与图像", "相似三角形的判定"],
      "difficulty_range": [1, 3]
    },
    {
      "section_order": 2,
      "section_title": "二、填空题",
      "question_type": "fill_blank",
      "count": 6,
      "score_per_question": 3,
      "knowledge_focus": ["勾股定理", "概率的简单应用", "整式的乘除与乘法公式"],
      "difficulty_range": [2, 4]
    },
    {
      "section_order": 3,
      "section_title": "三、解答题",
      "question_type": "calculation",
      "count": 8,
      "score_per_question": 9,
      "knowledge_focus": ["一元二次方程", "相似三角形的性质", "一次函数应用题"],
      "difficulty_range": [2, 5]
    }
  ]
}
```

---

## 二、Question Batch 契约

**文件**：`harness/contracts/question_batch.json`
**生产者**：QuestionSelector
**消费者**：PaperAssembler

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "QuestionBatch",
  "type": "object",
  "required": ["_meta", "plan_ref", "results"],
  "properties": {
    "_meta": {
      "type": "object",
      "required": ["generated_at", "generated_by", "plan_ref_hash"],
      "properties": {
        "generated_at": { "type": "string", "format": "date-time" },
        "generated_by": { "const": "QuestionSelector" },
        "plan_ref_hash": { "type": "string", "description": "paper_plan.json 的 MD5 哈希，用于溯源" },
        "similarity_threshold_used": { "type": "number", "minimum": 0, "maximum": 1, "description": "实际使用的检索阈值" },
        "retry_count": { "type": "integer", "minimum": 0 }
      }
    },
    "plan_ref": {
      "type": "object",
      "description": "引用的 Paper Plan 摘要（不重复完整内容，仅用于校验）",
      "required": ["plan_version", "total_questions_required"],
      "properties": {
        "plan_version": { "type": "string" },
        "total_questions_required": { "type": "integer", "description": "Plan 要求的总题目数" }
      }
    },
    "coverage_gaps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "knowledge_tag": { "type": "string", "description": "未能覆盖的知识点" },
          "required_count": { "type": "integer" },
          "retrieved_count": { "type": "integer" },
          "missing_count": { "type": "integer" }
        }
      },
      "description": "知识点覆盖缺口列表。空数组表示完全覆盖"
    },
    "results": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["question", "similarity_score", "section_target"],
        "properties": {
          "question": { "$ref": "unified_question.json", "description": "统一试题数据模型（引用 data/schema/unified_question.json）" },
          "similarity_score": { "type": "number", "minimum": 0, "maximum": 1, "description": "与检索 query 的相似度得分" },
          "section_target": { "type": "integer", "description": "目标 section_order" },
          "rank": { "type": "integer", "minimum": 1, "description": "在该 section 候选集中的排名" },
          "diversity_score": { "type": "number", "minimum": 0, "maximum": 1, "description": "多样性评分（与其他候选的差异化程度）" }
        }
      }
    }
  }
}
```

---

## 三、Assembled Paper 契约

**文件**：`harness/contracts/assembled_paper.json`
**生产者**：PaperAssembler
**消费者**：QualityChecker、PaperRenderer

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AssembledPaper",
  "type": "object",
  "required": ["_meta", "paper_meta", "questions", "answer_key"],
  "properties": {
    "_meta": {
      "type": "object",
      "required": ["generated_at", "generated_by", "plan_ref", "batch_ref"],
      "properties": {
        "generated_at": { "type": "string", "format": "date-time" },
        "generated_by": { "const": "PaperAssembler" },
        "plan_ref": { "type": "string", "description": "引用的 paper_plan.json 版本" },
        "batch_ref": { "type": "string", "description": "引用的 question_batch.json 哈希" },
        "assembly_version": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$" },
        "retry_count": { "type": "integer", "minimum": 0 }
      }
    },
    "paper_meta": {
      "type": "object",
      "required": ["title", "total_score", "question_count", "sections"],
      "properties": {
        "title": { "type": "string" },
        "subtitle": { "type": "string", "description": "副标题，如考试须知" },
        "total_score": { "type": "integer" },
        "actual_total_score": { "type": "integer", "description": "实际总分（需 paper_config.total_score ±2分）" },
        "question_count": { "type": "integer", "description": "试卷总题数" },
        "sections": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["section_order", "section_title", "question_count", "total_score"],
            "properties": {
              "section_order": { "type": "integer" },
              "section_title": { "type": "string" },
              "question_count": { "type": "integer" },
              "total_score": { "type": "integer" }
            }
          }
        }
      }
    },
    "questions": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["sequence_number", "section_order", "question"],
        "properties": {
          "sequence_number": { "type": "integer", "description": "试卷中的全局题号（1, 2, 3, ...）" },
          "section_order": { "type": "integer" },
          "section_sequence": { "type": "integer", "description": "大题内题号" },
          "question": { "$ref": "unified_question.json" },
          "assigned_score": { "type": "integer", "description": "该题实际分值" }
        }
      }
    },
    "answer_key": {
      "type": "object",
      "required": ["generated_at", "answers"],
      "properties": {
        "generated_at": { "type": "string", "format": "date-time" },
        "answers": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["sequence_number", "answer", "analysis"],
            "properties": {
              "sequence_number": { "type": "integer" },
              "answer": { "type": "string" },
              "analysis": { "type": "string" },
              "scoring_criteria": { "type": "string", "description": "主观题的评分标准" }
            }
          }
        }
      }
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "level": { "type": "string", "enum": ["info", "warning"] },
          "message": { "type": "string" },
          "affected_sequence_numbers": { "type": "array", "items": { "type": "integer" } }
        }
      },
      "description": "组装过程中产生的非致命警告"
    }
  }
}
```

---

## 四、Quality Report 契约

**文件**：`harness/contracts/quality_report.json`
**生产者**：QualityChecker
**消费者**：PaperRenderer、Orchestrator

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "QualityReport",
  "type": "object",
  "required": ["_meta", "status", "checks"],
  "properties": {
    "_meta": {
      "type": "object",
      "required": ["generated_at", "generated_by", "paper_ref"],
      "properties": {
        "generated_at": { "type": "string", "format": "date-time" },
        "generated_by": { "const": "QualityChecker" },
        "paper_ref": { "type": "string", "description": "引用的 assembled_paper.json 哈希" },
        "plan_ref": { "type": "string", "description": "引用的 paper_plan.json 版本" }
      }
    },
    "status": {
      "type": "string",
      "enum": ["PASS", "PASS_WITH_WARNING", "REJECT", "FATAL"],
      "description": "PASS=全部通过 PASS_WITH_WARNING=仅非硬性项失败 REJECT=硬性项失败 FATAL=累计3次REJECT"
    },
    "checks": {
      "type": "object",
      "required": ["coverage", "difficulty", "question_type", "region", "dedup", "answer_completeness"],
      "properties": {
        "coverage": {
          "type": "object",
          "required": ["passed", "actual_rate", "required_rate"],
          "properties": {
            "passed": { "type": "boolean" },
            "actual_rate": { "type": "number" },
            "required_rate": { "type": "number" },
            "missing_tags": { "type": "array", "items": { "type": "string" } }
          }
        },
        "difficulty": {
          "type": "object",
          "required": ["passed", "deviation"],
          "properties": {
            "passed": { "type": "boolean" },
            "deviation": { "type": "number", "description": "实际难度分布与 Plan 的偏差" },
            "planned": { "type": "object", "description": "Plan 中的难度分布" },
            "actual": { "type": "object", "description": "实际题目的难度分布" }
          }
        },
        "question_type": {
          "type": "object",
          "required": ["passed", "mismatches"],
          "properties": {
            "passed": { "type": "boolean" },
            "mismatches": { "type": "array", "items": { "type": "object" } }
          }
        },
        "region": {
          "type": "object",
          "required": ["passed", "match_rate"],
          "properties": {
            "passed": { "type": "boolean" },
            "match_rate": { "type": "number" },
            "mismatched_items": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "sequence_number": { "type": "integer" },
                  "expected_region": { "type": "string" },
                  "actual_region": { "type": "string" }
                }
              }
            }
          }
        },
        "dedup": {
          "type": "object",
          "required": ["passed", "max_intra_similarity"],
          "properties": {
            "passed": { "type": "boolean" },
            "max_intra_similarity": { "type": "number", "description": "试卷内最高相似度" },
            "duplicate_pairs": { "type": "array" }
          }
        },
        "answer_completeness": {
          "type": "object",
          "required": ["passed", "missing_count"],
          "properties": {
            "passed": { "type": "boolean" },
            "missing_count": { "type": "integer" },
            "missing_sequence_numbers": { "type": "array", "items": { "type": "integer" } }
          }
        }
      }
    },
    "summary": {
      "type": "string",
      "description": "人类可读的检查总结"
    },
    "recommendations": {
      "type": "array",
      "items": { "type": "string" },
      "description": "REJECT 时的改进建议"
    }
  }
}
```

---

## 五、契约版本管理

| 契约文件 | 当前版本 | 最后更新 | 变更策略 |
|---------|:------:|---------|---------|
| `paper_plan.json` | 1.0.0 | 2026-06-14 | 新增字段必须向后兼容（`required` 不删字段） |
| `question_batch.json` | 1.0.0 | 2026-06-14 | 同上 |
| `assembled_paper.json` | 1.0.0 | 2026-06-14 | 同上 |
| `quality_report.json` | 1.0.0 | 2026-06-14 | 同上 |

> 契约版本管理规则：Major.Minor.Patch
> - Major：删除 required 字段 / 修改字段语义 → 所有消费者必须同步升级
> - Minor：新增非 required 字段 → 消费者可选升级
> - Patch：修正 description / 格式约束 → 消费者无需升级
