"""Render local Markdown report tables to safe light-theme HTML; standard library only."""
from __future__ import annotations
import argparse, html, re
from pathlib import Path

def cells(line):return [x.replace('\\|','|').strip() for x in re.split(r'(?<!\\)\|',line.strip().strip('|'))]
def render(markdown,title):
    lines=markdown.splitlines();out=[];i=0
    while i<len(lines):
        s=lines[i].strip()
        if s.startswith('|') and i+1<len(lines) and all(re.fullmatch(r':?-{3,}:?',c.replace(' ','')) for c in cells(lines[i+1])):
            head=cells(s);out.append('<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+html.escape(c)+'</th>' for c in head)+'</tr></thead><tbody>');i+=2
            while i<len(lines) and lines[i].strip().startswith('|'):
                row=cells(lines[i])
                if len(row)!=len(head):raise ValueError(f'Table width mismatch at Markdown line {i+1}: {len(row)} cells, expected {len(head)}')
                out.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in row)+'</tr>');i+=1
            out.append('</tbody></table></div>');continue
        h=re.match(r'^(#{1,6})\s+(.*)$',s)
        if h:out.append(f'<h{len(h[1])}>'+html.escape(h[2])+f'</h{len(h[1])}>')
        elif s:out.append('<p>'+html.escape(s)+'</p>')
        i+=1
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font:16px/1.65 "Microsoft YaHei",sans-serif;color:#172a3a;background:#f5f7fa;margin:0}main{max-width:1400px;margin:24px auto;padding:28px;background:#fff}h1,h2,h3{color:#193c61}.notice{background:#fff4d5;padding:12px;border-left:4px solid #b78618}.table-wrap{overflow-x:auto;margin:18px 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #cbd5e1;padding:9px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e6eff8;text-align:left}tr:nth-child(even){background:#f8fafc}@media print{body{background:#fff}main{padding:0;margin:0;max-width:none}thead{display:table-header-group}tr{break-inside:avoid}}</style></head><body><main><p class="notice">内部审查 · 非递交件。此页是报告预览，不是投标DOCX或Office版式审核证明。</p>'+''.join(out)+'</main></body></html>\n'

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('markdown',type=Path);ap.add_argument('--output',type=Path);a=ap.parse_args()
    src=a.markdown.resolve(strict=True);out=(a.output or src.with_suffix('.html')).resolve()
    if out==src or out.exists():ap.error('Refusing to overwrite source or existing HTML. Choose a new report version.')
    try:result=render(src.read_text(encoding='utf-8-sig'),src.stem)
    except ValueError as e:ap.exit(2,str(e)+'\n')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(result,encoding='utf-8');print(out)
if __name__=='__main__':main()
