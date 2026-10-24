"""Loopback delivery adapter, authenticated by the same WeKnora session."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from artifact_access import ArtifactAccess


def make_app(state=None, run_id=None, session_id=None, upstream='http://127.0.0.1:18765', transport=None, bindings=None):
    configured = bindings if bindings is not None else {session_id: {'state': state, 'run_id': run_id}}
    accesses = {sid: ArtifactAccess(value['state'], value['run_id']) for sid, value in configured.items()}
    if not accesses or any(not isinstance(sid, str) or not sid for sid in accesses):
        raise ValueError('Explicit session bindings required')

    async def deliver(request):
        # The server binds session -> task. Caller never supplies DB or path.
        session_id = request.path_params['session_id']
        access = accesses.get(session_id)
        if access is None:
            return Response(status_code=404)
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer ') or len(auth) <= 7:
            return Response(status_code=401)
        try:
            headers = {'Authorization': auth}
            tenant = request.headers.get('x-tenant-id')
            if tenant:
                headers['X-Tenant-ID'] = tenant
            async with httpx.AsyncClient(timeout=10, transport=transport, follow_redirects=False) as client:
                verified = await client.get(upstream + '/api/v1/sessions/' + session_id,
                                            headers=headers)
            if verified.status_code != 200:
                return Response(status_code=404 if verified.status_code in (403, 404) else 401)
            payload = verified.json()
            if not payload.get('success') or payload.get('data', {}).get('id') != session_id:
                return Response(status_code=403)
        except (httpx.HTTPError, ValueError):
            return Response(status_code=503)
        if request.url.path.endswith('/libraries'):
            from library_http import library_response
            return await library_response(request, configured[session_id])
        artifact_id = request.path_params.get('artifact_id')
        if artifact_id is None:
            return JSONResponse({'data': await asyncio.to_thread(access.list)})
        try:
            name, data = await asyncio.to_thread(access.read, artifact_id)
        except KeyError:
            return Response(status_code=404)
        except (ValueError, OSError):
            return JSONResponse({'error': 'Registered file unavailable or changed'}, status_code=409)
        from urllib.parse import quote
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe=''),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    base = '/sessions/{session_id}/artifacts'
    return Starlette(routes=[Route('/sessions/{session_id}/libraries', deliver), Route(base, deliver), Route(base + '/{artifact_id}/download', deliver)])


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--session-id')
    parser.add_argument('--bindings', type=Path)
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    if args.bindings and any((args.state, args.run_id, args.session_id)):
        parser.error('Use bindings or a single state/run/session tuple, not both')
    bindings = json.loads(args.bindings.read_text()) if args.bindings else None
    uvicorn.run(make_app(args.state, args.run_id, args.session_id, bindings=bindings), host='127.0.0.1', port=args.port)
