#!/bin/bash

echo "正在停止学术论文评审系统..."

# 1. 停止所有 Python 后端进程 (pdf-api, pdftomod, paper_review_api, orchestrator)
echo "正在关闭 Python 后端服务..."
taskkill //F //IM python.exe //T

# 2. 停止所有 Node/React 前端进程 (academic-ai-review)
echo "正在关闭 Node.js 前端服务..."
taskkill //F //IM node.exe //T

echo "----------------------------------------"
echo "所有相关进程已尝试关闭。"
echo "您可以运行 'ps' 或在任务管理器中确认 python.exe 和 node.exe 是否已消失。"
echo "----------------------------------------"
