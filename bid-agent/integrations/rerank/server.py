"""Loopback-only CPU cross-encoder reranker; never logs document text."""
import asyncio,math,time
from contextlib import asynccontextmanager
from fastembed.rerank.cross_encoder import TextCrossEncoder
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn
MODEL='BAAI/bge-reranker-v2-m3-int8'
PATH='/home/ubuntu/codex-workspace/deta-rerank-models/bge-reranker-v2-m3-int8'
lock=asyncio.Lock()
@asynccontextmanager
async def lifespan(app):
    app.state.model=TextCrossEncoder(MODEL,specific_model_path=PATH,threads=2,providers=['CPUExecutionProvider'],local_files_only=True)
    app.state.model.model.tokenizer.enable_truncation(max_length=512)
    app.state.requests=0
    yield
async def health(request):
    return JSONResponse({'status':'ready','model':MODEL,'requests':request.app.state.requests,'max_pair_tokens':512})
async def rerank(request):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>2_000_000:return JSONResponse({'error':'Request too large'},status_code=413)
    try:
        import json
        data=json.loads(body);docs=data['documents'];query=data['query'];top=data.get('top_n',len(docs))
        if data.get('model')!=MODEL or not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError()
        if not isinstance(docs,list) or not 1<=len(docs)<=200 or any(not isinstance(d,str) or len(d)>32000 for d in docs):raise ValueError()
        if not isinstance(top,int) or not 1<=top<=len(docs):raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError):return JSONResponse({'error':'Invalid rerank input'},status_code=400)
    async with lock:
        start=time.monotonic()
        scores=await asyncio.to_thread(lambda:list(request.app.state.model.rerank(query,docs,batch_size=1)))
        request.app.state.requests+=1
    if len(scores)!=len(docs) or any(not math.isfinite(x) for x in scores):return JSONResponse({'error':'Invalid inference output'},status_code=500)
    results=[{'index':i,'relevance_score':1/(1+math.exp(-max(-80,min(80,s)))),'document':{'text':docs[i]}} for i,s in enumerate(scores)]
    results.sort(key=lambda r:(-r['relevance_score'],r['index']))
    return JSONResponse({'model':MODEL,'results':results[:top],'meta':{'duration_ms':round((time.monotonic()-start)*1000),'max_pair_tokens':512}})
app=Starlette(lifespan=lifespan,routes=[Route('/health',health),Route('/v1/rerank',rerank,methods=['POST'])])
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=18768,access_log=False)
