# PDF 转 Markdown（pdf_to_md）

## 简介

读取磁盘上的 PDF 文件，使用 **PyMuPDF** 等库解析版式与文本，输出 **Markdown** 及可选的版面/内容列表 JSON，供前端填入编辑区并提交调度器评阅。

## 在本系统中的位置

- **上游**：通常接在 `pdf_api` 之后，由前端传入上一步得到的 `file_path`；也可独立调用。
- **下游**：Markdown 正文进入 `website` 状态，最终随 `POST /api/v1/audit` 进入 **orchestrator**。
- **默认端口**：**8002**。

## 工作流程

1. 接收转换请求（路径或上传标识，以 API 定义为准）。
2. 分页/分块解析，处理图、表、公式等（策略见实现）。
3. 返回 `markdown` 字符串及可选 `content_list`、任务 id 等。
4. 长文档可能耗时较长，前端与代理需足够超时时间。

## 启动

```bash
pip install -r requirements.txt
python main.py
```

与 `pdf_api`、调度器、数据库相互独立；不依赖 pgvector。
