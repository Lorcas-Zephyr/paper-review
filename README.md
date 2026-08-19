# 软件工程硕士论文质量智能评价系统

本仓库是大学生创新创业训练项目“西北工业大学硕博论文质量智能评价方法”的工程工作区。当前成果是一个面向软件工程硕士论文的多智能体评阅原型：系统接收 PDF，转换为 Markdown，由逻辑、实验、格式和文献四个审计服务协同分析，再通过反思评估服务完成证据校验、冲突裁决、综合评分和报告生成。

> 当前状态：核心端到端原型已形成，但立项书承诺的千份数据集、强化学习评价策略、完整基线实验和结题材料尚未全部完成。详见[结题验收矩阵](docs/04-结题验收矩阵.md)。

## 仓库结构

```text
.
├── app/                         # 可运行系统源码
│   ├── website/                 # React + Ant Design 前端
│   ├── pdf_api/                 # PDF 上传服务
│   ├── pdf_to_md/               # PDF 转 Markdown 服务
│   ├── orchestrator/            # 多智能体调度、事件总线、任务持久化
│   ├── audit_logic/             # 逻辑与语义一致性审计
│   ├── audit_experiment/        # 实验设计与统计有效性审计
│   ├── audit_format/            # 格式、版面和写作规范审计
│   ├── audit_citation/          # 文献真实性、相关性和时效性审计
│   └── audit_reflection/        # 冲突消解、证据校验和综合报告
├── docs/                        # 项目分析与结题设计
├── materials/                   # 原始立项、答辩、指标、参考文献与架构图
│   ├── proposal/
│   ├── defense/
│   ├── figures/
│   └── reference/
└── README.md
```

## 设计文档

- [立项要求摘要](docs/01-立项要求摘要.md)：从原始立项书提炼研究目标、量化指标和交付物。
- [现状架构](docs/02-现状架构.md)：按实际源码记录服务、端口、数据流、技术栈和已知缺口。
- [结题目标架构与实施设计](docs/03-结题目标架构与实施设计.md)：给出从当前原型到可验收成果的设计。
- [结题验收矩阵](docs/04-结题验收矩阵.md)：逐项标记“已完成、部分完成、未完成”和验收证据。

原始文件保存在 [materials/proposal](materials/proposal) 和 [materials/defense](materials/defense)，未改写其内容。

## 本地运行

环境要求：Python 3.10+、Node.js LTS、PostgreSQL 14+、pgvector，以及一个兼容 OpenAI API 的大模型服务。PDF 转换服务使用本地 `mineru[core]` 推理，不再依赖远程 PDF 解析服务。

```bash
cp app/.env.example app/.env
cd app
pip install -r requirements-cuda.txt  # RTX/CUDA 机器；CPU 机器可用 requirements.txt
python scripts/download_local_models.py
cd website && npm ci && cd ..
bash start.sh
```

按实际环境填写 `app/.env`，不要提交密钥。启动脚本默认提供：前端 `3002`、上传 `5000`、转换 `8002`、文献 `8005`、实验 `8006`、格式 `8007`、逻辑 `8008`、反思 `8009`、调度器 `7860`。

首次部署需要下载开源模型：`python scripts/download_local_models.py`。脚本从 ModelScope 下载论文向量、格式/反思向量、逻辑 NLI 和 MinerU pipeline 到 `app/model_cache`，之后所有开源模型严格从本地加载并启用离线模式。DeepSeek 仍通过 API 调用，不下载权重。

## 项目边界

系统定位为专家辅助工具，不应替代学位论文最终人工评审。论文原文、评阅意见和专家标注均属于敏感数据；正式试验前必须完成脱敏、授权、访问控制和数据留存策略。
