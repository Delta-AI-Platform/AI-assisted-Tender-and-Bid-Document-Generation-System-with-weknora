"""Match tender requirements against retained same-model historical responses.

Matching is deliberately conservative — a wrong source is worse than "无历史":

1. 参数名精确   exact parameter name (normalised)
2. 同义词命中   alias list hit
3. 参数内含     alias with ``*`` — the historical row is a compound row that
                contains the value (e.g. 电池类型 also carries 容量/电压)
4. 关键词参照   paragraph clauses only; the historical row is corroborating
                evidence, never the answer source
5. 无历史       nothing trustworthy found

Cross-model sourcing is forbidden unless ``--allow-cross-model`` is given.

Usage
-----
    python match_history.py --req requirements.json --hist history.json \
        --alias aliases.json --same-model WLR-5 -o match.json
"""
import argparse
import json
import re
import sys
from collections import Counter


def norm(s):
    return re.sub(r"[\s\u3000（）()：:，,。；;、/·\-—]", "", str(s or ""))


def pick(hist, func, aliases, same_model):
    """Return (row, match_type, confidence) or (None, '无历史', 0)."""
    if not aliases:
        return None, "无历史", 0.0
    cands = [h for h in hist if not same_model or same_model in str(h.get("model") or "")]
    for a in aliases:
        contains = a.endswith("*")
        name = a.rstrip("*")
        hits = [h for h in cands if norm(h.get("param")) == norm(name)]
        if not hits and contains and norm(name):
            hits = [h for h in cands if norm(name) in norm(h.get("req"))[:40]]
        if not hits:
            continue
        hits.sort(key=lambda h: (str(h.get("resp")) in ("", "无偏离"),
                                 h.get("prio", 9), len(str(h.get("req") or ""))))
        h = hits[0]
        if norm(func) == norm(h.get("param")):
            return h, "参数名精确", 1.0
        return h, ("参数内含" if contains else "同义词命中"), 0.8
    return None, "无历史", 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--req", required=True)
    ap.add_argument("--hist", required=True)
    ap.add_argument("--alias")
    ap.add_argument("--hints", help='JSON: {"6.2.1": ["驾驶室"]} — paragraph corroboration only')
    ap.add_argument("--same-model", default="", help="substring identifying the retained model, e.g. WLR-5")
    ap.add_argument("--allow-cross-model", action="store_true")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    req = json.load(open(a.req, encoding="utf-8"))
    hist = json.load(open(a.hist, encoding="utf-8"))
    items = req.get("items") if isinstance(req, dict) else req
    hrows = hist.get("rows") if isinstance(hist, dict) else hist
    alias_raw = json.load(open(a.alias, encoding="utf-8")) if a.alias else {}
    hints_raw = json.load(open(a.hints, encoding="utf-8")) if a.hints else {}
    alias = alias_raw.get("alias") or alias_raw
    hints = hints_raw.get("hints") or hints_raw

    allowed_models = {str(i.get("model") or "") for i in items} if a.allow_cross_model else {a.same_model}
    out, seen = [], set()
    for it in items:
        it["sec"] = str(it.get("sec") or it.get("code") or "")
        rec = dict(it)
        rec.update({"hist_param": "", "hist_resp": "", "hist_no": "", "source": "",
                    "match_type": "无历史", "confidence": 0.0})
        hit, mt = None, "无历史"
        if it.get("kind") == "table":
            func = str(it.get("func") or "")
            # an explicit empty alias list means "no counterpart in the template"
            aliases = alias[func] if func in alias else ([func] if func else [])
            if a.allow_cross_model or a.same_model in str(it.get("model") or ""):
                hit, mt, conf = pick(hrows, it.get("func"), aliases, a.same_model)
                rec["confidence"] = conf
        elif a.allow_cross_model or a.same_model in str(it.get("model") or ""):
            for kw in hints.get(str(it.get("sec")), []):
                if (it.get("sec"), kw) in seen:
                    continue
                hit = next((h for h in hrows
                            if kw in str(h.get("req")) and kw in str(h.get("resp"))
                            and str(h.get("resp")) not in ("", "无偏离")), None)
                if hit:
                    seen.add((it.get("sec"), kw))
                    mt, rec["confidence"] = "关键词参照", 0.5
                    break
        if hit:
            rec.update({"hist_param": hit.get("param") or hit.get("no", ""),
                        "hist_resp": hit.get("resp", ""), "hist_no": hit.get("no", ""),
                        "source": "｜".join(str(hit.get(k) or "") for k in ("project", "model", "doc")),
                        "match_type": mt})
        out.append(rec)

    stats = Counter(r["match_type"] for r in out)
    by_model = {}
    for r in out:
        m = by_model.setdefault(str(r.get("model")), Counter())
        m["total"] += 1
        m[r["match_type"]] += 1
    json.dump({"stats": dict(stats), "by_model": {k: dict(v) for k, v in by_model.items()},
               "items": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("items:", len(out))
    print("stats:", dict(stats))
    for m, c in by_model.items():
        print("  %-22s total %d | %s" % (m, c["total"],
              ", ".join("%s %d" % (k, v) for k, v in c.items() if k != "total")))
    print("saved:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
