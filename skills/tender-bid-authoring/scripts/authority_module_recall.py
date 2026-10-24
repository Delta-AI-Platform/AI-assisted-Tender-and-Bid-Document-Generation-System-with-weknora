from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path


def norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--customer")
    parser.add_argument("--domain", choices=["price", "business", "technical"])
    parser.add_argument("--module-type", choices=["chapter_module", "table_module", "response_module", "evidence_module"])
    parser.add_argument("--title")
    parser.add_argument("--model")
    parser.add_argument("--scoring")
    parser.add_argument("--include-review", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    with gzip.open(args.module_dir / "authority.modules.json.gz", "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    modules = payload.get("modules") or []
    if not args.include_review:
        modules = [item for item in modules if item.get("reuse_eligibility") == "eligible"]
    if args.customer:
        key = norm(args.customer)
        modules = [item for item in modules if norm(item.get("customer_name")) == key]
    if args.domain:
        modules = [item for item in modules if item.get("domain") == args.domain]
    if args.module_type:
        modules = [item for item in modules if item.get("module_type") == args.module_type]
    if args.title:
        key = norm(args.title)
        modules = [item for item in modules if key in norm(" ".join((item.get("heading_path") or []) + [item.get("title") or ""]))]
    if args.model:
        key = norm(args.model)
        modules = [item for item in modules if key in {norm(value) for value in item.get("models") or []}]
    if args.scoring:
        key = norm(args.scoring)
        modules = [item for item in modules if key in norm(" ".join(item.get("scoring_tags") or []) + " " + (item.get("title") or ""))]
    modules.sort(key=lambda item: (
        0 if item.get("customer_specific") else 1,
        0 if item.get("module_type") == "chapter_module" else 1,
        int(item.get("heading_level") or 9),
        -len(item.get("full_text") or ""),
    ))
    selected = modules[: max(1, min(args.limit, 50))]
    print(json.dumps({"version": payload.get("version"), "match_count": len(modules), "modules": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
