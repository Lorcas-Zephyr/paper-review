# 论文评阅 Mock API（paper_review_api）

## 简介

在**不调用真实 LLM、不依赖四 Agent 独立进程**的情况下，用单一 FastAPI 进程**模拟**格式、文献、逻辑、代码、实验等审计端点，返回**结构合法**、分数随机的 JSON。用于前端与调度器的**联调、压测与离线演示**，降低 Token 与等待成本。

## 在本系统中的位置

- **替代对象**：正常情况下由 8005–8008 等真实 Agent 提供的响应。
- **接入方式**：将 `orchestrator` 中各 Agent 的 `url` 改为指向本 Mock 的 **8080**（或本服务实际端口），使调度器所有请求打到同一主机上的多个路由。
- **与生产**：勿将 Mock 部署为对外生产服务。

## 工作流程

1. 启动 Mock 服务，注册与真实协议相近的路由。
2. 调度器按原样 POST，Mock 返回固定 Schema。
3. 可插入 `asyncio.sleep` 模拟慢响应，观察前端轮询与超时配置。

## 启动

```bash
pip install fastapi uvicorn pydantic
python main.py
```

默认 `http://0.0.0.0:8080`。具体路径与调度器配置一一对应需自行在 Mock 代码中核对。
