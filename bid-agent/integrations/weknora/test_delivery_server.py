import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import httpx
from delivery_server import make_app


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_library_routes_enforce_session_auth_and_bound_read_tools(self):
        from unittest.mock import AsyncMock,patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'state.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE runs(id TEXT,scope TEXT); CREATE TABLE events(sequence INTEGER,run_id TEXT,kind TEXT,payload TEXT);')
                c.execute('INSERT INTO runs VALUES (?,?)',('run',json.dumps({'project':str(root)})))
            def upstream(req):
                return httpx.Response(200,json={'success':True,'data':{'id':'session'}}) if req.headers.get('authorization')=='Bearer owner' else httpx.Response(404)
            app=make_app(db,'run','session',transport=httpx.MockTransport(upstream))
            mock=AsyncMock(return_value={'ok':True,'data':{'results':[],'matches':0}})
            with patch('library_http.invoke',mock):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    url='/sessions/session/libraries?action=search&library=history&q=售后'
                    self.assertEqual((await client.get(url)).status_code,401)
                    self.assertEqual((await client.get(url,headers={'Authorization':'Bearer stranger'})).status_code,404)
                    mock.assert_not_called()
                    response=await client.get(url,headers={'Authorization':'Bearer owner'});self.assertEqual(response.status_code,200)
                    args=mock.call_args.args;self.assertEqual(args[0],db);self.assertEqual(args[1],'run');self.assertEqual(args[2]['name'],'search_library');self.assertEqual(args[2]['arguments']['library'],'history')
                    mock.reset_mock()
                    bad=await client.get('/sessions/session/libraries?action=import&path=/etc/passwd',headers={'Authorization':'Bearer owner'})
                    self.assertEqual(bad.status_code,400);mock.assert_not_called()

    async def test_two_session_bindings_do_not_share_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); db=root/'state.sqlite'
            with sqlite3.connect(db) as conn:
                conn.executescript('CREATE TABLE runs(id TEXT,scope TEXT); CREATE TABLE events(sequence INTEGER,run_id TEXT,kind TEXT,payload TEXT);')
                for i in (1,2):
                    name=f'file{i}.docx'; (root/name).write_bytes(str(i).encode())
                    conn.execute('INSERT INTO runs VALUES (?,?)',(f'run{i}',json.dumps({'project':str(root)})))
                    conn.execute('INSERT INTO events VALUES (?,?,?,?)',(i,f'run{i}','artifact_created',json.dumps({'output':name,'sha256':hashlib.sha256(str(i).encode()).hexdigest(),'final_delivery':True})))
            def upstream(req):
                self.assertEqual(req.headers['x-tenant-id'],'7')
                return httpx.Response(200,json={'success':True,'data':{'id':req.url.path.split('/')[-1]}})
            app=make_app(bindings={f's{i}':{'state':db,'run_id':f'run{i}'} for i in (1,2)},transport=httpx.MockTransport(upstream))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer valid','X-Tenant-ID':'7'}) as client:
                for i in (1,2):
                    base=f'/sessions/s{i}/artifacts'
                    response=await client.get(base)
                    self.assertEqual([r['name'] for r in response.json()['data']],[f'file{i}.docx'])
                    self.assertEqual((await client.get(base+f'/{3-i}/download')).status_code,404)
                    self.assertEqual((await client.get(base+f'/{i}/download')).content,str(i).encode())

    async def test_auth_binding_integrity_and_upstream_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'out.docx').write_bytes(b'version1')
            db = root/'state.sqlite'
            with sqlite3.connect(db) as conn:
                conn.executescript('CREATE TABLE runs(id TEXT,scope TEXT); CREATE TABLE events(sequence INTEGER,run_id TEXT,kind TEXT,payload TEXT);')
                conn.execute('INSERT INTO runs VALUES (?,?)', ('run', json.dumps({'project':str(root)})))
                conn.execute('INSERT INTO events VALUES (?,?,?,?)', (1,'run','artifact_created',json.dumps({'output':'out.docx','sha256':hashlib.sha256(b'version1').hexdigest(),'final_delivery':True})))
            calls=[]
            def upstream(req):
                calls.append(req)
                token=req.headers.get('authorization')
                if token=='Bearer unavailable': raise httpx.ConnectError('offline',request=req)
                if token=='Bearer stranger': return httpx.Response(404)
                if token!='Bearer valid': return httpx.Response(401)
                return httpx.Response(200,json={'success':True,'data':{'id':'session'}})
            app=make_app(db,'run','session',transport=httpx.MockTransport(upstream))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                url='/sessions/session/artifacts'
                self.assertEqual((await client.get(url)).status_code,401)
                self.assertEqual(calls,[])
                for token,expected in [('invalid',401),('stranger',404),('unavailable',503)]:
                    self.assertEqual((await client.get(url,headers={'Authorization':'Bearer '+token})).status_code,expected)
                headers={'Authorization':'Bearer valid'}
                self.assertEqual((await client.get('/sessions/other/artifacts',headers=headers)).status_code,404)
                result=await client.get(url,headers=headers)
                self.assertEqual(result.json()['data'][0]['name'],'out.docx')
                result=await client.get(url+'/1/download',headers=headers)
                self.assertEqual(result.content,b'version1')
                self.assertEqual(result.headers['cache-control'],'no-store')
                self.assertEqual((await client.get(url+'/2/download',headers=headers)).status_code,404)
                (root/'out.docx').write_bytes(b'version2')
                self.assertEqual((await client.get(url+'/1/download',headers=headers)).status_code,409)


if __name__=='__main__': unittest.main()
