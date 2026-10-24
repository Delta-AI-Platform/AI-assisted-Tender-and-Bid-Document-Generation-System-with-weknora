#!/usr/bin/env python3
"""Materialize failed OCR/vision work separately from the active knowledge status."""
import argparse,gzip,hashlib,json,os,tempfile
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 with gzip.open(a.manifest,'rt',encoding='utf-8') as f:data=json.load(f)
 items=[]
 for row in data.get('images',[]):
  failed=[]
  if row.get('ocr_status') not in ('success',):failed.append('ocr')
  if row.get('vision_status') not in ('success','partial'):failed.append('vision_facts')
  if failed:items.append({'content_id':row.get('content_id'),'sha256':row.get('sha256'),'failed_layers':failed,
                          'ocr_status':row.get('ocr_status'),'vision_status':row.get('vision_status'),
                          'source_document_ids':row.get('source_document_ids',[]),'canonical_source_rel':row.get('canonical_source_rel')})
 payload={'version':'knowledge-failed-image-queue-v1','generated_at':datetime.now(timezone.utc).isoformat(),
          'manifest_path':str(a.manifest),'manifest_sha256':hashlib.sha256(a.manifest.read_bytes()).hexdigest(),
          'items':items,'item_count':len(items),'layer_counts':dict(Counter(layer for x in items for layer in x['failed_layers'])),
          'retry_policy':'next atomic rebuild retries failed cache entries; successful cache entries are reused'}
 a.output.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix=a.output.name+'.',dir=a.output.parent)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False,indent=2)
  os.replace(tmp,a.output)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 print(json.dumps({'output':str(a.output),'items':len(items),'layer_counts':payload['layer_counts']},ensure_ascii=False))
if __name__=='__main__':main()
