"""Rebuild both connected indexes in staging. Never writes original documents."""
import collections,fcntl,gzip,hashlib,json,os,shutil,sys,threading,time,traceback
from pathlib import Path
from datetime import datetime,timezone
APP=Path('/opt/deta-bid/current').resolve()
sys.path.insert(0,str(APP/'backend'))
ROOT=Path('/mnt/ai_bid_knowledge')
BASE=Path('/srv/deta-bid/agent-library-refresh')
JOB=BASE/sys.argv[1]
JOB.mkdir(parents=True,exist_ok=True)
lock=(BASE/'refresh.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)

def record(phase,**kw):
 d={'phase':phase,'updated_at':datetime.now(timezone.utc).isoformat(),**kw};p=JOB/'status.json';t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.replace(t,p);print(json.dumps(d,ensure_ascii=False),flush=True)
def load(p):
 with gzip.open(p,'rt') as f:return json.load(f)
def save(p,d):
 with gzip.open(p,'wt',encoding='utf-8',compresslevel=6) as f:json.dump(d,f,ensure_ascii=False,separators=(',',':'))
def hashfile(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def atomic_copy(src,dst):
 t=dst.with_name('.'+dst.name+'.refresh-next');shutil.copy2(src,t);os.replace(t,dst)
def main():
 if shutil.disk_usage(BASE).free<1_500_000_000:raise RuntimeError('Insufficient staging disk space')
 record('inventory')
 inventory={}
 for p in ROOT.rglob('*'):
  if p.is_symlink():raise RuntimeError('Source symlink requires explicit review: '+str(p))
  if p.is_file():
   st=p.stat();inventory[str(p.relative_to(ROOT))]=[st.st_size,st.st_mtime_ns]
 (JOB/'source-inventory.json').write_text(json.dumps(inventory,ensure_ascii=False))
 manifest=load(Path('/srv/deta-bid/knowledge-index/rename_manifest.json.gz'))
 for row in manifest['records']:row['expected_sha256']=''
 save(JOB/'manifest-full-hash.json.gz',manifest)
 os.environ['ENTERPRISE_KNOWLEDGE_ROOT']=str(ROOT)
 os.environ['HISTORICAL_BID_AUTHORITY_DIR']=str(JOB/'authority')
 from service_modules import enterprise_knowledge as knowledge
 from service_modules import historical_authority as history
 count=0;guard=threading.Lock();original_hash=knowledge._sha256_file
 def counted(p):
  nonlocal count
  rel=str(p.relative_to(ROOT));before=p.stat();value=original_hash(p);after=p.stat()
  if [before.st_size,before.st_mtime_ns]!=inventory[rel] or [after.st_size,after.st_mtime_ns]!=inventory[rel]:raise RuntimeError('Original changed while hashing: '+rel)
  with guard:
   count+=1
   if count%200==0:record('knowledge_hashing',hashed=count,total_files=len(inventory))
  return value
 knowledge._sha256_file=counted
 knowledge.build_index(ROOT,JOB/'manifest-full-hash.json.gz',JOB/'knowledge',hash_workers=4)
 k=load(JOB/'knowledge/catalog.json.gz');members=[m for d in k['documents'] for m in d['members']]
 actual={m['source_rel']:m['sha256'] for m in members}
 if set(actual)!=set(inventory):raise RuntimeError('Knowledge catalog does not cover full inventory')
 if k.get('scan_errors') or any(not v for v in actual.values()):raise RuntimeError('Knowledge scan/hash errors; not activating')
 if count!=len(inventory):raise RuntimeError('Not every source was freshly hashed')
 record('history_parsing',total_files=len(history._source_inventory()),knowledge_files=count)
 cache=JOB/'authority/conversion-cache';cache.mkdir(parents=True,exist_ok=True)
 oldcache=Path('/srv/deta-bid/historical-bid-authority/conversion-cache')
 if oldcache.exists():
  for p in oldcache.glob('*.docx'):
   if p.is_file() and not p.is_symlink():os.link(p,cache/p.name)
 def current_hash(p):
  rel=str(p.relative_to(ROOT));st=p.stat()
  if [st.st_size,st.st_mtime_ns]!=inventory.get(rel):raise RuntimeError('Original changed before history parsing: '+rel)
  return actual[rel]
 history._sha256_file=current_hash
 history.rebuild_index(incremental=False,progress_callback=lambda done,total,msg:record('history_parsing',done=done,total=total) if done%5==0 or done==total else None)
 version=(JOB/'authority/current').resolve();idx=load(version/'authority.index.json.gz');pairs=load(version/'authority.pairs.json.gz');cases=load(version/'authority.cases.json.gz');meta=json.loads((version/'authority.meta.json').read_text())
 # The old summary counted all response rows, whereas pairs excludes non-reusable rows.
 total=idx['summary']['response_pair_count'];eligible=len(pairs['pairs'])
 for data in (idx,meta):data['summary'].update({'total_response_pair_count':total,'response_pair_count':eligible,'excluded_response_pair_count':total-eligible});data['full_refresh']={'freshly_hashed_originals':count,'incremental':False,'source_inventory':str(JOB/'source-inventory.json')}
 save(version/'authority.index.json.gz',idx);(version/'authority.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
 if len(cases['files'])!=idx['summary']['source_file_count']:raise RuntimeError('Authority inventory count mismatch')
 ids={f.get('source_id') for f in cases['files'] if f.get('source_id')}
 if any(p['source_id'] not in ids for p in pairs['pairs']):raise RuntimeError('Orphan response references')
 failures=[{k:f.get(k) for k in ['source_path','status','error']} for f in idx['files'] if f.get('status')=='unreadable']
 (JOB/'unreadable-history-files.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2))
 # Preserve the user's removal policy across later full refreshes.
 policy_file=Path('/srv/deta-bid/historical-bid-authority/current/excluded-unreadable-records.json')
 excluded=[]
 if policy_file.exists():
  prior=json.loads(policy_file.read_text());excluded_paths={x.get('absolute_source_path') for x in prior}
  excluded=[x for x in idx['files'] if x.get('status')=='unreadable' and x.get('absolute_source_path') in excluded_paths]
  if excluded:
   idx['files']=[x for x in idx['files'] if x not in excluded]
   removed_keys={(x.get('domain'),x.get('source_path')) for x in excluded}
   cases['files']=[x for x in cases['files'] if (x.get('domain'),x.get('source_path')) not in removed_keys]
   for data in (idx,meta):
    data['summary']['source_inventory_count']=data['summary']['source_file_count']
    data['summary']['source_file_count']=len(idx['files'])
    data['summary']['unreadable_file_count']=sum(x.get('status')=='unreadable' for x in idx['files'])
    data['summary']['excluded_unreadable_file_count']=len(excluded)
    data['summary']['source_size_bytes']=sum(int(x.get('size',0)) for x in idx['files'])
    data['fingerprint']=history._digest([[x.get('absolute_source_path'),x.get('sha256'),x.get('status')] for x in idx['files']])
    data['exclusion_policy']={'reason':'Preserve user removal of parse-failed records; readable repaired files may be re-indexed','original_files_deleted':False,'excluded_records':'excluded-unreadable-records.json'}
   save(version/'authority.index.json.gz',idx);save(version/'authority.cases.json.gz',cases)
   (version/'authority.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
   (version/'excluded-unreadable-records.json').write_text(json.dumps(excluded,ensure_ascii=False,indent=2))
 final={}
 for p in ROOT.rglob('*'):
  if p.is_symlink():raise RuntimeError('Source symlink appeared during refresh')
  if p.is_file():
   st=p.stat();final[str(p.relative_to(ROOT))]=[st.st_size,st.st_mtime_ns]
 if final!=inventory:raise RuntimeError('Source inventory changed during refresh; retain old active indexes')
 old=load(Path('/srv/deta-bid/knowledge-index/catalog.json.gz'));oldfiles={m['source_rel']:m.get('sha256') for d in old['documents'] for m in d['members']}
 report={'freshly_hashed_files':count,'knowledge_documents':len(k['documents']),'knowledge_active_documents':sum(bool(d.get('active_module')) for d in k['documents']),'added':len(set(actual)-set(oldfiles)),'removed':len(set(oldfiles)-set(actual)),'changed_hash':sum(actual[x]!=oldfiles[x] for x in set(actual)&set(oldfiles)),'history':meta['summary'],'history_version':meta['version'],'failures_report':str(JOB/'unreadable-history-files.json'),'source_unchanged':True,'model_calls':0}
 (JOB/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 # Retain known unreadable files explicitly, but do not accept newly unreadable sources silently.
 oldidx=load(Path('/srv/deta-bid/historical-bid-authority/current/authority.index.json.gz'))
 oldfail={f.get('absolute_source_path') for f in oldidx['files'] if f.get('status')=='unreadable'}
 newfail={f.get('absolute_source_path') for f in idx['files'] if f.get('status')=='unreadable'}
 if newfail-oldfail:record('needs_review_new_parse_failures',**report);return
 record('validated_ready',**report)
 backup=JOB/'previous';backup.mkdir(exist_ok=True)
 live=Path('/srv/deta-bid/knowledge-index');authlink=Path('/srv/deta-bid/historical-bid-authority/current');oldtarget=os.readlink(authlink)
 (backup/'authority-link.txt').write_text(oldtarget)
 for name in ['catalog.json.gz','catalog.meta.json']:shutil.copy2(live/name,backup/name)
 try:
  for name in ['catalog.json.gz','catalog.meta.json']:atomic_copy(JOB/'knowledge'/name,live/name)
  nxt=authlink.with_name('.current.agent-refresh');nxt.symlink_to(version);os.replace(nxt,authlink)
 except BaseException:
  for name in ['catalog.json.gz','catalog.meta.json']:atomic_copy(backup/name,live/name)
  raise
 record('complete',**report)
try:main()
except BaseException as e:
 record('failed',error=type(e).__name__+': '+str(e));traceback.print_exc();sys.exit(1)
