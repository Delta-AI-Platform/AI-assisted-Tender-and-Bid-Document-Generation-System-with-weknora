# -*- coding: utf-8 -*-
"""parse_marked_review.py — 解析业务在 xlsx 上的审查标记，产出结构化修订清单。

标记口径（2026-10-21 业务确认）：
- 黄色填充（FFFFFF00）          = 缺失，需要补齐（常为业务插入的标题行/空行，或整段漏转化的章节）
- 红字（FFFF0000/FFC00000）或划掉（strike）= 过度承诺，需要去除
- 单元格内蓝色等改写字体        = 业务手写的修正内容，应作为该格最终文本采纳

用法:
  python parse_marked_review.py 已标记.xlsx -o marked_issues.json
输出 JSON:
  { struck:  [{row, col, plain, bad:[{t,c,s}], kept}],   # 过度承诺：bad=要删的片段, kept=应保留片段
    yellow: [{row, col, text}],                          # 缺失标记
    rewritten: [{row, col, final_text}] }                # 含划掉格的“保留片段拼接”稿（剥编号、清理前）

要点：openpyxl 必须以 rich_text=True 读取，否则单元格内局部红字/划掉读不到
（cell.font 只反映整格格式，业务的真实标记藏在富文本 run 里）。
"""
import argparse, json, re

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock

RED = {"FFFF0000", "FFC00000", "00FF0000", "00C00000"}
YELLOW = {"FFFFFF00", "00FFFF00", "FFFFFF00"}


def _rgb(color):
    if color is None:
        return None
    v = getattr(color, "rgb", None)
    return v if isinstance(v, str) else None


def parse_cell(cell):
    """返回 (plain, segments=[{t, c, s}])。富文本逐段保留颜色与划掉标记。"""
    v = cell.value
    if v is None:
        return "", []
    if isinstance(v, CellRichText):
        segs, plain = [], []
        for part in v:
            if isinstance(part, TextBlock):
                col = _rgb(part.font.color if part.font else None)
                segs.append({"t": str(part.text), "c": col,
                             "s": bool(part.font and part.font.strike)})
                plain.append(str(part.text))
            else:
                segs.append({"t": str(part), "c": None, "s": False})
                plain.append(str(part))
        return "".join(plain), segs
    return str(v), [{"t": str(v), "c": None, "s": False}]


def fill_rgb(cell):
    f = cell.fill
    if f is not None and f.fill_type == "solid":
        fg = f.fgColor
        if fg.type == "rgb" and isinstance(fg.rgb, str):
            return fg.rgb
    return None


def clean_final(s):
    """保留片段拼接稿的标点清理：剥离后可能出现『，优于补充说明』『孤行』等。"""
    s = re.sub(r"，(\s*)优于补充说明：", r"。\n\n优于补充说明：", s)
    s = re.sub(r"优于补充说明：\n+", "优于补充说明：", s)
    s = re.sub(r"\n优于补充说明：", "\n\n优于补充说明：", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"。。", "。", s)
    return s.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--cols", default="5,6,7,8",
                    help="参与标记判定的列号（默认 5-8：答案/要求/响应/偏离）")
    args = ap.parse_args()
    cols = {int(x) for x in args.cols.split(",")}

    wb = openpyxl.load_workbook(args.xlsx, rich_text=True)
    ws = wb.active
    struck, yellow, rewritten = [], [], []
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(r, c)
            fr = fill_rgb(cell)
            if fr in YELLOW:
                yellow.append({"row": r, "col": c, "text": str(cell.value or "")})
            if c not in cols:
                continue
            plain, segs = parse_cell(cell)
            bad = [s for s in segs if s["s"] or (s["c"] or "") in RED]
            if not bad:
                continue
            kept = "".join(s["t"] for s in segs if not s["s"] and (s["c"] or "") not in RED)
            item = {"row": r, "col": c, "plain": plain,
                    "bad": bad, "kept": kept}
            # 行首编号剥除：生成器会把编号加回去，修订层只存正文
            kept2 = re.sub(r"^\s*(\d+(?:\.\d+)*[.、．]?\s*|（\d+）\s*)", "", kept.strip())
            item["final_text"] = clean_final(kept2)
            rewritten.append({"row": r, "col": c, "final_text": item["final_text"]})
            struck.append(item)

    json.dump({"struck": struck, "yellow": yellow, "rewritten": rewritten},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("struck(过度承诺):", len(struck), " yellow(缺失):", len(yellow),
          " rewritten:", len(rewritten), " ->", args.out)


if __name__ == "__main__":
    main()
