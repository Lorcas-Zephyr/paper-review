import argparse
import asyncio
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import numpy as np


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


from core.layout_analysis import LayoutAnalyzer
from core.rule_engine import RuleEngine
from core.semantic_check import SemanticChecker
from config import LAYOUT_ANALYSIS_TIMEOUT


class NpEncoder(json.JSONEncoder):
    def default(self, obj: Any):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _parse_pages_spec(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    raw = str(spec).strip()
    if not raw:
        return None
    out: set[int] = set()
    for part in re.split(r"[,\s]+", raw):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = (x.strip() for x in p.split("-", 1))
            if not a.isdigit() or not b.isdigit():
                continue
            start, end = int(a), int(b)
            if start <= 0 or end <= 0:
                continue
            if end < start:
                start, end = end, start
            for n in range(start, end + 1):
                out.add(n)
            continue
        if p.isdigit():
            n = int(p)
            if n > 0:
                out.add(n)
    if not out:
        return None
    return sorted(out)


def _norm_text(value: object) -> str:
    s = "" if value is None else str(value)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fmt_bbox(bbox: object) -> str:
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            x0, y0, x1, y1 = [float(x) for x in bbox]
            return f"[{x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f}]"
        except Exception:
            return _norm_text(bbox)
    return ""


def _write_issue_detail(f, issue: dict):
    evidence = issue.get("evidence")
    bbox = issue.get("bbox")
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    if not bbox and isinstance(location, dict):
        bbox = location.get("bbox")
    bbox_str = _fmt_bbox(bbox)
    if bbox_str:
        f.write(f"- **BBox**: {bbox_str}\n")
    if evidence:
        ev = _norm_text(evidence)
        if ev:
            if len(ev) <= 120:
                f.write(f"- **证据**: `{ev}`\n")
            else:
                f.write(f"- **证据**:\n\n```\n{ev}\n```\n")


async def run_audit(pdf_path: str, pages: str | None, output: str | None) -> None:
    rule_engine = RuleEngine()
    layout_analyzer = LayoutAnalyzer()
    semantic_checker = SemanticChecker()

    await rule_engine.load_rules_from_db()
    layout_analyzer.update_rules(rule_engine.rules)
    semantic_checker.update_rules(rule_engine.rules)

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    selected_pages = _parse_pages_spec(pages)
    selected_set = set(selected_pages or [])

    doc = fitz.open(pdf_path)
    try:
        text_content = ""
        for idx, page in enumerate(doc):
            page_num = idx + 1
            if selected_pages and page_num not in selected_set:
                continue
            text_content += page.get_text()

        try:
            layout_input = {"pdf_path": pdf_path, "pages": selected_pages} if selected_pages else pdf_path
            layout_data = await asyncio.wait_for(layout_analyzer.analyze(layout_input), timeout=LAYOUT_ANALYSIS_TIMEOUT)
        except asyncio.TimeoutError:
            layout_data = {
                "elements": [],
                "layout_result": {"layout_issues": []},
                "parse_errors": [{"error_type": "layout_timeout", "message": "layout analysis timeout"}],
                "parse_report": {"page_count": len(doc)} if doc else {},
            }
        except Exception as e:
            layout_data = {
                "elements": [],
                "layout_result": {"layout_issues": []},
                "parse_errors": [{"error_type": "layout_error", "message": str(e)}],
                "parse_report": {"page_count": len(doc)} if doc else {},
            }

        semantic_result = await semantic_checker.check(text_content, layout_data)
        layout_issues = layout_data.get("layout_result", {}).get("layout_issues", [])
        semantic_issues = semantic_result.get("semantic_issues", [])
        all_issues = layout_issues + semantic_issues

        score = semantic_checker._calculate_score(all_issues)
        counts = {"Critical": 0, "Warning": 0, "Info": 0}
        for issue in all_issues:
            level = issue.get("severity") or issue.get("level") or "Info"
            level = str(level)
            if level not in counts:
                level = "Info"
            counts[level] += 1

        critical = counts["Critical"]
        warning = counts["Warning"]
        info = counts["Info"]

        audit_level = "PASS"
        if score < 60:
            audit_level = "CRITICAL"
        elif score < 80:
            audit_level = "WARNING"

        print("\n" + "=" * 60)
        print(f"AUDIT REPORT: {os.path.basename(pdf_path)}")
        print(f"SCORE: {score}/100 ({audit_level})")
        print(f"TOTAL ISSUES: {len(all_issues)}")
        print("=" * 60)

        issues_by_type = {}
        for issue in all_issues:
            t = issue.get("issue_type", "Other")
            issues_by_type.setdefault(t, []).append(issue)

        for t, issues in issues_by_type.items():
            print(f"\n[ {t} ] - {len(issues)} issues")
            for issue in issues[:3]:
                msg = issue.get("message", "")
                pg = issue.get("page_num", "?")
                print(f"  - (Page {pg}) {msg}")
            if len(issues) > 3:
                print(f"  ... and {len(issues) - 3} more")

        repo_root = Path(__file__).resolve().parents[2]
        output_dir = repo_root / "paper"

        output_path = (output or "").strip()
        output_json_path: Path | None = None
        if output_path:
            if os.path.splitext(output_path)[1]:
                output_json_path = Path(output_path)
                output_dir = output_json_path.parent if str(output_json_path.parent) else Path(".")
            else:
                output_dir = Path(output_path)

        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        score_report_path = output_dir / f"{base_name}_score_report.md"
        deduction_report_path = output_dir / f"{base_name}_deduction_details.md"

        with open(score_report_path, "w", encoding="utf-8") as f:
            f.write("# 论文格式审计评分报告\n\n")
            f.write(f"**文件名**: {os.path.basename(pdf_path)}\n\n")
            f.write(f"**审计时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("## 审计结果\n")
            f.write(f"- **总分**: {score}/100\n")
            f.write(f"- **评级**: {audit_level}\n")
            f.write(f"- **问题总数**: {len(all_issues)}\n\n")
            f.write("## 问题统计\n")
            f.write(f"- **Critical (严重)**: {critical}\n")
            f.write(f"- **Warning (警告)**: {warning}\n")
            f.write(f"- **Info (提示)**: {info}\n\n")
            f.write("## 评分说明\n")
            f.write("本系统采用非线性扣分机制，避免单一类问题导致分数过低：\n")
            scoring = getattr(semantic_checker, "rules", {}) or {}
            scoring_cfg = scoring.get("scoring", {}) if isinstance(scoring, dict) else {}
            critical_w = float(scoring_cfg.get("critical_weight", 5.0) or 5.0)
            warning_w = float(scoring_cfg.get("warning_weight", 2.0) or 2.0)
            info_w = float(scoring_cfg.get("info_weight", 0.5) or 0.5)
            f.write(f"- **Critical**: 权重 {critical_w:g} (线性扣分)\n")
            f.write(f"- **Warning**: 权重 {warning_w:g} (平方根非线性扣分)\n")
            f.write(f"- **Info**: 权重 {info_w:g} (平方根非线性扣分)\n\n")
            deduction = critical_w * critical + warning_w * math.sqrt(warning) + info_w * math.sqrt(info)
            f.write(
                f"**总扣分计算**: `{critical_w:g} * {critical} + {warning_w:g} * sqrt({warning}) + {info_w:g} * sqrt({info})` ≈ `{deduction:.2f}`\n"
            )
            f.write(f"**最终得分**: `100 - {int(round(deduction))}` = `{score}`\n")

        with open(deduction_report_path, "w", encoding="utf-8") as f:
            f.write("# 论文格式审计扣分细则\n\n")
            f.write(f"**文件名**: {os.path.basename(pdf_path)}\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            if not all_issues:
                f.write("恭喜！未发现明显的格式问题。\n")
            else:
                f.write("## 1. 视觉布局分析 (CV Layout Analysis)\n")
                if not layout_issues:
                    f.write("未发现布局问题。\n\n")
                else:
                    layout_by_type = {}
                    for issue in layout_issues:
                        t = issue.get("issue_type", "Other")
                        layout_by_type.setdefault(t, []).append(issue)
                    for t, issues in layout_by_type.items():
                        f.write(f"### {t} ({len(issues)} 个问题)\n")
                        for i, issue in enumerate(issues):
                            msg = issue.get("message", "无描述")
                            pg = issue.get("page_num", "?")
                            severity = issue.get("severity", "Info")
                            suggestion = issue.get("suggestion", "")
                            f.write(f"#### {i+1}. [Page {pg}] {msg}\n")
                            f.write(f"- **严重程度**: {severity}\n")
                            if isinstance(issue, dict):
                                _write_issue_detail(f, issue)
                            if suggestion:
                                f.write(f"- **修改建议**: {suggestion}\n")
                            f.write("\n")
                f.write("\n")
                f.write("## 2. 语义内容分析 (LLM Semantic Analysis)\n")
                if not semantic_issues:
                    f.write("未发现语义问题。\n\n")
                else:
                    semantic_by_type = {}
                    for issue in semantic_issues:
                        t = issue.get("issue_type", "Other")
                        semantic_by_type.setdefault(t, []).append(issue)
                    for t, issues in semantic_by_type.items():
                        f.write(f"### {t} ({len(issues)} 个问题)\n")
                        for i, issue in enumerate(issues):
                            msg = issue.get("message", "无描述")
                            pg = issue.get("page_num", "?")
                            severity = issue.get("severity", "Info")
                            suggestion = issue.get("suggestion", "")
                            f.write(f"#### {i+1}. [Page {pg}] {msg}\n")
                            f.write(f"- **严重程度**: {severity}\n")
                            if isinstance(issue, dict):
                                _write_issue_detail(f, issue)
                            if suggestion:
                                f.write(f"- **修改建议**: {suggestion}\n")
                            f.write("\n")

        print("\nReports generated successfully:")
        print(f"1. Score Report: {score_report_path}")
        print(f"2. Deduction Details: {deduction_report_path}")

        if output_json_path and str(output_json_path).endswith(".json"):
            report = {"file": pdf_path, "score": score, "issues": all_issues}
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(report, f, cls=NpEncoder, ensure_ascii=False, indent=2)
            print(f"3. JSON Report: {output_json_path}")
    finally:
        try:
            doc.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages")
    parser.add_argument("--output")
    args = parser.parse_args()
    asyncio.run(run_audit(args.pdf, args.pages, args.output))


if __name__ == "__main__":
    main()
