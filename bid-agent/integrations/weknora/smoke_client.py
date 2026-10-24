"""Actual HTTP protocol check against the isolated bridge validation project."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    async with streamablehttp_client('http://127.0.0.1:8766/mcp/') as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            catalog = await session.list_tools()
            path = 'http-check-' + uuid.uuid4().hex + '.txt'
            created = await session.call_tool('write_text', {'path': path, 'content': 'MCP验证原文'})
            assert not created.isError, created
            readback = await session.call_tool('read_text', {'path': path})
            assert not readback.isError, readback
            assert json.loads(readback.content[0].text)['data']['content'] == 'MCP验证原文'
            overwrite = await session.call_tool('write_text', {'path': path, 'content': '不得覆盖'})
            assert overwrite.isError
            escape = await session.call_tool('read_text', {'path': '../outside'})
            assert escape.isError
            malformed = await session.call_tool('write_text', {'path': 'invalid.txt', 'content': 123})
            assert malformed.isError
            report = {'protocol': 'Streamable HTTP', 'tools': len(catalog.tools),
                      'write_and_readback': True, 'overwrite_rejected': True,
                      'path_escape_rejected': True, 'invalid_argument_rejected': True,
                      'artifact': path, 'scope': 'SDK client only; WeKnora Agent not yet connected'}
            Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))


asyncio.run(main())
