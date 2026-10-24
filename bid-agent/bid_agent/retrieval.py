import hashlib
import json
from pathlib import Path
from .tools import schema, STRING


def install_retrieval_tools(registry):
    def read_text_chunk(path, offset, limit):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            raise ValueError('Nonnegative character offset and limit 1–8000 required')
        source = registry.path(path)
        if source.stat().st_size > 2_000_000: raise ValueError('Text file exceeds 2 MB')
        raw = source.read_bytes();content = raw.decode('utf-8')
        return {'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'offset':offset,
                'total_characters':len(content),'content':content[offset:offset+limit],
                'has_more':offset+limit<len(content)}

    def read_json_value(path, pointer):
        source = registry.path(path)
        if source.stat().st_size > 2_000_000: raise ValueError('JSON file exceeds 2 MB')
        raw = source.read_bytes();value = json.loads(raw)
        if pointer:
            if not pointer.startswith('/'): raise ValueError('JSON pointer must be empty or start with /')
            for segment in pointer[1:].split('/'):
                segment = segment.replace('~1','/').replace('~0','~')
                if isinstance(value,list):
                    if not segment.isdecimal() or int(segment)>=len(value):raise ValueError('Array index outside JSON value')
                    value=value[int(segment)]
                elif isinstance(value,dict) and segment in value:value=value[segment]
                else:raise ValueError('JSON pointer not found')
        result={'path':path,'pointer':pointer,'sha256':hashlib.sha256(raw).hexdigest()}
        encoded=json.dumps(value,ensure_ascii=False)
        if len(encoded)<=8000:return {**result,'value':value,'truncated':False}
        return {**result,'truncated':True,'serialized_characters':len(encoded),
                'type':type(value).__name__,'length':len(value) if isinstance(value,(list,dict,str)) else None,
                'keys':list(value)[:100] if isinstance(value,dict) else None,
                'next_action':'Select a child JSON pointer; large strings can be read from the original file with read_text_chunk. No oversized value returned.'}

    def authorized_run(run_id):
        target=registry.store.get(run_id)
        current=registry.store.get(registry.run_id)
        if run_id!=registry.run_id and (not current['scope'].get('project') or target['scope'].get('project')!=current['scope'].get('project')):
            raise ValueError('History belongs to another project')
        return target

    def read_event(sequence, offset, limit):
        if offset<0 or not 1<=limit<=8000: raise ValueError('Invalid offset or limit (maximum 8000)')
        with registry.store.connect() as conn:
            row=conn.execute('SELECT run_id,kind,payload FROM events WHERE sequence=?',(sequence,)).fetchone()
        if row is None: raise ValueError('Event not found')
        authorized_run(row['run_id'])
        payload=json.loads(row['payload'])
        if row['kind']=='message': payload.pop('reasoning_content',None)
        text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        return {'sequence':sequence,'run_id':row['run_id'],'kind':row['kind'],'offset':offset,
                'total_characters':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest(),
                'content':text[offset:offset+limit],'has_more':offset+limit<len(text)}

    def read_event_value(sequence, pointer):
        with registry.store.connect() as conn:
            row=conn.execute('SELECT run_id,kind,payload FROM events WHERE sequence=?',(sequence,)).fetchone()
        if row is None:raise ValueError('Event not found')
        authorized_run(row['run_id'])
        value=json.loads(row['payload']);view='event_payload'
        if row['kind']=='message':
            value.pop('reasoning_content',None)
            if value.get('role')=='tool':
                try:value=json.loads(value['content']);view='decoded_tool_result'
                except (ValueError,TypeError):pass
        if pointer:
            if not pointer.startswith('/'):raise ValueError('JSON pointer must start with /')
            for part in pointer[1:].split('/'):
                part=part.replace('~1','/').replace('~0','~')
                if isinstance(value,list) and part.isdecimal() and int(part)<len(value):value=value[int(part)]
                elif isinstance(value,dict) and part in value:value=value[part]
                else:raise ValueError('JSON pointer not found')
        result={'sequence':sequence,'run_id':row['run_id'],'view':view,'pointer':pointer,
                'event_payload_sha256':hashlib.sha256(row['payload'].encode()).hexdigest()}
        encoded=json.dumps(value,ensure_ascii=False)
        if len(encoded)<=8000:return {**result,'value':value,'truncated':False}
        return {**result,'truncated':True,'type':type(value).__name__,'length':len(value) if isinstance(value,(dict,list,str)) else None,
                'keys':list(value)[:100] if isinstance(value,dict) else None,'next_action':'Select a narrower JSON pointer; do not read unrelated fields.'}

    def search_history(query, limit):
        if not query.strip() or not 1<=limit<=50: raise ValueError('Query required; limit 1–50')
        results=[]
        with registry.store.connect() as conn:
            rows=conn.execute('SELECT sequence,run_id,kind,payload FROM events ORDER BY sequence DESC')
            for row in rows:
                if row['kind'] in ('model_progress','executor_claimed','tool_started','message'): continue
                try: authorized_run(row['run_id'])
                except ValueError: continue
                data=json.loads(row['payload'])
                if row['kind']=='tool_observation':
                    call=conn.execute('SELECT tool FROM calls WHERE id=?',(data.get('call_id'),)).fetchone()
                    if call and call['tool'] in {'search_history','read_event','read_event_value','search_text','read_memory'}:
                        continue
                # Conversation copies and retrieval echoes are not new evidence.

                text=json.dumps(data,ensure_ascii=False);at=text.casefold().find(query.casefold())
                if at<0: continue
                results.append({'sequence':row['sequence'],'run_id':row['run_id'],'kind':row['kind'],
                                'snippet':text[max(0,at-100):at+500]})
                if len(results)>=limit: break
        return {'results':results,'limit':limit}

    def read_memory(run_id, kind, key):
        authorized_run(run_id)
        with registry.store.connect() as conn:
            row=conn.execute('SELECT revision,value FROM memory WHERE run_id=? AND kind=? AND key=? ORDER BY revision DESC LIMIT 1',
                             (run_id,kind,key)).fetchone()
        if row is None: raise ValueError('Memory record not found')
        value=json.loads(row['value'])
        if len(json.dumps(value))>50000: raise ValueError('Memory record too large; use history references')
        return {'run_id':run_id,'kind':kind,'key':key,'revision':row['revision'],'value':value}

    def search_text(query, limit):
        if not query.strip() or not 1<=limit<=50: raise ValueError('Query required; limit 1–50')
        results=[]
        for candidate in sorted(registry.root.rglob('*')):
            if candidate.suffix.lower() not in {'.txt','.md','.json','.csv'} or not candidate.is_file(): continue
            try: path=registry.path(str(candidate.relative_to(registry.root)))
            except ValueError: continue
            if path.stat().st_size>2_000_000: continue
            text=path.read_text(errors='replace')
            for index,line in enumerate(text.splitlines(),1):
                at=line.casefold().find(query.casefold())
                if at>=0:
                    results.append({'path':str(path.relative_to(registry.root)),'line':index,
                                    'snippet':line[max(0,at-100):at+500],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
                    if len(results)>=limit: return {'results':results,'truncated':True}
        return {'results':results,'truncated':False,'scope':'Text files only; use PDF/DOCX extraction for binary documents'}

    registry.add('read_event_value','按JSON Pointer直接读取历史结果字段；工具message自动解析一层content JSON，避免转义字符。空pointer看根字段，大值只返回键或长度。仅限本项目。',schema({'sequence':{'type':'integer'},'pointer':STRING}),read_event_value)
    registry.add('read_event','按事件序号分块读取归档工具结果/对话；仅限本项目，超大内容可分段取回。',schema({'sequence':{'type':'integer'},'offset':{'type':'integer'},'limit':{'type':'integer'}}),read_event)
    registry.add('read_text_chunk','直接按字符分段读取项目UTF-8原文件，避免反复读取带多层JSON转义的历史事件；返回原文件哈希。limit最多8000。',schema({'path':STRING,'offset':{'type':'integer'},'limit':{'type':'integer'}}),read_text_chunk)
    registry.add('read_json_value','按JSON Pointer定位项目JSON字段，例如/pages/0或/requirements/2；空pointer查看根。大对象仅返回键或长度，需继续选择子字段，不把整份大文件塞入上下文。',schema({'path':STRING,'pointer':STRING}),read_json_value)
    registry.add('search_history','检索同项目历史工具结果、用户答复和产物记录，返回可读取的事件序号。',schema({'query':STRING,'limit':{'type':'integer'}}),search_history)
    registry.add('read_memory','读取同项目某任务指定工作记忆的最新版本；仍须检查verification字段。',schema({'run_id':STRING,'kind':STRING,'key':STRING}),read_memory)
    registry.add('search_text','在项目文本资料中搜索并返回行号与文件哈希；不会扫描二进制文档。',schema({'query':STRING,'limit':{'type':'integer'}}),search_text)
