# Group 6 Experimental Data Audit API Reference

**Base URL**: `http://localhost:8000`

---

## 1. 提交审计请求 (Submit Audit)

提交论文内容或 ID 进行实验数据审计。

- **URL**: `/audit`
- **Method**: `POST`
- **Content-Type**: `application/json`

### 请求体 (Request Body)

| 字段名 | 类型 | 必填 | 默认值 | 描述 |
| :--- | :--- | :--- | :--- | :--- |
| `paper_id` | string | 是 | - | 论文唯一标识符 |
| `content` | string | 否 | null | [测试用] 直接传入待审计的文本内容。如果为空，将尝试从专家数据库读取。 |
| `callback_url` | string | 否 | null | 异步回调地址，用于接收审计完成通知 |
| `audit_scope` | array[string] | 否 | `["abstract", "methodology", "experiment", "code"]` | 指定需要审计的章节 |
| `model_preference` | string | 否 | `deepseek-chat` | 偏好的 AI 模型 |

**示例请求 (JSON)**:
```json
{
  "paper_id": "paper-2024-001",
  "content": "## 4. 实验结果\n本次实验在 MNIST 数据集上进行...",
  "audit_scope": ["experiment"],
  "model_preference": "deepseek-chat"
}
```

### 响应体 (Response Body)

返回 `AuditResponse` 对象。

| 字段名 | 类型 | 描述 |
| :--- | :--- | :--- |
| `group_id` | integer | 组 ID，固定为 6 |
| `audit_results` | array[object] | 审计结果列表 |

**AuditResultItem 对象结构**:

| 字段名 | 类型 | 描述 |
| :--- | :--- | :--- |
| `id` | string | 审计项唯一 ID |
| `point` | string | 具体的审核点（如"统计学显著性检验"） |
| `score` | integer | 评分 (0-100) |
| `level` | string | 严重级别 (`Critical`, `Warning`, `Info`) |
| `description` | string | 详细问题描述 |
| `evidence_quote` | string | 原文引用证据 |
| `location` | object | 问题定位信息 (如 `{"section": "4.2", "line_start": 45}`) |
| `suggestion` | string | 改进建议 |

**示例响应 (JSON)**:
```json
{
  "group_id": 6,
  "audit_results": [
    {
      "id": "item-001",
      "point": "统计学显著性检验",
      "score": 85,
      "level": "Warning",
      "description": "实验三数据分布不均，未进行正态性检验。",
      "evidence_quote": "原文第4.2节提到：'我们直接采用了T检验...'",
      "location": {
        "section": "4.2",
        "line_start": 45
      },
      "suggestion": "建议补充Shapiro-Wilk检验。"
    }
  ]
}
```

---

## 2. 获取最近一次审计结果 (Get Latest Audit)

获取服务器缓存的最近一次审计结果。

- **URL**: `/audit/latest`
- **Method**: `GET`

### 响应体 (Response Body)

结构与 `/audit` 接口的响应体一致。

**示例响应 (JSON)**:
```json
{
  "group_id": 6,
  "audit_results": [
    {
      "id": "item-001",
      "point": "统计学显著性检验",
      "score": 85,
      "level": "Warning",
      "description": "...",
      "evidence_quote": "...",
      "location": null,
      "suggestion": "..."
    }
  ]
}
```

---

## 数据模型定义 (Data Models)

### AuditRequest
```json
{
  "paper_id": "string",
  "callback_url": "string (optional)",
  "audit_scope": ["string"],
  "model_preference": "string",
  "content": "string (optional)"
}
```

### AuditResponse
```json
{
  "group_id": 6,
  "audit_results": [
    {
      "id": "string",
      "point": "string",
      "score": 0,
      "level": "Critical|Warning|Info",
      "description": "string",
      "evidence_quote": "string",
      "location": {
        "section": "string",
        "line_start": 0
      },
      "suggestion": "string"
    }
  ]
}
```
