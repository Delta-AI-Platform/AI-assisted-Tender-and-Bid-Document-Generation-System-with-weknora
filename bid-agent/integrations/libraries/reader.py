"""Fixed-source read-only adapter. Installed root-owned; run as deta-bid."""
import functools,gzip,hashlib,json,math,os,re,sqlite3,stat,subprocess,sys,tempfile,unicodedata,urllib.request
from decimal import Decimal,InvalidOperation
from pathlib import Path
ROOT=Path('/mnt/ai_bid_knowledge')
AUTH=Path('/srv/deta-bid/historical-bid-authority/current')
CAT=Path('/srv/deta-bid/knowledge-index/catalog.json.gz')
CONVERSION_ROOTS=(Path('/srv/deta-bid/historical-bid-authority/conversion-cache'),Path('/srv/deta-bid/agent-library-refresh/full-20261023/authority/conversion-cache'))
MODEL_HISTORY=Path('/srv/deta-bid/model-response-history/model-history-20261020-070430-scoped/history.sqlite3')
TEXT_CACHE=Path('/srv/deta-bid/knowledge-index/text-cache')
EVIDENCE_TEXT_INDEX=Path('/srv/deta-bid/evidence-retrieval/chunks.sqlite3')
APPROVED_MAPPINGS=Path('/srv/deta-bid/evidence-retrieval/approved-mappings.json')
CURATION=Path('/srv/deta-bid/knowledge-curation')
INSPECTION=Path('/srv/deta-bid/inspection-report-knowledge')

def knowledge_health(rows):
 result={'original_searchability':{'status':'ready' if any(x.get('active_module') for x in rows) else 'unavailable',
          'active_documents':sum(bool(x.get('active_module')) for x in rows)},
         'ocr':{'status':'not_indexed','counts':{}},'vision_facts':{'status':'not_indexed','counts':{}},
         'conclusion_use':{'status':'runtime_verification_required','preauthorized_conclusions':0,
          'rule':'retrieval candidates still require original-page, applicability, configuration-lock, and comparison gates'}}
 manifest=CURATION/'image_content_manifest.json.gz'
 if manifest.is_file():
  data=load(manifest);meta=data.get('meta',{});images=data.get('images',[])
  ocr=meta.get('ocr_status_counts') or {};vision=meta.get('vision_status_counts') or {}
  result['image_manifest']={'sha256':sha(manifest),'images':len(images),'generated_at':meta.get('generated_at'),'updated_at':meta.get('updated_at')}
  result['ocr']={'status':'ready_with_failures' if ocr.get('success') else 'failed','counts':ocr}
  result['vision_facts']={'status':'ready_with_failures' if vision.get('success') or vision.get('partial') else 'failed','counts':vision}
  retry=CURATION/'failed-image-retry-queue.json'
  if retry.is_file():
   queued=json.loads(retry.read_text());result['failed_image_retry_queue']={'status':'queued' if queued.get('item_count') else 'empty','items':queued.get('item_count'),'layer_counts':queued.get('layer_counts'),'sha256':sha(retry)}
 inspection=INSPECTION/'inspection-report.meta.json'
 if inspection.is_file():
  data=json.loads(inspection.read_text());result['inspection_parameter_store']={'status':data.get('status'),'sha256':sha(inspection),
   'reports':data.get('report_count'),'pages':data.get('page_count'),'facts':data.get('fact_count'),'generated_at':data.get('generated_at')}
 return result

def normalized(value):
 return ''.join(ch for ch in unicodedata.normalize('NFKC',str(value or '')).upper() if ch.isalnum())

def model_family(value):
 canonical=normalized(value)
 # Only the WLR-5 platform relationship has been explicitly approved by the
 # retained matching rules (for example WLR-5 -> WLR-5C(E)).  WC/GZC suffixes
 # identify materially different vehicles and must not be broadened by regex.
 m=re.match(r'^WLR(5)',canonical)
 return 'WLR'+m.group(1) if m else canonical

@functools.lru_cache(maxsize=1)
def authority_context():
 pairs=load(AUTH/'authority.pairs.json.gz')['pairs'];files={x.get('source_id'):x for x in catalog('history')}
 groups={}
 for pair in pairs:
  block=pair.get('block') or {};ref=block.get('source_ref') or {};key=(pair.get('source_id'),ref.get('block_order'))
  info=groups.setdefault(key,{'tokens':[]})
  text=str(block.get('requirement') or '')+' '+str(block.get('response') or '')
  info['tokens'] += [normalized(x) for x in re.findall(r'(?:WLR|WLL|WC|GZC|CJL)\s*[-－]?\s*\d+[A-Z0-9（）()\-]*',text,re.I)]
 records=[]
 for pair in pairs:
  block=pair.get('block') or {};requirement=str(block.get('requirement') or '');response=str(block.get('response') or '')
  heading=re.sub(r'^\s*\d+(?:\.\d+)*\s*[、.]?\s*','',requirement);ref=block.get('source_ref') or {};key=(pair.get('source_id'),ref.get('block_order'))
  records.append({'pair':pair,'block':block,'requirement':requirement,'response':response,
                  'heading_key':normalized(heading),'full_key':normalized(requirement+' '+response),
                  'group_tokens':groups.get(key,{}).get('tokens',[]),'source':files.get(pair.get('source_id'),{})})
 return records

@functools.lru_cache(maxsize=32)
def model_sql_rows(canonical):
 with sqlite3.connect('file:'+str(MODEL_HISTORY)+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  rows=db.execute('''SELECT r.*,m.model matched_model FROM response r JOIN response_model m ON r.id=m.row_id
                     WHERE r.eligible=1 AND m.model=? ORDER BY r.year DESC,r.id''',(canonical,)).fetchall()
 return [dict(x) for x in rows]

def search_model_history(model,parameter,aliases,limit):
 if not model or not parameter or type(limit)is not int or not 1<=limit<=20:raise ValueError('Model, parameter and limit required')
 names=[];compounds=[]
 for value in [parameter,*(aliases or [])]:
  key=normalized(str(value).rstrip('*'))
  target=compounds if str(value).endswith('*') else names
  if key and key not in target:target.append(key)
 if not names:raise ValueError('Parameter or aliases required')
 canonical=normalized(model);family=model_family(model)
 rows=model_sql_rows(canonical)
 out=[];seen=set()
 # The scoped SQL index contains compact parameter rows, while the authority
 # pair index retains newer WLR-5C(E) table rows.  Prefer rows that explicitly
 # name the requested model and whose requirement starts with the exact
 # parameter name or an approved alias.  Merely mentioning a keyword later in
 # a paragraph is not a match.
 # Some retained response tables declare the model once (for example in a
 # “5-seat command vehicle model: WLR-5C(E)” row) and do not repeat it in every
 # parameter row.  The older implementation consequently returned false
 # not_found results even though the same verified authority table contained a
 # direct answer.  Recover only model-exclusive table blocks: the block must
 # mention the requested exact model at least twice and may contain no model
 # outside the explicitly approved family.  This keeps table context while
 # refusing cross-model reuse.
 contextual=[]
 for record in authority_context():
  tokens=record['group_tokens']
  # A response table can contain WLR-5B-looking component identifiers while
  # its vehicle heading is WLR-5C(E).  Accept the table context only when the
  # exact requested vehicle is declared repeatedly and no different vehicle
  # family occurs. SQL fallback below remains exact-model only.
  if tokens.count(canonical)<2 or any(model_family(x)!=family for x in tokens):continue
  hkey=record['heading_key'];exact_name=next((name for name in names if hkey.startswith(name)),None);compound_name=next((name for name in compounds if hkey.startswith(name)),None)
  answer=record['response'].strip()
  if not answer or (not exact_name and not compound_name):continue
  source=record['source'];contextual.append((int(source.get('year') or 0),record['pair'].get('source_id'),record['block'],record['requirement'],answer,source,exact_name,compound_name))
 contextual.sort(key=lambda x:(-x[0],x[1],str((x[2].get('source_ref') or {}).get('block_order')),int((x[2].get('source_ref') or {}).get('table_row') or 0)))
 for _,source_id,block,requirement,answer,source,exact_name,compound_name in contextual:
  key=(answer,requirement,canonical)
  if key in seen:continue
  seen.add(key)
  out.append({'id':block.get('block_id'),'requested_model':model,'matched_model':model,
              'match_scope':'exact_model','match_type':'compound_alias' if compound_name and not exact_name else ('exact_parameter' if normalized(re.sub(r'^\s*\d+(?:\.\d+)*\s*[、.]?\s*','',requirement)).startswith(normalized(parameter)) else 'explicit_alias'),
              'parameter':parameter,'requirement':requirement,'standard_answer':answer,
              'deviation':'','proof':'','project':source.get('project_name',''),
              'document':source.get('filename',''),'source_path':source.get('source_path',''),'source_ref':block.get('source_ref'),
              'source_triplet':source.get('project_name','')+'｜'+model+'｜'+source.get('filename',''),
              'context_guard':'exact model declared repeatedly in the same authority table block; no other model family present'})
  if len(out)>=limit:break
 for record in authority_context():
  if len(out)>=limit:break
  pair=record['pair'];block=record['block'];requirement=record['requirement'];response=record['response']
  if canonical not in record['full_key']:continue
  hkey=record['heading_key']
  exact_name=next((name for name in names if hkey.startswith(name)),None)
  compound_name=next((name for name in compounds if hkey.startswith(name)),None)
  if not exact_name and not compound_name:continue
  source=record['source'];answer=response.strip()
  if not answer:continue
  key=(answer,requirement,canonical)
  if key in seen:continue
  seen.add(key)
  out.append({'id':block.get('block_id'),'requested_model':model,'matched_model':model,
              'match_scope':'exact_model','match_type':'compound_alias' if compound_name and not exact_name else ('exact_parameter' if hkey.startswith(normalized(parameter)) else 'explicit_alias'),
              'parameter':parameter,'requirement':requirement,'standard_answer':answer,
              'deviation':'','proof':'','project':source.get('project_name',''),
              'document':source.get('filename',''),'source_path':source.get('source_path',''),'source_ref':block.get('source_ref'),
              'source_triplet':source.get('project_name','')+'｜'+model+'｜'+source.get('filename','')})
  if len(out)>=limit:break
 for row in rows:
  if len(out)>=limit:break
  exact=normalized(row['parameter']) in names or normalized(row['parameter_key']) in names
  compound=any(normalized(re.sub(r'^\s*\d+(?:\.\d+)*\s*[、.]?\s*','',str(row['requirement'] or ''))).startswith(key) for key in compounds)
  if not exact and not compound:continue
  key=(row['response'],row['requirement'],row['matched_model'])
  if key in seen:continue
  seen.add(key)
  out.append({'id':row['id'],'requested_model':model,'matched_model':row['matched_model'],
              'match_scope':'exact_model' if row['matched_model']==canonical else 'same_model_family',
              'match_type':'compound_alias' if compound and not exact else ('exact_parameter' if normalized(row['parameter'])==normalized(parameter) else 'explicit_alias'),
              'parameter':row['parameter'],'requirement':row['requirement'],'standard_answer':row['response'],
              'deviation':row['deviation'],'proof':row['proof'],'project':row['project'],
              'document':Path(row['source_path']).name,'source_path':row['source_path'],'source_ref':row['source_ref'],
              'source_triplet':row['project']+'｜'+row['matched_model']+'｜'+Path(row['source_path']).name})
  if len(out)>=limit:break
 return {'status':'found' if out else 'not_found','requested_model':model,'parameter':parameter,
         'results':out,'usage':'direct_standard_answer' if out else 'leave_blank',
         'source_status':'verified_authority_library','matching':'approved object-to-model mapping, then exact returned model and parameter-title prefix; no second family expansion'}

@functools.lru_cache(maxsize=16)
def load(p):
 with gzip.open(p,'rt',encoding='utf-8') as f:return json.load(f)
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def is_inspection_document(row):
 name=str(row.get('display_name') or '')
 return row.get('category_code')=='TEST' or (not name.lower().endswith(('.rar','.zip','.7z')) and any(x in name for x in ('检验报告','检测报告','试验报告','测试报告','出厂检验')))

def catalog(lib):
 if lib in ('knowledge','enterprise_facts','inspection_reports'):
  rows=load(CAT)['documents']
  if lib=='enterprise_facts':return [x for x in rows if not is_inspection_document(x)]
  if lib=='inspection_reports':return [x for x in rows if is_inspection_document(x)]
  return rows
 if lib=='history':return load(AUTH/'authority.cases.json.gz')['files']
 raise ValueError('Unknown library')

def post_json(url,payload,timeout=60):
 body=json.dumps(payload,ensure_ascii=False).encode()
 request=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)

def cosine(a,b):
 den=math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))
 return sum(x*y for x,y in zip(a,b))/den if den else 0.0

@functools.lru_cache(maxsize=1)
def text_cache_files():
 out={}
 for path in TEXT_CACHE.glob('kbdoc_*.json.gz'):
  doc_id=path.name.split('.',1)[0]
  if doc_id not in out or path.stat().st_mtime_ns>out[doc_id].stat().st_mtime_ns:out[doc_id]=path
 return out

@functools.lru_cache(maxsize=1024)
def cached_document_text(doc_id):
 path=text_cache_files().get(doc_id)
 if not path:return ''
 try:return str(load(path).get('text') or '')
 except Exception:return ''

@functools.lru_cache(maxsize=1024)
def indexed_document_chunks(lib,doc_id):
 if not EVIDENCE_TEXT_INDEX.is_file():return []
 try:
  with sqlite3.connect('file:'+str(EVIDENCE_TEXT_INDEX)+'?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row
   rows=db.execute('''SELECT content,source_locators,member_id,member_page,source_sha256,chunk_index
                      FROM chunks WHERE library=? AND doc_id=? ORDER BY chunk_index''',(lib,doc_id)).fetchall()
  return [dict(x) for x in rows]
 except (OSError,sqlite3.Error):return []

def indexed_matching_doc_ids(lib,term):
 if not EVIDENCE_TEXT_INDEX.is_file() or not term:return set()
 try:
  with sqlite3.connect('file:'+str(EVIDENCE_TEXT_INDEX)+'?mode=ro',uri=True) as db:
   return {x[0] for x in db.execute("SELECT DISTINCT doc_id FROM chunks WHERE library=? AND instr(lower(content),lower(?))>0",(lib,term))}
 except (OSError,sqlite3.Error):return set()

def indexed_term_hit_counts(lib,terms):
 counts={};sources={}
 if not EVIDENCE_TEXT_INDEX.is_file():return counts,sources
 try:
  with sqlite3.connect('file:'+str(EVIDENCE_TEXT_INDEX)+'?mode=ro',uri=True) as db:
   for term in terms:
    for doc_id,digest in db.execute("SELECT DISTINCT doc_id,source_sha256 FROM chunks WHERE library=? AND instr(lower(content),lower(?))>0",(lib,term)):
     counts[doc_id]=counts.get(doc_id,0)+1;sources[doc_id]=digest
 except (OSError,sqlite3.Error):pass
 return counts,sources

def document_text(lib,row):
 cached=cached_document_text(row.get('id',''))
 indexed='\n'.join(x['content'] for x in indexed_document_chunks(lib,row.get('id','')) if x.get('content'))
 return (cached+'\n'+indexed).strip()

def retrieval_text(lib,row,terms):
 # Keep the leading text short because the cross encoder truncates at 512
 # tokens.  Long member-path lists would otherwise hide the matched page.
 base=' | '.join(str(x or '') for x in (row.get('display_name'),row.get('category_code'),row.get('material_type'),row.get('product_model')))+' | '+' '.join(row.get('tokens') or [])[:1000]
 body=document_text(lib,row)
 if not body:return base[:15000]
 windows=[];fold=body.casefold()
 for term in terms:
  start=0
  while len(windows)<12:
   pos=fold.find(term,start)
   if pos<0:break
   windows.append(body[max(0,pos-500):pos+1500]);start=pos+len(term)
 if windows:return (base+'\n'+'\n'.join(windows))[:15000]
 return (base+'\n'+body[:12000])[:15000]

def page_hints(lib,row,query):
 body=cached_document_text(row.get('id',''));hints=[]
 for match in re.finditer(re.escape(query),body,re.I):
  prefix=body[max(0,match.start()-1200):match.start()]
  names=re.findall(r'资料：([^\n]+?__P(\d{3})\.(?:jpg|jpeg|png|pdf))',prefix,re.I)
  if names:
   name,page=names[-1];item={'physical_page_hint':int(page),'cached_page_name':name,'matched_quote':body[max(0,match.start()-100):match.end()+220].replace('\n',' ')}
   if item not in hints:hints.append(item)
 for chunk in indexed_document_chunks(lib,row.get('id','')):
  content=str(chunk.get('content') or '')
  match=re.search(re.escape(query),content,re.I)
  if not match:continue
  try:locators=json.loads(chunk.get('source_locators') or '[]')
  except (TypeError,ValueError):locators=[]
  matching=[x for x in locators if isinstance(x,dict) and x.get('page') and (not x.get('quote') or query.casefold() in str(x.get('quote')).casefold())]
  if not matching:matching=[x for x in locators if isinstance(x,dict) and x.get('page')]
  for locator in matching[:3]:
   item={'physical_page_hint':int(locator['page']),'matched_quote':str(locator.get('quote') or content[max(0,match.start()-100):match.end()+220]).replace('\n',' '),
         'mapping':locator.get('mapping'),'bbox':locator.get('bbox'),'source_hash':locator.get('source_hash'),
         'native_member_id':chunk.get('member_id'),'native_member_page':chunk.get('member_page')}
   if item not in hints:hints.append(item)
 return hints[:10]

def evidence_search(lib,query,subject,model,limit):
 if lib not in ('enterprise_facts','inspection_reports'):raise ValueError('Evidence search requires an evidence library')
 if not isinstance(query,str) or not query.strip() or len(query)>500:raise ValueError('Query invalid')
 if not isinstance(subject,str) or len(subject)>120 or not isinstance(model,str) or len(model)>120:raise ValueError('Subject/model invalid')
 if type(limit)is not int or not 1<=limit<=20:raise ValueError('Limit must be 1..20')
 rows=[x for x in catalog(lib) if x.get('active_module') and x.get('members')]
 before=len(rows);model_key=normalized(model);subject_key=normalized(subject)
 if model_key:
  rows=[x for x in rows if normalized(x.get('product_model'))==model_key]
 terms=[x.casefold() for x in re.split(r'[\s,，、;；:：/]+',query.strip()) if x]
 if subject_key:
  indexed_subject=indexed_matching_doc_ids(lib,subject)
  rows=[x for x in rows if subject_key in normalized(str(x.get('search_text') or '')+' '+cached_document_text(x.get('id',''))) or x.get('id') in indexed_subject]
 filtered=len(rows)
 indexed_hits,indexed_sources=indexed_term_hit_counts(lib,terms)
 ranked=[]
 for row in rows:
  base=(' '.join(str(x or '') for x in (row.get('display_name'),row.get('search_text')))+' '+cached_document_text(row.get('id',''))).casefold()
  score=max(sum(1 for term in terms if term in base),indexed_hits.get(row.get('id'),0))
  if score:ranked.append((score,row,None))
 ranked.sort(key=lambda x:(-x[0],x[1].get('display_name',''),x[1].get('id','')))
 positive=[];seen_sources=set()
 for item in ranked:
  source_key=indexed_sources.get(item[1].get('id')) or item[1].get('sha256') or item[1].get('id')
  if source_key in seen_sources:continue
  seen_sources.add(source_key);positive.append(item)
 keyword=[(score,row,retrieval_text(lib,row,terms)) for score,row,_ in positive[:30]]
 if not keyword:
  return {'library':lib,'query':query,'subject':subject,'model':model,'results':[],
          'pipeline':{'subject_model_filter':{'before':before,'after':filtered},'keyword_retrieval':{'candidates':0},'vector_recall':{'candidates':0},'rerank':{'candidates':0},'rule_validation':{'passed':0},'original_page_verification':{'status':'not_started'}},
          'usage':'no_evidence_found; do not infer satisfaction'}
 texts=[query]+[x[2] for x in keyword]
 embedded=post_json('http://127.0.0.1:18766/v1/embeddings',{'model':'BAAI/bge-small-zh-v1.5','input':texts,'encoding_format':'float','dimensions':512})
 vectors=[x['embedding'] for x in sorted(embedded['data'],key=lambda x:x['index'])];qv=vectors[0]
 vector_ranked=sorted([(cosine(qv,vectors[i+1]),*item) for i,item in enumerate(keyword)],key=lambda x:(-x[0],-x[1],x[2].get('id','')))[:30]
 reranked=post_json('http://127.0.0.1:18768/v1/rerank',{'model':'BAAI/bge-reranker-v2-m3-int8','query':query,'documents':[x[3] for x in vector_ranked],'top_n':min(len(vector_ranked),max(limit*3,limit))},timeout=120)
 results=[]
 for hit in reranked.get('results',[]):
  vector_score,keyword_score,row,_=vector_ranked[hit['index']]
  members=[m for m in row.get('members',[]) if m.get('id') and m.get('sha256') and m.get('source_rel') and str(m.get('extension','')).lower() in {'.pdf','.docx','.doc','.xlsx','.xls','.png','.jpg','.jpeg','.txt'}]
  if not members:continue
  hints=page_hints(lib,row,query)
  if query.casefold() not in str(row.get('display_name') or '').casefold() and not hints:continue
  hinted_pages={x['physical_page_hint'] for x in hints}
  # A page-image collection can select the exact member by page_no. A single
  # PDF remains the bound original for every locator page and must not vanish
  # merely because its catalog member has no page_no field.
  selected_members=[m for m in members if m.get('page_no') is None or m.get('page_no') in hinted_pages] if hinted_pages else members
  results.append({'id':row['id'],'title':row.get('display_name'),'category':row.get('category_code'),'material_type':row.get('material_type'),
                  'model':row.get('product_model'),'keyword_score':keyword_score,'vector_score':round(vector_score,6),
                  'rerank_score':round(float(hit['relevance_score']),6),'members':selected_members[:20],
                  'cached_text_available':bool(cached_document_text(row.get('id',''))),'indexed_text_available':bool(indexed_document_chunks(lib,row.get('id',''))),'page_hints':hints,
                  'rule_validation':'library_scope_active_model_subject_source_and_exact_title_or_page_match_passed',
                  'original_page_verification':'required_before_fact_use'})
  if len(results)>=limit:break
 return {'library':lib,'query':query,'subject':subject,'model':model,'results':results,
         'pipeline':{'subject_model_filter':{'before':before,'after':filtered},'keyword_retrieval':{'candidates':len(keyword),'positive_keyword_candidates':len(positive)},
                     'vector_recall':{'model':'BAAI/bge-small-zh-v1.5','candidates':len(vector_ranked)},
                     'rerank':{'model':'BAAI/bge-reranker-v2-m3-int8','candidates':len(reranked.get('results',[]))},
                     'rule_validation':{'passed':len(results)},'original_page_verification':{'status':'required','method':'inspect/import the selected hash-bound original and verify the cited physical page'}},
         'usage':'candidate_evidence_only; no satisfaction or deviation conclusion'}
def safe_source(rel):
 p=Path(rel)
 if p.is_absolute() or '..' in p.parts:raise ValueError('Invalid source relative path')
 result=ROOT/p
 if not result.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Source escapes library root')
 for parent in [result,*result.parents]:
  if parent==ROOT:break
  if parent.is_symlink():raise ValueError('Source symlink refused')
 if not result.is_file():raise ValueError('Source file missing')
 return result

def open_confined(p):
 parts=p.relative_to(ROOT).parts
 fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for part in parts[:-1]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
   os.close(fd);fd=nxt
  file_fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
 finally:os.close(fd)
 return os.fdopen(file_fd,'rb')

def source(lib,id):
 rows=catalog(lib)
 if lib in ('knowledge','enterprise_facts','inspection_reports'):
  for d in rows:
   if not d.get('active_module',False):continue
   for m in d.get('members',[]):
    if m.get('id')==id:return safe_source(m['source_rel']),m.get('sha256'),m
 else:
  roots=json.loads((AUTH/'authority.meta.json').read_text())['source_roots']
  for d in rows:
   if d.get('source_id')==id:return safe_source(str(Path(roots[d['domain']])/d['source_path'])),d.get('sha256'),d
 raise ValueError('Source ID not found in enabled library')

def run_text(command,timeout):
 result=subprocess.run(command,capture_output=True,timeout=timeout)
 if result.returncode:raise ValueError(Path(command[0]).name+' failed: '+result.stderr.decode(errors='replace')[:300])
 return result.stdout.decode('utf-8',errors='replace')

def extract_original_page(path,physical_page,catalog_page):
 suffix=path.suffix.lower()
 if type(physical_page)is not int or not 1<=physical_page<=10000:raise ValueError('physical_page must be 1..10000')
 with tempfile.TemporaryDirectory(prefix='deta-page-verify-') as tmp:
  temporary=Path(tmp);ocr_target=None;native=''
  if suffix=='.pdf':
   info=run_text(['pdfinfo',str(path)],30)
   match=re.search(r'^Pages:\s+(\d+)\s*$',info,re.M)
   if not match or physical_page>int(match.group(1)):raise ValueError('Physical page is outside the PDF')
   native=run_text(['pdftotext','-f',str(physical_page),'-l',str(physical_page),'-layout',str(path),'-'],60)
   prefix=temporary/'page'
   result=subprocess.run(['pdftoppm','-f',str(physical_page),'-l',str(physical_page),'-singlefile','-png','-r','300',str(path),str(prefix)],capture_output=True,timeout=90)
   if result.returncode:raise ValueError('PDF page render failed: '+result.stderr.decode(errors='replace')[:300])
   ocr_target=prefix.with_suffix('.png')
  elif suffix in {'.png','.jpg','.jpeg'}:
   if catalog_page is not None and physical_page!=int(catalog_page):raise ValueError('Requested page differs from the indexed page-image number')
   if catalog_page is None and physical_page!=1:raise ValueError('Standalone image has only physical page 1')
   ocr_target=path
  else:raise ValueError('Original page verification supports PDF, PNG and JPEG only')
  if not ocr_target.is_file() or ocr_target.stat().st_size>35_000_000:raise ValueError('Rendered page missing or exceeds OCR bound')
  # Automatic page segmentation is materially more reliable for multi-column
  # certificates and report tables than treating the whole page as one block.
  ocr=run_text(['tesseract',str(ocr_target),'stdout','-l','chi_sim+eng','--psm','3'],90)
  combined=(native+'\n'+ocr).strip()
  if not normalized(combined):raise ValueError('No readable text was obtained from the selected original page')
  return combined,('pdf_text_plus_page_ocr' if suffix=='.pdf' else 'original_page_image_ocr'),len(native),len(ocr)

@functools.lru_cache(maxsize=64)
def cached_original_page(path_text,physical_page,catalog_page,source_sha256):
 # source_sha256 is part of the cache key. verify_evidence_page hashes the
 # original before and after every use, so a changed file can never reuse text
 # from an older version.
 return extract_original_page(Path(path_text),physical_page,catalog_page)

def verify_evidence_page(lib,id,expected_sha256,physical_page,expected):
 if lib not in ('enterprise_facts','inspection_reports'):raise ValueError('Original page verification requires an evidence library')
 if not re.fullmatch(r'[0-9a-f]{64}',str(expected_sha256 or '')):raise ValueError('Valid expected_sha256 required')
 allowed=('subject','model','submodel','document_number','test_condition','parameter','value','value_max','quote')
 if not isinstance(expected,dict) or set(expected)-set(allowed):raise ValueError('Unsupported expected page field')
 fields={k:str(expected.get(k) or '').strip() for k in allowed}
 if not any(fields.values()):raise ValueError('At least one expected page fact is required')
 if any(len(x)>500 for x in fields.values()):raise ValueError('Expected page fact exceeds 500 characters')
 path,indexed_sha,member=source(lib,id)
 with open_confined(path) as stream:
  if os.fstat(stream.fileno()).st_size>250_000_000:raise ValueError('Original exceeds verification size bound')
  actual=hashlib.file_digest(stream,'sha256').hexdigest()
 if actual!=expected_sha256 or indexed_sha!=expected_sha256:raise ValueError('Original/index/expected SHA256 binding mismatch')
 text,mode,native_chars,ocr_chars=cached_original_page(str(path),physical_page,member.get('page_no'),actual)
 page_key=normalized(text);checks={}
 for name,value in fields.items():
  if value:checks[name]={'expected':value,'matched':normalized(value) in page_key}
 alignment=True
 if fields['parameter'] and fields['value']:
  pkey=normalized(fields['parameter']);vkey=normalized(fields['value']);alignment=False
  start=0
  while True:
   pos=page_key.find(pkey,start)
   if pos<0:break
   if vkey in page_key[max(0,pos-120):pos+len(pkey)+500]:alignment=True;break
   start=pos+len(pkey)
  checks['parameter_value_alignment']={'matched':alignment,'rule':'value occurs within the parameter context on the same physical page'}
 if fields['parameter'] and fields['value_max']:
  pkey=normalized(fields['parameter']);vkey=normalized(fields['value_max']);alignment=False;start=0
  while True:
   pos=page_key.find(pkey,start)
   if pos<0:break
   if vkey in page_key[max(0,pos-120):pos+len(pkey)+500]:alignment=True;break
   start=pos+len(pkey)
  checks['parameter_value_max_alignment']={'matched':alignment,'rule':'upper value occurs within the parameter context on the same physical page'}
 passed=all(x['matched'] for x in checks.values())
 # Return bounded original-page text for human review; never return the whole document.
 raw_anchor=next((v for v in (fields['parameter'],fields['quote'],fields['value']) if v and v in text),'')
 pos=text.find(raw_anchor) if raw_anchor else 0
 excerpt=' '.join(text[max(0,pos-250):pos+900].split())[:1200]
 with open_confined(path) as stream:after=hashlib.file_digest(stream,'sha256').hexdigest()
 if after!=actual:raise ValueError('Original changed during page verification')
 return {'library':lib,'source_id':id,'source_name':path.name,'source_sha256':actual,'physical_page':physical_page,
         'extraction_mode':mode,'native_text_characters':native_chars,'ocr_text_characters':ocr_chars,
         'page_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'excerpt':excerpt,'checks':checks,
         'verification_status':'verified_original_page' if passed else 'failed_expected_fact_check',
         'fact_use_allowed':passed,'usage':'eligible_as_page-bound_candidate_evidence' if passed else 'do_not_use_as_fact_or_compliance_evidence',
         'limitations':['Verification is limited to the selected physical page and supplied expected fields','It does not decide tender compliance, deviation, certificate validity, or cross-document applicability']}

@functools.lru_cache(maxsize=1)
def approved_mappings():
 try:return json.loads(APPROVED_MAPPINGS.read_text())
 except (OSError,ValueError):return {'model_groups':[],'parameter_groups':[]}

def approved_equivalence(name,left,right):
 collection='model_groups' if name=='vehicle_model' else 'parameter_groups' if name=='parameter' else None
 if not collection:return None
 a=normalized(left);b=normalized(right)
 for group in approved_mappings().get(collection,[]):
  values={normalized(x) for x in group.get('values',[]) if x}
  if a in values and b in values:return group.get('id')
 return None

def applicability_check(name,required,observed,contains=False):
 required=str(required or '').strip();observed=str(observed or '').strip()
 if not required:return {'dimension':name,'status':'not_required','required':'','observed':observed}
 if not observed:return {'dimension':name,'status':'missing','required':required,'observed':''}
 left=normalized(required);right=normalized(observed)
 matched=(left in right) if contains else left==right
 mapping_id=None if matched else approved_equivalence(name,required,observed)
 return {'dimension':name,'status':'pass' if matched else ('pass_approved_mapping' if mapping_id else 'mismatch'),'required':required,'observed':observed,
         'match_rule':'normalized_containment' if contains and matched else ('normalized_exact' if matched else ('approved_mapping' if mapping_id else 'none')),
         **({'mapping_id':mapping_id} if mapping_id else {})}

def validate_evidence_applicability(lib,id,expected_sha256,physical_page,requirement,evidence):
 allowed_req={'requirement_id','source_quote','object_type','vehicle_model','submodel','component','component_model','parameter','unit','test_condition'}
 allowed_evidence={'subject','object_type','vehicle_model','submodel','component','component_model','document_number','test_condition','parameter','unit','value','value_max','quote'}
 if not isinstance(requirement,dict) or set(requirement)-allowed_req:raise ValueError('Unsupported requirement applicability field')
 if not isinstance(evidence,dict) or set(evidence)-allowed_evidence:raise ValueError('Unsupported evidence applicability field')
 rid=str(requirement.get('requirement_id') or '').strip();quote=str(requirement.get('source_quote') or '').strip()
 if not rid or not quote:raise ValueError('requirement_id and verbatim source_quote required')
 parameter=str(requirement.get('parameter') or '').strip()
 if not parameter:raise ValueError('Requirement parameter required for applicability validation')
 if lib=='inspection_reports':
  if any(not str(x.get('object_type') or '').strip() for x in (requirement,evidence)):raise ValueError('Inspection-report applicability requires object_type on both sides')
  object_type=normalized(requirement.get('object_type'))
  if object_type=='VEHICLE' and any(not str(x.get('vehicle_model') or '').strip() for x in (requirement,evidence)):raise ValueError('Vehicle evidence requires vehicle_model on both sides')
  if object_type=='COMPONENT' and any(not str(x.get('component') or '').strip() for x in (requirement,evidence)):raise ValueError('Component evidence requires component on both sides')
 # The declared parameter must actually be anchored in the tender quotation;
 # otherwise a caller could relabel a requirement before comparing evidence.
 anchor=applicability_check('requirement_parameter_anchor',parameter,quote,contains=True)
 page_expected={'subject':evidence.get('subject',''),'model':evidence.get('vehicle_model',''),'submodel':evidence.get('submodel',''),
                'document_number':evidence.get('document_number',''),'test_condition':evidence.get('test_condition',''),
                'parameter':evidence.get('parameter',''),'value':evidence.get('value',''),'value_max':evidence.get('value_max',''),'quote':evidence.get('quote','')}
 page=verify_evidence_page(lib,id,expected_sha256,physical_page,page_expected)
 checks=[anchor,
         applicability_check('object_type',requirement.get('object_type'),evidence.get('object_type')),
         applicability_check('vehicle_model',requirement.get('vehicle_model'),evidence.get('vehicle_model')),
         applicability_check('submodel',requirement.get('submodel'),evidence.get('submodel')),
         applicability_check('component',requirement.get('component'),evidence.get('component')),
         applicability_check('component_model',requirement.get('component_model'),evidence.get('component_model')),
         applicability_check('parameter',parameter,evidence.get('parameter')),
         applicability_check('unit',requirement.get('unit'),evidence.get('unit')),
         applicability_check('test_condition',requirement.get('test_condition'),evidence.get('test_condition'),contains=True)]
 failures=[x for x in checks if x['status'] in ('missing','mismatch')]
 applicable=page['fact_use_allowed'] and not failures
 return {'requirement_id':rid,'source_quote':quote,'library':lib,'source_id':id,'source_sha256':page['source_sha256'],'physical_page':physical_page,
         'original_page_verification':{'verification_status':page['verification_status'],'fact_use_allowed':page['fact_use_allowed'],'checks':page['checks'],'page_text_sha256':page['page_text_sha256'],'excerpt':page['excerpt']},
         'applicability_checks':checks,'approved_mappings_used':[{'dimension':x['dimension'],'mapping_id':x['mapping_id']} for x in checks if x['status']=='pass_approved_mapping'],
         'applicability_status':'applicable' if applicable else 'not_applicable',
         'comparison_allowed':applicable,'compliance_or_deviation_conclusion_allowed':False,
         'reasons':[x['dimension']+':'+x['status'] for x in failures]+([] if page['fact_use_allowed'] else ['original_page_verification:failed']),
         'execution_action':'continue_ai_execution','next_gate':'structured value/comparator evaluation' if applicable else 'record no applicable evidence, leave manual response/deviation cells blank, and continue with the next requirement',
         'limitations':['Applicability does not compare the tender threshold with the observed value','No satisfaction or deviation conclusion is created']}

UNIT_DEFINITIONS={
 'KG':('mass',Decimal('1'),'kg'),'千克':('mass',Decimal('1'),'kg'),'公斤':('mass',Decimal('1'),'kg'),
 'T':('mass',Decimal('1000'),'kg'),'吨':('mass',Decimal('1000'),'kg'),'G':('mass',Decimal('0.001'),'kg'),'克':('mass',Decimal('0.001'),'kg'),
 'MM':('length',Decimal('0.001'),'m'),'毫米':('length',Decimal('0.001'),'m'),'CM':('length',Decimal('0.01'),'m'),'厘米':('length',Decimal('0.01'),'m'),'M':('length',Decimal('1'),'m'),'米':('length',Decimal('1'),'m'),
 'S':('time',Decimal('1'),'s'),'秒':('time',Decimal('1'),'s'),'MIN':('time',Decimal('60'),'s'),'分钟':('time',Decimal('60'),'s'),'H':('time',Decimal('3600'),'s'),'小时':('time',Decimal('3600'),'s'),
 'W':('power',Decimal('1'),'W'),'KW':('power',Decimal('1000'),'W'),'V':('voltage',Decimal('1'),'V'),'KV':('voltage',Decimal('1000'),'V'),
 'AH':('capacity',Decimal('1'),'Ah'),'MAH':('capacity',Decimal('0.001'),'Ah'),
 'KMH':('speed',Decimal('1'),'km/h'),'公里小时':('speed',Decimal('1'),'km/h'),'MS':('speed',Decimal('3.6'),'km/h'),'米秒':('speed',Decimal('3.6'),'km/h'),
 '%':('ratio',Decimal('1'),'%'),'百分比':('ratio',Decimal('1'),'%')}

def unit_definition(value):
 raw=str(value or '').strip()
 key='%' if raw=='%' else normalized(raw.replace('/',''))
 if not key:return None
 result=UNIT_DEFINITIONS.get(key)
 if not result:raise ValueError('Unsupported or ambiguous unit: '+str(value))
 return result

def decimal_value(value):
 text=unicodedata.normalize('NFKC',str(value or '')).strip().replace(',','')
 if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)',text):raise ValueError('A single explicit decimal value is required: '+str(value))
 try:return Decimal(text)
 except InvalidOperation:raise ValueError('Invalid decimal value')

def converted(value,unit):
 definition=unit_definition(unit)
 if not definition:raise ValueError('Unit required for numeric comparison')
 return decimal_value(value)*definition[1],definition

def compare_structured_value(comparison,evidence_value,evidence_unit='',evidence_max=''):
 if not isinstance(comparison,dict) or set(comparison)-{'operator','required_value','required_max','unit','enum_values'}:raise ValueError('Unsupported comparison field')
 operator=str(comparison.get('operator') or '')
 if operator in {'ge','le','gt','lt','eq','between','covers'}:
  observed,obs_unit=converted(evidence_value,evidence_unit);required,req_unit=converted(comparison.get('required_value'),comparison.get('unit'))
  if obs_unit[0]!=req_unit[0]:raise ValueError('Evidence and requirement units have different dimensions')
  details={'operator':operator,'required_value':str(comparison.get('required_value')),'required_unit':comparison.get('unit'),'observed_value':str(evidence_value),'observed_unit':evidence_unit,'base_unit':req_unit[2],'required_base':str(required),'observed_base':str(observed)}
  if operator=='ge':matched=observed>=required
  elif operator=='le':matched=observed<=required
  elif operator=='gt':matched=observed>required
  elif operator=='lt':matched=observed<required
  elif operator=='eq':matched=observed==required
  else:
   upper,upper_unit=converted(comparison.get('required_max'),comparison.get('unit'))
   if upper_unit[0]!=req_unit[0] or upper<required:raise ValueError('Invalid requirement interval')
   details['required_max']=str(comparison.get('required_max'));details['required_max_base']=str(upper)
   if operator=='between':matched=required<=observed<=upper
   else:
    observed_upper,observed_upper_unit=converted(evidence_max,evidence_unit)
    if observed_upper_unit[0]!=obs_unit[0] or observed_upper<observed:raise ValueError('Invalid evidence interval')
    details['observed_max']=str(evidence_max);details['observed_max_base']=str(observed_upper)
    matched=observed<=required and observed_upper>=upper
  return {'relation':'true' if matched else 'false','comparison_type':'numeric','details':details}
 if operator in {'enum_eq','enum_contains_all'}:
  expected=comparison.get('enum_values')
  if not isinstance(expected,list) or not expected or any(not str(x).strip() for x in expected):raise ValueError('enum_values must be a non-empty string array')
  observed=str(evidence_value or '').strip()
  if not observed:raise ValueError('Observed enum value required')
  if operator=='enum_eq':matched=len(expected)==1 and normalized(expected[0])==normalized(observed)
  else:matched=all(normalized(x) in normalized(observed) for x in expected)
  return {'relation':'true' if matched else 'false','comparison_type':'enum','details':{'operator':operator,'required_values':[str(x) for x in expected],'observed_value':observed}}
 raise ValueError('Unsupported comparison operator')

def evaluate_evidence_value_relation(lib,id,expected_sha256,physical_page,requirement,evidence,comparison):
 applicability=validate_evidence_applicability(lib,id,expected_sha256,physical_page,requirement,evidence)
 if not applicability['comparison_allowed']:
  return {'requirement_id':requirement.get('requirement_id'),'evaluation_status':'blocked_by_applicability','applicability':applicability,
          'mathematical_relation':None,'compliance_or_deviation_conclusion_allowed':False,'execution_action':'continue_ai_execution','next_gate':'record the applicability gap and continue with the next requirement'}
 # The exact threshold and unit must be visibly anchored in the verbatim tender
 # quotation before any arithmetic is attempted.
 raw_quote=str(requirement.get('source_quote') or '');quote=normalized(raw_quote)
 operator=str(comparison.get('operator') or '')
 if operator in {'enum_eq','enum_contains_all'}:
  enum_values=comparison.get('enum_values') or []
  anchors={'enum_values':bool(enum_values) and all(normalized(x) in quote for x in enum_values)}
  if not all(anchors.values()):
   return {'requirement_id':requirement.get('requirement_id'),'evaluation_status':'blocked_by_tender_anchor','applicability':applicability,'tender_anchor_checks':anchors,
           'mathematical_relation':None,'compliance_or_deviation_conclusion_allowed':False,'execution_action':'continue_ai_execution','next_gate':'record the tender-structure error and continue independent requirements'}
  relation=compare_structured_value(comparison,evidence.get('value'),evidence.get('unit'),evidence.get('value_max',''))
  return {'requirement_id':requirement.get('requirement_id'),'evaluation_status':'calculated','applicability':applicability,'tender_anchor_checks':anchors,
          'mathematical_relation':relation,'compliance_or_deviation_conclusion_allowed':False,
          'execution_action':'continue_ai_execution','next_gate':'record the mathematical relation in the internal evidence ledger and continue; do not write manual response/deviation cells',
          'limitations':['A true enum relation is not a tender-compliance conclusion','Document validity, completeness, promises and business selection remain separate']}
 required=str(comparison.get('required_value') or '')
 unit=str(comparison.get('unit') or '')
 unit_anchor=(('%' in raw_quote or '百分' in raw_quote) if unit=='%' else normalized(unit.replace('/','')) in quote)
 operator_patterns={'ge':('≥','>=','不小于','不少于','不低于'),'le':('≤','<=','不大于','不超过','不高于'),
                    'between':('～','~','至'),'covers':('～','~','至')}
 if operator=='eq':operator_anchor=not any(x in raw_quote for x in ('≥','≤','>','<','不小于','不大于','不低于','不高于','不少于','不超过'))
 elif operator=='gt':operator_anchor=('>' in raw_quote and '>=' not in raw_quote) or any(x in raw_quote for x in ('大于','高于'))
 elif operator=='lt':operator_anchor=('<' in raw_quote and '<=' not in raw_quote) or any(x in raw_quote for x in ('小于','低于'))
 else:operator_anchor=any(x in raw_quote for x in operator_patterns.get(operator,()))
 anchors={'required_value':bool(required and normalized(required) in quote),'unit':not unit or unit_anchor,'operator':operator_anchor}
 if operator in {'between','covers'}:
  anchors['required_max']=bool(str(comparison.get('required_max') or '') and normalized(comparison.get('required_max')) in quote)
 if not all(anchors.values()):
  return {'requirement_id':requirement.get('requirement_id'),'evaluation_status':'blocked_by_tender_anchor','applicability':applicability,'tender_anchor_checks':anchors,
          'mathematical_relation':None,'compliance_or_deviation_conclusion_allowed':False,'execution_action':'continue_ai_execution','next_gate':'record the tender-structure error and continue independent requirements'}
 relation=compare_structured_value(comparison,evidence.get('value'),evidence.get('unit'),evidence.get('value_max',''))
 return {'requirement_id':requirement.get('requirement_id'),'evaluation_status':'calculated','applicability':applicability,'tender_anchor_checks':anchors,
         'mathematical_relation':relation,'compliance_or_deviation_conclusion_allowed':False,
         'execution_action':'continue_ai_execution','next_gate':'record the mathematical relation in the internal evidence ledger and continue; do not write manual response/deviation cells',
         'limitations':['A true mathematical relation is not a tender-compliance conclusion','Document validity, completeness, promises and business selection remain separate']}

def conclude_locked_configuration_deviation(lib,id,expected_sha256,physical_page,requirement,evidence,comparison,locked_configuration):
 if not isinstance(locked_configuration,dict):raise ValueError('locked_configuration required')
 required={'selection_status','selected_vehicle_model','selection_source','source_index_sha256','requirement_text_sha256'}
 if not required.issubset(locked_configuration):raise ValueError('locked_configuration fields incomplete')
 if locked_configuration.get('selection_status')!='locked_for_project':raise ValueError('selected configuration is not locked for project')
 selected=str(locked_configuration.get('selected_vehicle_model') or '').strip()
 if not selected or normalized(selected)!=normalized(evidence.get('vehicle_model')):raise ValueError('locked selected model must exactly match evidence vehicle model')
 if not str(locked_configuration.get('selection_source') or '').strip():raise ValueError('selection_source required')
 for field in ('source_index_sha256','requirement_text_sha256'):
  if not re.fullmatch(r'[0-9a-f]{64}',str(locked_configuration.get(field) or '')):raise ValueError(field+' must be SHA256')
 requirement_text=str(requirement.get('source_quote') or '')
 actual_text_hash=hashlib.sha256(requirement_text.encode('utf-8')).hexdigest()
 if actual_text_hash!=locked_configuration['requirement_text_sha256']:raise ValueError('locked requirement text hash mismatch')
 evaluation=evaluate_evidence_value_relation(lib,id,expected_sha256,physical_page,requirement,evidence,comparison)
 relation=(evaluation.get('mathematical_relation') or {}).get('relation')
 if evaluation.get('evaluation_status')!='calculated' or relation not in ('true','false'):
  return {**evaluation,'locked_configuration':locked_configuration,'deviation_decision_status':'undetermined',
          'deviation_conclusion':None,'deviation_conclusion_allowed':False,'execution_action':'continue_ai_execution'}
 conclusion='负偏离'
 if relation=='true':
  details=evaluation['mathematical_relation'].get('details',{});operator=details.get('operator')
  conclusion='无偏离'
  if evaluation['mathematical_relation'].get('comparison_type')=='numeric':
   observed=Decimal(details['observed_base']);required=Decimal(details['required_base'])
   if (operator in ('ge','gt') and observed>required) or (operator in ('le','lt') and observed<required):conclusion='正偏离'
   elif operator=='covers':
    observed_max=Decimal(details['observed_max_base']);required_max=Decimal(details['required_max_base'])
    if observed<required or observed_max>required_max:conclusion='正偏离'
 return {**evaluation,'locked_configuration':locked_configuration,'deviation_decision_status':'concluded',
         'deviation_conclusion':conclusion,'deviation_conclusion_allowed':True,
         'decision_basis':'locked project model + locked verbatim tender requirement + verified applicable original page + deterministic structured comparison',
         'execution_action':'continue_ai_execution','next_gate':'write the machine deviation conclusion to the internal traceability ledger and continue'}

def batch_evaluate_evidence_relations(lib,items):
 if lib not in ('enterprise_facts','inspection_reports'):raise ValueError('Batch evaluation requires an evidence library')
 if not isinstance(items,list) or not 1<=len(items)<=20:raise ValueError('Batch requires 1..20 items')
 results=[]
 for position,item in enumerate(items,1):
  task_id=item.get('task_id') if isinstance(item,dict) else None
  if not isinstance(task_id,str) or not task_id.strip():task_id='item-'+str(position)
  try:
   if not isinstance(item,dict):raise ValueError('Item must be an object')
   mode=item.get('mode')
   if mode=='gap':
    reason=str(item.get('gap_reason') or '').strip()
    if not reason:raise ValueError('gap_reason required')
    result={'requirement_id':(item.get('requirement') or {}).get('requirement_id'),'evaluation_status':'no_applicable_evidence',
            'mathematical_relation':None,'gap_reason':reason,'compliance_or_deviation_conclusion_allowed':False,
            'execution_action':'continue_ai_execution','next_gate':'continue with the next requirement'}
   elif mode=='evaluate':
    locked=item.get('locked_configuration')
    if locked:
     result=conclude_locked_configuration_deviation(lib,item.get('id',''),item.get('expected_sha256',''),item.get('physical_page'),item.get('requirement'),item.get('evidence'),item.get('comparison'),locked)
    else:
     result=evaluate_evidence_value_relation(lib,item.get('id',''),item.get('expected_sha256',''),item.get('physical_page'),item.get('requirement'),item.get('evidence'),item.get('comparison'))
   else:raise ValueError('mode must be evaluate or gap')
   results.append({'task_id':task_id,'position':position,'item_status':'processed','result':result})
  except Exception as error:
   results.append({'task_id':task_id,'position':position,'item_status':'item_error','error_type':type(error).__name__,'error':str(error)[:500],
                   'execution_action':'record error and continue with the next requirement'})
 statuses=[x.get('result',{}).get('evaluation_status') for x in results]
 relations=[x.get('result',{}).get('mathematical_relation',{}).get('relation') for x in results if isinstance(x.get('result',{}).get('mathematical_relation'),dict)]
 deviations=[x.get('result',{}).get('deviation_conclusion') for x in results]
 summary={'items':len(items),'processed':sum(x['item_status']=='processed' for x in results),'item_errors':sum(x['item_status']=='item_error' for x in results),
          'calculated_true':relations.count('true'),'calculated_false':relations.count('false'),'no_applicable_evidence':statuses.count('no_applicable_evidence'),
          'blocked':sum(x in ('blocked_by_applicability','blocked_by_tender_anchor') for x in statuses),
          'deviation_conclusions':sum(x in ('正偏离','无偏离','负偏离') for x in deviations),'positive_deviations':deviations.count('正偏离'),'negative_deviations':deviations.count('负偏离')}
 return {'library':lib,'results':results,'summary':summary,'all_items_visited':len(results)==len(items),
         'execution_status':'completed_with_item_issues' if summary['item_errors'] or summary['blocked'] or summary['no_applicable_evidence'] else 'completed',
         'execution_action':'continue_ai_execution','manual_response_or_deviation_cells_written':False}

def verify_history(offset,count):
 from zipfile import ZipFile,is_zipfile
 from xml.etree import ElementTree as ET
 import subprocess
 if type(offset)is not int or offset<0 or type(count)is not int or not 1<=count<=5:raise ValueError('Offset>=0 and count1..5 required')
 rows=catalog('history');roots=json.loads((AUTH/'authority.meta.json').read_text())['source_roots'];results=[]
 for row in rows[offset:offset+count]:
  item={'source_id':row.get('source_id'),'source_path':row.get('source_path'),'format':row.get('suffix'),'readable':False}
  try:
   p=safe_source(str(Path(roots[row['domain']])/row['source_path']))
   with open_confined(p) as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
   item['sha256']=actual
   if actual!=row['sha256']:raise ValueError('Index/original hash mismatch')
   target=p;mode='original'
   if p.suffix.lower()=='.doc':
    candidates=[root/(actual+'.docx') for root in CONVERSION_ROOTS]
    target=next((x for x in candidates if x.is_file() and not x.is_symlink()),None)
    if target is None:raise ValueError('Legacy DOC has no hash-bound readable conversion')
    item['converted_sha256']=sha(target);mode='hash_bound_docx_conversion'
   suffix=target.suffix.lower()
   if suffix in ['.docx','.xlsx'] or is_zipfile(target):
    with ZipFile(target) as z:
     xmlpaths=['word/document.xml'] if 'word/document.xml' in z.namelist() else [n for n in z.namelist() if n=='xl/workbook.xml' or n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]
     if not xmlpaths:raise ValueError('Missing document/workbook XML')
     chars=0
     for name in xmlpaths:
      if z.getinfo(name).file_size>100_000_000:raise ValueError('Document XML exceeds readable bound')
      node=ET.fromstring(z.read(name));chars+=sum(len(x.text or '') for x in node.iter())
     item['xml_parts_read']=len(xmlpaths);item['text_characters']=chars
   elif suffix=='.pdf':
    result=subprocess.run(['pdfinfo',str(target)],capture_output=True,timeout=30)
    if result.returncode:raise ValueError('PDF structure reader failed')
    item['pdf_structure_readable']=True
   else:raise ValueError('Unsupported format')
   item['readable']=True;item['read_mode']=mode
  except Exception as e:item['error']=type(e).__name__+': '+str(e)[:300]
  results.append(item)
 return {'library':'history','total_files':len(rows),'offset':offset,'results':results,'next_offset':offset+count if offset+count<len(rows) else None,'scope':'Original full-byte SHA256 and document text/container readability; legacy DOC uses hash-bound conversion. No page-layout, factual validity, or embedded-image visual approval.'}

def handle(a):
 lib=a.get('library');op=a['operation']
 if lib not in ['knowledge','enterprise_facts','inspection_reports','history']:raise ValueError('Unknown library')
 if op=='model_standard':
  if lib!='history':raise ValueError('Model standards are in the history authority library')
  return search_model_history(a.get('model',''),a.get('parameter',''),a.get('aliases',[]),a.get('limit',10))
 if op=='model_standard_batch':
  if lib!='history':raise ValueError('Model standards are in the history authority library')
  queries=a.get('queries')
  if not isinstance(queries,list) or not 1<=len(queries)<=500:raise ValueError('Batch requires 1..500 queries')
  results=[]
  for query in queries:
   if not isinstance(query,dict) or not isinstance(query.get('requirement_id'),str):raise ValueError('Each batch query needs requirement_id')
   result=search_model_history(query.get('model',''),query.get('parameter',''),query.get('aliases',[]),query.get('limit',10))
   results.append({'requirement_id':query['requirement_id'],**result})
  return {'library':'history','results':results,'query_count':len(results),'index_sha256':sha(AUTH/'authority.pairs.json.gz'),'source_read_only':True}
 if op=='verify_readability':
  if lib!='history':raise ValueError('This verification covers the historical library')
  return verify_history(a['offset'],a['count'])
 if op=='evidence_search':
  return evidence_search(lib,a.get('query',''),a.get('subject',''),a.get('model',''),a.get('limit',10))
 if op=='verify_evidence_page':
  return verify_evidence_page(lib,a.get('id',''),a.get('expected_sha256',''),a.get('physical_page'),a.get('expected'))
 if op=='validate_evidence_applicability':
  return validate_evidence_applicability(lib,a.get('id',''),a.get('expected_sha256',''),a.get('physical_page'),a.get('requirement'),a.get('evidence'))
 if op=='evaluate_evidence_value_relation':
  return evaluate_evidence_value_relation(lib,a.get('id',''),a.get('expected_sha256',''),a.get('physical_page'),a.get('requirement'),a.get('evidence'),a.get('comparison'))
 if op=='batch_evaluate_evidence_relations':
  return batch_evaluate_evidence_relations(lib,a.get('items'))
 if op=='conclude_locked_configuration_deviation':
  return conclude_locked_configuration_deviation(lib,a.get('id',''),a.get('expected_sha256',''),a.get('physical_page'),a.get('requirement'),a.get('evidence'),a.get('comparison'),a.get('locked_configuration'))
 if op=='status':
  if lib in ('knowledge','enterprise_facts','inspection_reports'):
   d=load(CAT);rows=catalog(lib);return {'library':lib,'root':str(ROOT),'index_sha256':sha(CAT),'index_generated_at':d.get('generated_at'),'documents':len(rows),'active_documents':sum(bool(x.get('active_module')) for x in rows),'source_read_only':True,'health':knowledge_health(rows),'boundary':'legacy_combined_view' if lib=='knowledge' else ('enterprise certificates/finance/performance/facts; inspection reports excluded' if lib=='enterprise_facts' else 'inspection/test reports only; certificates and commercial facts excluded')}
  d=json.loads((AUTH/'authority.meta.json').read_text());p=load(AUTH/'authority.pairs.json.gz')['pairs'];return {'library':lib,'version':d['version'],'index_root':str(AUTH.resolve()),'files':len(catalog(lib)),'cases':len(load(AUTH/'authority.cases.json.gz')['cases']),'actual_response_pairs':len(p),'total_response_rows':d['summary'].get('total_response_pair_count',d['summary']['response_pair_count']),'excluded_response_rows':d['summary'].get('excluded_response_pair_count'),'declared_response_pairs':d['summary']['response_pair_count'],'count_consistent':len(p)==d['summary']['response_pair_count'],'index_sha256':sha(AUTH/'authority.pairs.json.gz'),'source_read_only':True}
 if op=='search':
  q=a['query'].strip().casefold();limit=a['limit']
  if not q or len(q)>200 or type(limit)is not int or not 1<=limit<=20:raise ValueError('Query/limit invalid')
  terms=q.split();out=[]
  if lib in ('knowledge','enterprise_facts','inspection_reports'):
   for d in catalog(lib):
    if not d.get('active_module'):continue
    s=d.get('search_text','')+' '+d.get('display_name','')
    if all(x in s.casefold() for x in terms):out.append({'id':d['id'],'title':d['display_name'],'category':d.get('category_code'),'model':d.get('product_model'),'members':len(d.get('members',[])),'snippet':s[:600]})
  else:
   files={d['source_id']:d for d in catalog(lib) if d.get('source_id')}
   for r in load(AUTH/'authority.pairs.json.gz')['pairs']:
    b=r['block'];f=files.get(r['source_id'],{});s=' '.join(str(x or '') for x in [f.get('project_name'),f.get('filename'),b.get('section_title'),b.get('requirement'),b.get('response')])
    if all(x in s.casefold() for x in terms):out.append({'id':b['block_id'],'source_id':r['source_id'],'case_id':r.get('case_id'),'project':f.get('project_name'),'title':b.get('section_title'),'requirement':b.get('requirement','')[:700],'response':b.get('response','')[:700],'excluded_from_reuse':b.get('excluded_from_reuse'),'sensitive_flags':b.get('sensitive_flags',[]),'source_ref':b.get('source_ref')})
   if not out:
    for f in files.values():
     s=json.dumps(f,ensure_ascii=False)
     if all(x in s.casefold() for x in terms):out.append({'id':f['source_id'],'source_id':f['source_id'],'title':f['filename'],'project':f.get('project_name'),'models':f.get('models',[])})
  return {'library':lib,'matches':len(out),'results':out[:limit],'truncated':len(out)>limit,'verification':'retrieval_only; use evidence_search for filtered hybrid retrieval; no result is a satisfaction or deviation conclusion'}
 if op=='read':
  id=a['id'];offset=a['offset'];count=a['count']
  if type(offset)is not int or offset<0 or type(count)is not int or not 1<=count<=10:raise ValueError('Invalid range')
  if lib in ('knowledge','enterprise_facts','inspection_reports'):
   for d in catalog(lib):
    if d['id']==id and d.get('active_module'):
     return {'library':lib,'id':id,'title':d['display_name'],'members':d.get('members',[])[offset:offset+count],'total_members':len(d.get('members',[])),'offset':offset}
  else:
   if id.startswith('har-'):
    for r in load(AUTH/'authority.pairs.json.gz')['pairs']:
     if r['block']['block_id']==id:return {'library':lib,'record':r}
   for f in load(AUTH/'authority.index.json.gz')['files']:
    if f.get('source_id')==id:
     blocks=f.get('blocks',[]);return {'library':lib,'source_id':id,'source_sha256':f.get('sha256'),'project':f.get('project_name'),'total_blocks':len(blocks),'offset':offset,'blocks':blocks[offset:offset+count]}
  raise ValueError('Record ID not found')
 if op in ['source','fetch']:
  p,indexed,record=source(lib,a['id']);suffix=p.suffix.lower()
  if suffix not in {'.pdf','.docx','.doc','.xlsx','.xls','.png','.jpg','.jpeg','.txt'}:raise ValueError('Unsupported original type')
  with open_confined(p) as f:
   st=os.fstat(f.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_size>250_000_000:raise ValueError('Source size/type not supported')
   actual=hashlib.file_digest(f,'sha256').hexdigest()
   if op=='source':return {'library':lib,'id':a['id'],'name':p.name,'suffix':suffix,'bytes':st.st_size,'source_rel':str(p.relative_to(ROOT)),'sha256':actual,'indexed_sha256':indexed,'index_hash_matches':indexed==actual}
   if actual!=a['expected_sha256']:raise ValueError('Original source version changed')
   f.seek(0)
   while chunk:=f.read(1024*1024):sys.stdout.buffer.write(chunk)
   return None
 raise ValueError('Unknown operation')
if __name__=='__main__':
 try:
  a=json.load(sys.stdin);r=handle(a)
  if r is not None:print(json.dumps(r,ensure_ascii=False))
 except Exception as e:
  print(type(e).__name__+': '+str(e),file=sys.stderr);sys.exit(1)
