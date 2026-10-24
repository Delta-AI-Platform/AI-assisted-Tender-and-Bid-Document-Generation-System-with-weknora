"""Bounded, durable continuation of one authorized native project session."""
import argparse,base64,datetime,fcntl,hashlib,hmac,json,sqlite3,time,uuid
from pathlib import Path
import httpx

ROOT=Path('/home/ubuntu/codex-workspace')
AUTH_DB=ROOT/'weknora-build-tools/data/weknora.db'
STATE_DB=ROOT/'bid-agent/runs/native-projects.sqlite'
CONFIG=ROOT/'weknora-build-tools/evaluation-env.json'
AGENT='5546cce7-872d-483a-a260-893339fbe05d'
PREFIX='【系统自动续跑】'

def database(path):
 c=sqlite3.connect(path,timeout=30);c.row_factory=sqlite3.Row;return c

def current(sid,project):
 with database(STATE_DB) as c:
  b=c.execute('select * from bindings where session=? and project=?',(sid,project)).fetchone()
 if not b:raise RuntimeError('Project/session binding changed')
 with database(AUTH_DB) as c:
  session=c.execute('select tenant_id,user_id,agent_id,agent_config,deleted_at from sessions where id=?',(sid,)).fetchone()
  latest=c.execute('select id,role,content,is_completed,created_at from messages where session_id=? order by julianday(created_at) desc,rowid desc limit 1',(sid,)).fetchone()
 if not session or session['deleted_at'] or session['tenant_id']!=b['tenant'] or (session['agent_id'] or json.loads(session['agent_config'] or '{}').get('agent_id'))!=AGENT:raise RuntimeError('Session ownership changed')
 return dict(b),dict(session),dict(latest) if latest else None

def stages(project):
 # Use the same policy/hash and artifact-integrity view exposed by
 # resume_project. Reading receipt files alone can continue past a stage that
 # became stale after a policy or upstream artifact change.
 from project_stages import status
 rows=status(ROOT/'bid-agent/runs/native-projects'/project);result=[]
 for row in rows:
  if row.get('status')!='passed':break
  result.append(int(row['stage']))
 return result

def ensure_queue(project):
 from project_stages import REQUIRED
 with database(STATE_DB) as c:
  c.execute('''CREATE TABLE IF NOT EXISTS continuation_tasks(project TEXT,stage INTEGER,task_key TEXT,status TEXT,attempts INTEGER DEFAULT 0,heartbeat REAL,last_error TEXT,PRIMARY KEY(project,stage,task_key))''')
  for stage,kinds in REQUIRED.items():
   c.execute('insert or ignore into continuation_tasks(project,stage,task_key,status,heartbeat,last_error) values(?,?,?,?,?,?)',(project,stage,'stage:'+','.join(kinds),'pending',time.time(),''))

def queue_state(project,stage,status,error=''):
 with database(STATE_DB) as c:
  c.execute('update continuation_tasks set status=?,attempts=attempts+?,heartbeat=?,last_error=? where project=? and stage=?',(status,1 if status=='running' else 0,time.time(),error[:500],project,stage))

def file_signature(project):
 root=ROOT/'bid-agent/runs/native-projects'/project
 records=[]
 for folder in ('analysis','outputs','stage_receipts'):
  for p in (root/folder).rglob('*'):
   if p.is_file() and p.name not in ('auto-continue-status.json','latest-delivery-audit.json') and not p.name.endswith('.history.json'):
    records.append((str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns))
 return hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()

def jwt(cfg,uid,tenant_id):
 def b64(v):return base64.urlsafe_b64encode(json.dumps(v,separators=(',',':')).encode()).rstrip(b'=')
 now=int(time.time());header=b64({'alg':'HS256','typ':'JWT'});payload=b64({'user_id':uid,'email':'admin@example.invalid','tenant_id':tenant_id,'iat':now,'exp':now+3600,'type':'access'})
 signed=header+b'.'+payload
 return (signed+b'.'+base64.urlsafe_b64encode(hmac.new(cfg['JWT_SECRET'].encode(),signed,hashlib.sha256).digest()).rstrip(b'=')).decode()

def one_turn(sid,uid,tenant_id,stage):
 from project_stages import REQUIRED
 from workflow_policy import policy_prompt
 cfg=json.loads(CONFIG.read_text());token=jwt(cfg,uid,tenant_id);ident=str(uuid.uuid4())
 expiry=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).isoformat()
 with database(AUTH_DB) as c:c.execute('insert into auth_tokens(id,user_id,token,token_type,expires_at) values(?,?,?,?,?)',(ident,uid,token,'access_token',expiry))
 query=(f'{PREFIX}用户已授权在当前真实项目中完成Agent范围内十步自动续跑。首先调用resume_project；当前最早未登记为第{stage}步。'
        f'本步record_project_stage必须登记的产物种类为：{json.dumps(REQUIRED[stage],ensure_ascii=False)}。'
        '发现MCP工具时limit不得超过50；目录cursor失效时立即无cursor重新列举，不得反复提交旧cursor。'
        '只读取resume_project返回的当前项目source_sha256与source_path；若会话历史附件哈希不一致必须忽略。需要原件字节时先调用stage_project_file导出source_path，再读取sandbox_output_dir下对应文件。'
        '读取tender-bid-authoring与bid-model-response-matrix。三库已批准供AI使用，精确对象直接使用；缺失、冲突或单项错误记录后继续。'
        '招标原文逐字、按来源顺序；条款机器门禁通过后自动继续。八列表第7、8列为空，正式技术响应及偏离填写区保留原表并为空。'
        '第2步source_extraction必须调用extract_project_source生成。原件不超过40页时可直接调用build_project_tender_analysis；超过40页时，如resume_project返回pending_tender_analysis_payloads，直接把其中项目相对path与SHA-256传给首屏工具analyze_tender_payload，禁止用shell、read_file或read_json_value重读其内容；否则把完整JSON写入当前项目sandbox output目录并计算SHA-256后调用该工具。服务器按每批不超过20页校验、持久化并合并；也可逐批保存并最终合并。禁止把整份大数组塞入工具参数。八类分析的每条引文逐字锚定物理页，page_reviews逐页覆盖全部原件，禁止抽样；单原件哈希由服务器补全，连续无可执行要求页可用end_physical_page压缩，含要求页仍须逐页绑定同页条目。'
        '第2步正式tender_analysis生成后，必须优先调用首屏工具a_finalize_stage2_baseline；服务器直接推导technical物理页并一次生成technical_source_index、requirement_atomization和atomization_review，禁止回读page_reviews、手工整理页码或手抄原文。仅在该复合工具明确失败时，才分别调用index_project_technical_pages、build_requirement_atomization_baseline与render_requirement_atomization_review。'
        '技术来源索引页必须与tender_analysis分类为technical的主文件物理页完全一致；禁止把联系方式、格式页或相邻非技术页带入条款原子化。'
        '第3步优先调用首屏工具a_build_project_skeleton_draft（兼容名build_project_skeleton_draft），由服务器从tender_analysis直接生成真实骨架DOCX、内部PDF和五项锁定产物，禁止逐页重读整份原件；已有明确客户模板时才制作DOCX/PDF后调用build_dynamic_skeleton_artifacts。不得自行编写骨架锁。'
        '第4步优先调用首屏工具a_build_stage4_baseline，由服务器从tender_analysis和skeleton_bundle直接生成项目事实、资格账本、已填骨架DOCX和证据清单；能由原件证明的项目名称/编号必须填写，企业原件不足项记missing后继续。'
        '第5步优先调用首屏工具a_build_stage5_baseline，由服务器从tender_analysis和第4步filled_docx直接生成评分账本、承诺账本、商务评分DOCX和装配清单；主观分不自授、价格未批准不计算、未批准承诺不进正文。'
        '第6步如当前项目没有业务批准的拟供车型和成套报告，优先调用首屏工具a_build_stage6_gap_baseline，由服务器从原子化条款生成明确缺口的选型锁、全量任务队列、批次结果和技术证据账本；不猜车型也不中断。'
        '第7步如第6步为无选型gap账本，优先调用首屏工具a_build_stage7_gap_baseline，由服务器从同一原子化对象生成八列DOCX、XLSX和逐行追溯JSON；不跨车型套用历史答案，第5、7、8列保持空白。'
        '第8步优先调用首屏工具a_build_stage8_baseline，由服务器从第4、6、7步真实文件重新计算跨文件一致性和五类内容门禁；问题披露不影响Agent继续。'
        '第9步优先调用首屏工具a_build_stage9_baseline，完成候选标书DOCX、内部PDF和三项网站产物的原子发布；发布后网站outputs必须恰好三项。'
        '第10步直接优先调用a_complete_stage10_visual_review；服务器按当前PDF哈希自动复用或生成哈希隔离的全部page_image，并发补齐逐页inspect_page_image观察，再生成page_review与delivery_decision。禁止先向固定文件名批量page_image，禁止删除旧页图；复合工具明确失败时才使用新版本目录逐页处理。不得自行编写通过回执。'
        '第9步网站outputs只允许完整标书DOCX、八列表DOCX、八列表XLSX；内部渲染PDF及全部过程产物写analysis。'
        '第9步a_build_stage9_baseline已原子发布三项文件并返回output_manifest，禁止再次调用publish_final_website_outputs；只有复合工具明确失败且采用低层回退时才单独调用发布工具。不得自行把过程文件复制到outputs。'
        '按当前项目动态数据制作，禁止旧项目名、固定车型、固定条款数、固定附件数、固定页数或固定文件名。'
        '完成本步除stage_validation外的真实文件后，必须先调用create_stage_validation；把其返回path作为stage_validation加入artifacts，再调用record_project_stage。'
        '不得自行编写stage_validation JSON。record_project_stage成功后立即用一句话结束本轮，不再调用save_project_checkpoint、audit或下一步工具；续跑器会开启下一步。缺失人员、报价、业绩、签章只写open_items，不中断其他可独立工作。'
        '不要要求用户回复继续。本轮末保存检查点；十步结束最高状态agent_scope_complete，不得称可递交。'+policy_prompt())
 try:
  deadline=time.time()+1800
  with httpx.Client(trust_env=False,timeout=httpx.Timeout(2400,connect=20)) as c:
   req={'query':query,'agent_enabled':True,'agent_id':AGENT,'disable_title':True,'channel':'web'}
   while True:
    with c.stream('POST','http://127.0.0.1:18765/api/v1/agent-chat/'+sid,headers={'Authorization':'Bearer '+token,'X-Tenant-ID':str(tenant_id)},json=req) as r:
     if r.status_code==409 and time.time()<deadline:
      time.sleep(10);continue
     r.raise_for_status()
     for _ in r.iter_lines():pass
     break
 finally:
  with database(AUTH_DB) as c:c.execute('delete from auth_tokens where id=?',(ident,))

def write_status(path,record):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2));temp.replace(path)

def wait_for_idle(sid,project,seconds=1200):
 deadline=time.time()+seconds
 while True:
  b,s,latest=current(sid,project)
  if latest is None or latest['is_completed']:return b,s,latest
  if time.time()>deadline:raise RuntimeError('Native turn did not complete before timeout')
  time.sleep(5)

def rotate_session(sid,project):
 """Start the next stage with an empty model context while preserving ownership.

 A stage can legitimately inspect hundreds of source items. Reusing that turn's
 message history makes later stages slow and encourages the model to reuse stale
 arrays. The durable project ledgers, rather than chat history, are the handoff.
 """
 new=str(uuid.uuid4());now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
 with database(AUTH_DB) as c:
  row=c.execute('select * from sessions where id=?',(sid,)).fetchone()
  if not row:raise RuntimeError('Cannot rotate a missing session')
  data=dict(row);data.update(id=new,parent_session_id='',forked_from_message_id=None,fork_bootstrap=None,created_at=now,updated_at=now,deleted_at=None)
  cols=list(data);c.execute('insert into sessions('+','.join(cols)+') values('+','.join('?' for _ in cols)+')',[data[x] for x in cols])
  assignment=c.execute('select * from session_sandbox_assignments where session_id=?',(sid,)).fetchone()
  if assignment:
   assignment=dict(assignment);assignment.update(session_id=new,released_at=None,cleanup_due_at=None)
   cols=list(assignment);c.execute('insert or replace into session_sandbox_assignments('+','.join(cols)+') values('+','.join('?' for _ in cols)+')',[assignment[x] for x in cols])
 with database(STATE_DB) as c:
  binding=c.execute('select * from bindings where session=? and project=?',(sid,project)).fetchone()
  if not binding:raise RuntimeError('Cannot rotate an unbound project session')
  data=dict(binding);data['session']=new;data['updated']=time.time();cols=list(data)
  c.execute('insert or replace into bindings('+','.join(cols)+') values('+','.join('?' for _ in cols)+')',[data[x] for x in cols])
  c.execute('update continuation_runs set session=?,heartbeat=? where project=?',(new,time.time(),project))
 binding_file=ROOT/'bid-agent/runs/native-projects'/project/'binding.json'
 if binding_file.is_file():
  payload=json.loads(binding_file.read_text());payload['session_id']=new
  temp=binding_file.with_suffix('.tmp');temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2));temp.replace(binding_file)
 return new

def main():
 p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--project',required=True);p.add_argument('--max-turns',type=int,default=0,help='0 means continue until completion, user steering, repeated stall, or error');args=p.parse_args()
 root=ROOT/'bid-agent/runs/native-projects'/args.project
 if not root.is_dir():raise RuntimeError('Project not found')
 lock=(root/'auto-continue.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 status=root/'analysis/auto-continue-status.json'
 ensure_queue(args.project)
 state={'session':args.session,'project':args.project,'status':'running','started':time.time(),'turns':0,'stage_completed':stages(args.project),'reason':''}
 write_status(status,state)
 b,s,latest=wait_for_idle(args.session,args.project)
 baseline=latest['id'] if latest else None;user_id=s['user_id'];tenant_id=s['tenant_id'];stalled=0
 try:
  turn=0
  while args.max_turns<=0 or turn<args.max_turns:
   b,s,latest=wait_for_idle(args.session,args.project)
   if s['user_id']!=user_id:raise RuntimeError('Owner changed')
   if latest is not None and not latest['is_completed']:raise RuntimeError('Another turn is running')
   # Never supersede a new human instruction received while the worker runs.
   with database(AUTH_DB) as c:
    if baseline:
     rows=c.execute('select id,content from messages where session_id=? and role=? and julianday(created_at)>(select julianday(created_at) from messages where id=?)',(args.session,'user',baseline)).fetchall()
    else:rows=[]
   if any(not row['content'].startswith(PREFIX) for row in rows):
    state.update(status='paused_for_user',reason='A newer human message was received');break
   completed=stages(args.project)
   if len(completed)==10:
    state.update(status='review_pending',reason='All ten stage candidates recorded; human quality and business approvals remain');break
   next_stage=len(completed)+1
   before=file_signature(args.project)
   turn+=1
   state.update(turns=turn,current_stage=next_stage,stage_completed=completed,heartbeat=time.time());write_status(status,state)
   queue_state(args.project,next_stage,'running')
   try:one_turn(args.session,user_id,tenant_id,next_stage)
   except Exception as exc:
    queue_state(args.project,next_stage,'retry',str(exc));raise
   after=file_signature(args.project);new_completed=stages(args.project)
   queue_state(args.project,next_stage,'completed' if next_stage in new_completed else 'pending')
   state.update(stage_completed=new_completed,heartbeat=time.time());write_status(status,state)
   stalled=0 if after!=before or len(new_completed)>len(completed) else stalled+1
   if next_stage in new_completed and len(new_completed)<10:
    args.session=rotate_session(args.session,args.project);state['session']=args.session;baseline=None
    write_status(status,state)
   if stalled>=3:
    state.update(status='stalled',reason='Three completed turns produced no checkpoint, artifact, or stage progress');break
  if args.max_turns>0 and turn>=args.max_turns and state.get('status')=='running':state.update(status='turn_limit',reason='Operator-supplied turn limit reached; project can resume from checkpoint')
 except Exception as exc:
  state.update(status='error',reason=str(exc)[:500]);raise
 finally:
  state['ended']=time.time();write_status(status,state)

if __name__=='__main__':main()
