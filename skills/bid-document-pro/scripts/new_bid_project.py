"""Create a new isolated bid project and unfilled internal checklists, never overwrite."""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
from datetime import datetime,timezone
RESERVED={'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,required=True);ap.add_argument('--name',required=True);ap.add_argument('--dry-run',action='store_true');a=ap.parse_args()
    name=a.name
    if not name.strip() or name!=name.strip() or name.endswith('.') or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name in {'.','..'} or name.split('.')[0].upper() in RESERVED:ap.error('Use one safe project directory name, not a path or reserved Windows name.')
    root=a.root.resolve();dest=(root/name).resolve()
    if dest.parent!=root:ap.error('Project must stay directly inside the specified root.')
    if dest.exists():ap.error('Project exists. Reuse it or explicitly choose a new project/version; nothing overwritten.')
    home=Path(__file__).resolve().parents[1];templates=home/'templates'
    plans=[('招标文件解读表.md','01_招标文件解读表.md'),('废标项与符合性检查表.md','02_废标项与符合性检查表.md'),('评分响应矩阵.md','03_评分响应矩阵.md')]
    contents={target:(templates/source).read_text(encoding='utf-8') for source,target in plans}
    contents['05_标书自查报告.md']='# 标书自查报告（内部非递交件）\n\n状态：尚未检查，不是通过证明。\n\n| 检查级别 | 检查项 | 实际范围/位置 | 结果 | 待补/建议 |\n|---|---|---|---|---|\n'
    contents['06_递交与待补清单.md']='# 递交与待补清单（内部非递交件）\n\n状态：尚未核查，不代表可递交。\n\n| 类别 | 要求及来源 | 截止/方式 | 当前材料/位置 | 状态 | 责任人 |\n|---|---|---|---|---|---|\n'
    if a.dry_run:
        print(json.dumps({'status':'dry_run','project_path':str(dest),'checklists':list(contents)},ensure_ascii=False));return
    dest.mkdir(parents=True,exist_ok=False)
    internal=dest/'内部审查_非递交件';internal.mkdir();(dest/'00_输入原件').mkdir();(dest/'04_投标文件正文').mkdir();(dest/'work').mkdir()
    for filename,text in contents.items():(internal/filename).write_text(text,encoding='utf-8')
    (dest/'project.json').write_text(json.dumps({'name':name,'created_at_utc':datetime.now(timezone.utc).isoformat(),'skill':'bid-document-pro','version':'1.0.0','status':'initialized_not_reviewed','inputs':[],'qualification':'unresolved','delivery_ready':False},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'initialized_not_reviewed','project_path':str(dest),'delivery_ready':False},ensure_ascii=False))
if __name__=='__main__':main()
