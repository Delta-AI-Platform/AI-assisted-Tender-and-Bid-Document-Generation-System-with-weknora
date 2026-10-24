"""Authenticated UI facade to the same project-bound Agent tools."""
import json,sqlite3
from contextlib import closing
from pathlib import Path
from starlette.responses import JSONResponse
from server import invoke

async def library_response(request,binding):
 args=request.query_params;action=args.get('action','status');library=args.get('library','enterprise_facts')
 if library not in ['knowledge','enterprise_facts','inspection_reports','history']:return JSONResponse({'error':'Unknown library'},status_code=400)
 async def call(name,arguments):
  result=await invoke(Path(binding['state']),binding['run_id'],{'method':'call','name':name,'arguments':arguments})
  if not result.get('ok'):raise ValueError(result.get('error','Library tool failed'))
  return result['data']
 try:
  if action=='status':
   libraries=[await call('library_status',{'library':x}) for x in ['enterprise_facts','inspection_reports','history']]
   # Only an explicit project memory record; never accepts a caller file path.
   with closing(sqlite3.connect(binding['state'])) as c:
    row=c.execute('SELECT value FROM memory WHERE run_id=? AND kind=? AND key=? ORDER BY revision DESC LIMIT 1',(binding['run_id'],'evidence','history-readability-20261023')).fetchone()
   memory=json.loads(row[0]) if row else None
   data={'libraries':libraries,'readability':{k:memory.get(k) for k in ['total','readable','unreadable','read_modes','excluded_parse_failed_records']} if memory else None}
  elif action=='skills':
   mounted=await call('list_skills',{});items=[]
   for name in mounted['skills']:
    verified=await call('read_skill',{'name':name,'path':'SKILL.md'})
    items.append({'name':name,'sha256':verified['sha256'],'execution':'project_mcp','verified':True})
   data={'skills':items,'run_id':binding['run_id']}
  elif action=='search':
   query=args.get('q','').strip()
   if not query or len(query)>200:return JSONResponse({'error':'请输入不超过200字的检索词'},status_code=400)
   data=await call('search_library',{'library':library,'query':query,'limit':10})
  elif action=='evidence-search':
   if library not in ['enterprise_facts','inspection_reports']:return JSONResponse({'error':'Evidence search requires an evidence library'},status_code=400)
   query=args.get('q','').strip()
   if not query or len(query)>500:return JSONResponse({'error':'请输入不超过500字的检索词'},status_code=400)
   data=await call('search_evidence_candidates',{'library':library,'query':query,'subject':args.get('subject','')[:120],'model':args.get('model','')[:120],'limit':10})
  elif action=='read':
   data=await call('read_library_record',{'library':library,'id':args.get('id',''),'offset':int(args.get('offset','0')),'count':5})
  elif action=='source':data=await call('inspect_library_source',{'library':library,'id':args.get('id','')})
  else:return JSONResponse({'error':'Unsupported operation'},status_code=400)
  return JSONResponse({'data':data},headers={'Cache-Control':'no-store'})
 except (ValueError,KeyError):return JSONResponse({'error':'资料暂时无法读取，请确认项目权限和来源版本。'},status_code=400)
