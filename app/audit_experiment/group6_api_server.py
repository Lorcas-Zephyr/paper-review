import json
import re
import os
import sys
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import time

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from llm_config import get_deepseek_config

DEEPSEEK_CONFIG = get_deepseek_config()

# Try to import openai, handle missing dependency gracefully
try:
    from openai import OpenAI
except ImportError:
    print("错误: 缺少 'openai' 库。请运行 'pip install openai' 安装。")
    sys.exit(1)

try:
    import psycopg2
    from psycopg2.errors import UndefinedTable
except ImportError:
    psycopg2 = None
    UndefinedTable = Exception
    print("警告: 未安装 'psycopg2'，专家数据库功能将不可用。")

# --- 1. 定义数据模型 (Pydantic Models) ---
# 定义详细的审计结果条目
class AuditResultItem(BaseModel):
    id: str = Field(..., description="审计项唯一ID")
    point: str = Field(..., description="具体的审核点，如'统计学显著性检验'")
    score: int = Field(..., ge=0, le=100, description="评分 (0-100)")
    level: str = Field(..., pattern="^(Critical|Warning|Info)$", description="严重级别")
    description: str = Field(..., description="详细描述")
    evidence_quote: str = Field(..., description="原文引用证据")
    location: Optional[Dict[str, Union[str, int]]] = Field(None, description="问题定位信息")
    suggestion: str = Field(..., description="改进建议")

# 定义响应体结构
class AuditResponse(BaseModel):
    group_id: int = 6
    audit_results: List[AuditResultItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "group_id": 6,
                "audit_results": [
                    {
                        "id": "item-001",
                        "point": "统计学显著性检验",
                        "score": 85,
                        "level": "Warning",
                        "description": "实验三数据分布不均，未进行正态性检验。",
                        "evidence_quote": "原文第4.2节提到：'我们直接采用了T检验...'",
                        "location": {"section": "4.2", "line_start": 45},
                        "suggestion": "建议补充Shapiro-Wilk检验。"
                    }
                ]
            }
        }
    }

# 定义请求体结构
class AuditRequest(BaseModel):
    request_id: Optional[str] = Field(None, description="请求唯一标识符")
    paper_id: str = Field(..., description="论文唯一标识符")
    callback_url: Optional[str] = Field(None, description="异步回调地址")
    audit_scope: List[str] = Field(["abstract", "methodology", "experiment", "code"], description="指定需要审计的章节")
    model_preference: Optional[str] = Field("deepseek-v4-flash", description="偏好的AI模型")
    enable_rules: bool = Field(True, description="为 False 时跳过规则引擎与数据库结构化规则，仅用大模型做实验数据审阅")
    # 调度器传入与库一致的全文；未传时从专家库按 paper_id 聚合
    content: Optional[str] = Field(None, description="论文全文 Markdown；优先于数据库拉取")

# --- 2. 健康审查响应模型 ---
class HealthCheckResponse(BaseModel):
    status: str = Field(..., description="整体健康状态: healthy, degraded, unhealthy")
    service_name: str = Field(..., description="服务名称")
    version: str = Field(..., description="服务版本")
    timestamp: str = Field(..., description="检查时间戳")
    uptime_seconds: float = Field(..., description="服务运行时间(秒)")
    components: Dict[str, Dict[str, Any]] = Field(..., description="各组件健康状态详情")
    checks: List[Dict[str, Any]] = Field(..., description="健康检查项详情")

# --- 3. 统计学知识库 (Expert Knowledge Base - Task A) ---
STATISTICAL_KNOWLEDGE_BASE = """
【统计学专家知识库】
1. 显著性检验 (Significance Testing):
   - 必须报告 P-value (P值)，单纯的 "显著提高" 描述是无效的。
   - 0.01 <= P < 0.05 视为显著 (*)，P < 0.01 视为极显著 (**)。
   - 多次实验对比必须使用 T-test 或 Wilcoxon 检验。
   - 样本量 N < 30 时，必须先进行正态性检验 (Shapiro-Wilk Test)。

2. 误差报告 (Error Reporting):
   - 只要有均值 (Mean)，必须伴随标准差 (STD) 或标准误 (SEM)。
   - 格式规范：Mean ± STD (e.g., 95.2% ± 0.3%)。
   - 图表中必须包含误差棒 (Error Bars)。

3. 图表一致性 (Chart Consistency & Validity):
   - 文本中宣称的数值必须与图表（Markdown Table 或 SVG/Image 描述）一致。
   - 警惕"截断坐标轴"造成的视觉误导（例如坐标轴从 80% 开始，夸大差距）。
   - 【Task B重点】如果正文宣称某个数值（如99%），但表格或图表中该数值低于宣称值（如98%），视为严重欺诈（Critical）。

4. 基准对比 (Baseline):
   - 必须与至少一种 SOTA (State-of-the-Art) 方法对比。
   - 训练集和测试集必须严格划分，严禁数据泄露。
"""


class ExpertDatabase:
    def __init__(self):
        self.host = os.getenv("EXPERT_DB_HOST", "127.0.0.1")
        self.port = os.getenv("EXPERT_DB_PORT", "5432")
        self.dbname = os.getenv("EXPERT_DB_NAME", "")
        self.user = os.getenv("EXPERT_DB_USER", "postgres")
        self.password = os.getenv("EXPERT_DB_PASSWORD", "")
        self.paper_query = os.getenv(
            "EXPERT_DB_PAPER_QUERY",
            """
            SELECT CONCAT_WS(
                E'\n\n',
                'Title: ' || COALESCE(p.title, ''),
                'Abstract:\n' || COALESCE(p.abstract, ''),
                COALESCE(sec.sections_text, ''),
                COALESCE(para.paragraphs_text, ''),
                COALESCE(tab.tables_text, ''),
                COALESCE(cod.codes_text, '')
            ) AS content
            FROM public.papers p
            LEFT JOIN LATERAL (
                SELECT 'Sections:\n' || STRING_AGG(
                    '## ' || COALESCE(section_name, 'Unknown Section') || E'\n' || COALESCE(section_content, ''),
                    E'\n\n'
                    ORDER BY section_id
                ) AS sections_text
                FROM public.paper_sections
                WHERE paper_id = p.paper_id
            ) sec ON TRUE
            LEFT JOIN LATERAL (
                SELECT 'Paragraphs:\n' || STRING_AGG(
                    '[' || COALESCE(paragraph_name, 'Paragraph') || '] ' || COALESCE(paragraph_content, ''),
                    E'\n\n'
                    ORDER BY paragraph_id
                ) AS paragraphs_text
                FROM public.paper_paragraphs
                WHERE paper_id = p.paper_id
            ) para ON TRUE
            LEFT JOIN LATERAL (
                SELECT 'Tables:\n' || STRING_AGG(
                    'Caption: ' || COALESCE(table_caption, '') || E'\nBody:\n' || COALESCE(table_body, '') || E'\nFootnote: ' || COALESCE(table_footnote, ''),
                    E'\n\n'
                    ORDER BY table_id
                ) AS tables_text
                FROM public.tables
                WHERE paper_id = p.paper_id
            ) tab ON TRUE
            LEFT JOIN LATERAL (
                SELECT 'Codes:\n' || STRING_AGG(COALESCE(code_content, ''), E'\n\n' ORDER BY code_id) AS codes_text
                FROM public.codes
                WHERE paper_id = p.paper_id
            ) cod ON TRUE
            WHERE p.paper_id::text = %s
            LIMIT 1
            """
        )
        self.rules_query = os.getenv(
            "EXPERT_DB_RULES_QUERY",
            """
            SELECT CONCAT(
                '[', rule_code, '] ', rule_title,
                ' | indicator=', indicator_name,
                ' ', operator, ' ', COALESCE(threshold_value::text, 'NULL'),
                CASE WHEN threshold_secondary IS NOT NULL THEN CONCAT('(secondary=', threshold_secondary::text, ')') ELSE '' END,
                ' ', COALESCE(threshold_unit, ''),
                ' | severity=', severity,
                ' | weight=', ROUND(weight::numeric, 2)::text,
                ' | hard_rule=', CASE WHEN is_hard_rule THEN 'true' ELSE 'false' END,
                ' | text=', rule_text
            )
            FROM public.expert_comments
            WHERE active = TRUE
            ORDER BY is_hard_rule DESC, weight DESC, rule_code ASC
            """
        )

        self.pool_initialized = False
        if psycopg2 is None:
            return
        if not self.dbname or not self.password:
            print("警告: EXPERT_DB_NAME 或 EXPERT_DB_PASSWORD 未配置，专家数据库功能将不可用。")
            return

        self.pool_initialized = True

    def get_connection(self):
        if not self.pool_initialized:
            raise Exception("数据库未初始化")
        return psycopg2.connect(
            host=self.host, port=self.port, dbname=self.dbname,
            user=self.user, password=self.password
        )

    def health_check(self) -> Dict[str, Any]:
        """检查数据库连接健康状态"""
        check_result = {
            "status": "healthy",
            "component_type": "postgresql",
            "details": {
                "host": self.host,
                "port": self.port,
                "dbname": self.dbname,
                "user": self.user,
                "pool_initialized": self.pool_initialized
            },
            "timestamp": datetime.now().isoformat()
        }

        if not self.pool_initialized:
            check_result["status"] = "unhealthy"
            check_result["error"] = "数据库连接池未初始化，可能缺少依赖或配置"
            return check_result

        conn = None
        try:
            # 测试数据库连接
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchone()
                if result and result[0] == 1:
                    check_result["details"]["connection_test"] = "success"

                    # 获取数据库信息
                    cur.execute("SELECT version()")
                    db_version = cur.fetchone()
                    if db_version:
                        check_result["details"]["db_version"] = db_version[0]

                    # 检查关键表是否存在
                    try:
                        cur.execute("""
                            SELECT COUNT(*) FROM information_schema.tables
                            WHERE table_schema = 'public'
                            AND table_name IN ('papers', 'expert_comments')
                        """)
                        table_count = cur.fetchone()
                        check_result["details"]["essential_tables_found"] = table_count[0] if table_count else 0
                    except Exception as table_error:
                        check_result["details"]["table_check_error"] = str(table_error)
                        check_result["status"] = "degraded"
                else:
                    check_result["status"] = "unhealthy"
                    check_result["error"] = "数据库连接测试失败"

        except Exception as e:
            check_result["status"] = "unhealthy"
            check_result["error"] = f"数据库连接异常: {str(e)}"
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return check_result

    def open(self):
        print("专家数据库已配置为随用随连。")

    def close(self):
        print("专家数据库连接清理完成。")

    def fetch_paper_content(self, paper_id: str) -> Optional[str]:
        if not self.pool_initialized:
            return None
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(self.paper_query, (paper_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"读取论文内容失败: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def fetch_expert_knowledge(self) -> str:
        if not self.pool_initialized:
            return ""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(self.rules_query)
                rows = cur.fetchall()
                return "\n".join(str(r[0]) for r in rows if r and r[0]) if rows else ""
        except UndefinedTable:
            try:
                if conn:
                    conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT review_content FROM public.reviews WHERE review_content IS NOT NULL ORDER BY review_id ASC LIMIT 100"
                    )
                    rows = cur.fetchall()
                    return "\n".join(str(r[0]) for r in rows if r and r[0]) if rows else ""
            except Exception as fallback_error:
                print(f"读取专家规则失败(兜底也失败): {fallback_error}")
                return ""
        except Exception as e:
            print(f"读取专家规则失败: {e}")
            return ""
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def fetch_structured_rules(self) -> List[Dict[str, Any]]:
        if not self.pool_initialized:
            return []
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        rule_code,
                        rule_category,
                        rule_title,
                        rule_text,
                        indicator_name,
                        operator,
                        threshold_value,
                        threshold_secondary,
                        threshold_unit,
                        severity,
                        weight,
                        is_hard_rule,
                        evidence_pattern,
                        active
                    FROM public.expert_comments
                    WHERE active = TRUE
                    ORDER BY is_hard_rule DESC, weight DESC, rule_code ASC
                    """
                )
                rows = cur.fetchall()

                rules: List[Dict[str, Any]] = []
                for r in rows:
                    rules.append(
                        {
                            "rule_code": r[0],
                            "rule_category": r[1],
                            "rule_title": r[2],
                            "rule_text": r[3],
                            "indicator_name": r[4],
                            "operator": r[5],
                            "threshold_value": r[6],
                            "threshold_secondary": r[7],
                            "threshold_unit": r[8],
                            "severity": r[9],
                            "weight": float(r[10]) if r[10] is not None else 0.5,
                            "is_hard_rule": bool(r[11]),
                            "evidence_pattern": r[12] or "",
                            "active": bool(r[13]),
                        }
                    )
                return rules
        except UndefinedTable:
            return []
        except Exception as e:
            print(f"读取结构化规则失败: {e}")
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

# --- 4. Agent 实现 ---
class ExperimentDataAgent:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or DEEPSEEK_CONFIG.api_key
        self.base_url = base_url or DEEPSEEK_CONFIG.base_url
        self.model = model or DEEPSEEK_CONFIG.model
        if self.api_key:
             self.client = OpenAI(
                 api_key=self.api_key,
                 base_url=self.base_url,
                 timeout=DEEPSEEK_CONFIG.timeout_seconds,
             )
        else:
            self.client = None

    def health_check(self) -> Dict[str, Any]:
        """检查AI客户端健康状态"""
        check_result = {
            "status": "healthy",
            "component_type": "ai_client",
            "details": {
                "model": self.model,
                "base_url": self.base_url,
                "client_initialized": self.client is not None
            },
            "timestamp": datetime.now().isoformat()
        }

        if not self.client:
            check_result["status"] = "unhealthy"
            check_result["error"] = "AI客户端未初始化，API密钥可能未配置"
            return check_result

        # 测试AI服务连接
        try:
            # 尝试一个简单的ping请求
            start_time = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=10.0
            )
            end_time = time.time()

            check_result["details"]["ping_response"] = "success"
            check_result["details"]["response_time_ms"] = round((end_time - start_time) * 1000, 2)
            check_result["details"]["model_available"] = True

        except Exception as e:
            check_result["status"] = "unhealthy"
            check_result["error"] = f"AI服务连接失败: {str(e)}"
            check_result["details"]["ping_response"] = "failed"

        return check_result

    def run_full_audit(
        self,
        content: str,
        db_knowledge: str = "",
        structured_rules: Optional[List[Dict[str, Any]]] = None,
        *,
        enable_rules: bool = True,
    ) -> AuditResponse:
        """执行完整审计流程。enable_rules=False 时不跑规则引擎，仅保留大模型审计。"""
        if not enable_rules:
            structured_rules = []
        engine_results = self._run_rule_engine(content, structured_rules or [])

        if not self.client:
            if engine_results:
                return AuditResponse(group_id=6, audit_results=engine_results)
            return AuditResponse(
                group_id=6,
                audit_results=[
                    AuditResultItem(
                        id="sys-err",
                        point="System",
                        score=0,
                        level="Critical",
                        description="AI Client未初始化，无法执行审计。",
                        evidence_quote="N/A",
                        suggestion="检查API Key配置。",
                    )
                ],
            )

        merged_knowledge = STATISTICAL_KNOWLEDGE_BASE
        if db_knowledge:
            merged_knowledge += f"\n\n【专家数据库补充规则】\n{db_knowledge}"

        # Task B: 优化 Prompt - 增加图表异常检测与证据引用要求
        system_prompt = f"""
        你是由"反思评估组"监管的"第六组：实验数据审计Agent"。
        你的核心任务是作为一名【统计学专家】，依据以下知识库对论文片段进行严格审查。

        {merged_knowledge}

        【严禁幻觉】
        你指出的每一个问题（Critical/Warning），必须在 `evidence_quote` 字段中准确引用原文原句。
        如果找不到原文证据，请不要凭空捏造问题。

        【Task C: 复杂表格处理】
        请仔细比对文本描述与Markdown表格数据。

        请以 JSON 格式返回结果，严格遵守以下 JSON 结构：
        {{
            "group_id": 6,
            "audit_results": [
                {{
                    "id": "item-001",
                    "point": "统计学显著性检验",
                    "score": 85,
                    "level": "Warning",
                    "description": "...",
                    "evidence_quote": "...",
                    "location": {{"section": "...", "line_start": 0}},
                    "suggestion": "..."
                }}
            ]
        }}
        """

        user_prompt = f"""
        请审计以下论文内容：

        {content}

        请输出 JSON。
        """

        try:
            print(f"[*] Sending request to {self.model}...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=DEEPSEEK_CONFIG.temperature,
                max_tokens=DEEPSEEK_CONFIG.max_tokens,
                **({"response_format": {"type": "json_object"}} if DEEPSEEK_CONFIG.json_mode else {})
            )
            raw_text = response.choices[0].message.content
            llm_response = self._parse_json_safely(raw_text)
            merged = self._merge_audit_results(engine_results, llm_response.audit_results)
            return AuditResponse(group_id=6, audit_results=merged)

        except Exception as e:
            print(f"❌ Error: {e}")
            return AuditResponse(group_id=6, audit_results=engine_results)

    def _run_rule_engine(self, content: str, rules: List[Dict[str, Any]]) -> List[AuditResultItem]:
        if not content or not rules:
            return []

        findings: List[AuditResultItem] = []
        c = content
        c_lower = c.lower()

        has_p_value = bool(re.search(r"\bp\s*[- ]?value\b|\bp\s*[<=>]\s*0?\.\d+|p值", c_lower))
        has_sig_claim = bool(re.search(r"显著|显著提升|significant", c_lower))
        has_t_or_wilcoxon = bool(re.search(r"t-test|ttest|wilcoxon", c_lower))
        has_shapiro = bool(re.search(r"shapiro", c_lower))
        has_std_sem = bool(re.search(r"\bstd\b|\bsem\b|标准差|标准误|±", c_lower))
        has_error_bar = bool(re.search(r"error\s*bar|误差棒|置信区间", c_lower))
        has_baseline = bool(re.search(r"\bsota\b|baseline|state-of-the-art|对比", c_lower))
        has_split = bool(re.search(r"训练集|测试集|train|test|split", c_lower))
        has_leak = bool(re.search(r"数据泄露|leak", c_lower))

        claim_99 = bool(re.search(r"超过\s*99%|99\s*%", c_lower))
        all_perc = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", c)]
        any_below_99 = any(v < 99.0 for v in all_perc) if all_perc else False

        for rule in rules:
            code = str(rule.get("rule_code") or "")
            indicator = str(rule.get("indicator_name") or "")
            title = str(rule.get("rule_title") or "规则检查")

            triggered = False
            evidence = ""
            desc = ""
            suggestion = ""

            if indicator == "p_value_max":
                if has_sig_claim and not has_p_value:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"显著", r"significant", r"提升"], fallback="未发现P值报告")
                    desc = "检测到显著性结论，但未发现P值或等价显著性表达。"
                    suggestion = "补充P值并注明显著性检验方法（如T-test/Wilcoxon）。"

            elif indicator == "multi_group_test_required":
                if has_baseline and not has_t_or_wilcoxon:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"对比", r"baseline", r"sota"], fallback="检测到对比实验描述")
                    desc = "检测到多组/基线对比，但未检测到T-test/Wilcoxon等检验方法。"
                    suggestion = "为关键对比补充统计检验方法与结果。"

            elif indicator == "sample_n_min_for_normality_skip":
                if re.search(r"n\s*<\s*30|样本量\s*<\s*30", c_lower) and not has_shapiro:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"N\s*<\s*30", r"样本量"], fallback="发现小样本条件")
                    desc = "检测到小样本条件(N<30)，但未发现正态性检验证据。"
                    suggestion = "补充Shapiro-Wilk正态性检验，再选择T-test或非参数检验。"

            elif indicator == "mean_requires_dispersion":
                if ("mean" in c_lower or "均值" in c_lower or bool(all_perc)) and not has_std_sem:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"mean", r"均值", r"%"], fallback="发现均值/百分比结果")
                    desc = "检测到均值/百分比结果，但未发现STD/SEM/±等离散度指标。"
                    suggestion = "报告 Mean ± STD/SEM，并在图表中给出误差范围。"

            elif indicator == "error_bar_required":
                if ("table" in c_lower or "表" in c or "图" in c) and not has_error_bar:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"表", r"图", r"table"], fallback="检测到图表描述")
                    desc = "检测到图表比较，但未发现误差棒/置信区间信息。"
                    suggestion = "在图表中增加误差棒或置信区间。"

            elif indicator == "text_chart_value_gap_max":
                if claim_99 and any_below_99:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"99\s*%", r"\d+(?:\.\d+)?\s*%"], fallback="检测到宣称值与表格值可能不一致")
                    desc = "检测到宣称值与部分百分比结果存在潜在不一致（存在低于99%的结果）。"
                    suggestion = "核对正文宣称与表格数值的一致性，并明确是平均值还是最高值。"

            elif indicator == "sota_baseline_min_count":
                if not has_baseline:
                    triggered = True
                    evidence = content[:150].replace("\n", " ").strip() if content else "无原文"
                    desc = "未发现明确SOTA或基线对比。"
                    suggestion = "补充至少一种SOTA基线并报告同口径结果。"

            elif indicator == "data_leakage_forbidden":
                if has_leak or not has_split:
                    triggered = True
                    evidence = self._pick_evidence(c, [r"泄露", r"train", r"test", r"训练集", r"测试集"], fallback="未检测到清晰的数据划分说明")
                    desc = "数据划分描述不足或存在泄露风险提示。"
                    suggestion = "明确训练/测试划分流程，补充防泄露控制。"

            if triggered:
                findings.append(self._rule_to_item(rule, title, desc, evidence, suggestion, len(findings) + 1))

        return findings

    def _pick_evidence(self, content: str, patterns: List[str], fallback: str) -> str:
        for p in patterns:
            m = re.search(p, content, flags=re.IGNORECASE)
            if m:
                start = max(0, m.start() - 25)
                end = min(len(content), m.end() + 45)
                return content[start:end].replace("\n", " ").strip()
        # 如果没有匹配到更精准的，退退一步返回原文本的部分内容，而不能返回不是原文的 fallback
        # 取 content 的前 100 个字符作为 evidence，保证一定在原文中
        return content[:150].replace("\n", " ").strip() if content else fallback

    def _rule_to_item(self, rule: Dict[str, Any], point: str, description: str, evidence: str, suggestion: str, index: int) -> AuditResultItem:
        severity = str(rule.get("severity") or "Warning")
        if severity not in ("Critical", "Warning", "Info"):
            severity = "Warning"
        weight = float(rule.get("weight") or 0.5)
        score = max(0, min(100, int(round(weight * 100))))
        return AuditResultItem(
            id=f"rule-{index:03d}",
            point=point,
            score=score,
            level=severity,
            description=description,
            evidence_quote=evidence or "N/A",
            location={"section": "RuleEngine", "line_start": 0},
            suggestion=suggestion,
        )

    def _merge_audit_results(self, engine_results: List[AuditResultItem], llm_results: List[AuditResultItem]) -> List[AuditResultItem]:
        merged: List[AuditResultItem] = []
        seen = set()

        for item in engine_results + llm_results:
            key = (item.point.strip().lower(), item.level)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        for idx, item in enumerate(merged):
            item.id = f"item-{idx+1:03d}"
        return merged

    def _parse_json_safely(self, text: str) -> AuditResponse:
        try:
            # 简单清洗 Markdown
            clean_text = text
            if "```json" in clean_text:
                match = re.search(r"```json(.*?)```", clean_text, re.DOTALL)
                if match:
                    clean_text = match.group(1).strip()
            elif "```" in clean_text:
                clean_text = clean_text.replace("```", "")

            try:
                data = json.loads(clean_text)
            except json.JSONDecodeError:
                data = self._extract_first_json_object(clean_text)

            # 确保关键字段存在 (容错处理)
            if "audit_results" not in data:
                data["audit_results"] = []

            normalized_results: List[AuditResultItem] = []
            for idx, item in enumerate(data.get("audit_results", [])):
                normalized_results.append(self._normalize_audit_item(item, idx))

            return AuditResponse(group_id=6, audit_results=normalized_results)
        except Exception as e:
            print(f"JSON Parse Error: {e}\nRaw Text: {text}")
            return AuditResponse(group_id=6, audit_results=[
                AuditResultItem(
                    id="parse-error", point="JSON Format", score=0, level="Critical",
                    description=f"AI 返回格式解析失败: {str(e)}", evidence_quote="N/A", suggestion="系统错误"
                )
            ])

    def _extract_first_json_object(self, text: str) -> Dict:
        start = text.find("{")
        if start == -1:
            raise ValueError("未找到 JSON 对象起始符号 '{'")
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text[start:])
        if not isinstance(obj, dict):
            raise ValueError("提取到的 JSON 不是对象")
        return obj

    def _normalize_audit_item(self, item: Dict, idx: int) -> AuditResultItem:
        # 统一级别: 仅允许 Critical/Warning/Info
        raw_level = str(item.get("level", "Info") or "Info").strip().lower()
        level_map = {
            "critical": "Critical",
            "warning": "Warning",
            "info": "Info",
            "pass": "Info"
        }
        level = level_map.get(raw_level, "Info")

        # 评分归一到 0-100
        try:
            score = int(float(item.get("score", 60)))
        except Exception:
            score = 60
        score = max(0, min(100, score))

        raw_location = item.get("location") if isinstance(item.get("location"), dict) else {}
        section = str(raw_location.get("section", "Unknown") or "Unknown")
        line_start_raw = raw_location.get("line_start", 0)
        try:
            line_start = int(line_start_raw)
        except Exception:
            line_start = 0
        if line_start < 0:
            line_start = 0

        return AuditResultItem(
            id=str(item.get("id") or f"item-{idx+1:03d}"),
            point=str(item.get("point") or "未命名审核点"),
            score=score,
            level=level,
            description=str(item.get("description") or "未提供描述"),
            evidence_quote=str(item.get("evidence_quote") or "N/A"),
            location={"section": section, "line_start": line_start},
            suggestion=str(item.get("suggestion") or "建议补充对应证据与修正方案。")
        )

# --- 5. FastAPI 应用初始化 ---
app = FastAPI(title="Group 6 Audit Agent Service", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
RESULT_FILE = Path(__file__).resolve().parent / "audit_result.json"
latest_audit_result: Optional[AuditResponse] = None
startup_time = time.time()  # 记录服务启动时间

# 从环境变量读取
API_KEY = DEEPSEEK_CONFIG.api_key or None
BASE_URL = DEEPSEEK_CONFIG.base_url

global_agent = ExperimentDataAgent(api_key=API_KEY, base_url=BASE_URL)
expert_db = ExpertDatabase()


@app.on_event("startup")
async def on_startup():
    expert_db.open()
    startup_time = time.time()  # 重置启动时间


@app.on_event("shutdown")
async def on_shutdown():
    expert_db.close()

def save_audit_result_to_db(task_id: str, paper_id: str, result_obj, error_msg: str = None):
    if not expert_db.pool_initialized:
        print("ExpertDB not initialized, cannot save result to db.")
        return
    conn = None
    try:
        conn = expert_db.get_connection()
        cur = conn.cursor()

        # 计算 score, level, tokens, latency
        score = 85
        level = "Pass"
        if result_obj.audit_results:
            scores = [r.score for r in result_obj.audit_results if r.score is not None]
            if scores:
                score = sum(scores) // len(scores)
                if score < 60:
                    level = "Critical"
                elif score < 80:
                    level = "Warning"
                else:
                    level = "Pass"

                # 检查是否有单个 Critical 或 Warning 的项
                if any(r.level == "Critical" for r in result_obj.audit_results):
                    level = "Critical"
                elif level != "Critical" and any(r.level == "Warning" for r in result_obj.audit_results):
                    level = "Warning"
                    score = min(score, 79) # 有Warning项，最高不超过中等/良好分界线

        tokens = 0
        latency = 0

        import random, uuid, json
        random_id = random.getrandbits(63)
        def _safe_uuid(val):
            import uuid
            try:
                return str(uuid.UUID(str(val)))
            except:
                return str(uuid.uuid4())

        safe_task_id = _safe_uuid(task_id)
        safe_paper_id = _safe_uuid(paper_id)

        cur.execute("""
            INSERT INTO agent_audits (
                id, task_id, paper_id, chunk_id, agent_name, agent_version, status,
                score, audit_level, result_json, error_msg, usage_tokens, latency_ms,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """, (
            random_id, safe_task_id, safe_paper_id, "full_paper", "Experiment_Agent", "1.0", 'SUCCESS',
            score, level, json.dumps(result_obj.model_dump(), ensure_ascii=False), error_msg, tokens, latency
        ))
        conn.commit()
        cur.close()
        print(f"实验审计结果已保存到 agent_audits，task_id: {safe_task_id}", flush=True)
    except Exception as e:
        print(f"保存实验审计结果到数据库失败: {e}", flush=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.post("/audit", response_model=AuditResponse)
async def audit_endpoint(request: AuditRequest, background_tasks: BackgroundTasks):
    """
    接收来自中枢组或用户的审计请求。
    """
    print(f"收到审计请求: {request.paper_id}")

    # 获取审计内容
    # 逻辑：优先使用请求体中的 content (测试模式)。
    # 如果 content 为空，则优先查询专家数据库，未命中时使用默认 Mock 数据 (演示模式)。
    content_to_audit = request.content

    if not content_to_audit:
        content_to_audit = expert_db.fetch_paper_content(request.paper_id)
        if content_to_audit:
            print("未提供 content，已从专家数据库读取论文内容。")
        else:
            print("未提供 content 且数据库未命中，使用默认 Mock 数据用于演示异常检测 (Task B & C)。")
            content_to_audit = """
            [Default Mock Paper Content]
            ## 4. 实验结果
            本次实验在 MNIST 数据集上进行。
            我们提出的模型 (Ours) 准确率达到了 99.5%。
            下表展示了具体结果：

            | Model | Accuracy | F1-Score |
            |-------|----------|----------|
            | CNN   | 98.0%    | 0.97     |
            | Ours  | 98.2%    | 0.98     |

            如图所示，我们的方法显著优于 SOTA。
            但由于计算资源限制，我们未能计算 P-value。
            """

    # 执行审计
    db_knowledge = expert_db.fetch_expert_knowledge()
    structured_rules = expert_db.fetch_structured_rules()
    er = bool(getattr(request, "enable_rules", True))
    result = global_agent.run_full_audit(
        content_to_audit,
        db_knowledge=db_knowledge,
        structured_rules=structured_rules,
        enable_rules=er,
    )

    global latest_audit_result
    latest_audit_result = result
    try:
        RESULT_FILE.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:
        print(f"写入 audit_result.json 失败: {e}")

    # 模拟回调通知
    if request.callback_url:
        print(f"Callback registered: {request.callback_url}")

    # 保存结果到数据库
    background_tasks.add_task(save_audit_result_to_db, request.request_id, request.paper_id, result)

    return result


@app.get("/audit/latest", response_model=AuditResponse, summary="获取最近一次审计结果")
async def audit_latest_endpoint():
    global latest_audit_result

    if latest_audit_result:
        return latest_audit_result

    if RESULT_FILE.exists():
        try:
            data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            latest_audit_result = AuditResponse(**data)
            return latest_audit_result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"最近结果文件解析失败: {e}")

    raise HTTPException(status_code=404, detail="暂无审计结果，请先调用 /audit")

@app.get("/health", response_model=HealthCheckResponse, summary="健康审查端点")
async def health_check_endpoint():
    """
    健康审查端点，检查API服务核心组件状态。

    返回:
        - status: 整体健康状态 (healthy/degraded/unhealthy)
        - 各组件详情: FastAPI应用、数据库连接、AI客户端状态
        - 服务运行时间
    """
    checks = []
    components_status = {}

    # 1. 检查FastAPI应用状态
    app_check = {
        "component": "fastapi_app",
        "status": "healthy",
        "details": {
            "service_name": "Group 6 Audit Agent Service",
            "version": "1.0",
            "status": "running"
        },
        "timestamp": datetime.now().isoformat()
    }
    checks.append(app_check)
    components_status["fastapi_app"] = {
        "status": "healthy",
        "details": app_check["details"]
    }

    # 2. 检查数据库连接状态
    db_check = expert_db.health_check()
    checks.append(db_check)
    components_status["database"] = {
        "status": db_check["status"],
        "details": db_check.get("details", {}),
        "error": db_check.get("error")
    }

    # 3. 检查AI客户端状态
    ai_check = global_agent.health_check()
    checks.append(ai_check)
    components_status["ai_client"] = {
        "status": ai_check["status"],
        "details": ai_check.get("details", {}),
        "error": ai_check.get("error")
    }

    # 4. 检查文件系统状态
    try:
        file_check = {
            "component": "file_system",
            "status": "healthy",
            "details": {
                "result_file_path": str(RESULT_FILE),
                "result_file_exists": RESULT_FILE.exists(),
                "result_file_writable": os.access(RESULT_FILE.parent, os.W_OK)
            },
            "timestamp": datetime.now().isoformat()
        }
        if not file_check["details"]["result_file_writable"]:
            file_check["status"] = "degraded"
            file_check["error"] = "结果文件目录不可写"
    except Exception as e:
        file_check = {
            "component": "file_system",
            "status": "unhealthy",
            "error": f"文件系统检查失败: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }
    checks.append(file_check)
    components_status["file_system"] = {
        "status": file_check["status"],
        "details": file_check.get("details", {}),
        "error": file_check.get("error")
    }

    # 5. 检查审计结果缓存
    result_check = {
        "component": "audit_cache",
        "status": "healthy" if latest_audit_result else "info",
        "details": {
            "has_cached_result": latest_audit_result is not None,
            "cached_result_count": len(latest_audit_result.audit_results) if latest_audit_result else 0
        },
        "timestamp": datetime.now().isoformat()
    }
    checks.append(result_check)
    components_status["audit_cache"] = {
        "status": result_check["status"],
        "details": result_check["details"]
    }

    # 确定整体状态
    status_counts = {
        "healthy": 0,
        "degraded": 0,
        "unhealthy": 0,
        "info": 0
    }

    for check in checks:
        status = check.get("status", "healthy")
        status_counts[status] = status_counts.get(status, 0) + 1

    if status_counts["unhealthy"] > 0:
        overall_status = "unhealthy"
    elif status_counts["degraded"] > 0:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return HealthCheckResponse(
        status=overall_status,
        service_name="Group 6 Audit Agent Service",
        version="1.0",
        timestamp=datetime.now().isoformat(),
        uptime_seconds=round(time.time() - startup_time, 2),
        components=components_status,
        checks=checks
    )

# --- 6. 启动入口 ---
if __name__ == "__main__":
    print("启动 FastAPI 服务...")
    print("访问 http://localhost:8006/docs 查看接口文档")
    print("健康审查端点: GET http://localhost:8006/health")

    # 运行 uvicorn 服务器
    uvicorn.run(app, host="127.0.0.1", port=8006)
