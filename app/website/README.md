# 前端（Website）

## 简介

基于 **React** 与 **Ant Design** 的论文评阅控制台：支持 PDF 上传与解析链路、测试 Markdown 加载、发起多智能体评审、轮询任务状态、展示各组得分与反思结论，并可配置审计细则、导师对话等选项。

## 在本系统中的位置

- **上游**：用户浏览器。
- **下游**：
  - 调度器（默认 `7860`，提交评阅与查询任务）；
  - PDF 上传与转换服务（端口见 `src/apiConfig.js` / `setupProxy.js`）；
  - 反思服务健康检查等（如 8009）。
- **本开发服务默认端口**：3000（`npm start`）。

## 工作流程（用户侧）

1. 用户上传 PDF 或加载测试 Markdown，得到待评阅正文 `paperContent`。
2. 点击开始评审 → `axios.post` 调度器 `/api/v1/audit`，请求体含 `title`、`content`、`config`（`enable_rules`、`enable_mentor_dialogue` 等）。
3. 使用返回的 `request_id` 轮询 `/api/v1/task/...`，解析 `aggregated_report`：各 Agent 分数、等级、`reflection` 综合分与 `verdict`。
4. 界面将综合分映射为四档等级（优秀 / 良好 / 一般 / 较差），优先展示服务端 `verdict` 全文。

## 架构要点

- **代理**：`setupProxy.js` 将 `/proxy/orchestrator` 等前缀转发到本机各后端，避免 CORS 并延长超时，适配长任务。
- **结果解析**：对嵌套 `audit_results`、缺失顶层 `score` 等结构做防御性解析，避免页面异常。
- **配置集中**：API 基址等在 `apiConfig.js` 维护，与部署环境一致即可。

## 运行方式

```bash
cd website
npm install
npm start
```

浏览器访问 `http://localhost:3000`。生产构建：`npm run build`。
