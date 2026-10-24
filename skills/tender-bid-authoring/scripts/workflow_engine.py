#!/usr/bin/env python3
"""Mandatory ten-stage bid-production workflow.

The engine owns stage state, dependencies, blocking, content hashes and
invalidation.  It does not generate bid content.  Existing production tools
must submit their deterministic and agent results here before a candidate can
advance or be declared delivery-ready.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile
from business_rules import (
    check_blank_forms,
    check_blank_policy,
    check_certification_placement,
    check_commitment_coverage,
    check_scoring_and_qualification,
)
from skeleton_tools import (
    SkeletonError,
    check_docx_against_lock,
    check_requirement_coverage,
    check_skeleton_contract,
    check_slot_fill,
    check_structure_frozen,
    validate_binding,
)


SCHEMA_VERSION = "3.2"
WORKFLOW_VERSION = "3.2.0"
DEFAULT_SKILL_VERSION = "v1.38"
STATE_FILE = "workflow.json"
EVENT_FILE = "events.jsonl"

STAGE_DEFINITIONS: dict[int, dict[str, Any]] = {
    1: {"name": "项目模板与要求解析", "mode": "R-A-R", "requires": ["input_manifest", "format_baseline", "scoring_ledger", "prohibition_ledger", "responsiveness_ledger", "form_inventory", "section_tree", "requirement_coverage_matrix", "skeleton_bundle_manifest", "skeleton_docx", "skeleton_slot_manifest", "technical_requirement_manifest", "skeleton_lock", "skeleton_validation", "skeleton_render_manifest"]},
    2: {"name": "基础字段填写", "mode": "R", "requires": ["filled_base_docx", "fill_inventory", "intentional_blank_policy", "skeleton_binding", "slot_fill_manifest"]},
    3: {"name": "资格资料", "mode": "R-A-R", "requires": ["qualification_matrix", "qualification_decision"]},
    4: {"name": "商务得分资料", "mode": "R-A-R", "requires": ["commercial_scoring_matrix", "certification_placement", "commercial_candidate_docx", "skeleton_binding", "slot_fill_manifest"]},
    5: {"name": "承诺与声明", "mode": "R-A-R", "requires": ["commitment_inventory", "commitment_docx", "commitment_coverage", "skeleton_binding", "slot_fill_manifest"]},
    6: {"name": "技术资料接收", "mode": "R-A-R", "requires": ["technical_evidence_matrix", "evidence_slot_mapping"]},
    7: {"name": "技术评分响应", "mode": "R-A-R", "requires": ["technical_scoring_matrix", "technical_response_docx", "skeleton_binding", "slot_fill_manifest"]},
    8: {"name": "响应性核对", "mode": "R-A-R", "requires": ["response_check", "internal_deviation_list", "blank_forms_manifest", "business_candidate_docx", "skeleton_binding", "slot_fill_manifest"]},
    9: {"name": "装配与目录", "mode": "R", "requires": ["final_bundle_manifest", "final_slot_fill_manifest", "final_skeleton_binding", "final_blank_forms_manifest", "final_content_completion_manifest", "attachment_placement_manifest", "rendered_bundle_manifest", "page_map", "rendered_pages_manifest"]},
    10: {"name": "逐页终审", "mode": "R-A-R", "requires": ["page_review_manifest", "historical_feedback_review", "delivery_decision"]},
}
STAGE_COUNT = len(STAGE_DEFINITIONS)

V138_ARTIFACTS: dict[int, list[str]] = {
    1: STAGE_DEFINITIONS[1]["requires"] + ["technical_source_manifest", "technical_alignment_blank_xlsx"],
    7: ["technical_scoring_matrix", "technical_alignment_xlsx", "technical_plan_docx", "skeleton_binding", "slot_fill_manifest"],
    8: ["response_check", "technical_alignment_review", "blank_forms_manifest", "business_candidate_docx", "skeleton_binding", "slot_fill_manifest"],
}


def stage_required_artifacts(stage: int, skill_version: str) -> list[str]:
    return V138_ARTIFACTS.get(stage, STAGE_DEFINITIONS[stage]["requires"]) if skill_version == "v1.38" else STAGE_DEFINITIONS[stage]["requires"]

VALID_STATES = {"pending", "ready", "running", "awaiting_agent", "verifying", "candidate", "passed", "blocked", "stale"}
ADVANCE_STATES = {"candidate", "passed"}
AGENT_REQUIRED = {stage for stage, item in STAGE_DEFINITIONS.items() if "A" in item["mode"]}
RULEBASE_REQUIRED = set(STAGE_DEFINITIONS)


class WorkflowError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Invalid JSON: {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def append_event(root: Path, event: dict[str, Any]) -> None:
    previous = None
    event_path = root / EVENT_FILE
    if event_path.is_file():
        lines = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            previous = json.loads(lines[-1]).get("event_sha256")
    event = {
        "at": utc_now(),
        "previous_event_sha256": previous,
        "workflow_sha256": sha256(root / STATE_FILE) if (root / STATE_FILE).is_file() else None,
        **event,
    }
    event["event_sha256"] = canonical_hash(event)
    with event_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def verify_event_chain(root: Path) -> None:
    event_path = root / EVENT_FILE
    if not event_path.is_file():
        raise WorkflowError(f"Missing workflow event log: {event_path}")
    previous = None
    last = None
    for number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Invalid event JSON at line {number}") from exc
        stored_hash = event.pop("event_sha256", None)
        if event.get("previous_event_sha256") != previous or stored_hash != canonical_hash(event):
            raise WorkflowError(f"Workflow event chain failed at line {number}")
        previous = stored_hash
        last = event
    if last is None:
        raise WorkflowError("Workflow event log is empty")
    current_state_hash = sha256(root / STATE_FILE)
    if last.get("workflow_sha256") != current_state_hash:
        raise WorkflowError("workflow.json changed without a recorded workflow event")


@contextmanager
def workflow_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".workflow.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise WorkflowError(f"Workflow is locked by another operation: {lock_path}") from exc
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "at": utc_now()}).encode("utf-8"))
        os.close(descriptor)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def stage_key(stage: int) -> str:
    if stage not in STAGE_DEFINITIONS:
        raise WorkflowError(f"Stage must be 1..{STAGE_COUNT}, got {stage}")
    return f"{stage:02d}"


def dependencies(stage: int) -> list[int]:
    return [] if stage == 1 else [stage - 1]


def descendants(stage: int) -> Iterable[int]:
    return range(stage + 1, STAGE_COUNT + 1)


def empty_stage(stage: int) -> dict[str, Any]:
    item = STAGE_DEFINITIONS[stage]
    return {
        "number": stage,
        "name": item["name"],
        "mode": item["mode"],
        "dependencies": dependencies(stage),
        "state": "ready" if stage == 1 else "pending",
        "revision": 0,
        "reason": None,
        "started_at": None,
        "completed_at": None,
        "invalidated_at": None,
        "spec": None,
        "rulebase": None,
        "agent": None,
        "artifact_manifest": None,
        "artifacts": [],
        "dependency_fingerprints": {},
        "pass_fingerprint": None,
    }


def new_workflow(project_id: str, skill_version: str, rulebase_version: str | None) -> dict[str, Any]:
    created = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "skill_version": skill_version,
        "rulebase_version": rulebase_version,
        "project_id": project_id,
        "created_at": created,
        "updated_at": created,
        "delivery": {"status": "not_ready", "checked_at": None, "reason": "Ten stages are not complete."},
        "stages": {stage_key(i): empty_stage(i) for i in range(1, STAGE_COUNT + 1)},
    }


def load_workflow(root: Path) -> dict[str, Any]:
    verify_event_chain(root)
    workflow = read_json(root / STATE_FILE)
    if workflow.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError("Unsupported workflow schema version; keep the old run read-only and create a new ten-stage workflow")
    for i in range(1, STAGE_COUNT + 1):
        item = workflow.get("stages", {}).get(stage_key(i))
        if not item or item.get("state") not in VALID_STATES:
            raise WorkflowError(f"Invalid or missing stage {i}")
    return workflow


def save_workflow(root: Path, workflow: dict[str, Any]) -> None:
    workflow["updated_at"] = utc_now()
    atomic_write_json(root / STATE_FILE, workflow)


def resolve_ref(path_value: str, base: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (base / path).resolve()


def file_ref(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise WorkflowError(f"File does not exist: {path}")
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}


def ref_is_current(ref: dict[str, Any] | None) -> tuple[bool, str | None]:
    if not ref:
        return True, None
    path = Path(ref["path"])
    if not path.is_file():
        return False, f"missing file: {path}"
    actual = sha256(path)
    if actual != ref.get("sha256"):
        return False, f"hash changed: {path}"
    return True, None


def mark_stale(workflow: dict[str, Any], stage: int, reason: str, include_self: bool = True) -> list[int]:
    changed: list[int] = []
    targets = range(stage, STAGE_COUNT + 1) if include_self else descendants(stage)
    for number in targets:
        item = workflow["stages"][stage_key(number)]
        if item["state"] == "pending" and number != stage:
            continue
        if item["state"] != "stale" or item.get("reason") != reason:
            item["state"] = "stale"
            item["reason"] = reason
            item["invalidated_at"] = utc_now()
            item["pass_fingerprint"] = None
            changed.append(number)
    workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": reason}
    return changed


def update_ready_states(workflow: dict[str, Any]) -> None:
    for number in range(1, STAGE_COUNT + 1):
        item = workflow["stages"][stage_key(number)]
        if item["state"] not in {"pending", "ready"}:
            continue
        deps = dependencies(number)
        item["state"] = "ready" if all(
            workflow["stages"][stage_key(dep)]["state"] in ADVANCE_STATES for dep in deps
        ) else "pending"


def refresh(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if workflow.get("workflow_version") != WORKFLOW_VERSION:
        changed = mark_stale(workflow, 1, f"Workflow engine version changed: {workflow.get('workflow_version')} -> {WORKFLOW_VERSION}")
        findings.append({"stage": 1, "kind": "workflow_version", "reason": "engine version mismatch", "invalidated": changed})
    if workflow.get("skill_version") not in {"v1.37", DEFAULT_SKILL_VERSION}:
        changed = mark_stale(workflow, 1, f"Skill version changed: {workflow.get('skill_version')} -> {DEFAULT_SKILL_VERSION}")
        findings.append({"stage": 1, "kind": "skill_version", "reason": "skill version mismatch", "invalidated": changed})
    for number in range(1, STAGE_COUNT + 1):
        item = workflow["stages"][stage_key(number)]
        if item["state"] not in {"candidate", "passed", "blocked", "running", "awaiting_agent", "verifying"}:
            continue
        refs: list[tuple[str, dict[str, Any] | None]] = [
            ("spec", item.get("spec")),
            ("rulebase", item.get("rulebase")),
            ("artifact_manifest", item.get("artifact_manifest")),
        ]
        agent = item.get("agent") or {}
        refs.extend([("agent_task", agent.get("task")), ("agent_result", agent.get("result"))])
        refs.extend((f"artifact:{ref.get('id', '?')}", ref) for ref in item.get("artifacts", []))
        for label, ref in refs:
            current, reason = ref_is_current(ref)
            if not current:
                changed = mark_stale(workflow, number, f"Stage {number} {label} is stale: {reason}")
                findings.append({"stage": number, "kind": label, "reason": reason, "invalidated": changed})
                break
        if item["state"] == "stale":
            continue
        for dep in dependencies(number):
            dep_item = workflow["stages"][stage_key(dep)]
            expected = item.get("dependency_fingerprints", {}).get(stage_key(dep))
            if dep_item["state"] not in ADVANCE_STATES or not expected or dep_item.get("pass_fingerprint") != expected:
                changed = mark_stale(workflow, number, f"Stage {number} dependency {dep} changed or is incomplete")
                findings.append({"stage": number, "kind": "dependency", "reason": f"stage {dep}", "invalidated": changed})
                break
    update_ready_states(workflow)
    return findings


def validate_agent_task(task: dict[str, Any], stage: int) -> None:
    required = {"stage", "input_sources", "role", "task_requirements", "rulebase_locks", "output_contract"}
    missing = sorted(required - set(task))
    if missing:
        raise WorkflowError(f"Agent task is missing fields: {', '.join(missing)}")
    if int(task["stage"]) != stage:
        raise WorkflowError("Agent task stage does not match")
    for key in ("input_sources", "task_requirements", "rulebase_locks", "output_contract"):
        if not task[key]:
            raise WorkflowError(f"Agent task field must not be empty: {key}")


def validate_agent_result(result: dict[str, Any], stage: int, task_hash: str) -> None:
    required = {"stage", "task_sha256", "status", "observations"}
    missing = sorted(required - set(result))
    if missing:
        raise WorkflowError(f"Agent result is missing fields: {', '.join(missing)}")
    if int(result["stage"]) != stage or result["task_sha256"] != task_hash:
        raise WorkflowError("Agent result is not bound to the current task")
    if result["status"] not in {"pass", "candidate", "blocked"}:
        raise WorkflowError("Agent result status must be pass, candidate or blocked")
    if not isinstance(result["observations"], list) or not result["observations"]:
        raise WorkflowError("Agent result observations must be a non-empty list")
    if result["status"] == "candidate":
        if result.get("execution_mode") not in {"code_fallback", "agent_partial", "manual_pending"}:
            raise WorkflowError("Candidate agent result needs a recognized execution_mode")
        if not isinstance(result.get("open_items"), list) or not result["open_items"]:
            raise WorkflowError("Candidate agent result needs non-empty open_items")


def load_artifacts(manifest_path: Path, stage: int, skill_version: str = DEFAULT_SKILL_VERSION) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(manifest_path)
    if int(manifest.get("stage", -1)) != stage:
        raise WorkflowError("Artifact manifest stage does not match")
    raw_items = manifest.get("artifacts")
    if not isinstance(raw_items, list) or not raw_items:
        raise WorkflowError("Artifact manifest must contain artifacts")
    ids: set[str] = set()
    refs: list[dict[str, Any]] = []
    for item in raw_items:
        for field in ("id", "kind", "path"):
            if not item.get(field):
                raise WorkflowError(f"Artifact is missing {field}")
        if item["id"] in ids:
            raise WorkflowError(f"Duplicate artifact id: {item['id']}")
        ids.add(item["id"])
        path = resolve_ref(item["path"], manifest_path.parent)
        ref = {"id": item["id"], "kind": item["kind"], **file_ref(path)}
        if item.get("sha256") and item["sha256"].lower() != ref["sha256"]:
            raise WorkflowError(f"Artifact declared hash differs: {item['id']}")
        refs.append(ref)
    kinds = {item["kind"] for item in refs}
    required = set(stage_required_artifacts(stage, skill_version))
    missing = sorted(required - kinds)
    if missing:
        raise WorkflowError(f"Stage {stage} artifact kinds missing: {', '.join(missing)}")
    return manifest, refs


def validate_rendered_pages_manifest(path: Path, bundle_path: Path) -> dict[tuple[str, int], str]:
    data = read_json(path)
    bundle = {item["id"]: item for item in bundle_volumes(bundle_path)}
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise WorkflowError("Rendered pages manifest needs outputs")
    page_hashes: dict[tuple[str, int], str] = {}
    seen_outputs: set[str] = set()
    for output in outputs:
        output_id = str(output.get("output_id", ""))
        if output_id not in bundle or output_id in seen_outputs:
            raise WorkflowError(f"Invalid or duplicate rendered-page output: {output_id}")
        seen_outputs.add(output_id)
        if output.get("docx_sha256") != bundle[output_id]["sha256"]:
            raise WorkflowError(f"Rendered pages DOCX hash mismatch: {output_id}")
        pages = output.get("pages")
        if not isinstance(pages, list) or not pages:
            raise WorkflowError(f"Rendered pages are missing: {output_id}")
        seen_pages: set[int] = set()
        for page in pages:
            number = int(page.get("page", 0))
            if number < 1 or number in seen_pages:
                raise WorkflowError(f"Invalid or duplicate rendered page: {output_id}:{number}")
            seen_pages.add(number)
            image_path = resolve_ref(str(page.get("path", "")), path.parent)
            expected_hash = str(page.get("sha256", ""))
            if not image_path.is_file() or not expected_hash or sha256(image_path) != expected_hash:
                raise WorkflowError(f"Rendered page image is missing or changed: {output_id}:{number}")
            page_hashes[(output_id, number)] = expected_hash
        expected_pages = set(range(1, len(pages) + 1))
        if seen_pages != expected_pages:
            raise WorkflowError(f"Rendered page numbers are not continuous: {output_id}")
    if seen_outputs != set(bundle):
        raise WorkflowError(f"Rendered page output coverage mismatch; missing={sorted(set(bundle)-seen_outputs)}")
    return page_hashes


def validate_page_review(path: Path, rendered_pages_path: Path | None = None) -> bool:
    data = read_json(path)
    rendered_hashes = read_json(rendered_pages_path) if rendered_pages_path else None
    expected_page_hashes: dict[tuple[str, int], str] = {}
    if rendered_hashes:
        for output in rendered_hashes.get("outputs", []):
            output_id = str(output.get("output_id", ""))
            for page in output.get("pages", []):
                expected_page_hashes[(output_id, int(page.get("page", 0)))] = str(page.get("sha256", ""))
    outputs = data.get("final_outputs")
    pages = data.get("pages")
    if not isinstance(outputs, list) or not outputs or not isinstance(pages, list) or not pages:
        raise WorkflowError("Page review manifest needs final_outputs and pages")
    output_map: dict[str, dict[str, Any]] = {}
    for item in outputs:
        for field in ("id", "path", "sha256", "page_count"):
            if field not in item:
                raise WorkflowError(f"Final output is missing {field}")
        path_obj = Path(item["path"])
        if not path_obj.is_file() or sha256(path_obj) != item["sha256"]:
            raise WorkflowError(f"Final output changed or is missing: {item['id']}")
        output_map[item["id"]] = item
    required_page_fields = {
        "output_id", "page", "page_image_sha256", "page_type", "checklist_results",
        "visual_result", "visual_note", "reviewer", "review_method",
    }
    seen: dict[str, set[int]] = {key: set() for key in output_map}
    has_unresolved = False
    for page in pages:
        missing = required_page_fields - set(page)
        if missing:
            raise WorkflowError(f"Page review is missing fields: {', '.join(sorted(missing))}")
        output_id = page["output_id"]
        if output_id not in output_map:
            raise WorkflowError(f"Unknown page output id: {output_id}")
        number = int(page["page"])
        if number in seen[output_id]:
            raise WorkflowError(f"Duplicate page review: {output_id} page {number}")
        seen[output_id].add(number)
        if page["visual_result"] not in {"pass", "issue", "blocked", "unresolved"}:
            raise WorkflowError("Invalid page visual result")
        if page["visual_result"] != "pass":
            has_unresolved = True
        if expected_page_hashes and expected_page_hashes.get((output_id, number)) != page["page_image_sha256"]:
            raise WorkflowError(f"Page review image hash differs from stage 9 render: {output_id} page {number}")
        if not str(page["visual_note"]).strip() or not str(page["page_type"]).strip():
            raise WorkflowError("Page review needs a specific type and note")
        checks = page["checklist_results"]
        if not isinstance(checks, list) or not checks:
            raise WorkflowError("Every page needs applicable historical-checklist conclusions")
        for check in checks:
            if not all(str(check.get(key, "")).strip() for key in ("item_id", "result", "note")):
                raise WorkflowError("Checklist result needs item_id, result and note")
    for output_id, item in output_map.items():
        expected = set(range(1, int(item["page_count"]) + 1))
        if seen[output_id] != expected:
            missing = sorted(expected - seen[output_id])[:20]
            extra = sorted(seen[output_id] - expected)[:20]
            raise WorkflowError(f"Page coverage mismatch for {output_id}; missing={missing}, extra={extra}")
    if expected_page_hashes and set(expected_page_hashes) != {
        (output_id, number) for output_id, numbers in seen.items() for number in numbers
    }:
        raise WorkflowError("Page review does not cover the exact stage 9 rendered page set")
    return has_unresolved


def validate_delivery_decision(path: Path) -> None:
    data = read_json(path)
    if data.get("status") not in {"ready", "candidate", "blocked"}:
        raise WorkflowError("Delivery decision status must be ready, candidate or blocked")
    if not isinstance(data.get("blocking_items"), list) or not isinstance(data.get("business_open_items"), list):
        raise WorkflowError("Delivery decision needs blocking_items and business_open_items lists")
    if data["status"] == "ready" and data["blocking_items"]:
        raise WorkflowError("A ready delivery decision cannot contain blocking items")


def bundle_volumes(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    volumes = data.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise WorkflowError(f"Bundle manifest needs non-empty volumes: {path}")
    result = []
    ids = set()
    for item in volumes:
        if not item.get("id") or not item.get("path") or item["id"] in ids:
            raise WorkflowError("Bundle volume ids and paths must be present and unique")
        ids.add(item["id"])
        volume_path = resolve_ref(item["path"], path.parent)
        if not volume_path.is_file():
            raise WorkflowError(f"Bundle volume is missing: {volume_path}")
        actual_hash = sha256(volume_path)
        if item.get("sha256") and item["sha256"].lower() != actual_hash:
            raise WorkflowError(f"Bundle volume hash changed: {item['id']}")
        result.append({**item, "path": volume_path, "sha256": actual_hash})
    return result


def bundle_role(path: Path, role: str) -> Path:
    matches = [item["path"] for item in bundle_volumes(path) if item.get("role", item["id"]) == role]
    if len(matches) != 1:
        raise WorkflowError(f"Bundle requires exactly one {role} volume")
    return matches[0]


def validate_skeleton_render_manifest(path: Path, lock_path: Path) -> None:
    data = read_json(path)
    lock = read_json(lock_path)
    if data.get("skeleton_id") != lock.get("skeleton_id"):
        raise WorkflowError("Skeleton render manifest is not bound to the current skeleton")
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise WorkflowError("Skeleton render manifest needs outputs")
    expected = {item["id"]: item for item in lock.get("volumes", [])}
    seen = set()
    for item in outputs:
        volume_id = item.get("volume_id")
        if volume_id not in expected or volume_id in seen:
            raise WorkflowError(f"Invalid or duplicate rendered skeleton volume: {volume_id}")
        seen.add(volume_id)
        if item.get("docx_sha256") != expected[volume_id].get("sha256"):
            raise WorkflowError(f"Rendered skeleton does not match frozen DOCX: {volume_id}")
        pdf = resolve_ref(item.get("pdf_path", ""), path.parent)
        if not pdf.is_file() or (item.get("pdf_sha256") and sha256(pdf) != item["pdf_sha256"]):
            raise WorkflowError(f"Rendered skeleton PDF is missing or changed: {volume_id}")
        if int(item.get("page_count", 0)) < 1:
            raise WorkflowError(f"Rendered skeleton has no pages: {volume_id}")
    if seen != set(expected):
        raise WorkflowError(f"Skeleton render coverage mismatch; missing={sorted(set(expected)-seen)}")


def validate_final_render_manifest(path: Path, bundle_path: Path, lock_path: Path) -> None:
    data = read_json(path)
    lock = read_json(lock_path)
    if data.get("skeleton_id") != lock.get("skeleton_id"):
        raise WorkflowError("Final render manifest is not bound to the current skeleton")
    expected = {item["id"]: item for item in bundle_volumes(bundle_path)}
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise WorkflowError("Final render manifest needs outputs")
    seen = set()
    for item in outputs:
        volume_id = item.get("volume_id")
        if volume_id not in expected or volume_id in seen:
            raise WorkflowError(f"Invalid or duplicate final rendered volume: {volume_id}")
        seen.add(volume_id)
        if item.get("docx_sha256") != expected[volume_id]["sha256"]:
            raise WorkflowError(f"Final render DOCX hash mismatch: {volume_id}")
        pdf = resolve_ref(item.get("pdf_path", ""), path.parent)
        if not pdf.is_file() or (item.get("pdf_sha256") and sha256(pdf) != item["pdf_sha256"]):
            raise WorkflowError(f"Final rendered PDF is missing or changed: {volume_id}")
        if int(item.get("page_count", 0)) < 1:
            raise WorkflowError(f"Final rendered volume has no pages: {volume_id}")
    if seen != set(expected):
        raise WorkflowError(f"Final render coverage mismatch; missing={sorted(set(expected)-seen)}")


def validate_final_content_completion(
    path: Path,
    bundle_path: Path,
    lock_path: Path,
    slot_manifest_path: Path,
    technical_manifest_path: Path,
    fill_manifest_paths: Iterable[Path],
    attachment_manifest_path: Path,
    skill_version: str = "v1.37",
) -> list[str]:
    """Cross-check the completion claim against actual slots, rows and attachments."""
    data = read_json(path)
    lock = read_json(lock_path)
    slots = read_json(slot_manifest_path).get("slots", [])
    requirements = read_json(technical_manifest_path).get("requirements", [])
    attachment_data = read_json(attachment_manifest_path)
    attachment_items = {str(item.get("id", "")): item for item in attachment_data.get("items", []) if item.get("id")}
    bundle = {item["id"]: item for item in bundle_volumes(bundle_path)}
    errors: list[str] = []
    if data.get("skeleton_id") != lock.get("skeleton_id"):
        errors.append("content completion manifest is not bound to the current skeleton")
    if int(data.get("stage", -1)) != 9:
        errors.append("content completion manifest stage must be 9")
    volume_items = data.get("volumes")
    if not isinstance(volume_items, list) or not volume_items:
        return errors + ["content completion manifest needs non-empty volumes"]
    allowed = {"filled", "business_directed_blank", "source_missing", "not_applicable"}
    seen_volumes: set[str] = set()
    slots_by_volume: dict[str, set[str]] = {}
    for slot in slots:
        slots_by_volume.setdefault(str(slot.get("volume_id", "")), set()).add(str(slot.get("slot_id", "")))
    fill_status_by_volume: dict[str, dict[str, str]] = {}
    for fill_path in fill_manifest_paths:
        fill = read_json(fill_path)
        volume_id = str(fill.get("volume_id", ""))
        if not volume_id or volume_id in fill_status_by_volume:
            errors.append(f"missing or duplicate completion fill volume: {volume_id}")
            continue
        fill_status_by_volume[volume_id] = {
            str(item.get("slot_id", "")): str(item.get("status", ""))
            for item in fill.get("items", []) if item.get("slot_id")
        }
    for volume in volume_items:
        volume_id = str(volume.get("volume_id", ""))
        if volume_id not in bundle or volume_id in seen_volumes:
            errors.append(f"invalid or duplicate completion volume: {volume_id}")
            continue
        seen_volumes.add(volume_id)
        if volume.get("docx_sha256") != bundle[volume_id]["sha256"]:
            errors.append(f"completion manifest DOCX hash mismatch: {volume_id}")
        sections = volume.get("sections")
        if not isinstance(sections, list) or not sections:
            errors.append(f"completion volume has no section records: {volume_id}")
            continue
        covered_slots: set[str] = set()
        seen_sections: set[str] = set()
        for section in sections:
            section_id = str(section.get("section_id", ""))
            status = section.get("status")
            if not section_id or section_id in seen_sections:
                errors.append(f"missing or duplicate completion section: {volume_id}:{section_id}")
                continue
            seen_sections.add(section_id)
            if status not in allowed:
                errors.append(f"invalid completion status: {volume_id}:{section_id}:{status}")
                continue
            section_slots = {str(value) for value in section.get("slot_ids", []) if value}
            unknown = section_slots - slots_by_volume.get(volume_id, set())
            if unknown:
                errors.append(f"completion section references unknown slots: {volume_id}:{section_id}:{sorted(unknown)}")
            covered_slots.update(section_slots)
            evidence_ids = {str(value) for value in section.get("evidence_ids", []) if value}
            unknown_evidence = evidence_ids - set(attachment_items)
            if unknown_evidence:
                errors.append(f"completion section references unknown evidence: {volume_id}:{section_id}:{sorted(unknown_evidence)}")
            filled_slots = {
                slot_id for slot_id in section_slots
                if fill_status_by_volume.get(volume_id, {}).get(slot_id) == "filled"
            }
            embedded_evidence = {
                item_id for item_id in evidence_ids
                if attachment_items.get(item_id, {}).get("selected", True)
                and attachment_items.get(item_id, {}).get("embedded") is True
            }
            if status == "filled" and not filled_slots and not embedded_evidence:
                errors.append(f"filled section has no actually filled slot or embedded evidence: {volume_id}:{section_id}")
            if status != "filled" and not str(section.get("reason", "")).strip():
                errors.append(f"non-filled section lacks a specific reason: {volume_id}:{section_id}")
        missing_slots = slots_by_volume.get(volume_id, set()) - covered_slots
        if missing_slots:
            errors.append(f"completion sections do not cover all volume slots: {volume_id}:{sorted(missing_slots)}")
    if seen_volumes != set(bundle):
        errors.append(f"completion volume coverage mismatch; missing={sorted(set(bundle)-seen_volumes)}")
    technical = data.get("technical", {})
    expected_count = len(requirements)
    if int(technical.get("requirement_count", -1)) != expected_count:
        errors.append("technical requirement count differs from the frozen requirement manifest")
    if skill_version == "v1.38":
        if int(technical.get("alignment_count", -1)) != expected_count:
            errors.append("every frozen technical requirement must map to the internal alignment workbook")
        if int(technical.get("formal_blank_count", -1)) != expected_count:
            errors.append("formal technical response slots must remain blank in v1.38")
    elif int(technical.get("responded_count", -1)) != expected_count:
        errors.append("every frozen technical requirement must have a formal response before final assembly")
    if int(technical.get("unresolved_count", -1)) < 0:
        errors.append("technical unresolved_count must be a non-negative integer")
    final_slot_status = {
        slot_id: status
        for statuses in fill_status_by_volume.values()
        for slot_id, status in statuses.items()
    }
    unfilled_responses = [
        str(item.get("requirement_id", ""))
        for item in requirements
        if final_slot_status.get(str(item.get("response_slot_id", ""))) != "filled"
    ]
    if skill_version == "v1.38":
        actual_blank_count = sum(
            1 for item in requirements
            if final_slot_status.get(str(item.get("response_slot_id", ""))) == "blank"
        )
        if actual_blank_count != expected_count:
            errors.append("formal technical response slots are not all recorded as blank")
        incorrectly_filled = [
            str(item.get("requirement_id", ""))
            for item in requirements
            if final_slot_status.get(str(item.get("response_slot_id", ""))) == "filled"
        ]
        if incorrectly_filled:
            errors.append(f"formal technical response slots unexpectedly filled: {sorted(incorrectly_filled)}")
    elif unfilled_responses:
        errors.append(f"technical requirements lack actual formal response content: {sorted(unfilled_responses)}")
    selected_count = sum(1 for item in attachment_items.values() if item.get("selected", True))
    embedded_count = sum(
        1 for item in attachment_items.values()
        if item.get("selected", True) and item.get("embedded") is True
    )
    summary = data.get("attachment_summary", {})
    if int(summary.get("selected_count", -1)) != selected_count:
        errors.append("attachment selected_count differs from the placement manifest")
    if int(summary.get("embedded_count", -1)) != embedded_count:
        errors.append("attachment embedded_count differs from the placement manifest")
    return errors


def validate_attachment_placement(path: Path, bundle_path: Path, lock_path: Path, slot_manifest_path: Path) -> list[str]:
    data = read_json(path)
    lock = read_json(lock_path)
    bundle = {item["id"]: item for item in bundle_volumes(bundle_path)}
    slot_volumes = {
        str(item.get("slot_id", "")): str(item.get("volume_id", ""))
        for item in read_json(slot_manifest_path).get("slots", [])
    }
    errors: list[str] = []
    if data.get("skeleton_id") != lock.get("skeleton_id"):
        errors.append("attachment placement manifest is not bound to the current skeleton")
    if int(data.get("stage", -1)) != 9:
        errors.append("attachment placement manifest stage must be 9")
    items = data.get("items")
    if not isinstance(items, list):
        return errors + ["attachment placement items must be a list"]
    seen: set[str] = set()
    for item in items:
        item_id = str(item.get("id", ""))
        if not item_id or item_id in seen:
            errors.append(f"missing or duplicate attachment id: {item_id}")
            continue
        seen.add(item_id)
        volume_id = str(item.get("volume_id", ""))
        slot_id = str(item.get("slot_id", ""))
        if volume_id not in bundle:
            errors.append(f"attachment targets unknown volume: {item_id}")
        if slot_id not in slot_volumes:
            errors.append(f"attachment targets unknown slot: {item_id}")
        elif slot_volumes[slot_id] != volume_id:
            errors.append(f"attachment slot belongs to another volume: {item_id}")
        source = resolve_ref(str(item.get("source_path", "")), path.parent)
        if not source.is_file():
            errors.append(f"attachment source is missing: {item_id}")
        elif not item.get("source_sha256"):
            errors.append(f"attachment source hash is missing: {item_id}")
        elif item["source_sha256"].lower() != sha256(source):
            errors.append(f"attachment source hash changed: {item_id}")
        selected = item.get("selected", True)
        if not selected and not str(item.get("reason", "")).strip():
            errors.append(f"unselected attachment lacks a reason: {item_id}")
        if selected and item.get("embedded") is not True:
            errors.append(f"selected attachment was not embedded in the final DOCX: {item_id}")
        if selected and item.get("embedded") is True:
            if not str(item.get("output_location", "")).strip():
                errors.append(f"embedded attachment lacks a final output location: {item_id}")
            parts = item.get("output_parts")
            if not isinstance(parts, list) or not parts:
                errors.append(f"embedded attachment lacks verifiable DOCX output parts: {item_id}")
                continue
            if volume_id not in bundle:
                continue
            try:
                with ZipFile(bundle[volume_id]["path"]) as archive:
                    names = set(archive.namelist())
                    for part in parts:
                        part_name = str(part.get("part", ""))
                        expected_hash = str(part.get("sha256", ""))
                        if not part_name or part_name not in names:
                            errors.append(f"attachment DOCX part is missing: {item_id}:{part_name}")
                        elif not expected_hash or hashlib.sha256(archive.read(part_name)).hexdigest() != expected_hash:
                            errors.append(f"attachment DOCX part hash mismatch: {item_id}:{part_name}")
            except BadZipFile:
                errors.append(f"attachment target is not a valid DOCX package: {item_id}")
    return errors


def validate_skeleton_stage(
    by_kind: dict[str, list[Path]],
    current: Any,
) -> list[str]:
    bundle = current("skeleton_bundle_manifest")
    slots = current("skeleton_slot_manifest")
    technical = current("technical_requirement_manifest")
    lock = current("skeleton_lock")
    validation = read_json(current("skeleton_validation"))
    problems = check_skeleton_contract(bundle, slots, technical)
    problems += check_requirement_coverage(current("section_tree"), current("requirement_coverage_matrix"), slots)
    problems += check_structure_frozen(bundle, lock)
    if validation.get("status") != "pass" or validation.get("errors"):
        problems.append("skeleton_validation must be a passing, empty-error result")
    lock_data = read_json(lock)
    if lock_data.get("tool_version") is None or lock_data.get("skeleton_id") is None:
        problems.append("skeleton lock is incomplete")
    volume_items = bundle_volumes(bundle)
    roles = {item.get("role", item["id"]) for item in volume_items}
    if not {"business", "technical"}.issubset(roles):
        problems.append("skeleton bundle must contain business and technical volumes")
    declared = {path.resolve() for path in by_kind.get("skeleton_docx", [])}
    bundled = {item["path"].resolve() for item in volume_items}
    if declared != bundled:
        problems.append("stage skeleton_docx artifacts must exactly match bundle volumes")
    validate_skeleton_render_manifest(current("skeleton_render_manifest"), lock)
    return problems


def validate_filled_stage(
    stage: int,
    candidate: Path,
    binding: Path,
    fill_manifest: Path,
    lock: Path,
    slots: Path,
    expected_volume: str,
) -> list[str]:
    problems = validate_binding(binding, lock, stage, expected_volume)
    fill = read_json(fill_manifest)
    if fill.get("volume_id") != expected_volume:
        problems.append(f"fill manifest volume must be {expected_volume}")
    problems += check_slot_fill(candidate, slots, fill_manifest, lock)
    return problems


def stage_fingerprint(workflow: dict[str, Any], stage: int) -> str:
    item = workflow["stages"][stage_key(stage)]
    material = {
        "workflow_version": workflow["workflow_version"],
        "skill_version": workflow["skill_version"],
        "rulebase_version": workflow.get("rulebase_version"),
        "stage": stage,
        "revision": item["revision"],
        "spec": (item.get("spec") or {}).get("sha256"),
        "rulebase": (item.get("rulebase") or {}).get("sha256"),
        "agent_task": ((item.get("agent") or {}).get("task") or {}).get("sha256"),
        "agent_result": ((item.get("agent") or {}).get("result") or {}).get("sha256"),
        "artifact_manifest": (item.get("artifact_manifest") or {}).get("sha256"),
        "artifacts": [{k: ref[k] for k in ("id", "kind", "sha256")} for ref in item.get("artifacts", [])],
        "dependencies": item.get("dependency_fingerprints", {}),
    }
    return canonical_hash(material)


def cmd_init(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    if (root / STATE_FILE).exists():
        raise WorkflowError(f"Workflow already exists: {root / STATE_FILE}")
    root.mkdir(parents=True, exist_ok=True)
    workflow = new_workflow(args.project_id, args.skill_version, args.rulebase_version)
    save_workflow(root, workflow)
    append_event(root, {"action": "init", "project_id": args.project_id, "workflow_version": WORKFLOW_VERSION})
    print(json.dumps({"status": "created", "workflow": str(root / STATE_FILE)}, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    findings = refresh(workflow)
    if findings:
        save_workflow(root, workflow)
        for finding in findings:
            append_event(root, {"action": "auto_invalidate", **finding})
    rows = [
        {"stage": i, "name": workflow["stages"][stage_key(i)]["name"], "state": workflow["stages"][stage_key(i)]["state"], "reason": workflow["stages"][stage_key(i)].get("reason")}
    for i in range(1, STAGE_COUNT + 1)
    ]
    print(json.dumps({"project_id": workflow["project_id"], "delivery": workflow["delivery"], "stages": rows, "refresh_findings": findings}, ensure_ascii=False, indent=2))
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    findings = refresh(workflow)
    number = args.stage
    item = workflow["stages"][stage_key(number)]
    deps = dependencies(number)
    blockers = [dep for dep in deps if workflow["stages"][stage_key(dep)]["state"] not in ADVANCE_STATES]
    if blockers:
        raise WorkflowError(f"Stage {number} dependencies are not complete enough to continue: {blockers}")
    if item["state"] not in {"ready", "blocked", "stale", "candidate", "passed"}:
        raise WorkflowError(f"Stage {number} cannot begin from state {item['state']}")
    invalidated = mark_stale(workflow, number, f"Stage {number} restarted", include_self=False)
    item["state"] = "running"
    item["revision"] += 1
    item["reason"] = None
    item["started_at"] = utc_now()
    item["completed_at"] = None
    item["invalidated_at"] = None
    item["spec"] = file_ref(args.spec) if args.spec else None
    item["rulebase"] = None
    item["agent"] = None
    item["artifact_manifest"] = None
    item["artifacts"] = []
    item["pass_fingerprint"] = None
    item["dependency_fingerprints"] = {
        stage_key(dep): workflow["stages"][stage_key(dep)]["pass_fingerprint"] for dep in deps
    }
    workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": f"Stage {number} started"}
    save_workflow(root, workflow)
    append_event(root, {"action": "begin", "stage": number, "revision": item["revision"], "invalidated": invalidated, "refresh_findings": findings})
    print(json.dumps({"status": "running", "stage": number, "invalidated": invalidated}, ensure_ascii=False))
    return 0


def cmd_record_rulebase(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    refresh(workflow)
    item = workflow["stages"][stage_key(args.stage)]
    if item["state"] not in {"running", "verifying", "awaiting_agent"}:
        raise WorkflowError("Rulebase result can only be recorded for an active stage")
    result = read_json(args.result)
    status = result.get("status")
    result_stage = str(result.get("stage", "")).split("-", 1)[0]
    try:
        result_stage_number = int(result_stage)
    except ValueError:
        result_stage_number = -1
    binding_errors = []
    if result_stage_number != args.stage:
        binding_errors.append("stage does not match")
    if item.get("spec") and result.get("spec_sha256") != item["spec"]["sha256"]:
        binding_errors.append("spec hash does not match")
    if workflow.get("rulebase_version") and result.get("rulebase_version") != workflow["rulebase_version"]:
        binding_errors.append("rulebase version does not match")
    checks = result.get("results")
    if not isinstance(checks, list) or not checks or any(not isinstance(check, dict) or check.get("passed") is not True for check in checks):
        binding_errors.append("rulebase needs nonempty passing checks")
    if result.get("required_failure_count") != 0 or not result.get("script_sha256"):
        binding_errors.append("rulebase run metadata is incomplete or failed")
    if binding_errors:
        status = "blocked"
    ref = {**file_ref(args.result), "status": status, "rulebase_version": result.get("rulebase_version"), "script_sha256": result.get("script_sha256"), "spec_sha256": result.get("spec_sha256")}
    item["rulebase"] = ref
    if status != "pass":
        item["state"] = "blocked"
        detail = f"; binding errors: {', '.join(binding_errors)}" if binding_errors else ""
        item["reason"] = f"Rulebase status is {status or 'missing'}{detail}"
    else:
        item["state"] = "awaiting_agent" if args.stage in AGENT_REQUIRED and not item.get("agent") else "verifying"
        item["reason"] = None
    save_workflow(root, workflow)
    append_event(root, {"action": "record_rulebase", "stage": args.stage, "status": status, "sha256": ref["sha256"]})
    print(json.dumps({"stage": args.stage, "state": item["state"], "rulebase_status": status}, ensure_ascii=False))
    return 0 if status == "pass" else 2


def cmd_record_agent(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    refresh(workflow)
    if args.stage not in AGENT_REQUIRED:
        raise WorkflowError(f"Stage {args.stage} does not require an agent result")
    item = workflow["stages"][stage_key(args.stage)]
    if item["state"] not in {"running", "awaiting_agent", "verifying"}:
        raise WorkflowError("Agent result can only be recorded for an active stage")
    task = read_json(args.task)
    task_ref = file_ref(args.task)
    validate_agent_task(task, args.stage)
    result = read_json(args.result)
    validate_agent_result(result, args.stage, task_ref["sha256"])
    result_ref = file_ref(args.result)
    item["agent"] = {
        "task": task_ref,
        "result": result_ref,
        "status": result["status"],
        "execution_mode": result.get("execution_mode", "agent"),
        "open_items": result.get("open_items", []),
    }
    if result["status"] == "blocked":
        item["state"] = "blocked"
        item["reason"] = "Agent result is blocked"
    else:
        item["state"] = "verifying" if (item.get("rulebase") or {}).get("status") == "pass" else "running"
        item["reason"] = "Agent result has open items" if result["status"] == "candidate" else None
    save_workflow(root, workflow)
    append_event(root, {"action": "record_agent", "stage": args.stage, "status": result["status"], "task_sha256": task_ref["sha256"], "result_sha256": result_ref["sha256"]})
    print(json.dumps({"stage": args.stage, "state": item["state"], "agent_status": result["status"]}, ensure_ascii=False))
    return 0 if result["status"] in {"pass", "candidate"} else 2


def cmd_complete(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    findings = refresh(workflow)
    if findings:
        raise WorkflowError("Workflow changed while completing the stage; review stale stages first")
    number = args.stage
    item = workflow["stages"][stage_key(number)]
    if item["state"] not in {"verifying", "running"}:
        raise WorkflowError(f"Stage {number} cannot complete from state {item['state']}")
    if number in RULEBASE_REQUIRED and (item.get("rulebase") or {}).get("status") != "pass":
        raise WorkflowError("A current passing Rulebase result is required")
    if number in AGENT_REQUIRED and (item.get("agent") or {}).get("status") not in {"pass", "candidate"}:
        raise WorkflowError("A current pass or candidate Agent result is required")
    manifest, refs = load_artifacts(args.artifact_manifest, number, str(workflow.get("skill_version", "")))
    by_kind: dict[str, list[Path]] = {}
    for ref in refs:
        by_kind.setdefault(ref["kind"], []).append(Path(ref["path"]))
    def current(kind: str) -> Path:
        paths = by_kind.get(kind, [])
        if len(paths) != 1:
            raise WorkflowError(f"Stage {number} requires exactly one {kind}")
        return paths[0]
    def previous(stage: int, kind: str) -> Path:
        return find_kind(workflow["stages"][stage_key(stage)], kind)
    stage_has_open_items = False
    if number == 1:
        problems = validate_skeleton_stage(by_kind, current)
        if workflow.get("skill_version") == "v1.38":
            from technical_alignment import validate as validate_alignment
            alignment_manifest = current("technical_source_manifest")
            source = Path(read_json(alignment_manifest)["source_path"])
            try:
                validate_alignment(source, current("technical_alignment_blank_xlsx"), alignment_manifest)
            except (ValueError, KeyError, OSError) as exc:
                problems.append(f"technical Word-to-Excel source coverage failed: {exc}")
    elif number == 2:
        problems = check_blank_policy(current("intentional_blank_policy"))
        problems += validate_filled_stage(
            number,
            current("filled_base_docx"),
            current("skeleton_binding"),
            current("slot_fill_manifest"),
            previous(1, "skeleton_lock"),
            previous(1, "skeleton_slot_manifest"),
            "business",
        )
    elif number == 3:
        from business_rules import check_qualification
        problems = check_qualification(current("qualification_decision"))
    elif number == 4:
        problems = check_scoring_and_qualification(
            previous(3, "qualification_decision"),
            current("commercial_scoring_matrix"),
            current("certification_placement"),
        )
        problems += check_certification_placement(current("commercial_candidate_docx"), current("certification_placement"))
        problems += validate_filled_stage(
            number,
            current("commercial_candidate_docx"),
            current("skeleton_binding"),
            current("slot_fill_manifest"),
            previous(1, "skeleton_lock"),
            previous(1, "skeleton_slot_manifest"),
            "business",
        )
    elif number == 5:
        problems = check_commitment_coverage(
            previous(1, "prohibition_ledger"),
            previous(1, "responsiveness_ledger"),
            current("commitment_inventory"),
            current("commitment_docx"),
        )
        problems += validate_filled_stage(
            number,
            current("commitment_docx"),
            current("skeleton_binding"),
            current("slot_fill_manifest"),
            previous(1, "skeleton_lock"),
            previous(1, "skeleton_slot_manifest"),
            "business",
        )
    elif number == 6:
        mapping = read_json(current("evidence_slot_mapping"))
        known_slots = {item.get("slot_id") for item in read_json(previous(1, "skeleton_slot_manifest")).get("slots", [])}
        mapped_slots = {item.get("slot_id") for item in mapping.get("items", [])}
        problems = [f"technical evidence references unknown slot: {slot}" for slot in sorted(mapped_slots - known_slots)]
    elif number == 7:
        problems = validate_filled_stage(
            number,
            current("technical_plan_docx") if workflow.get("skill_version") == "v1.38" else current("technical_response_docx"),
            current("skeleton_binding"),
            current("slot_fill_manifest"),
            previous(1, "skeleton_lock"),
            previous(1, "skeleton_slot_manifest"),
            "technical",
        )
        if workflow.get("skill_version") == "v1.38":
            from technical_alignment import validate as validate_alignment
            alignment_manifest = previous(1, "technical_source_manifest")
            source = Path(read_json(alignment_manifest)["source_path"])
            try:
                validate_alignment(source, current("technical_alignment_xlsx"), alignment_manifest, require_responses=True)
            except (ValueError, KeyError, OSError) as exc:
                problems.append(f"technical alignment workbook differs from the tender source: {exc}")
    elif number == 8:
        candidate = current("business_candidate_docx")
        problems = check_blank_forms(candidate, current("blank_forms_manifest") if number == 8 else current("final_blank_forms_manifest"))
        problems += check_commitment_coverage(
            previous(1, "prohibition_ledger"),
            previous(1, "responsiveness_ledger"),
            previous(5, "commitment_inventory"),
            candidate,
        )
        problems += validate_filled_stage(
            number,
            candidate,
            current("skeleton_binding"),
            current("slot_fill_manifest"),
            previous(1, "skeleton_lock"),
            previous(1, "skeleton_slot_manifest"),
            "business",
        )
    elif number == 9:
        bundle = current("final_bundle_manifest")
        lock = previous(1, "skeleton_lock")
        slots = previous(1, "skeleton_slot_manifest")
        problems = validate_binding(current("final_skeleton_binding"), lock, number)
        problems += check_structure_frozen(bundle, lock)
        volume_items = bundle_volumes(bundle)
        fill_by_volume: dict[str, Path] = {}
        for manifest_path in by_kind.get("final_slot_fill_manifest", []):
            fill_data = read_json(manifest_path)
            volume_id = fill_data.get("volume_id")
            if not volume_id or volume_id in fill_by_volume:
                problems.append(f"missing or duplicate final fill volume: {volume_id}")
            else:
                fill_by_volume[volume_id] = manifest_path
        for volume in volume_items:
            fill_manifest = fill_by_volume.get(volume["id"])
            if not fill_manifest:
                problems.append(f"final bundle volume lacks fill manifest: {volume['id']}")
            else:
                problems += check_slot_fill(volume["path"], slots, fill_manifest, lock)
        business_candidate = bundle_role(bundle, "business")
        problems += check_blank_forms(business_candidate, current("final_blank_forms_manifest"))
        problems += check_commitment_coverage(
            previous(1, "prohibition_ledger"),
            previous(1, "responsiveness_ledger"),
            previous(5, "commitment_inventory"),
            business_candidate,
        )
        problems += check_certification_placement(business_candidate, previous(4, "certification_placement"))
        problems += validate_final_content_completion(
            current("final_content_completion_manifest"),
            bundle,
            lock,
            slots,
            previous(1, "technical_requirement_manifest"),
            fill_by_volume.values(),
            current("attachment_placement_manifest"),
            str(workflow.get("skill_version", "")),
        )
        problems += validate_attachment_placement(
            current("attachment_placement_manifest"),
            bundle,
            lock,
            slots,
        )
        validate_final_render_manifest(current("rendered_bundle_manifest"), bundle, lock)
        validate_rendered_pages_manifest(current("rendered_pages_manifest"), bundle)
    else:
        problems = []
    if problems:
        raise WorkflowError(f"Stage {number} business checks failed: {'; '.join(problems)}")
    if number == STAGE_COUNT:
        for kind in ("page_review_manifest", "historical_feedback_review", "delivery_decision"):
            if len(by_kind.get(kind, [])) != 1:
                raise WorkflowError(f"Stage {STAGE_COUNT} requires exactly one {kind}")
        stage_has_open_items = validate_page_review(
            by_kind["page_review_manifest"][0],
            previous(9, "rendered_pages_manifest"),
        )
        validate_delivery_decision(by_kind["delivery_decision"][0])
        page_review = read_json(by_kind["page_review_manifest"][0])
        skeleton_lock = read_json(previous(1, "skeleton_lock"))
        if page_review.get("skeleton_id") != skeleton_lock.get("skeleton_id"):
            raise WorkflowError("Final page review is not bound to the current skeleton")
        final_bundle = previous(9, "final_bundle_manifest")
        expected_outputs = {item["sha256"] for item in bundle_volumes(final_bundle)}
        reviewed_outputs = {item.get("sha256") for item in page_review.get("final_outputs", [])}
        if reviewed_outputs != expected_outputs:
            raise WorkflowError("Final page review outputs differ from the stage 9 final bundle")
    item["artifact_manifest"] = file_ref(args.artifact_manifest)
    item["artifacts"] = refs
    agent_status = (item.get("agent") or {}).get("status")
    completion_state = "candidate" if agent_status == "candidate" or stage_has_open_items else "passed"
    item["state"] = completion_state
    item["reason"] = "Stage artifacts are usable, but Agent review has unresolved open items" if completion_state == "candidate" else None
    item["completed_at"] = utc_now()
    item["pass_fingerprint"] = stage_fingerprint(workflow, number)
    update_ready_states(workflow)
    workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": f"Run check-delivery after stage {STAGE_COUNT}."}
    save_workflow(root, workflow)
    append_event(root, {"action": "complete", "stage": number, "revision": item["revision"], "pass_fingerprint": item["pass_fingerprint"], "artifact_manifest_sha256": item["artifact_manifest"]["sha256"], "artifact_count": len(refs)})
    print(json.dumps({"status": completion_state, "stage": number, "pass_fingerprint": item["pass_fingerprint"]}, ensure_ascii=False))
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    item = workflow["stages"][stage_key(args.stage)]
    item["state"] = "blocked"
    item["reason"] = args.reason
    item["pass_fingerprint"] = None
    invalidated = mark_stale(workflow, args.stage, f"Stage {args.stage} blocked: {args.reason}", include_self=False)
    workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": args.reason}
    save_workflow(root, workflow)
    append_event(root, {"action": "block", "stage": args.stage, "reason": args.reason, "invalidated": invalidated})
    print(json.dumps({"status": "blocked", "stage": args.stage, "invalidated": invalidated}, ensure_ascii=False))
    return 2


def cmd_invalidate(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    changed = mark_stale(workflow, args.stage, args.reason, include_self=True)
    update_ready_states(workflow)
    save_workflow(root, workflow)
    append_event(root, {"action": "invalidate", "stage": args.stage, "reason": args.reason, "invalidated": changed})
    print(json.dumps({"status": "stale", "invalidated": changed}, ensure_ascii=False))
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    changes = {}
    start_stage = STAGE_COUNT + 1
    if args.skill_version and args.skill_version != workflow.get("skill_version"):
        changes["skill_version"] = [workflow.get("skill_version"), args.skill_version]
        workflow["skill_version"] = args.skill_version
        start_stage = min(start_stage, 1)
    if args.rulebase_version and args.rulebase_version != workflow.get("rulebase_version"):
        changes["rulebase_version"] = [workflow.get("rulebase_version"), args.rulebase_version]
        workflow["rulebase_version"] = args.rulebase_version
        start_stage = min(start_stage, 1)
    if not changes:
        print(json.dumps({"status": "unchanged"}, ensure_ascii=False))
        return 0
    invalidated = mark_stale(workflow, start_stage, f"Workflow configuration changed: {changes}")
    save_workflow(root, workflow)
    append_event(root, {"action": "upgrade", "changes": changes, "invalidated": invalidated})
    print(json.dumps({"status": "updated", "changes": changes, "invalidated": invalidated}, ensure_ascii=False))
    return 0


def find_kind(item: dict[str, Any], kind: str) -> Path:
    matches = [Path(ref["path"]) for ref in item.get("artifacts", []) if ref.get("kind") == kind]
    if len(matches) != 1:
        raise WorkflowError(f"Expected exactly one {kind} artifact, found {len(matches)}")
    return matches[0]


def cmd_check_delivery(args: argparse.Namespace) -> int:
    root = args.workflow_dir.resolve()
    workflow = load_workflow(root)
    findings = refresh(workflow)
    not_passed = [i for i in range(1, STAGE_COUNT + 1) if workflow["stages"][stage_key(i)]["state"] != "passed"]
    if findings or not_passed:
        reason = f"Stages not currently passed: {not_passed}; refresh findings: {len(findings)}"
        workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": reason}
        save_workflow(root, workflow)
        append_event(root, {"action": "check_delivery", "status": "not_ready", "reason": reason})
        print(json.dumps({"delivery_ready": False, "reason": reason}, ensure_ascii=False))
        return 2
    qualification = read_json(find_kind(workflow["stages"][stage_key(3)], "qualification_decision"))
    if qualification.get("overall_status") != "met":
        reason = f"Qualification is {qualification.get('overall_status')}; required evidence was not submitted"
        workflow["delivery"] = {"status": "not_ready", "checked_at": utc_now(), "reason": reason}
        save_workflow(root, workflow)
        append_event(root, {"action": "check_delivery", "status": "not_ready", "reason": reason})
        print(json.dumps({"delivery_ready": False, "reason": reason}, ensure_ascii=False))
        return 2
    final_stage = workflow["stages"][stage_key(STAGE_COUNT)]
    decision = read_json(find_kind(final_stage, "delivery_decision"))
    validate_delivery_decision(find_kind(final_stage, "delivery_decision"))
    if decision["status"] != "ready":
        reason = f"Delivery decision is {decision['status']}"
        workflow["delivery"] = {"status": decision["status"], "checked_at": utc_now(), "reason": reason}
        save_workflow(root, workflow)
        append_event(root, {"action": "check_delivery", "status": decision["status"], "reason": reason})
        print(json.dumps({"delivery_ready": False, "reason": reason, "business_open_items": decision["business_open_items"]}, ensure_ascii=False))
        return 2
    workflow["delivery"] = {"status": "ready", "checked_at": utc_now(), "reason": None, "final_stage_fingerprint": final_stage["pass_fingerprint"]}
    save_workflow(root, workflow)
    append_event(root, {"action": "check_delivery", "status": "ready", "final_stage_fingerprint": final_stage["pass_fingerprint"]})
    print(json.dumps({"delivery_ready": True, "final_stage_fingerprint": final_stage["pass_fingerprint"], "business_open_items": decision["business_open_items"]}, ensure_ascii=False))
    return 0


def legacy_status(root: Path, stage: int) -> tuple[str, list[str], str]:
    evidence: list[str] = []
    status = "missing"
    reason = "No strict stage evidence found."
    names: dict[int, list[str]] = {
        1: ["stage01_result.json"],
        2: ["format_scoring_baseline.json"],
        3: ["project_identity.json", "identity_ledger.json"],
        4: ["procurement_config.json", "procurement_ledger.json"],
        5: ["authority_assets.txt", "template_assets.txt", "module_assets.txt"],
        6: ["evidence_sources.json", "group_candidates.json"],
        7: ["module_reuse.json"],
        8: ["module_reuse.json"],
        9: ["chapter_tree.json"],
        10: ["fill_inventory.json", "attachments.json"],
        11: ["attachments.json", "internal_items.json"],
        12: ["candidate_rulebase_result.json"],
        13: ["v133_final_results.json", "page_map.json"],
        14: ["final_visual_page_records_v133.json", "visual_review_summary.json"],
    }
    found = [root / name for name in names[stage] if (root / name).is_file()]
    evidence = [str(path) for path in found]
    if not found:
        return status, evidence, reason
    status, reason = "partial", "Some conventional evidence exists, but it is not a current workflow-bound pass record."
    if stage in {1, 12}:
        candidates = [path for path in found if path.suffix.lower() == ".json"]
        for path in candidates:
            data = read_json(path)
            if data.get("status") == "blocked":
                return "blocked", evidence, f"Legacy result is blocked: {path.name}"
            if stage == 1 and data.get("status") == "pass":
                return "candidate_pass", evidence, "Input stage has a passing legacy Rulebase record; import still requires explicit workflow execution."
    if stage == 14:
        page_file = root / "final_visual_page_records_v133.json"
        if page_file.is_file():
            pages = read_json(page_file)
            required = {"page_type", "checklist_results", "visual_result", "visual_note", "reviewer", "review_method"}
            if pages and not required.issubset(set(pages[0])):
                return "partial", evidence, "Page enumeration exists, but semantic per-page review fields are missing."
    return status, evidence, reason


def cmd_audit_legacy(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    report = {
        "schema_version": "1.0",
        "workflow_version": WORKFLOW_VERSION,
        "source": str(source),
        "audited_at": utc_now(),
        "warning": "This command never upgrades legacy evidence to passed workflow state.",
        "stages": [],
    }
    for stage in range(1, 15):
        status, evidence, reason = legacy_status(source, stage)
        report["stages"].append({"legacy_stage": stage, "status": status, "evidence": evidence, "reason": reason})
    report["strictly_complete"] = False
    output = args.out.resolve()
    atomic_write_json(output, report)
    print(json.dumps({"status": "audited", "report": str(output), "strictly_complete": False}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a new ten-stage workflow")
    init.add_argument("--workflow-dir", required=True, type=Path)
    init.add_argument("--project-id", required=True)
    init.add_argument("--skill-version", default=DEFAULT_SKILL_VERSION)
    init.add_argument("--rulebase-version", default="1.4.0")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Refresh hashes and show workflow status")
    status.add_argument("--workflow-dir", required=True, type=Path)
    status.set_defaults(func=cmd_status)

    begin = sub.add_parser("begin", help="Start or restart one stage")
    begin.add_argument("--workflow-dir", required=True, type=Path)
    begin.add_argument("--stage", required=True, type=int)
    begin.add_argument("--spec", type=Path)
    begin.set_defaults(func=cmd_begin)

    rb = sub.add_parser("record-rulebase", help="Attach the current deterministic result")
    rb.add_argument("--workflow-dir", required=True, type=Path)
    rb.add_argument("--stage", required=True, type=int)
    rb.add_argument("--result", required=True, type=Path)
    rb.set_defaults(func=cmd_record_rulebase)

    agent = sub.add_parser("record-agent", help="Attach the current agent task and result")
    agent.add_argument("--workflow-dir", required=True, type=Path)
    agent.add_argument("--stage", required=True, type=int)
    agent.add_argument("--task", required=True, type=Path)
    agent.add_argument("--result", required=True, type=Path)
    agent.set_defaults(func=cmd_record_agent)

    complete = sub.add_parser("complete", help="Validate and pass one active stage")
    complete.add_argument("--workflow-dir", required=True, type=Path)
    complete.add_argument("--stage", required=True, type=int)
    complete.add_argument("--artifact-manifest", required=True, type=Path)
    complete.set_defaults(func=cmd_complete)

    block = sub.add_parser("block", help="Block a stage and invalidate descendants")
    block.add_argument("--workflow-dir", required=True, type=Path)
    block.add_argument("--stage", required=True, type=int)
    block.add_argument("--reason", required=True)
    block.set_defaults(func=cmd_block)

    invalidate = sub.add_parser("invalidate", help="Invalidate a stage and all descendants")
    invalidate.add_argument("--workflow-dir", required=True, type=Path)
    invalidate.add_argument("--stage", required=True, type=int)
    invalidate.add_argument("--reason", required=True)
    invalidate.set_defaults(func=cmd_invalidate)

    upgrade = sub.add_parser("upgrade", help="Change skill/rulebase version and invalidate affected stages")
    upgrade.add_argument("--workflow-dir", required=True, type=Path)
    upgrade.add_argument("--skill-version")
    upgrade.add_argument("--rulebase-version")
    upgrade.set_defaults(func=cmd_upgrade)

    delivery = sub.add_parser("check-delivery", help="Apply the final, non-bypassable delivery gate")
    delivery.add_argument("--workflow-dir", required=True, type=Path)
    delivery.set_defaults(func=cmd_check_delivery)

    audit = sub.add_parser("audit-legacy", help="Audit old project evidence without granting pass state")
    audit.add_argument("--source", required=True, type=Path)
    audit.add_argument("--out", required=True, type=Path)
    audit.set_defaults(func=cmd_audit_legacy)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "audit-legacy":
            return int(args.func(args))
        with workflow_lock(args.workflow_dir.resolve()):
            return int(args.func(args))
    except (WorkflowError, SkeletonError, ValueError, OSError, IndexError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
