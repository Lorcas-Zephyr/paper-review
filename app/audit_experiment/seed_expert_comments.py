import os
import psycopg


def build_embedding(
    threshold_value,
    sample_n_threshold,
    is_p_rule,
    require_dispersion,
    chart_consistency,
    baseline_required,
    severity,
    is_hard_rule,
):
    severity_score_map = {"Critical": 1.0, "Warning": 0.7, "Info": 0.4}
    severity_score = severity_score_map.get(severity, 0.5)

    tv = 0.0 if threshold_value is None else float(threshold_value)
    # Simple normalization to keep values in [0,1] for rule retrieval similarity.
    tv_norm = max(0.0, min(1.0, tv if tv <= 1 else tv / 100.0))

    n_th = 0.0 if sample_n_threshold is None else float(sample_n_threshold)
    n_norm = max(0.0, min(1.0, n_th / 100.0))

    vec = [
        tv_norm,
        n_norm,
        1.0 if is_p_rule else 0.0,
        1.0 if require_dispersion else 0.0,
        1.0 if chart_consistency else 0.0,
        1.0 if baseline_required else 0.0,
        severity_score,
        1.0 if is_hard_rule else 0.0,
    ]
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def main():
    cfg = dict(
        host=os.getenv("EXPERT_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("EXPERT_DB_PORT", "5432")),
        dbname=os.getenv("EXPERT_DB_NAME", "postgres"),
        user=os.getenv("EXPERT_DB_USER", "postgres"),
        password=os.getenv("EXPERT_DB_PASSWORD", ""),
    )

    rules = [
        {
            "rule_code": "STAT-P-001",
            "rule_category": "significance",
            "rule_title": "必须报告显著性P值",
            "rule_text": "论文若宣称显著提升，必须报告P值并说明检验方法。",
            "indicator_name": "p_value_max",
            "operator": "<",
            "threshold_value": 0.05,
            "threshold_secondary": 0.01,
            "threshold_unit": "p-value",
            "severity": "Critical",
            "weight": 0.95,
            "is_hard_rule": True,
            "evidence_pattern": "显著|显著提升|p-value|P值",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": True,
            "require_dispersion": False,
            "chart_consistency": False,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-P-002",
            "rule_category": "significance",
            "rule_title": "多组比较需要检验方法",
            "rule_text": "多组实验对比应使用T-test或Wilcoxon检验，不得仅给均值结论。",
            "indicator_name": "multi_group_test_required",
            "operator": "=",
            "threshold_value": 1,
            "threshold_secondary": None,
            "threshold_unit": "bool",
            "severity": "Warning",
            "weight": 0.85,
            "is_hard_rule": True,
            "evidence_pattern": "对比|baseline|T-test|Wilcoxon",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": True,
            "require_dispersion": False,
            "chart_consistency": False,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-N-001",
            "rule_category": "sample_size",
            "rule_title": "小样本需正态性检验",
            "rule_text": "当样本量N<30时，应先进行Shapiro-Wilk正态性检验。",
            "indicator_name": "sample_n_min_for_normality_skip",
            "operator": ">=",
            "threshold_value": 30,
            "threshold_secondary": None,
            "threshold_unit": "count",
            "severity": "Warning",
            "weight": 0.80,
            "is_hard_rule": True,
            "evidence_pattern": "样本量|N<30|Shapiro",
            "active": True,
            "sample_n_threshold": 30,
            "is_p_rule": False,
            "require_dispersion": False,
            "chart_consistency": False,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-E-001",
            "rule_category": "error_reporting",
            "rule_title": "均值必须配STD/SEM",
            "rule_text": "只报告Mean而不报告STD/SEM视为误差报告不完整。",
            "indicator_name": "mean_requires_dispersion",
            "operator": "=",
            "threshold_value": 1,
            "threshold_secondary": None,
            "threshold_unit": "bool",
            "severity": "Warning",
            "weight": 0.82,
            "is_hard_rule": True,
            "evidence_pattern": "mean|均值|STD|SEM",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": False,
            "require_dispersion": True,
            "chart_consistency": False,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-E-002",
            "rule_category": "error_reporting",
            "rule_title": "图表应包含误差棒",
            "rule_text": "图表若展示均值比较，应提供误差棒或不确定性范围。",
            "indicator_name": "error_bar_required",
            "operator": "=",
            "threshold_value": 1,
            "threshold_secondary": None,
            "threshold_unit": "bool",
            "severity": "Info",
            "weight": 0.65,
            "is_hard_rule": False,
            "evidence_pattern": "误差棒|error bar|置信区间",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": False,
            "require_dispersion": True,
            "chart_consistency": False,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-C-001",
            "rule_category": "consistency",
            "rule_title": "正文与图表数值一致",
            "rule_text": "正文宣称值必须与图表/表格一致，不一致需标记为高风险。",
            "indicator_name": "text_chart_value_gap_max",
            "operator": "<=",
            "threshold_value": 0,
            "threshold_secondary": None,
            "threshold_unit": "absolute_gap",
            "severity": "Critical",
            "weight": 0.97,
            "is_hard_rule": True,
            "evidence_pattern": "表|图|accuracy|F1|宣称",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": False,
            "require_dispersion": False,
            "chart_consistency": True,
            "baseline_required": False,
        },
        {
            "rule_code": "STAT-B-001",
            "rule_category": "baseline",
            "rule_title": "至少一个SOTA基线",
            "rule_text": "实验应与至少一种SOTA方法对比。",
            "indicator_name": "sota_baseline_min_count",
            "operator": ">=",
            "threshold_value": 1,
            "threshold_secondary": None,
            "threshold_unit": "count",
            "severity": "Warning",
            "weight": 0.78,
            "is_hard_rule": False,
            "evidence_pattern": "SOTA|baseline|对比",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": False,
            "require_dispersion": False,
            "chart_consistency": False,
            "baseline_required": True,
        },
        {
            "rule_code": "STAT-B-002",
            "rule_category": "baseline",
            "rule_title": "训练测试严格分离",
            "rule_text": "训练集与测试集必须严格划分，禁止数据泄露。",
            "indicator_name": "data_leakage_forbidden",
            "operator": "=",
            "threshold_value": 1,
            "threshold_secondary": None,
            "threshold_unit": "bool",
            "severity": "Critical",
            "weight": 0.92,
            "is_hard_rule": True,
            "evidence_pattern": "训练集|测试集|泄露|split",
            "active": True,
            "sample_n_threshold": None,
            "is_p_rule": False,
            "require_dispersion": False,
            "chart_consistency": False,
            "baseline_required": True,
        },
    ]

    ddl = """
    CREATE TABLE IF NOT EXISTS public.expert_comments (
        comment_id BIGSERIAL PRIMARY KEY,
        rule_code VARCHAR(64) UNIQUE NOT NULL,
        rule_category VARCHAR(64) NOT NULL,
        rule_title VARCHAR(128) NOT NULL,
        rule_text TEXT NOT NULL,
        indicator_name VARCHAR(128) NOT NULL,
        operator VARCHAR(8) NOT NULL,
        threshold_value DOUBLE PRECISION,
        threshold_secondary DOUBLE PRECISION,
        threshold_unit VARCHAR(32),
        severity VARCHAR(16) NOT NULL CHECK (severity IN ('Critical','Warning','Info')),
        weight DOUBLE PRECISION NOT NULL CHECK (weight >= 0 AND weight <= 1),
        is_hard_rule BOOLEAN NOT NULL DEFAULT FALSE,
        evidence_pattern TEXT,
        embedding VECTOR(8) NOT NULL,
        source VARCHAR(64) NOT NULL DEFAULT 'group6_seed',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
    );
    """

    upsert = """
    INSERT INTO public.expert_comments (
        rule_code, rule_category, rule_title, rule_text,
        indicator_name, operator, threshold_value, threshold_secondary, threshold_unit,
        severity, weight, is_hard_rule, evidence_pattern, embedding, source, active, updated_at
    ) VALUES (
        %(rule_code)s, %(rule_category)s, %(rule_title)s, %(rule_text)s,
        %(indicator_name)s, %(operator)s, %(threshold_value)s, %(threshold_secondary)s, %(threshold_unit)s,
        %(severity)s, %(weight)s, %(is_hard_rule)s, %(evidence_pattern)s, %(embedding)s::vector, 'group6_seed', %(active)s, NOW()
    )
    ON CONFLICT (rule_code) DO UPDATE SET
        rule_category = EXCLUDED.rule_category,
        rule_title = EXCLUDED.rule_title,
        rule_text = EXCLUDED.rule_text,
        indicator_name = EXCLUDED.indicator_name,
        operator = EXCLUDED.operator,
        threshold_value = EXCLUDED.threshold_value,
        threshold_secondary = EXCLUDED.threshold_secondary,
        threshold_unit = EXCLUDED.threshold_unit,
        severity = EXCLUDED.severity,
        weight = EXCLUDED.weight,
        is_hard_rule = EXCLUDED.is_hard_rule,
        evidence_pattern = EXCLUDED.evidence_pattern,
        embedding = EXCLUDED.embedding,
        source = EXCLUDED.source,
        active = EXCLUDED.active,
        updated_at = NOW();
    """

    with psycopg.connect(**cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            for r in rules:
                r = dict(r)
                r["embedding"] = build_embedding(
                    threshold_value=r.get("threshold_value"),
                    sample_n_threshold=r.get("sample_n_threshold"),
                    is_p_rule=r.get("is_p_rule", False),
                    require_dispersion=r.get("require_dispersion", False),
                    chart_consistency=r.get("chart_consistency", False),
                    baseline_required=r.get("baseline_required", False),
                    severity=r.get("severity", "Warning"),
                    is_hard_rule=r.get("is_hard_rule", False),
                )
                cur.execute(upsert, r)

            cur.execute("SELECT count(*) FROM public.expert_comments WHERE active = TRUE")
            print("active_rules=", cur.fetchone()[0])

            cur.execute(
                """
                SELECT rule_code, indicator_name, operator, threshold_value, severity, weight, embedding
                FROM public.expert_comments
                ORDER BY is_hard_rule DESC, weight DESC, rule_code ASC
                LIMIT 5
                """
            )
            for row in cur.fetchall():
                print(row)


if __name__ == "__main__":
    main()
