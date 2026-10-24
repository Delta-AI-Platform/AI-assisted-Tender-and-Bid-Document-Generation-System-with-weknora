"""Reconcile interrupted writes using durable evidence, never blind replay."""
import hashlib
import json
import fcntl
from pathlib import Path


def recover(store, run_id, registry):
    lock_path=store.path.parent/(run_id+'.lock')
    with lock_path.open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Task still has a live executor') from None
        return reconcile_locked(store, run_id, registry)


def reconcile_locked(store, run_id, registry):
    """Caller must hold the task's exclusive OS lock throughout reconciliation."""
    if store.get(run_id)['status']!='needs_recovery': raise ValueError('Task is not awaiting recovery')
    with store.connect() as conn:
        calls=[dict(r) for r in conn.execute("SELECT * FROM calls WHERE run_id=? AND status='interrupted' AND side_effect=1",(run_id,))]
    results=[];unresolved=[]
    for call in calls:
        args=json.loads(call['args']);result=None
        if call['tool']=='write_text':
            path=registry.path(args['path'])
            if not path.exists():
                result={'ok':False,'recovered':True,'error':'Interrupted write left no output; inspect and retry if still needed'}
            elif path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==hashlib.sha256(args['content'].encode()).hexdigest():
                result={'ok':True,'recovered':True,'data':registry.artifact_info(args['path'])}
        elif call['tool']=='remember':
            with store.connect() as conn:
                rows=conn.execute('SELECT revision,value FROM memory WHERE run_id=? AND kind=? AND key=? AND created>=?',
                                  (run_id,args['kind'],args['key'],call['created'])).fetchall()
            expected={**args['value'],'verification':'unverified'}
            for row in rows:
                if json.loads(row['value'])==expected:
                    result={'ok':True,'recovered':True,'data':{'kind':args['kind'],'key':args['key'],'revision':row['revision'],'verification':'unverified'}}
        elif 'target' in args:
            path=registry.path(args['target'])
            if not path.exists():
                result={'ok':False,'recovered':True,'error':'Interrupted operation left no output; inspect before retry'}
            elif path.is_file():
                actual=registry.artifact_info(args['target'])
                with store.connect() as conn:
                    receipts=conn.execute("SELECT payload FROM events WHERE run_id=? AND kind='artifact_created' AND created>=?",(run_id,call['created'])).fetchall()
                for receipt in receipts:
                    data=json.loads(receipt['payload'])
                    if data.get('output')==args['target'] and data.get('operation')==call['tool'] and data.get('sha256')==actual['sha256']:
                        result={'ok':True,'recovered':True,'data':data}
        if result is None:
            unresolved.append({'call_id':call['id'],'tool':call['tool'],'reason':'Missing or conflicting completion evidence; output preserved for inspection'})
            continue
        store.end_call(run_id,call['id'],result)
        results.append({'call_id':call['id'],'tool':call['tool'],**result})
    with store.connect() as conn:
        store.event(conn,run_id,'recovery_audit',{'resolved':results,'unresolved':unresolved})
    if not unresolved:
        store.transition(run_id,'pending',{'recovery':'Evidence reconciled; previous observations remain in audit log','resolved':results})
    return {'resolved':results,'unresolved':unresolved,'status':store.get(run_id)['status']}
