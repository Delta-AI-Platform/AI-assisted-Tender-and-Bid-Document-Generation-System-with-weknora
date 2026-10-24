from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def main() -> int:
    discovered = Path.home() / ".codex" / "data" / "bid-customer-template-library" / "current"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template-dir",
        default=os.environ.get("BID_CUSTOMER_TEMPLATE_DIR") or (str(discovered) if discovered.exists() else None),
    )
    parser.add_argument("--customer", required=True)
    parser.add_argument("--domain", choices=["price", "business", "technical"])
    parser.add_argument("--scoring", default="")
    parser.add_argument("--allow-alias", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--ledger")
    args = parser.parse_args()
    if not args.template_dir:
        parser.error("--template-dir or BID_CUSTOMER_TEMPLATE_DIR is required")
    path = Path(args.template_dir) / "customer.templates.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        library = json.load(handle)
    target = norm(args.customer)
    exact = [item for item in library.get("customers") or [] if norm(item.get("customer_name") or "") == target]
    alias = []
    if not exact and args.allow_alias:
        alias = [item for item in library.get("customers") or [] if target in {norm(value) for value in item.get("aliases") or []}]
    matched = exact or alias
    if len(matched) != 1:
        suggestions = [
            item.get("customer_name")
            for item in library.get("customers") or []
            if target and (target in norm(item.get("customer_name") or "") or norm(item.get("customer_name") or "") in target)
        ][:10]
        print(json.dumps({"status": "not_found" if not matched else "ambiguous", "customer": args.customer, "suggestions": suggestions}, ensure_ascii=False, indent=2))
        return 2
    customer = matched[0]
    modules = customer.get("modules") or []
    if args.domain:
        modules = [item for item in modules if item.get("domain") == args.domain]
    if args.scoring:
        key = norm(args.scoring)
        modules = [item for item in modules if key in norm(f"{item.get('section_title', '')} {item.get('full_text', '')[:1000]}")]
    primary_ids = set((customer.get("primary_module_ids") or {}).get(args.domain, [])) if args.domain else {
        item for values in (customer.get("primary_module_ids") or {}).values() for item in values
    }
    modules.sort(key=lambda item: (0 if item.get("module_id") in primary_ids else 1, 0 if item.get("customer_specific") else 1, -int(item.get("source_score") or 0), -len(item.get("full_text") or "")))
    selected = modules[: max(1, min(args.limit, 50))]
    result = {
        "status": "matched",
        "match_level": "exact_customer" if exact else "registered_alias",
        "library_version": library.get("version"),
        "customer_id": customer.get("customer_id"),
        "customer_name": customer.get("customer_name"),
        "aliases": customer.get("aliases") or [],
        "template_completeness": customer.get("template_completeness") or {},
        "domain": args.domain,
        "scoring": args.scoring,
        "modules": selected,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.ledger:
        ledger = {
            "library_version": library.get("version"),
            "customer_id": customer.get("customer_id"),
            "customer_name": customer.get("customer_name"),
            "match_level": result["match_level"],
            "selected_module_ids": [item.get("module_id") for item in selected],
            "requires_project_field_replacement": True,
            "requires_current_evidence_review": True,
        }
        Path(args.ledger).write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
