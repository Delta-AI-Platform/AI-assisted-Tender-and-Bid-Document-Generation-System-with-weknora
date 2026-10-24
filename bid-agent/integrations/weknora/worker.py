"""Project-bound tool dispatcher. Run with the existing bid tool interpreter."""
import contextlib
import fcntl
import hashlib
import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from bid_agent.store import Store
from bid_agent.tools import ProjectTools, validate_args


def dispatch(state, run_id, request):
    if request['method'] == 'runtime':
        from project_gateway import runtime
        return runtime(request)
    if request['method'] == 'call' and not request.get('scoped'):
        from project_gateway import route
        return route(state, run_id, request)
    store = Store(Path(state))
    run = store.get(run_id)
    scope = run['scope']
    registry = ProjectTools(scope['project'], store, run_id, scope.get('skills'))
    if request['method'] == 'list':
        from project_gateway import catalog
        # Keep the stage composites on the first discovery page.  They are the
        # stable public workflow entry points; forcing the model to paginate
        # through 100+ low-level tools made long runs depend on fragile cursors.
        tools=list(registry.tools.values())
        tools.sort(key=lambda t:(0 if t.name.startswith('a_') else 1))
        return {'tools': catalog() + [{'name': t.name, 'description': t.description,
                           'inputSchema': t.parameters,
                           'annotations': {'readOnlyHint': not t.side_effect}}
                          for t in tools]}
    if request['method'] != 'call':
        raise ValueError('Unknown method')
    tool = registry.tools[request['name']]
    args = request['arguments']
    validate_args(tool, args)
    # Use the same task lock as the native executor. A separate project lock
    # serializes bridge calls across tasks; all existing native project runs are
    # also locked, so a live native task cannot overlap an external operation.
    project_id = hashlib.sha256(str(registry.root).encode()).hexdigest()
    with contextlib.ExitStack() as stack:
        project_lock = stack.enter_context((store.path.parent / (project_id + '.mcp.lock')).open('a'))
        fcntl.flock(project_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.connect() as conn:
            related = sorted(r['id'] for r in conn.execute('SELECT id,scope FROM runs')
                             if json.loads(r['scope']).get('project') == scope['project'])
        for task_id in related:
            handle = stack.enter_context((store.path.parent / (task_id + '.lock')).open('a'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Project has a live executor; retry after it yields') from None
        if store.get(run_id)['status'] not in {'pending', 'yielded', 'running', 'needs_recovery'}:
            raise ValueError('Task is not available for external tool calls')
        with store.connect() as conn:
            uncertain = conn.execute("SELECT id FROM calls WHERE run_id=? AND side_effect=1 AND status IN ('running','interrupted')", (run_id,)).fetchall()
        if uncertain and tool.side_effect:
            # All existing project task locks are held: no cooperating worker
            # can still be writing. A live native lease remains an extra guard.
            current = store.get(run_id)
            if current['owner'] and current['lease_until'] > time.time():
                raise RuntimeError('Task still has a live executor lease')
            with store.connect() as conn:
                conn.execute("UPDATE calls SET status='interrupted' WHERE run_id=? AND status='running'", (run_id,))
            store.transition(run_id, 'needs_recovery', {'reason': 'Orphaned write found under exclusive project and task locks'})
            from bid_agent.recovery import reconcile_locked
            audit = reconcile_locked(store, run_id, registry)
            # Do not execute the newly requested write in the same call, even
            # when recovery succeeded. Let the Agent observe existing output.
            return {'ok': False, 'error_type': 'RecoveryObservation',
                    'error': 'Prior write reconciled; requested action was NOT executed. Inspect recovery outcomes before choosing the next action.',
                    'recovery': audit, 'quality_status': 'not_verified'}
        call_id = store.begin_call(run_id, tool, args)
        try:
            result = {'ok': True, 'data': tool.handler(**args)}
        except Exception as exc:
            result = {'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)[:1000]}
        store.end_call(run_id, call_id, result)
        return {**result, 'call_id': call_id, 'quality_status': 'not_verified'}


if __name__ == '__main__':
    try:
        # Some document libraries print optional-feature notices to stdout.
        # stdout is the JSON protocol channel, so redirect all incidental
        # output while the request is executing.
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch(sys.argv[1], sys.argv[2], json.load(sys.stdin))
    except Exception as exc:
        result = {'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)[:1000]}
    print(json.dumps(result, ensure_ascii=False))
