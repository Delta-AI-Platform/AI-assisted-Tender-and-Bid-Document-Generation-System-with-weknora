from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    discovered = Path.home() / ".codex" / "data" / "bid-customer-template-library" / "current"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template-dir",
        default=os.environ.get("BID_CUSTOMER_TEMPLATE_DIR") or (str(discovered) if discovered.exists() else None),
    )
    args = parser.parse_args()
    if not args.template_dir:
        parser.error("--template-dir or BID_CUSTOMER_TEMPLATE_DIR is required")
    root = Path(args.template_dir).resolve()
    meta_path = root / "customer.templates.meta.json"
    library_path = root / "customer.templates.json.gz"
    packages = root / "customer_templates"
    errors: list[str] = []
    for path in (meta_path, library_path, packages):
        if not path.exists():
            errors.append(f"missing: {path}")
    if errors:
        print("\n".join(errors))
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(library_path.read_bytes()).hexdigest()
    if actual_hash != meta.get("template_sha256"):
        errors.append("customer.templates.json.gz checksum mismatch")
    with gzip.open(library_path, "rt", encoding="utf-8") as handle:
        library = json.load(handle)
    customers = library.get("customers") or []
    if len(customers) != int(meta.get("customer_count") or -1):
        errors.append("customer count differs from meta")
    package_dirs = [item for item in packages.iterdir() if item.is_dir()]
    if len(package_dirs) != len(customers):
        errors.append("customer package directory count differs from library")
    for customer in customers:
        matches = list(packages.glob(f"{customer.get('customer_id')}_*"))
        if len(matches) != 1:
            errors.append(f"package missing or duplicated: {customer.get('customer_name')}")
            continue
        package = matches[0]
        required = ["manifest.json", "price_modules.json", "business_modules.json", "technical_modules.json", "scoring_directory_candidates.json"]
        for name in required:
            if not (package / name).is_file():
                errors.append(f"missing {name}: {customer.get('customer_name')}")
        if customer.get("template_completeness", {}).get("price") == "source_derived" and not customer.get("module_counts", {}).get("price"):
            errors.append(f"invalid price completeness: {customer.get('customer_name')}")
    shared_default = []
    for customer in customers:
        primary = {item for values in (customer.get("primary_module_ids") or {}).values() for item in values}
        for module in customer.get("modules") or []:
            if module.get("module_id") in primary and module.get("cross_customer_count", 1) > 1 and module.get("reuse_policy") != "shared_historical_text_requires_customer_specific_adaptation":
                shared_default.append(module.get("module_id"))
    if shared_default:
        errors.append(f"shared modules lack adaptation guard: {len(shared_default)}")
    print(json.dumps({"ok": not errors, "version": library.get("version"), "customer_count": len(customers), "package_count": len(package_dirs), "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
