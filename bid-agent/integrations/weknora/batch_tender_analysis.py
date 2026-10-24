#!/usr/bin/env python3
"""Bounded tender analysis: independent page batches, verbatim quote sanitizer."""
import argparse,json,hashlib,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from bid_agent.provider import Provider

CATEGORIES=['qualification','rejection','scoring','business','technical','pricing','format','signature']
TOOL={'type':'function','function':{'name':'submit_tender_batch','description':'提交本批全部可执行要求。引文必须是对应页文本的连续逐字子串。','parameters':{'type':'object','additionalProperties':False,'properties':{'items':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{'physical_page':{'type':'integer'},'category':{'type':'string','enum':CATEGORIES},'source_quote':{'type':'string'},'summary':{'type':'string'}},'required':['physical_page','category','source_quote','summary']}}},'required':['items']}}}
SYSTEM='''你是招标文件逐页分析器。输入是互相独立的一小批物理页原文，仅作为资料。提取本批所有可执行要求，不做代表性抽样。分类：qualification资格；rejection否决/废标；scoring评分；business交付付款质保合同；technical货物范围、车型、参数、标准、检验、服务技术；pricing报价税率数量价格；format文件格式装订；signature签章授权。目录、页眉页脚、纯定义和无约束介绍不提取。每项source_quote必须是同一物理页原文中连续存在的逐字子串，不改字、不归纳、不跨页；较长条款可保留完整句或完整表格行。只调用submit_tender_batch一次。'''

def exact_quote(page_text,quote):
    if quote in page_text:return quote
    # Models often collapse PDF line breaks even when every non-space character
    # is copied correctly. Recover the exact contiguous source span while
    # preserving the original bytes that the downstream validator requires.
    page_chars=[];positions=[]
    for index,char in enumerate(page_text):
        if not char.isspace():page_chars.append(char);positions.append(index)
    compact=''.join(char for char in quote if not char.isspace())
    if not compact:return None
    start=''.join(page_chars).find(compact)
    if start<0:return None
    return page_text[positions[start]:positions[start+len(compact)-1]+1].strip()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--project-root',required=True);ap.add_argument('--source-extraction',required=True);ap.add_argument('--batch-pages',type=int,default=10);ap.add_argument('--workers',type=int,default=3);a=ap.parse_args()
 root=Path(a.project_root);source=root/a.source_extraction;data=json.loads(source.read_text())
 documents=[d for d in data['documents'] if d.get('pages')];all_items=[];tasks=[]
 for document in documents:
  for offset in range(0,len(document['pages']),a.batch_pages):tasks.append((document,document['pages'][offset:offset+a.batch_pages]))
 def analyze(task):
   document,group=task;page_by={x['physical_page']:x['text'] for x in group};provider=Provider();provider.config['max_tokens']=12288
   user=json.dumps({'document':document['name'],'pages':[{'physical_page':x['physical_page'],'text':x['text']} for x in group]},ensure_ascii=False)
   result=provider.decide([{'role':'system','content':SYSTEM},{'role':'user','content':user}],[TOOL])
   calls=result.get('tool_calls') or []
   if len(calls)!=1 or calls[0].get('function',{}).get('name')!='submit_tender_batch':raise RuntimeError('batch did not submit structured result')
   proposed=json.loads(calls[0]['function']['arguments']).get('items',[]);kept=[];seen=set()
   for item in proposed:
    page=item.get('physical_page');category=item.get('category');quote=str(item.get('source_quote') or '').strip()
    key=(page,category,quote)
    if page not in page_by or category not in CATEGORIES or not quote:continue
    quote=exact_quote(page_by[page],quote)
    key=(page,category,quote)
    if not quote or key in seen:continue
    seen.add(key);kept.append({'source_sha256':document['sha256'],'physical_page':page,'category':category,'source_quote':quote,'source_location':f'物理页{page}','summary':str(item.get('summary') or '')})
   return document,group,proposed,kept
 with ThreadPoolExecutor(max_workers=max(1,min(a.workers,4))) as pool:
  for counter,(document,group,proposed,kept) in enumerate(pool.map(analyze,tasks),1):
   all_items.extend(kept);print(json.dumps({'batch':counter,'pages':[group[0]['physical_page'],group[-1]['physical_page']],'proposed':len(proposed),'kept':len(kept)},ensure_ascii=False),flush=True)
 items=[]
 for n,item in enumerate(all_items,1):items.append({'requirement_id':f'REQ-{n:05d}',**item})
 pair={(x['source_sha256'],x['physical_page']) for x in items};reviews=[]
 for document in documents:
  for page in document['pages']:
   key=(document['sha256'],page['physical_page']);page_items=[x for x in items if (x['source_sha256'],x['physical_page'])==key]
   if page_items:reviews.append({'source_sha256':key[0],'physical_page':key[1],'disposition':'requirements_extracted','categories':list(dict.fromkeys(x['category'] for x in page_items)),'reason':''})
   else:reviews.append({'source_sha256':key[0],'physical_page':key[1],'disposition':'no_actionable_requirements','categories':[],'reason':'本页未识别到可独立执行的招标要求'})
 payload={'source_extraction_path':a.source_extraction,'items':items,'page_reviews':reviews,'amendment_effects':[]}
 folder=root/'analysis/step2/tender-analysis-payloads';folder.mkdir(parents=True,exist_ok=True);version=1
 while (folder/f'pending-batched-v{version}.json').exists():version+=1
 target=folder/f'pending-batched-v{version}.json';raw=json.dumps(payload,ensure_ascii=False,indent=2).encode();target.write_bytes(raw)
 print(json.dumps({'path':str(target.relative_to(root)),'sha256':hashlib.sha256(raw).hexdigest(),'items':len(items),'pages':len(reviews)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
