import httpx,hashlib,json
from pathlib import Path
root=Path('/home/ubuntu/codex-workspace/deta-rerank-models/bge-reranker-v2-m3-int8');root.mkdir(parents=True,exist_ok=True)
base='https://modelscope.cn/api/v1/models/onnx-community/bge-reranker-v2-m3-ONNX/repo';report=[]
with httpx.Client(trust_env=False,follow_redirects=True,timeout=120) as c:
 listing=c.get(base+'/files',params={'Revision':'master','Recursive':'true'}).json()['Data']['Files'];byname={x['Path']:x for x in listing}
 for name in ['config.json','special_tokens_map.json','tokenizer.json','tokenizer_config.json','onnx/model_int8.onnx']:
  item=byname[name];dest=root/name;dest.parent.mkdir(exist_ok=True);tmp=dest.with_suffix(dest.suffix+'.part')
  with c.stream('GET',base,params={'Revision':item['Revision'],'FilePath':name}) as r:
   r.raise_for_status()
   with tmp.open('wb') as f:
    for b in r.iter_bytes(1024*1024):f.write(b)
  with tmp.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
  assert sha==item['Sha256'] and tmp.stat().st_size==item['Size'];tmp.replace(dest)
  report.append({'file':name,'bytes':item['Size'],'sha256':sha,'revision':item['Revision']});print(name,item['Size'],flush=True)
(root/'deployment-manifest.json').write_text(json.dumps({'repo':'onnx-community/bge-reranker-v2-m3-ONNX','source':'ModelScope','files':report},indent=2))
