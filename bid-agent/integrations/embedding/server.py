"""Loopback-only Chinese embedding service, isolated from the bid runtime."""
import asyncio
import math
from contextlib import asynccontextmanager
from fastembed import TextEmbedding
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

MODEL='BAAI/bge-small-zh-v1.5'
lock=asyncio.Lock()
@asynccontextmanager
async def lifespan(app):
    app.state.model=TextEmbedding(model_name=MODEL,cache_dir='/home/ubuntu/codex-workspace/deta-embedding-models',threads=2)
    yield
async def health(request):
    return JSONResponse({'status':'ready','model':MODEL,'dimensions':512})
async def embed(request:Request):
    if int(request.headers.get('content-length','0'))>1048576:
        return JSONResponse({'error':'Request too large'},status_code=413)
    try:
        data=await request.json();texts=data.get('input');texts=[texts] if isinstance(texts,str) else texts
        if data.get('model')!=MODEL or not isinstance(texts,list) or not 1<=len(texts)<=128 or any(not isinstance(x,str) or not x.strip() or len(x)>16000 for x in texts):
            raise ValueError('Invalid model or input')
        if data.get('encoding_format','float')!='float':raise ValueError('Float encoding required')
        if data.get('dimensions',512)!=512:raise ValueError('Dimensions must be 512')
    except (ValueError,TypeError,AttributeError):
        return JSONResponse({'error':'Invalid model or input'},status_code=400)
    async with lock:
        vectors=await asyncio.to_thread(lambda:[v.tolist() for v in request.app.state.model.embed(texts,batch_size=8)])
    if any(len(v)!=512 or not all(math.isfinite(x) for x in v) for v in vectors):
        return JSONResponse({'error':'Invalid embedding output'},status_code=500)
    return JSONResponse({'object':'list','model':MODEL,'data':[{'object':'embedding','index':i,'embedding':v} for i,v in enumerate(vectors)]})
app=Starlette(lifespan=lifespan,routes=[Route('/health',health),Route('/v1/embeddings',embed,methods=['POST'])])
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=18766,access_log=False)
