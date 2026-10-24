"""Local Streamable HTTP endpoint; state and task binding are operator supplied."""
import argparse
import asyncio
import contextlib
import json
import os
import hmac
import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
import uvicorn

HERE = Path(__file__).resolve().parent
PYTHON = Path(os.environ.get('DETA_BID_WORKER_PYTHON', str(HERE.parents[1] / '.venv/bin/python')))


def preflight():
    """Fail before exposing tools when the controlled runtimes are incomplete."""
    from workflow_policy import load_policy
    policy = load_policy()
    if not PYTHON.is_file():
        raise RuntimeError('Configured DETA_BID_WORKER_PYTHON does not exist: '+str(PYTHON))
    code = ('import docx,pypdf,openpyxl,pymupdf,lxml,docxcompose,PIL; '
            'print("document-runtime-ok")')
    checked = subprocess.run([str(PYTHON), '-c', code], capture_output=True, text=True, timeout=30)
    if checked.returncode:
        raise RuntimeError('Document worker dependency check failed: '+checked.stderr[-500:])
    missing = [name for name in ('libreoffice', 'tesseract', 'fc-list') if not shutil.which(name)]
    if missing:
        raise RuntimeError('Required document executable missing: '+', '.join(missing))
    fonts = subprocess.run(['fc-list', ':lang=zh'], capture_output=True, timeout=30).stdout
    if not fonts:
        raise RuntimeError('No Chinese font is available to the document renderer')
    return {'worker_python':str(PYTHON),'policy_version':policy['version'],'policy_sha256':policy['policy_sha256']}


async def invoke(state, run_id, request):
    process = await asyncio.create_subprocess_exec(
        str(PYTHON), str(HERE / 'worker.py'), str(state), run_id,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    # Do not blindly kill/retry a mutating worker on client disconnection.
    # The worker owns the task lock and durable journal until completion.
    output, error = await process.communicate(json.dumps(request).encode())
    if process.returncode:
        raise RuntimeError('Tool worker exited; inspect durable call journal before retry')
    return json.loads(output)


def make_app(state, run_id):
    server = Server('deta-bid-tools')

    @server.list_tools()
    async def list_tools():
        result = await invoke(state, run_id, {'method': 'list'})
        if 'tools' not in result:
            raise RuntimeError(result.get('error', 'Catalog unavailable'))
        return [Tool(**tool) for tool in result['tools']]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        result = await invoke(state, run_id, {'method': 'call', 'name': name, 'arguments': arguments})
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                              isError=not result.get('ok', False))

    manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    async def project_runtime(request):
        secret = os.environ.get('DETA_PROJECT_SCOPE_KEY', '')
        if not secret or not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer '+secret):
            return JSONResponse({'error':'unauthorized'}, status_code=401)
        try:
            payload = await request.json()
            # Run with the existing bid interpreter, not the MCP environment.
            result = await invoke(state, run_id, {'method':'runtime', **payload})
            return JSONResponse(result, status_code=200 if result.get('ok') else 409)
        except Exception:
            return JSONResponse({'error':'Project runtime unavailable'}, status_code=503)

    return Starlette(routes=[Route('/project-runtime', project_runtime, methods=['POST']), Mount('/mcp', app=handle)], lifespan=lifespan)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    preflight()
    # Evaluation endpoint only. Authenticated container/network deployment is
    # separate work; no public bind option is exposed here.
    uvicorn.run(make_app(args.state.resolve(), args.run_id), host='127.0.0.1', port=args.port)
