# API 参考文档

> 智能试卷生成系统 RESTful API 完整参考。

---

## 基础信息

- **Base URL**: `http://localhost:5000`
- **Content-Type**: `application/json`
- **字符编码**: UTF-8

---

## 端点总览

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/generate_paper` | 生成试卷（同步） |
| `POST` | `/api/generate_paper_async` | 生成试卷（异步） |
| `GET` | `/api/task_status/<task_id>` | 查询异步任务状态 |
| `GET` | `/api/task_result/<task_id>` | 获取异步任务结果 |
| `POST` | `/api/replace_question` | 替换单道题目 |
| `POST` | `/api/search_similar` | 搜索相似题目 |
| `GET` | `/api/stats` | 服务统计 |
| `GET` | `/api/cache_stats` | 缓存统计 |
| `GET` | `/api/task_stats` | 任务统计 |
| `GET` | `/metrics` | Prometheus 指标 |

---

## 1. 健康检查

```http
GET /api/health
```

**响应**：
```json
{
  "status": "ok"
}
```

---

## 2. 生成试卷（同步）

```http
POST /api/generate_paper
```

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `subject` | string | 是 | 学科 (math/chinese/english/physics/chemistry/biology/history/geography/politics) |
| `grade` | string | 是 | 年级 (grade_1 ~ grade_12 或 "初三"/"八年级") |
| `region` | string | 否 | 地域 (beijing/shanghai/guangdong)，默认不限 |
| `knowledge_points` | string[] | 否 | 知识点列表，默认自动分配 |
| `difficulty` | int | 否 | 难度 1-5，默认 3 |
| `num_questions` | int | 否 | 题目数量（1-100），默认 5 |

**请求示例**：
```json
{
  "subject": "math",
  "grade": "grade_9",
  "region": "beijing",
  "knowledge_points": ["一元二次方程", "二次函数"],
  "difficulty": 3,
  "num_questions": 10
}
```

**响应** (200)：
```json
{
  "questions": [
    {
      "type": "choice",
      "text": "方程 x² - 5x + 6 = 0 的解是？",
      "options": ["A. x=2,x=3", "B. x=-2,x=-3", "C. x=1,x=6", "D. x=-1,x=-6"],
      "answer": "A",
      "analysis": "因式分解 (x-2)(x-3)=0，所以 x₁=2, x₂=3"
    }
  ]
}
```

---

## 3. 生成试卷（异步）

```http
POST /api/generate_paper_async
```

请求体与同步接口相同。

**响应** (202)：
```json
{
  "task_id": "abc123def456",
  "status": "pending"
}
```

### 查询任务状态

```http
GET /api/task_status/<task_id>
```

**响应**：
```json
{
  "task_id": "abc123def456",
  "status": "running",
  "created_at": 1718000000.0,
  "elapsed": 5.2
}
```

状态枚举：`pending` → `running` → `done` | `error`

### 获取任务结果

```http
GET /api/task_result/<task_id>
```

**响应** (任务完成时)：
```json
{
  "status": "done",
  "result": {
    "questions": [...]
  }
}
```

**响应** (任务执行中)：
```json
{
  "status": "running",
  "message": "任务尚未完成"
}
```

**响应** (任务失败)：
```json
{
  "status": "error",
  "error": "检索超时"
}
```

> 任务超时时间：120 秒。超时后返回 `{"status": "timeout"}`。

---

## 4. 替换单道题目

```http
POST /api/replace_question
```

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `subject` | string | 是 | 学科 |
| `knowledge_points` | string[] | 否 | 知识点 |
| `difficulty` | int | 否 | 难度 1-5 |
| `question_index` | int | 否 | 要替换的题号（从 0 开始） |
| `current_type` | string | 否 | 原题型，系统尽量匹配同类 |

**请求示例**：
```json
{
  "subject": "math",
  "knowledge_points": ["勾股定理"],
  "difficulty": 3,
  "question_index": 2,
  "current_type": "choice"
}
```

**响应** (200)：
```json
{
  "question": {
    "type": "choice",
    "text": "在直角三角形中，两直角边分别为 3 和 4，斜边为？",
    "options": ["A. 5", "B. 6", "C. 7", "D. 8"],
    "answer": "A",
    "analysis": "根据勾股定理：3² + 4² = 5²"
  }
}
```

---

## 5. 搜索相似题目

```http
POST /api/search_similar
```

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `query` | string | 是 | 搜索关键词 |
| `subject` | string | 否 | 学科过滤 |
| `top_k` | int | 否 | 返回数量（1-50），默认 5 |
| `region` | string | 否 | 地域代码 |

**请求示例**：
```json
{
  "query": "一元一次方程应用题",
  "subject": "math",
  "top_k": 5,
  "region": "beijing"
}
```

**响应** (200)：
```json
{
  "results": [
    {
      "id": "mock_1",
      "text": "小明买了 3 个笔记本花了 21 元...",
      "subject": "math",
      "difficulty": 2,
      "score": 0.92
    }
  ]
}
```

---

## 6. 统计端点

### 服务统计

```http
GET /api/stats
```

```json
{
  "request_counts": {"generate_paper": 42, "search_similar": 15},
  "cache": {"hits_l1": 120, "hit_rate": 0.85},
  "tasks": {"total_tasks": 10, "by_status": {"done": 8, "running": 2}}
}
```

### 缓存统计

```http
GET /api/cache_stats
```

### 任务统计

```http
GET /api/task_stats
```

---

## 7. 错误处理

所有错误响应格式：

```json
{
  "error": "错误描述",
  "code": "ERROR_CODE"
}
```

| HTTP 状态码 | 说明 |
|:---------:|------|
| 200 | 成功 |
| 202 | 异步任务已接受 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 405 | 方法不允许 |
| 500 | 服务器内部错误 |
| 503 | 服务暂时不可用（数据未就绪） |

---

## 8. 客户端示例

### Python

```python
import requests

# 生成试卷
resp = requests.post("http://localhost:5000/api/generate_paper", json={
    "subject": "math",
    "grade": "grade_9",
    "region": "beijing",
    "knowledge_points": ["一元二次方程"],
    "num_questions": 5,
})
paper = resp.json()
for q in paper["questions"]:
    print(f"[{q['type']}] {q['text']}")
```

### JavaScript

```javascript
const resp = await fetch("http://localhost:5000/api/generate_paper", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    subject: "math",
    grade: "grade_9",
    region: "beijing",
    knowledge_points: ["一元二次方程"],
    num_questions: 5,
  }),
});
const paper = await resp.json();
```

### cURL

```bash
curl -s -X POST http://localhost:5000/api/generate_paper \
  -H "Content-Type: application/json" \
  -d '{"subject":"math","grade":"grade_9","region":"beijing",
       "knowledge_points":["一元二次方程"],"num_questions":5}' | python -m json.tool
```
