"""Load and validate the one authoritative bid workflow policy."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "process-policy/policy.json"
SCHEMA_PATH = HERE / "process-policy/workflow-policy.schema.json"


def _require(condition, message):
    if not condition:
        raise ValueError("Invalid workflow policy: " + message)


@lru_cache(maxsize=1)
def load_policy() -> dict:
    raw = POLICY_PATH.read_bytes()
    data = json.loads(raw)
    schema = json.loads(SCHEMA_PATH.read_text())
    for key in schema["required"]:
        _require(key in data, "missing " + key)
    _require(data["schema_version"] == "deta-bid-workflow-v1", "unsupported schema_version")
    _require(data["quality_states"] == ["in_progress", "agent_scope_complete", "structure_passed", "content_passed", "business_reviewed", "submission_ready"], "quality state order changed")
    _require(data["agent_max_state"] == "agent_scope_complete", "Agent may not award a later state")
    _require(data["website_outputs"] == ["final_bid_docx", "eight_column_docx", "eight_column_xlsx"], "website output set changed")
    _require(data["internal_render_pdf"] is True, "internal PDF rule missing")
    _require(data["eight_column_manual_columns"] == [7, 8], "manual columns must be 7 and 8")
    _require(data["atomization"]["downstream_after_machine_gate"] is True, "AI continuation must not wait for row approval")
    result = dict(data)
    result["policy_sha256"] = hashlib.sha256(raw).hexdigest()
    result["policy_path"] = str(POLICY_PATH)
    return result


def policy_prompt() -> str:
    p = load_policy()
    return (
        "\n\n【服务器权威流程策略】版本%s，SHA-256 %s。"
        "招标技术原文保持逐字与来源顺序；六列表机器门禁通过后自动继续，不等待逐行人工确认。"
        "八列表第7、8列始终为空，正式技术响应/偏离填写区保留原表并为空。"
        "网站仅展示完整标书DOCX、八列表DOCX、八列表XLSX；PDF仅作内部检查。"
        "Agent十步结束最高状态为agent_scope_complete，不得授予business_reviewed或submission_ready。"
        % (p["version"], p["policy_sha256"])
    )


def clear_policy_cache():
    load_policy.cache_clear()
