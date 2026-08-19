# PDF 转 Markdown（pdf_to_md）

## 简介

读取磁盘上的 PDF 文件，使用 **MinerU 3.4.5 本地 pipeline** 解析版式、表格和公式，输出 **Markdown**、`content_list.json` 及 `layout.pdf`，供前端填入编辑区并提交调度器评阅。运行时不调用远程 PDF 解析服务。

## 在本系统中的位置

- **上游**：通常接在 `pdf_api` 之后，由前端传入上一步得到的 `file_path`；也可独立调用。
- **下游**：Markdown 正文进入 `website` 状态，最终随 `POST /api/v1/audit` 进入 **orchestrator**。
- **默认端口**：**8002**。

## 工作流程

1. 接收转换请求（路径或上传标识，以 API 定义为准）。
2. 在线程池中调用 `mineru.cli.common.do_parse`，避免阻塞 FastAPI 事件循环。
3. 递归收集 MinerU 输出，识别 Markdown、内容列表和版面 PDF。
4. 返回 `markdown` 字符串及可选 `content_list`、任务 id 等。
5. 长文档可能耗时较长，前端与代理需足够超时时间。

## 启动

```bash
pip install -r requirements.txt
python main.py
```

首次运行需要下载 MinerU 的模型文件；可通过 `MINERU_BACKEND`、`MINERU_PARSE_METHOD`、`MINERU_FORMULA_ENABLE` 和 `MINERU_TABLE_ENABLE` 调整本地解析策略。

模型可提前下载到本机缓存（示例使用 ModelScope；运行时仍是本地推理）：

```bash
python -m mineru.cli.models_download -s modelscope -m pipeline
```

如果目标机已经准备好模型目录，可将 `MINERU_MODEL_SOURCE=local` 写入 `app/.env`。

与 `pdf_api`、调度器、数据库相互独立；不依赖 pgvector。
