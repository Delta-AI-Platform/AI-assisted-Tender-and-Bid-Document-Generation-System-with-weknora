"""Numbering derivation for the model standard response matrix.

Derived numbers are ALWAYS ``<section code> + <original number in the tender>``.
Nothing is re-sequenced from 1, nothing is invented. When the tender itself
repeats a number (or gives none), the duplicate/absence is preserved and only
reported by the verifier.

Row kinds
---------
table : a requirement row inside a numbered parameter table (5.1.1, 5.2, ...)
        -> ``<sec>.<row number>``
para  : a clause paragraph (6.x / 7 / 8 / 9) following the state machine below.

Paragraph state machine (mirrors the tender's own nesting)
----------------------------------------------------------
* bare number ``1.`` / ``1、``            -> ``<sec>.<n>``  and opens a top group
* parenthesised ``（1）`` under a top group -> ``<sec>.<top>.<n>``
* parenthesised with no top group         -> ``<sec>.<n>``
* a *short-title* paren (``（2）技术服务要求``, no trailing 。, <= 12 chars)
  becomes a parent for the bare numbers that follow -> ``<sec>.<p>.<n>``
* an item with no original number          -> ``<sec>`` joined with ``、``
  (the clause number itself is still an original number, so it is used rather
  than inventing a sequence). This is the ``6.2.6、驱动电机为……`` form.

Both derive functions return ``[(level_no, separator), ...]`` so the caller can
compose a cell without re-deciding the punctuation.
"""

import re

PAREN = re.compile(r"^\s*[（(](\d{1,2})[）)]\s*$")
BARE = re.compile(r"^\s*(\d{1,2})\s*[.．、]?\s*$")
LEAD = re.compile(r"^\s*(（\d{1,2}）|\(\d{1,2}\)|\d{1,2}[.．、])\s*")
LEVEL = re.compile(r"^\d+(?:\.\d+)+$")


def strip_lead(text):
    """Remove one leading original number (（1） / 1. / 1、)."""
    return LEAD.sub("", str(text or ""), count=1)


def embed(no, body, sep=" "):
    """Prefix ``body`` with ``no`` while avoiding a duplicated leading number.

    ``no`` is the derived level number (5.1.1.15); ``body`` must NOT already
    carry it. A stale leading original number (1. / （1）) is stripped first.
    """
    body = str(body or "")
    if not no:
        return body
    if re.match(r"^\s*" + re.escape(no) + r"(?=[\s、])", body):
        return body
    return ("%s%s%s" % (no, sep, strip_lead(body) if LEAD.match(body) else body)).strip()


def derive_table_numbers(sec, rows):
    """Table rows: ``sec`` + tender row number. Sub-rows injected under the same
    original number (``12-1``) share that number.
    """
    out = []
    for r in rows:
        raw = str(r.get("orig_no") or "").split("-")[0].strip()
        out.append(("%s.%s" % (sec, raw), " ") if raw else ("", ""))
    return out


def derive_para_numbers(sec, clauses):
    """Return ``(level_no, sep)`` for each clause, in tender order."""
    out, top, sub = [], None, None
    for c in clauses:
        no = str(c.get("orig_no") or "").strip()
        txt = str(c.get("text") or "").strip()
        mp, mb = PAREN.match(no), BARE.match(no)
        if mp:
            p = mp.group(1)
            lvl = "%s.%s.%s" % (sec, top, p) if top else "%s.%s" % (sec, p)
            short = len(txt) <= 12 and not txt.endswith("。")
            if short:
                sub = p
            elif not top:
                sub = None
            out.append((lvl, " "))
        elif mb:
            n = mb.group(1)
            if top is None and sub:
                out.append(("%s.%s.%s" % (sec, sub, n), " "))
            else:
                top, sub = n, None
                out.append(("%s.%s" % (sec, n), " "))
        else:
            # no sub-number in the tender: use the clause number itself
            out.append((sec, "、") if sec else ("", ""))
    return out


def head_number(text):
    """Extract the leading level number from a composed cell (req/resp column)."""
    m = re.match(r"^\s*(\d+(?:\.\d+)+)(?=[\s\u3000、])", str(text or ""))
    return m.group(1) if m else ""


def is_level(s):
    return bool(LEVEL.match(str(s or "").strip()))
