from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.module_dir
    meta_path = root / "authority.modules.meta.json"
    errors: list[str] = []
    if not meta_path.exists():
        print(json.dumps({"ok": False, "errors": ["missing authority.modules.meta.json"]}, ensure_ascii=False, indent=2))
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for name, expected in (meta.get("asset_sha256") or {}).items():
        path = root / name
        if not path.exists():
            errors.append(f"missing asset: {name}")
        elif sha256_file(path) != expected:
            errors.append(f"sha256 mismatch: {name}")
    modules_path = root / "authority.modules.json.gz"
    modules = []
    if modules_path.exists():
        with gzip.open(modules_path, "rt", encoding="utf-8") as handle:
            modules = json.load(handle).get("modules") or []
    ids = [item.get("module_id") for item in modules]
    if len(ids) != len(set(ids)):
        errors.append("duplicate module_id")
    for item in modules:
        if not item.get("source_path") or not item.get("source_sha256") or not item.get("block_ids"):
            errors.append(f"traceability incomplete: {item.get('module_id')}")
            if len(errors) > 50:
                break
    expected_count = int((meta.get("module_counts") or {}).get("all") or 0)
    if len(modules) != expected_count:
        errors.append(f"module count mismatch: {len(modules)} != {expected_count}")
    result = {
        "ok": not errors,
        "version": meta.get("version"),
        "module_count": len(modules),
        "ready_count": meta.get("ready_count"),
        "needs_review_count": meta.get("needs_review_count"),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
