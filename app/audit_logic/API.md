# Deep Logic Auditor Agent API 文档

## 接口列表

| 接口路径                | 方法   | 描述                            |
| ------------------- | ---- | ----------------------------- |
| `/audit/integrated` | POST | 一体化审计接口（从数据库读取论文，完成语义建模和矛盾检测） |

***

## 数据库配置

| 配置项      | 值            |
| -------- | ------------ |
| Host     | 127.0.0.1   |
| Port     | 5432         |
| Database | postgres     |
| User     | admin        |
| Password | <通过环境变量设置>   |

**说明**：审计结果会自动写入数据库的 `agent_audits` 表。

***

## 1. `/audit/integrated` - 一体化审计接口

### 功能描述

一体化审计接口是一个端到端的解决方案，它会：

1. 从数据库中读取指定论文的完整内容
2. 自动进行论文切片和命题提取
3. 执行语义建模，构建语义关系图
4. 进行矛盾检测和逻辑分析
5. 生成审计结果并保存到数据库

### 请求格式

**请求方式：** POST

**请求参数：**

| 参数         | 类型     | 必填 | 说明                     |
| ---------- | ------ | -- | ---------------------- |
| `paper_id` | string | 是  | 论文ID（UUID格式，数据库中存在的论文） |

**请求示例：**

```
POST http://localhost:8000/audit/integrated?paper_id=c757eff2-76cd-4833-bc5d-51f2a162c4a0
```

### 响应格式

```json
{
  "group_id": 3,
  "paper_id": "c757eff2-76cd-4833-bc5d-51f2a162c4a0",
  "timestamp": "2026-03-14T10:30:00",
  "group_name": "逻辑审计组",
  "audit_results": [
    {
      "id": "item-3-001",
      "level": "Critical",
      "point": "Contradictory Claim",
      "score": 60,
      "location": {
        "section": "chunk_5",
        "line_start": 12
      },
      "suggestion": "请核实实验数据，确保前后一致",
      "description": "数值矛盾：摘要声称 90%，正文声称 95%",
      "evidence_quote": "通过对比实验，本文方法准确率达到95%"
    },
    {
      "id": "item-3-002",
      "level": "Warning",
      "point": "Logic Leap",
      "score": 75,
      "location": {
        "section": "chunk_3",
        "line_start": 8
      },
      "suggestion": "添加\"因此\"\"所以\"等过渡词，使论证连贯",
      "description": "同一段落内句子之间缺乏逻辑衔接（缺少过渡词）",
      "evidence_quote": "实验结果显示... → 该方法有效..."
    }
  ]
}
```

**字段说明：**

| 字段                | 类型     | 说明                     |
| ----------------- | ------ | ---------------------- |
| `group_id`        | int    | 组ID（固定为3）               |
| `paper_id`        | string | 论文ID                    |
| `timestamp`       | string | 审计时间戳（ISO格式）            |
| `group_name`      | string | 组名（固定为"逻辑审计组"）        |
| `audit_results`   | array  | 审计结果列表                  |
| `audit_results[].id` | string | 审计项目ID                 |
| `audit_results[].level` | string | 审计级别（Critical/Warning/Info） |
| `audit_results[].point` | string | 问题类型（空格分隔的英文）          |
| `audit_results[].score` | int    | 该问题的评分                  |
| `audit_results[].location` | object | 问题位置信息                 |
| `audit_results[].location.section` | string | 所在章节或切片                |
| `audit_results[].location.line_start` | int    | 起始行号                    |
| `audit_results[].suggestion` | string | 改进建议                    |
| `audit_results[].description` | string | 问题描述                    |
| `audit_results[].evidence_quote` | string | 证据引用                    |

### 处理流程

1. **数据读取**：从数据库中获取论文的完整内容
2. **切片处理**：将论文切分为多个切片，并提取命题
3. **语义建模**：使用NLI模型计算命题间的语义关系
4. **矛盾检测**：检测数值矛盾、逻辑跳跃等问题
5. **结果生成**：计算评分，生成审计报告
6. **存储**：将结果保存到数据库
7. **响应**：返回审计结果给客户端

### 数据库存储

调用 `/audit/integrated` 接口后，审计结果会自动写入数据库的 `agent_audits` 表，包含以下字段：

| 字段             | 说明                     |
| -------------- | ---------------------- |
| id             | 自动生成的唯一ID              |
| task\_id       | 任务ID（系统自动生成）          |
| paper\_id      | 论文ID（从请求参数中获取）        |
| agent\_name    | 代理名称（逻辑审计组）            |
| agent\_version | 代理版本（1.0.0）             |
| status         | 状态（SUCCESS）            |
| score          | 审计评分（100表示无问题，60表示有严重问题） |
| audit\_level   | 审计级别（Pass/Warning）      |
| result\_json   | 完整审计结果（新格式JSON）         |
| latency\_ms    | 处理延迟                   |
| created\_at    | 创建时间                   |
| updated\_at    | 更新时间                   |

### 注意事项

- 该接口会占用较多计算资源，处理时间较长（取决于论文长度）
- 建议在后台任务中调用此接口
- 确保数据库中存在指定的论文ID

***

## 问题类型说明

| 问题类型   | 英文标识                  | 说明                       |
| ------ | --------------------- | ------------------------ |
| 矛盾声明   | `Contradictory_Claim` | 摘要与正文内容存在数值矛盾或语义矛盾       |
| 逻辑跳跃   | `Logic_Leap`          | 同一段落内句子之间缺乏逻辑衔接（缺少过渡词） |
| 无支持的论点 | `Unsupported_Arg`     | 论点缺乏证据支撑                 |
| 循环论证   | `Circular_Reasoning`  | 论证逻辑存在自我证明（如"本文证明...有效"） |

***

## 评分规则

### 单个问题评分

| 问题类型   | 评分  | 审计级别     |
| ------ | --- | -------- |
| 矛盾声明   | 60  | Critical |
| 逻辑跳跃   | 75  | Warning  |
| 无支持的论点 | 80  | Warning  |
| 循环论证   | 70  | Warning  |

### 整体评分

| 审计结果    | 评分  | 审计级别     |
| ------- | --- | -------- |
| 无问题     | 100 | Pass     |
| 存在任何问题 | 60  | Warning  |

***

## 启动服务

```bash
# 进入项目目录
cd DeepLogicAuditorAgent2

# 安装依赖
pip install -r requirements.txt

# 安装 psycopg2（用于数据库连接）
pip install psycopg2-binary

# 启动服务
uvicorn src.logic_auditor.main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后，访问 `http://localhost:8000/docs` 查看自动生成的Swagger文档。

***

## 完整工作流程

```
1. API 调用
   POST /audit/integrated?paper_id=xxx-xxx

2. 数据读取
   - 从数据库中获取论文的完整内容

3. 处理流程
   - 切片处理：将论文切分为多个切片，并提取命题
   - 语义建模：使用NLI模型计算命题间的语义关系
   - 矛盾检测：检测数值矛盾、逻辑跳跃等问题
   - 结果生成：生成符合规范的审计结果

4. 数据库存储
   - 审计结果自动写入 agent_audits 表

5. 响应返回
   - 返回新格式的审计结果，包含 group_id、paper_id、audit_results 等字段
```

***

## AI API 汇总

### Agent功能

- 语义建模：命题提取 + 关系识别 (NLI自然语言推理)
- 矛盾检测：基于规则的数值矛盾检测 (无需AI)
- 逻辑分析：检测逻辑跳跃、循环论证等问题

### 预计请求频率

- 单篇论文调用1次 `/audit/integrated` 接口即可完成整篇论文审计

### 文本处理规模

- 输入：一篇完整论文，约 5000-20000 字
- 输出：问题列表（通常 0-10 个问题）

### Token估算

- 单篇论文Token：约 10,000 tokens (取决于论文长度)
- 计算方式: 汉字 × 1.5 + 其他字符 × 0.3

### 所需模型

- 本地模型 (HuggingFace)：
  - `cross-encoder/nli-deberta-v3-base` 或
  - `cross-encoder/nli-roberta-base`
- 无需外部API（完全离线运行）

### 响应速度要求

- 3-5秒内完成单篇论文审计 (GPU加速)

### 特殊能力

- 不需要：联网搜索、长上下文、图像识别、函数调用
- 需要：GPU加速 (CUDA)

### 架构特点

| 组件    | 技术                               |
| ----- | -------------------------------- |
| NLI推理 | 本地HuggingFace模型 + PyTorch + CUDA |
| 矛盾检测  | 纯规则 (正则匹配数值)                     |
| 逻辑分析  | 规则+启发式方法                        |
| API服务 | FastAPI                          |
| 数据库   | PostgreSQL                       |

### 响应格式

- 符合统一规范的JSON格式
- 包含 group_id、paper_id、timestamp、group_name、audit_results 等字段
- audit_results 包含详细的问题信息，如级别、位置、建议等
