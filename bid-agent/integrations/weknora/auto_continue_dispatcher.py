"""Generic durable dispatcher for website-requested project continuations."""
import os
import sqlite3
import subprocess
import time
from pathlib import Path

HERE=Path(__file__).resolve().parent
STATE=HERE.parents[1]/'runs/native-projects.sqlite'
PYTHON=Path(os.environ.get('DETA_AUTO_CONTINUE_PYTHON',str(HERE.parents[2]/'weknora-mcp-env/bin/python')))
WORKER=HERE/'auto_continue.py'
PROJECTS=HERE.parents[1]/'runs/native-projects'

def connect():
 c=sqlite3.connect(STATE,timeout=30);c.row_factory=sqlite3.Row
 c.execute('''CREATE TABLE IF NOT EXISTS continuation_runs(project TEXT PRIMARY KEY,tenant INTEGER,session TEXT,owner TEXT,status TEXT,requested REAL,heartbeat REAL,pid INTEGER,last_error TEXT)''')
 return c

def process_alive(pid):
 if not pid:return False
 try:os.kill(int(pid),0);return True
 except (ProcessLookupError,ValueError,TypeError):return False
 except PermissionError:return True

def recover_orphaned_runs(c):
 """A dispatcher restart must not strand durable work in running/launching."""
 for row in c.execute("select project,pid from continuation_runs where status in ('running','launching')").fetchall():
  if not process_alive(row['pid']):
   c.execute('update continuation_runs set status=?,heartbeat=?,pid=null,last_error=? where project=?',
             ('requested',time.time(),'Recovered after continuation worker exited or dispatcher restarted',row['project']))

def main():
 children={}
 with connect() as c:recover_orphaned_runs(c)
 while True:
  with connect() as c:
   for project,proc in list(children.items()):
    code=proc.poll()
    if code is None:
     c.execute('update continuation_runs set status=?,heartbeat=?,pid=? where project=?',('running',time.time(),proc.pid,project));continue
    c.execute('update continuation_runs set status=?,heartbeat=?,pid=null,last_error=? where project=?',('completed' if code==0 else 'failed',time.time(),'' if code==0 else 'auto_continue exit '+str(code),project));children.pop(project,None)
   if not children:
    row=c.execute("select * from continuation_runs where status='requested' order by requested limit 1").fetchone()
    if row:
     root=PROJECTS/row['project'];log=root/'analysis/auto-continue-dispatch.log';log.parent.mkdir(parents=True,exist_ok=True)
     stream=log.open('ab',buffering=0)
     proc=subprocess.Popen([str(PYTHON),str(WORKER),'--session',row['session'],'--project',row['project']],cwd=str(HERE.parents[1]),stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
     children[row['project']]=proc;c.execute('update continuation_runs set status=?,heartbeat=?,pid=?,last_error=? where project=?',('launching',time.time(),proc.pid,'',row['project']))
  time.sleep(5)

if __name__=='__main__':main()
