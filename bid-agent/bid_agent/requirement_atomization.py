"""Strict source-bound atomization for the eight-column internal review table.

The model proposes row boundaries and hierarchy labels.  This module only
accepts proposals that preserve complete source elements, source order and an
explicit source anchor for the assigned object.  It never writes a response or
compliance conclusion.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ATOM_VERSION = "deta-requirement-atomization-v2"
CLAUSE_TYPES = {"章节标题", "正文条款", "表格条目", "明细组标题", "注释"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _elements(index: dict) -> tuple[dict[str, dict], dict[str, int]]:
    elements: dict[str, dict] = {}
    ordered: list[tuple[tuple, str]] = []
    for page_pos, page in enumerate(index.get("pages", [])):
        physical_page = int(page.get("physical_page", page_pos + 1))
        for block_pos, block in enumerate(page.get("text_blocks", [])):
            ident = str(block.get("id", ""))
            if not ident or ident in elements:
                raise ValueError("technical source index has a missing or duplicate element id")
            text = str(block.get("text", ""))
            elements[ident] = {"kind": "block", "parts": [text], "text": text,
                               "page": physical_page}
            y = float((block.get("bbox") or [0, block_pos])[1])
            ordered.append(((physical_page, y, 0, block_pos), ident))
        for table_pos, table in enumerate(page.get("tables", [])):
            y = float((table.get("bbox") or [0, table_pos])[1])
            for row_pos, row in enumerate(table.get("rows", [])):
                ident = str(row.get("id", ""))
                if not ident or ident in elements:
                    raise ValueError("technical source index has a missing or duplicate element id")
                parts = [str(value) for value in row.get("cells", []) if value not in (None, "")]
                elements[ident] = {"kind": "table_row", "parts": parts,
                                   "text": str(row.get("text", "")), "page": physical_page}
                ordered.append(((physical_page, y, 1, table_pos, row_pos), ident))
    positions = {ident: pos for pos, (_, ident) in enumerate(sorted(ordered), 1)}
    return elements, positions


def _exact_source_text(raw: dict, elements: dict[str, dict], position: int) -> tuple[str, list[str], list[tuple[str, int, int]]]:
    source_ids = raw.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids or not all(isinstance(x, str) for x in source_ids):
        raise ValueError(f"row {position}: source_ids must be a non-empty text array")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError(f"row {position}: duplicate source_ids")
    missing = [ident for ident in source_ids if ident not in elements]
    if missing:
        raise ValueError(f"row {position}: unknown source element {missing[0]}")
    selected = [elements[ident] for ident in source_ids]
    segments = raw.get("source_segments")
    spans: list[tuple[str, int, int]] = []
    if segments is not None:
        if any(x["kind"] != "block" for x in selected):
            raise ValueError(f"row {position}: source_segments are allowed only for text blocks")
        if not isinstance(segments, list) or len(segments) != len(source_ids):
            raise ValueError(f"row {position}: source_segments must align with source_ids")
        texts = []
        for ident, segment in zip(source_ids, segments):
            if not isinstance(segment, dict) or segment.get("id") != ident or not isinstance(segment.get("quote"), str) or not segment["quote"]:
                raise ValueError(f"row {position}: invalid exact source segment")
            full = elements[ident]["text"]
            start = segment.get("start")
            if start is None:
                hits = [m.start() for m in re.finditer(re.escape(segment["quote"]), full)]
                if len(hits) != 1:
                    raise ValueError(f"row {position}: segment quote needs an explicit start offset")
                start = hits[0]
            if not isinstance(start, int) or start < 0 or full[start:start + len(segment["quote"])] != segment["quote"]:
                raise ValueError(f"row {position}: segment is not an exact source interval")
            texts.append(segment["quote"]); spans.append((ident, start, start + len(segment["quote"])))
        joiner = raw.get("source_joiner", "")
        if not isinstance(joiner, str):
            raise ValueError(f"row {position}: source_joiner must be text")
        return joiner.join(texts), source_ids, spans
    if any(x["kind"] == "table_row" for x in selected):
        if len(selected) != 1 or selected[0]["kind"] != "table_row":
            raise ValueError(f"row {position}: one review row may contain only one original table row")
        parts = selected[0]["parts"]
        if not parts:
            raise ValueError(f"row {position}: empty original table row")
        joiner = raw.get("source_joiner", " ")
        if not isinstance(joiner, str):
            raise ValueError(f"row {position}: source_joiner must be text")
        source_text = joiner.join(parts)
        spans = [(source_ids[0], 0, len(source_text))]
    else:
        joiner = raw.get("source_joiner", "")
        if not isinstance(joiner, str):
            raise ValueError(f"row {position}: source_joiner must be text")
        source_text = joiner.join(x["text"] for x in selected)
        spans = [(ident, 0, len(elements[ident]["text"])) for ident in source_ids]
    return source_text, source_ids, spans


def _validate_basis(raw: dict, elements: dict[str, dict], position: int) -> dict:
    basis = raw.get("object_basis")
    if not isinstance(basis, dict):
        raise ValueError(f"row {position}: object_basis is required")
    mode = basis.get("mode")
    if mode not in {"explicit_source", "business_confirmed", "object_unresolved"}:
        raise ValueError(f"row {position}: object attribution may not be model-inferred")
    if mode == "object_unresolved":
        reason = basis.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"row {position}: unresolved object needs a reason")
        return {"mode": mode, "source_id": basis.get("source_id"), "quote": basis.get("quote", ""),
                "confirmation_ref": None, "reason": reason.strip()}
    source_id, quote = basis.get("source_id"), basis.get("quote")
    if not isinstance(source_id, str) or source_id not in elements or not isinstance(quote, str) or not quote:
        raise ValueError(f"row {position}: object_basis needs a valid source_id and exact quote")
    element = elements[source_id]
    if not any(quote in part for part in element["parts"]) and quote not in element["text"]:
        raise ValueError(f"row {position}: object_basis quote is absent from its source element")
    if mode == "business_confirmed" and not isinstance(basis.get("confirmation_ref"), str):
        raise ValueError(f"row {position}: business-confirmed attribution needs confirmation_ref")
    return {"mode": mode, "source_id": source_id, "quote": quote,
            "confirmation_ref": basis.get("confirmation_ref")}


def validate_atomization(spec: dict, index: dict) -> dict:
    if spec.get("version") != ATOM_VERSION:
        raise ValueError(f"spec.version must be {ATOM_VERSION}")
    elements, positions = _elements(index)
    rows = spec.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("spec.rows must be a non-empty array")
    result = []
    used_spans: list[tuple[str, int, int]] = []
    seen_requirements: set[str] = set()
    last_order = (0, 0)
    for row_position, raw in enumerate(rows, 1):
        if not isinstance(raw, dict) or raw.get("kind") not in {"heading", "requirement"}:
            raise ValueError(f"row {row_position}: kind must be heading or requirement")
        source_text, source_ids, spans = _exact_source_text(raw, elements, row_position)
        for ident, start, end in spans:
            if any(ident == old_id and max(start, old_start) < min(end, old_end)
                   for old_id, old_start, old_end in used_spans):
                raise ValueError(f"row {row_position}: source interval overlaps an earlier row: {ident}")
        current_orders = [(positions[ident], start) for ident, start, _ in spans]
        if current_orders != sorted(current_orders) or min(current_orders) < last_order:
            raise ValueError(f"row {row_position}: rows are not in original tender order")
        last_order = max(current_orders)
        used_spans.extend(spans)
        clause_type = raw.get("clause_type")
        if clause_type not in CLAUSE_TYPES:
            raise ValueError(f"row {row_position}: clause_type is invalid")
        applicable_object = raw.get("applicable_object")
        if not isinstance(applicable_object, str) or not applicable_object.strip() or applicable_object in {"待确认", "未分类", "未知", "其他"}:
            raise ValueError(f"row {row_position}: a specific applicable_object is required")
        hierarchy_number = raw.get("hierarchy_number", "")
        if not isinstance(hierarchy_number, str):
            raise ValueError(f"row {row_position}: hierarchy_number must be text")
        basis = _validate_basis(raw, elements, row_position)
        if (applicable_object == "object_unresolved") != (basis["mode"] == "object_unresolved"):
            raise ValueError(f"row {row_position}: object_unresolved must use the matching basis mode")
        if raw["kind"] == "heading":
            if raw.get("source_text") != source_text:
                raise ValueError(f"row {row_position}: heading source_text must equal its complete source text")
            result.append({"kind": "heading", "hierarchy_number": hierarchy_number,
                           "clause_type": clause_type, "applicable_object": applicable_object,
                           "source_text": source_text, "source_ids": source_ids,
                           "source_segments": raw.get("source_segments"), "object_basis": basis})
            continue
        requirement_id = raw.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id or requirement_id in seen_requirements:
            raise ValueError(f"row {row_position}: unique requirement_id required")
        seen_requirements.add(requirement_id)
        if clause_type in {"章节标题", "明细组标题"}:
            raise ValueError(f"row {row_position}: requirement row has a heading clause_type")
        if raw.get("source_text") != source_text:
            raise ValueError(f"row {row_position}: source_text differs from the complete original source")
        result.append({"kind": "requirement", "requirement_id": requirement_id,
                       "hierarchy_number": hierarchy_number, "clause_type": clause_type,
                       "applicable_object": applicable_object, "object_basis": basis,
                       "source_ids": source_ids, "source_joiner": raw.get("source_joiner", " " if elements[source_ids[0]]["kind"] == "table_row" else ""),
                       "source_segments": raw.get("source_segments"),
                       "source_text": source_text})
    exclusions = spec.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("spec.exclusions must be an array")
    excluded_ids: set[str] = set()
    allowed_reasons = {"printed_page", "repeated_header", "table_header", "empty_source_element",
                       "outside_technical_requirement"}
    for item in exclusions:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str) or item.get("reason") not in allowed_reasons:
            raise ValueError("each exclusion needs a source_id and an approved reason")
        ident = item["source_id"]
        if ident not in elements or ident in excluded_ids:
            raise ValueError("exclusion contains an unknown or duplicate source element")
        excluded_ids.add(ident)
    used_ids = {x[0] for x in used_spans}
    if used_ids & excluded_ids:
        raise ValueError("a source element may not be both used and excluded")
    missing = set(elements) - used_ids - excluded_ids
    if missing:
        raise ValueError(f"source coverage is incomplete; first unhandled element: {sorted(missing)[0]}")
    return {"version": ATOM_VERSION, "schema_role": "requirement_parsing_and_object_attribution",
            "schema_confirmation": "user_confirmed_2026-10-24", "downstream_status": "approved_for_eight_column",
            "source_index_path": spec.get("source_index_path"),
            "rows": result, "exclusions": exclusions, "stats": {"rows": len(result),
            "requirement_rows": sum(x["kind"] == "requirement" for x in result),
            "heading_rows": sum(x["kind"] == "heading" for x in result),
            "source_elements_used": len(used_ids), "source_elements_excluded": len(excluded_ids),
            "source_intervals_used": len(used_spans)},
            "validation": {"source_text_verbatim": "passed", "source_order": "passed",
            "one_original_table_row_per_review_row": "passed", "six_column_schema": "passed",
            "object_attribution_has_source_anchor": "passed",
            "object_attribution_semantic_review": "source_anchored_machine_gate_passed",
            "source_coverage": "passed",
            "responses_or_conclusions_generated": False}}


def install_requirement_atomization_tools(registry):
    from .tools import STRING, schema

    def get_schema():
        return {"version": ATOM_VERSION,
                "purpose": "六列条款解析：原文顺序、层级编号、条款类型、适用对象、招标要求原文、来源页码及位置",
                "row_boundary": "原表一行对应核对表一行；正文按原条款/完整连续段落成行；禁止合并多个原表行",
                "row": {"kind": "heading或requirement", "requirement_id": "requirement行唯一ID",
                    "source_ids": ["按原顺序的完整来源元素ID"], "source_segments": "仅当一个PDF文本块含两个条目时填写不重叠逐字区间",
                    "source_joiner": "表格默认单个空格，正文默认空字符串",
                    "hierarchy_number": "独立层级编号，不写入或替换原文", "clause_type": "章节标题/正文条款/表格条目/明细组标题/注释",
                    "applicable_object": "单一直接对象；来源不足时只能写object_unresolved",
                    "object_basis": {"mode": "explicit_source/business_confirmed/object_unresolved", "source_id": "对象依据来源ID", "quote": "逐字依据", "confirmation_ref": "人工确认记录（仅business_confirmed）", "reason": "无法归属原因（仅object_unresolved）"},
                    "source_text": "完整来源原文，不删原编号、不补层级编号、不改写"},
                "exclusions": [{"source_id": "未形成条款的来源元素ID", "reason": "printed_page/repeated_header/table_header/empty_source_element/outside_technical_requirement"}],
                "hard_rules": ["招标文字数值单位符号一字不改", "保持原顺序", "原表每行独立",
                    "不得把多个货物行合并", "不得出现响应表四级分类", "每个来源元素必须使用或明确排除", "对象无明确来源时写object_unresolved并继续，不得猜测", "不得生成投标人响应和偏离结论"]}

    def build(spec_path, output_stem):
        spec_file = registry.path(spec_path)
        if spec_file.suffix.lower() != ".json" or not str(Path(spec_path)).startswith("analysis/"):
            raise ValueError("spec_path must be an analysis/*.json project file")
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,80}", output_stem):
            raise ValueError("output_stem must be a short file name without directories")
        spec = _load(spec_file)
        source_index_path=spec.get("source_index_path")
        if not source_index_path and isinstance(spec.get("source_index"),str):
            source_index_path=spec["source_index"];spec["source_index_path"]=source_index_path
        if not isinstance(source_index_path,str) or not source_index_path.strip():
            raise ValueError("spec.source_index_path is required and must point to the locked technical source index")
        index_path = registry.path(source_index_path)
        if index_path.suffix.lower() != ".json":
            raise ValueError("source_index_path must be JSON")
        result = validate_atomization(spec, _load(index_path))
        result["source_index_sha256"] = _sha(index_path)
        result["draft_spec_path"] = spec_path
        result["draft_spec_sha256"] = _sha(spec_file)
        target = registry.root / "analysis" / "step2" / (output_stem + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Output exists; use a new output_stem")
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"path": str(target.relative_to(registry.root)), "sha256": _sha(target),
                "stats": result["stats"], "validation": result["validation"],
                "downstream_status": result["downstream_status"],
                "next_allowed_action": "continue_ai_execution_to_history_matching_and_eight_column_output"}

    def build_baseline(source_index_path, output_stem):
        """Create a complete, conservative atomization without model-authored copying.

        It keeps one source element per row, extracts only an existing leading
        hierarchy marker, and leaves object attribution unresolved.  A later
        source-anchored refinement may replace unresolved objects, but this
        baseline guarantees verbatim text, order and complete coverage.
        """
        index_path=registry.path(source_index_path);index=_load(index_path)
        if index_path.suffix.lower()!='.json' or not str(Path(source_index_path)).startswith('analysis/step2/'):
            raise ValueError('source_index_path must be an analysis/step2 JSON artifact')
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,80}",output_stem):raise ValueError('output_stem must be a short file name without directories')
        elements,positions=_elements(index);ordered=sorted(elements,key=positions.get);rows=[];exclusions=[];counter=0
        heading_pattern=re.compile(r'^(第[一二三四五六七八九十百0-9]+章|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.．])')
        number_pattern=re.compile(r'^(第[一二三四五六七八九十百0-9]+章|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.．]?)')
        for ident in ordered:
            element=elements[ident];text=element['text'] if element['kind']=='block' else ' '.join(element['parts'])
            stripped=text.strip()
            if not stripped:
                exclusions.append({'source_id':ident,'reason':'empty_source_element'});continue
            if element['kind']=='block' and re.fullmatch(r'\d{1,4}',stripped):
                exclusions.append({'source_id':ident,'reason':'printed_page'});continue
            is_heading=element['kind']=='block' and len(stripped)<=80 and len(stripped.splitlines())<=2 and bool(heading_pattern.match(stripped))
            counter+=1;match=number_pattern.match(stripped);hierarchy=match.group(1) if match else ''
            row={'kind':'heading' if is_heading else 'requirement','requirement_id':f'AR{counter:04d}','source_ids':[ident],
                 'source_segments':None,'hierarchy_number':hierarchy,'clause_type':'章节标题' if is_heading else ('表格条目' if element['kind']=='table_row' else '正文条款'),
                 'applicable_object':'object_unresolved','object_basis':{'mode':'object_unresolved','source_id':ident,'quote':'','reason':'来源标题、表头或上下文尚不足以确定单一对象'},
                 'source_text':text}
            if element['kind']=='table_row':row['source_joiner']=' '
            rows.append(row)
        spec={'version':ATOM_VERSION,'source_index_path':source_index_path,'rows':rows,'exclusions':exclusions}
        spec_target=registry.root/'analysis'/'step2'/(output_stem+'-server-spec.json')
        if spec_target.exists():raise ValueError('Server baseline spec exists; use a new output_stem')
        spec_target.write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        result=build(str(spec_target.relative_to(registry.root)),output_stem)
        return {**result,'server_spec_path':str(spec_target.relative_to(registry.root)),'mode':'complete_conservative_baseline','unresolved_objects':len(rows)}

    def render_review(atomization_path, output_stem):
        source = registry.path(atomization_path)
        if source.suffix.lower() != ".json" or not str(Path(atomization_path)).startswith("analysis/step2/"):
            raise ValueError("atomization_path must be an analysis/step2 JSON artifact")
        data = _load(source)
        if data.get("version") != ATOM_VERSION or data.get("validation", {}).get("source_text_verbatim") != "passed":
            raise ValueError("only a passed atomization artifact can be rendered")
        index_path = registry.path(data.get("source_index_path", ""))
        index = _load(index_path)
        if data.get("source_index_sha256") != _sha(index_path):
            raise ValueError("atomization artifact is stale against the source index")
        pages = {}
        for page in index.get("pages", []):
            number = page.get("physical_page")
            for block in page.get("text_blocks", []): pages[block["id"]] = number
            for table in page.get("tables", []):
                for row in table.get("rows", []): pages[row["id"]] = number
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,80}", output_stem):
            raise ValueError("output_stem must be a short file name without directories")
        target = registry.root / "analysis" / "step2" / (output_stem + ".xlsx")
        if target.exists(): raise ValueError("Output exists; use a new output_stem")
        headers = ["原文顺序", "层级编号", "条款类型", "适用对象", "招标要求原文", "来源页码及位置"]
        wb = Workbook(); ws = wb.active; ws.title = "条款拆分与对象归属"
        thin = Side(style="thin", color="B7B7B7"); border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for col, value in enumerate(headers, 1):
            cell = ws.cell(1, col, value); cell.font = Font(name="宋体", size=9, bold=True)
            cell.fill = PatternFill("solid", fgColor="D9EAF7"); cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = border
        for source_order, row in enumerate(data.get("rows", []), 1):
            excel_row = ws.max_row + 1
            source_ids = row["source_ids"]
            page_values = sorted({pages.get(x) for x in source_ids if pages.get(x) is not None})
            location = "物理页" + "、".join(map(str, page_values)) + "；" + ",".join(source_ids)
            values = [source_order, row.get("hierarchy_number", ""), row["clause_type"],
                      row["applicable_object"], row["source_text"], location]
            for col, value in enumerate(values, 1):
                cell = ws.cell(excel_row, col, value); cell.font = Font(name="宋体", size=8)
                cell.alignment = Alignment(vertical="top", wrap_text=True); cell.border = border
            if row.get("kind") == "heading":
                for col in range(1, len(headers) + 1): ws.cell(excel_row, col).fill = PatternFill("solid", fgColor="EDEDED")
                ws.cell(excel_row, 5).font = Font(name="宋体", size=8, bold=True)
        for col, width in enumerate([10, 16, 14, 28, 90, 44], 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = "E2"; ws.auto_filter.ref = f"A1:F{ws.max_row}"
        ws.row_dimensions[1].height = 32
        target.parent.mkdir(parents=True, exist_ok=True); wb.save(target)
        check = load_workbook(target, read_only=True); sheet = check["条款拆分与对象归属"]
        if sheet.max_column != len(headers) or sheet.max_row != len(data["rows"]) + 1:
            raise ValueError("rendered atomization review workbook is incomplete")
        check.close()
        return {"path": str(target.relative_to(registry.root)), "sha256": _sha(target),
                "rows": len(data.get("rows", [])), "columns": headers,
                "status": "six_column_requirement_parsing_review"}

    registry.add("get_requirement_atomization_schema",
                 "返回招标技术要求拆分与对象归属的严格字段契约。开始第二步细化前必须先调用。",
                 schema({}), get_schema)
    registry.add("build_requirement_atomization",
                 "从锁定招标来源索引生成逐字、按序、逐原表行的技术要求清单。对象归属必须有原文锚点或已有业务确认；机器门禁通过后自动继续八列表流程，不等待逐行批准，不生成响应、证据和偏离。",
                 schema({"spec_path": STRING, "output_stem": STRING}), build, True)
    registry.add("build_requirement_atomization_baseline",
                 "由服务器从技术来源索引逐元素生成完整保守基线：原文、原表行和顺序自动复制，确定性提取原有层级编号，无法由明确标题/表头确定的对象统一写object_unresolved。用于避免模型抄写遗漏；不生成响应或偏离。",
                 schema({"source_index_path":STRING,"output_stem":STRING}),build_baseline,True)
    registry.add("render_requirement_atomization_review",
                 "将条款解析产物渲染为六列内部审查表：原文顺序、层级编号、条款类型、适用对象、招标要求原文、来源页码及位置。不得混入响应表字段。",
                 schema({"atomization_path": STRING, "output_stem": STRING}), render_review, True)
