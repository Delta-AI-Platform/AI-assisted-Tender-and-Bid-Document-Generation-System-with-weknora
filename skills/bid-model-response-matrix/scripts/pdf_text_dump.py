"""Dump a tender PDF's text as one normalised blob for verbatim checks.

Page numbers / repeated running heads break naive substring matching, so this
script (a) drops isolated numeric footer lines, (b) joins hyphen-free line
breaks, and (c) writes a blob with no markers by default — ready to feed
``verify_matrix.py --source``.

Usage
-----
    python pdf_text_dump.py tender.pdf -o tender_text.txt
    python pdf_text_dump.py tender.pdf -o tender_text.txt --markers --pages 40-70
"""
import argparse
import re
import sys

import pymupdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--pages", help="1-based range, e.g. 40-70")
    ap.add_argument("--markers", action="store_true", help="keep '===== PAGE n =====' lines")
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    pages = list(range(doc.page_count))
    if a.pages:
        pages = []
        for part in a.pages.split(","):
            if "-" in part:
                x, y = part.split("-", 1)
                pages.extend(range(int(x) - 1, int(y)))
            else:
                pages.append(int(part) - 1)

    chunks = []
    for i in pages:
        if not (0 <= i < doc.page_count):
            continue
        lines = [l for l in doc[i].get_text("text").split("\n")
                 if not re.fullmatch(r"\s*\d{1,4}\s*", l)]
        if a.markers:
            chunks.append("\n===== PAGE %d =====\n" % (i + 1))
        chunks.append("".join(l.strip() for l in lines))

    text = "\n".join(chunks)
    if not a.markers:
        text = re.sub(r"\s+", "", text)
    open(a.out, "w", encoding="utf-8").write(text)
    print("pages: %d | chars: %d -> %s" % (len(pages), len(text), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
