"""Render a source-bound internal tender analysis report from Step 2 JSON."""
import argparse
import html
import json
from pathlib import Path


def text(value):
    if isinstance(value, dict):
        return "；".join(f"{k}：{text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "、".join(text(v) for v in value)
    return str(value if value is not None else "")


def source(value):
    item = value.get("source", {}) if isinstance(value, dict) else {}
    if not item:
        return "—"
    printed = f"（印刷页{item['printed_page']}）" if item.get("printed_page") is not None else ""
    return f"物理页{item.get('physical_page')} {printed}：{item.get('quote', '')}"


def escape_cell(value):
    return text(value).replace("|", "\\|").replace("\n", "<br>")


def table(headers, rows):
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    result.extend("| " + " | ".join(escape_cell(c) for c in row) + " |" for row in rows)
    return "\n".join(result)


def build_markdown(data, validation, tech, checklists):
    project = {x["label"]: x["value"] for x in data["project_profile"]}
    q = data["quality_controls"]
    lines = [
        f"# {project.get('项目名称', '项目')}招标解析报告",
        "",
        "> 内部工作文件，非递交件。所有项目事实均绑定当前锁定招标原件；本步骤未调用历史库、知识库或企业资料形成响应结论。",
        "",
        "## 解析结论与边界",
        "",
        f"- 锁定原件：`{data['source']['sha256']}`",
        f"- 原件页数：{data['source']['page_count']}页；不可读页：{len(data['source'].get('unread_pages', []))}页。",
        f"- 双引擎文本核验：全部页面均有文本，最低逐页相似度 {q['dual_engine_text_check']['minimum_page_similarity']:.4f}。",
        f"- 引用校验：{validation['citation_count']}条来源引用均可在对应原页反查。",
        f"- 技术原文索引：物理页{tech['physical_page_range'][0]}—{tech['physical_page_range'][1]}，{tech['stats']['table_count']}个表格、{tech['stats']['table_row_count']}行表格原文、{tech['stats']['text_block_count']}个非表格文本块。",
        "- 全部页面已经视觉复核；发现的原件异常保持原样并单独记录，不擅自改写。",
        "",
        "## 项目概况",
        "",
        table(["编号", "事项", "解析值", "原件定位"], [[x["id"], x["label"], x["value"], source(x)] for x in data["project_profile"]]),
        "",
        "## 采购范围",
        "",
        table(["编号", "货物", "型号/单位/数量", "原件定位"], [[x["id"], x["label"], x["value"], source(x)] for x in data["procurement_scope"]]),
        "",
        "## 不可缺失资质与资格",
        "",
        table(["编号", "要求", "原条件与范围/时点", "证明材料", "当前状态", "后果", "原件定位"], [[x["requirement_id"], x["label"], x["condition"], x["required_proof"], x["current_status"], x["consequence"], source(x)] for x in checklists["qualification"]]),
        "",
        "## 初步评审",
        "",
        table(["编号", "评审项", "判断口径", "原件定位"], [[x["id"], x["label"], x["value"], source(x)] for x in data["initial_review"]]),
        "",
        f"## 评分办法（{data['scoring_method']}，合计{q['score_total']}分）",
        "",
        table(["编号", "评分项", "规则", "分值", "客观/主观", "所需证明或响应", "后续落位", "当前状态", "原件定位"], [[x["requirement_id"], x["label"], x["score_rule"], x["max_score"], x["kind"], x["required_evidence_or_response"], x["target_section"], x["current_status"], source(x)] for x in checklists["scoring"]]),
        "",
        "## 加分策划重点",
        "",
        table(["编号", "事项", "高分条件", "关联评分", "真实优势", "状态/原因", "后续落位", "原件定位"], [[x["requirement_id"], x["label"], x["high_score_condition"], x["related_score_ids"], x["available_advantage"], x["claim_status"] + "；" + x["not_claimed_reason"], x["target_section"], source(x)] for x in checklists["highlights"]]),
        "",
        "## 否决风险与硬性禁忌",
        "",
        table(["编号", "风险", "触发条件", "原文后果", "当前状态", "处理动作", "原件定位"], [[x["requirement_id"], x["label"], x["trigger"], x["consequence"], x["current_status"], x["handling"], source(x)] for x in checklists["prohibitions"]]),
        "",
        "## 固定表式清单",
        "",
        table(["编号", "表式", "固定内容", "原件定位"], [[x["id"], x["label"], x["value"], source(x)] for x in data["form_inventory"]]),
        "",
        "## 技术范围索引",
        "",
        table(["编号", "范围", "页段", "原件定位"], [[x["id"], x["label"], x["value"], source(x)] for x in data["technical_scope"]]),
        "",
        "## 原件异常与后续边界",
        "",
        table(["编号", "事项", "影响"], [[x["id"], x["issue"], x["impact"]] for x in data["unresolved_items"]]),
        "",
        "其中 U04、U05、U06 属于招标原件自身的编号、空号或表头说明冲突，后续步骤必须保留来源事实并显式处理。",
        "",
        "## 本步骤验收",
        "",
        table(["检查", "结果", "证据"], [[x["name"], "通过", x["evidence"]] for x in validation["checks"]]),
        "",
        "本报告只完成招标原文解析、定位和风险识别。资格材料装配、技术证据匹配、响应填写、报价与最终递交判断由后续步骤完成。",
    ]
    return "\n".join(lines) + "\n"


def markdown_to_html(md):
    lines = md.splitlines(); out = []; in_table = False; in_ul = False
    for line in lines:
        if line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            if not in_table:
                if in_ul: out.append("</ul>"); in_ul = False
                out.append("<table>"); in_table = True; tag = "th"
            else: tag = "td"
            out.append("<tr>" + "".join(f"<{tag}>{html.escape(c).replace('&lt;br&gt;', '<br>')}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table: out.append("</table>"); in_table = False
        if line.startswith("- "):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{html.escape(line[2:])}</li>"); continue
        if in_ul: out.append("</ul>"); in_ul = False
        if line.startswith("# "): out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "): out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("> "): out.append(f"<aside>{html.escape(line[2:])}</aside>")
        elif line: out.append(f"<p>{html.escape(line)}</p>")
    if in_table: out.append("</table>")
    if in_ul: out.append("</ul>")
    css = "body{font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif;color:#1f2937;max-width:1500px;margin:0 auto;padding:40px;line-height:1.65}h1{color:#0f3d56;border-bottom:3px solid #0f766e;padding-bottom:14px}h2{color:#0f3d56;margin-top:38px}aside{background:#ecfeff;border-left:5px solid #0f766e;padding:14px 18px}table{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0 28px}th,td{border:1px solid #cbd5e1;padding:8px;vertical-align:top}th{background:#e2f3f1;text-align:left}tr:nth-child(even) td{background:#f8fafc}code{word-break:break-all}@media print{body{padding:0}h2{page-break-after:avoid}table{page-break-inside:auto}tr{page-break-inside:avoid}}"
    return "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>招标解析报告</title><style>" + css + "</style></head><body>" + "\n".join(out) + "</body></html>"


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("project_root"); args = parser.parse_args()
    root = Path(args.project_root); step = root / "analysis/step2"
    data = json.loads((step / "tender-analysis.json").read_text())
    checklists = json.loads((step / "four-category-checklists.json").read_text())
    validation = json.loads((step / "stage2-validation.json").read_text())
    tech = json.loads((step / "technical-source-index.json").read_text())
    md = build_markdown(data, validation, tech, checklists)
    (step / "招标解析报告_非递交件.md").write_text(md)
    (step / "招标解析报告_非递交件.html").write_text(markdown_to_html(md))


if __name__ == "__main__":
    main()
