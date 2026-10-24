"""Dump requirement tables from a tender PDF and stitch cross-page continuations.

Tender tables routinely break across pages. Naively concatenating them attaches
the next section's table (e.g. 备品备件明细) to the previous one, or treats the
page footer number as a row. A table is treated as a continuation ONLY when all
three conditions hold:

1. the first row's first cell is a bare 1–2 digit row number
2. that number equals the previous table's last row number + 1
3. the first row still has content in the second cell (it is not a new header)

Usage
-----
    python pdf_table_dump.py tender.pdf -o tables.json
    python pdf_table_dump.py tender.pdf -o tables.json --pages 40-60 --min-cols 3
"""
import argparse
import json
import re
import sys

import pymupdf


def clean(v):
    return re.sub(r"\s+", "", str(v or "")).strip()


def parse_pages(expr, total):
    if not expr:
        return list(range(total))
    pages = []
    for part in expr.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a) - 1, int(b)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < total]


def is_continuation(prev, cur):
    if not prev or not cur or not prev.get("rows") or not cur.get("rows"):
        return False
    first = cur["rows"][0]
    if len(first) < 2 or not first[1]:
        return False
    if not re.fullmatch(r"\d{1,2}", str(first[0] or "").strip()):
        return False
    prev_nums = [r[0] for r in prev["rows"] if re.fullmatch(r"\d{1,2}", str(r[0] or "").strip())]
    if not prev_nums:
        return False
    return int(first[0]) == int(prev_nums[-1]) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--pages", help="1-based page range, e.g. 40-60 or 1,3,5")
    ap.add_argument("--min-cols", type=int, default=3)
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    raw = []
    for pno in parse_pages(a.pages, doc.page_count):
        for tab in doc[pno].find_tables().tables:
            rows = [[clean(c) for c in r] for r in tab.extract()]
            rows = [r for r in rows if sum(1 for c in r if c) >= 2]
            if not rows:
                continue
            if "序" in rows[0][0] and len(rows[0]) > 1 and "名称" in rows[0][1]:
                rows = rows[1:]
            # drop page-footer rows: a single isolated number
            rows = [r for r in rows if not (sum(1 for c in r if c) == 1
                                            and re.fullmatch(r"\d{1,3}", next(c for c in r if c)))]
            rows = [r for r in rows if len(r) >= a.min_cols and r[1]]
            if rows:
                raw.append({"page": pno + 1, "rows": rows})

    tables = []
    for t in raw:
        if is_continuation(tables[-1] if tables else None, t):
            tables[-1]["rows"].extend(t["rows"])
            tables[-1]["pages"].append(t["page"])
            tables[-1]["continued"] = True
        else:
            tables.append({"pages": [t["page"]], "rows": t["rows"], "continued": False})

    json.dump({"file": a.pdf, "page_count": doc.page_count,
               "table_count": len(tables), "tables": tables},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("physical tables: %d | logical tables: %d" % (len(raw), len(tables)))
    for i, t in enumerate(tables, 1):
        print("  T%-2d pages %-8s rows %-3d first=%s | last=%s%s" % (
            i, ",".join(map(str, t["pages"])), len(t["rows"]),
            (t["rows"][0][1] or "")[:14], (t["rows"][-1][1] or "")[:14],
            "  [stitched]" if t["continued"] else ""))
    print("saved:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
