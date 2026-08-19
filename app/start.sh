#!/usr/bin/env bash

# ====================================================
# 学术论文评审系统 - 一键启动（Git Bash / Linux / macOS）
# 依赖：Python 3.10+、Node.js LTS；各子项目已 pip install 依赖
# ====================================================

set -u

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
export PYTHONUNBUFFERED=1

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/.env"
  set +a
fi

# ---------- 0. 统一日志目录 ----------
LOG_DIR="$SCRIPT_DIR/log"
mkdir -p "$LOG_DIR"

echo -e "${BLUE}工作目录: ${SCRIPT_DIR}${NC}"
echo -e "${BLUE}正在启动学术论文评审系统（全量服务）...${NC}"

# ---------- 1. 文件与解析 ----------
echo -e "${GREEN}[1/10] pdf_api 上传服务 :5000${NC}"
(cd "$SCRIPT_DIR/pdf_api" && python -u main.py) > "$LOG_DIR/logs_pdf_api.log" 2>&1 &

echo -e "${GREEN}[2/10] pdf_to_md 解析服务 :8002${NC}"
(cd "$SCRIPT_DIR/pdf_to_md" && python -u main.py) > "$LOG_DIR/logs_pdftomd.log" 2>&1 &

# ---------- 2. 四个审计 Agent（与 orchestrator 端口一致）----------
echo -e "${GREEN}[3/10] audit_citation 文献审计 :8005${NC}"
(cd "$SCRIPT_DIR/audit_citation" && python -u -m uvicorn main:app --host 0.0.0.0 --port 8005) > "$LOG_DIR/logs_agent_citation.log" 2>&1 &

echo -e "${GREEN}[4/10] audit_experiment 实验审计 :8006${NC}"
(cd "$SCRIPT_DIR/audit_experiment" && python -u group6_api_server.py) > "$LOG_DIR/logs_agent_experiment.log" 2>&1 &

echo -e "${GREEN}[5/10] audit_format 格式审计 :8007${NC}"
(cd "$SCRIPT_DIR/audit_format" && python -u main.py) > "$LOG_DIR/logs_agent_format.log" 2>&1 &

echo -e "${GREEN}[6/10] audit_logic 逻辑审计 :8008${NC}"
(cd "$SCRIPT_DIR/audit_logic" && python -u -m uvicorn src.logic_auditor.main:app --host 0.0.0.0 --port 8008) > "$LOG_DIR/logs_agent_logic.log" 2>&1 &

# ---------- 3. 反思评估 HTTP 服务（前端健康检查；调度器内嵌管线不依赖此进程）----------
echo -e "${GREEN}[7/10] audit_reflection 评估 API :8009${NC}"
(cd "$SCRIPT_DIR/audit_reflection" && python -u main.py) > "$LOG_DIR/logs_reflection_api.log" 2>&1 &

# ---------- 4. 调度器与前端 ----------
echo -e "${GREEN}[8/10] orchestrator 调度中心 :7860${NC}"
(cd "$SCRIPT_DIR/orchestrator" && python -u orchestrator.py) > "$LOG_DIR/logs_orchestrator.log" 2>&1 &

echo -e "${GREEN}[9/10] website 前端 :3002${NC}"
(cd "$SCRIPT_DIR/website" && PORT=3002 npm start) > "$LOG_DIR/logs_frontend.log" 2>&1 &

echo -e "${GREEN}[10/10] 全部已在后台启动${NC}"
sleep 2

check_health() {
  local name="$1"
  local url="$2"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      echo -e "${GREEN}[HEALTH] ${name} OK${NC}"
    else
      echo -e "${YELLOW}[HEALTH] ${name} FAIL -> ${url}${NC}"
    fi
  else
    echo -e "${YELLOW}[HEALTH] 未检测到 curl，跳过健康检查${NC}"
  fi
}

check_health "pdf_api" "http://127.0.0.1:5000/health"
check_health "pdf_to_md" "http://127.0.0.1:8002/health"
check_health "audit_citation" "http://127.0.0.1:8005/health"
check_health "audit_experiment" "http://127.0.0.1:8006/health"
check_health "audit_format" "http://127.0.0.1:8007/health"
check_health "audit_logic" "http://127.0.0.1:8008/health"
check_health "audit_reflection" "http://127.0.0.1:8009/health"
check_health "orchestrator" "http://127.0.0.1:7860/health"

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}前端:     http://localhost:3002${NC}"
echo -e "${BLUE}调度器:   http://localhost:7860/docs${NC}"
echo -e "${BLUE}反思API:  http://localhost:8009/docs${NC}"
echo -e "${YELLOW}日志位于 log/ 目录（log/logs_*.log）${NC}"
echo -e "${YELLOW}停止进程: 在任务管理器中结束对应 Python/Node，或自行 pkill${NC}"
echo -e "${BLUE}====================================================${NC}"
