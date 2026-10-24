"""Server-side builders for the business ledgers used by stages 4--8."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .project_ledgers import _atomic_new, _project, _sha, _verify_quote_on_source, load_configuration_lock, build_project_facts, create_configuration_lock
from .tools import STRING, schema


def _target(registry, target, prefix):
    if not target.startswith(prefix) or not target.endswith('.json'):
        raise ValueError('Target must be a versioned JSON under '+prefix)
    return registry.path(target)


def _source(registry, value, quote_required=True):
    path=registry.path(value.get('path',''))
    if not path.is_file() or _sha(path)!=value.get('sha256'):
        raise ValueError('Source is missing or changed: '+str(value.get('path')))
    page=value.get('physical_page')
    if page is not None and (type(page) is not int or page<1):raise ValueError('physical_page must be positive')
    quote=str(value.get('quote') or '').strip()
    if quote_required and not quote:raise ValueError('Source quote is required')
    location=str(value.get('location') or '').strip()
    if not location:raise ValueError('Source location is required')
    if quote:_verify_quote_on_source(path,page,quote)
    return {'path':value['path'],'sha256':value['sha256'],'physical_page':page,'location':location,'quote':quote}


def _write(registry,target,payload,prefix):
    meta=_project(registry)
    payload={'project_id':meta['project_id'],'source_sha256':meta['source_sha256'],**payload}
    result=_atomic_new(_target(registry,target,prefix),payload)
    return {'path':str(result['path'].relative_to(registry.root)),'sha256':result['sha256'],'bytes':result['bytes']}


def _next(registry, folder, stem, suffix):
    base=registry.path(folder);base.mkdir(parents=True,exist_ok=True);version=1
    while (base/f'{stem}-v{version}{suffix}').exists():version+=1
    return base/f'{stem}-v{version}{suffix}'


def _next_stage2_composite_version(registry):
    """Return a version unused by every output of the Stage 2 composite."""
    version=1
    while any(registry.path(path).exists() for path in (
        f'analysis/step2/requirement-atomization-server-v{version}.json',
        f'analysis/step2/requirement-atomization-server-v{version}-server-spec.json',
        f'analysis/step2/atomization-review-v{version}.xlsx')):
        version+=1
    return version


def _next_stage7_composite_version(registry):
    """Return a version unused by every Stage 7 renderer output."""
    base=registry.path('analysis/step7');version=1
    while any((base/f'eight-column-internal-review-v{version}{suffix}').exists()
              for suffix in ('.docx','.xlsx','.manifest.json','.traceability.json')):
        version+=1
    return version


def build_stage2_finalize(registry,tender_analysis_path):
    """Finish the deterministic half of Stage 2 from the approved analysis.

    The model has already classified each physical page.  Requiring it to read
    the large analysis back merely to copy the technical page numbers caused
    repeated discovery/read loops.  This builder derives that exact page set,
    indexes the locked PDF, and creates the verbatim conservative atomization
    and six-column review in one server-side transaction.
    """
    import pymupdf
    analysis_file=registry.path(tender_analysis_path)
    analysis=json.loads(analysis_file.read_text())
    project=_project(registry)
    if analysis.get('project_id')!=project['project_id'] or analysis.get('source_sha256')!=project['source_sha256']:
        raise ValueError('Tender analysis belongs to another project/source')
    if analysis.get('version')!='tender-analysis-v1':
        raise ValueError('A completed project tender analysis is required')
    pages=sorted({int(item['physical_page']) for item in analysis.get('items',[])
                  if item.get('category')=='technical' and item.get('source_sha256')==project['source_sha256']})
    review_pages=sorted(int(row['physical_page']) for row in analysis.get('page_reviews',[])
                        if row.get('source_sha256')==project['source_sha256'] and 'technical' in row.get('categories',[]))
    if not pages or pages!=review_pages:
        raise ValueError('Technical item pages and page-review classifications must be identical and non-empty')
    source=None
    for candidate in sorted(registry.path('input').glob('*.pdf')):
        if _sha(candidate)==project['source_sha256']:source=candidate;break
    if source is None:raise ValueError('Hash-locked tender PDF is missing')
    indexed=[];table_rows=0;text_blocks=0
    with pymupdf.open(source) as document:
        if pages[-1]>len(document):raise ValueError('Technical page selection exceeds tender page count')
        for number in pages:
            page=document[number-1];found=page.find_tables().tables;boxes=[pymupdf.Rect(t.bbox) for t in found];tables=[]
            for table_index,table in enumerate(found,1):
                rows=[]
                for row_index,cells in enumerate(table.extract(),1):
                    values=[None if cell is None else str(cell) for cell in cells]
                    text_value=' | '.join('' if cell is None else str(cell).replace('\n',' ').strip() for cell in cells)
                    rows.append({'id':f'TECH-P{number:03}-T{table_index:02}-R{row_index:03}','row':row_index,
                                 'cells':values,'text':text_value,'text_sha256':hashlib.sha256(text_value.encode()).hexdigest()})
                table_rows+=len(rows);tables.append({'table':table_index,'bbox':[round(x,3) for x in table.bbox],'rows':rows})
            blocks=[]
            for block_index,block in enumerate(page.get_text('blocks'),1):
                rect=pymupdf.Rect(block[:4]);text_value=str(block[4]).strip()
                if not text_value:continue
                overlaps=False
                for box in boxes:
                    intersection=rect & box
                    if not intersection.is_empty and rect.get_area()>0 and intersection.get_area()/rect.get_area()>=0.5:
                        overlaps=True;break
                if overlaps:continue
                blocks.append({'id':f'TECH-P{number:03}-B{block_index:03}','block':block_index,
                               'bbox':[round(x,3) for x in rect],'text':text_value,
                               'text_sha256':hashlib.sha256(text_value.encode()).hexdigest()})
            text_blocks+=len(blocks)
            indexed.append({'physical_page':number,'printed_page':None,'tables':tables,'text_blocks':blocks})
    target=registry.path('analysis/step2/technical-source-index.json')
    payload={'version':'technical-source-index-v1','project_id':project['project_id'],
             'source_path':str(source.relative_to(registry.root)),'source_sha256':project['source_sha256'],
             'physical_pages':pages,'physical_page_range':[pages[0],pages[-1]],'pages':indexed,
             'stats':{'page_count':len(indexed),'table_count':sum(len(x['tables']) for x in indexed),
                      'table_row_count':table_rows,'text_block_count':text_blocks},
             'semantic_split':'not_performed','generated_from_tender_analysis_path':tender_analysis_path,
             'generated_from_tender_analysis_sha256':_sha(analysis_file),
             'scope_note':'页清单严格来自逐页分析technical分类；保持表格原行与表外文本块顺序，不生成响应或偏离'}
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n');temporary.replace(target)
    # A prior run can contain only part of a version set (for example an old
    # review workbook but no matching atomization JSON).  Pick a version for
    # which every file created by the composite is free.  This keeps policy
    # upgrades and interrupted same-project reruns inside the server workflow
    # instead of forcing the model to rename or delete files.
    version=_next_stage2_composite_version(registry)
    stem=f'requirement-atomization-server-v{version}'
    atom=registry.tools['build_requirement_atomization_baseline'].handler('analysis/step2/technical-source-index.json',stem)
    review=registry.tools['render_requirement_atomization_review'].handler(atom['path'],f'atomization-review-v{version}')
    return {'technical_source_index':{'path':'analysis/step2/technical-source-index.json','sha256':_sha(target),'stats':payload['stats']},
            'requirement_atomization':atom,'atomization_review':review,'technical_pages':pages}


def build_stage4_baseline(registry,tender_analysis_path,skeleton_bundle_path):
    """Build a truthful Stage 4 baseline without asking the model to stream ledgers."""
    from docx import Document
    import pymupdf
    project=_project(registry);analysis_file=registry.path(tender_analysis_path);analysis=json.loads(analysis_file.read_text())
    bundle_file=registry.path(skeleton_bundle_path);bundle=json.loads(bundle_file.read_text())
    if analysis.get('project_id')!=project['project_id'] or analysis.get('source_sha256')!=project['source_sha256']:
        raise ValueError('Tender analysis belongs to another project/source')
    skeleton=registry.path(bundle.get('skeleton_docx_path',''))
    if not skeleton.is_file() or _sha(skeleton)!=bundle.get('skeleton_docx_sha256'):raise ValueError('Skeleton bundle is stale')
    source=None
    for candidate in sorted(registry.path('input').glob('*.pdf')):
        if _sha(candidate)==project['source_sha256']:source=candidate;break
    if source is None:raise ValueError('Hash-locked tender PDF is missing')
    source_rel=str(source.relative_to(registry.root));pages=[]
    with pymupdf.open(source) as pdf:
        for i in range(min(20,len(pdf))):pages.append((i+1,pdf[i].get_text('text')))
    def locate(value):
        for page,text in pages:
            if value and value in text:return page,value
        return None,''
    title=project.get('title') or '';title_page,title_quote=locate(title)
    number='';number_page=None;number_quote=''
    for page,text in pages:
        match=re.search(r'((?:项目编号|招标编号|采购项目编号|采购编号)\s*[：:]\s*([^\s，,；;）)】]+))',text)
        if match:number=match.group(2).strip();number_page=page;number_quote=match.group(1);break
    facts=[]
    if title_quote:
        facts.append({'field_code':'project_title','label':'项目名称','value':title,'state':'source_verified','sources':[{'path':source_rel,'sha256':project['source_sha256'],'physical_page':title_page,'location':f'物理页{title_page}','quote':title_quote}],'allowed_slots':['slot_project_title'],'reason':'招标原件直接证明'})
    else:
        facts.append({'field_code':'project_title','label':'项目名称','value':title,'state':'derived_verified','sources':[],'allowed_slots':['slot_project_title'],'reason':'由当前哈希锁定项目记录生成'})
    if number:
        facts.append({'field_code':'project_number','label':'项目编号','value':number,'state':'source_verified','sources':[{'path':source_rel,'sha256':project['source_sha256'],'physical_page':number_page,'location':f'物理页{number_page}','quote':number_quote}],'allowed_slots':['slot_project_number'],'reason':'招标原件直接证明'})
    else:
        facts.append({'field_code':'project_number','label':'项目编号','value':None,'state':'missing','sources':[],'allowed_slots':['slot_project_number'],'reason':'前20个物理页未识别出明确编号'})
    facts.append({'field_code':'bidder_name','label':'投标人','value':None,'state':'missing','sources':[],'allowed_slots':['slot_bidder_name'],'reason':'当前项目尚无可复核投标人原件事实'})
    facts_target=str(_next(registry,'analysis/step4','project-facts','.json').relative_to(registry.root))
    facts_result=build_project_facts(registry,facts,facts_target)
    qualifications=[]
    for item in analysis.get('items',[]):
        if item.get('category')!='qualification':continue
        qualifications.append({'requirement_id':item['requirement_id'],'tender_source':{'path':source_rel,'sha256':project['source_sha256'],'physical_page':item['physical_page'],'location':item.get('source_location') or f"物理页{item['physical_page']}",'quote':item['source_quote']},'status':'missing','subject':'投标人','evidence':[],'reason':'当前项目未绑定足以证明本资格项的适用企业原件'})
    if not qualifications:raise ValueError('Tender analysis contains no qualification requirements')
    qualification_target=str(_next(registry,'analysis/step4','qualification-ledger','.json').relative_to(registry.root))
    qualification_result=build_qualification(registry,qualifications,qualification_target)
    filled=_next(registry,'analysis/step4','facts-qualification-draft','.docx');shutil.copy2(skeleton,filled)
    document=Document(filled)
    values={'项目名称':title,'项目编号':number,'投标人':''}
    if not document.tables:raise ValueError('Skeleton has no project identity table')
    for row in document.tables[0].rows:
        label=row.cells[0].text.strip()
        if label in values:row.cells[1].text=values[label]
    document.save(filled)
    trusted={x['field_code']:x for x in facts if x['state'] in {'source_verified','derived_verified','business_provided'} and x.get('value') not in (None,'')}
    fills=[{'field_code':code,'slot_id':slot,'value':fact['value']} for code,fact in trusted.items() for slot in fact['allowed_slots']]
    manifest_target=str(_next(registry,'analysis/step4','evidence-manifest','.json').relative_to(registry.root))
    manifest=build_fill_evidence_manifest(registry,facts_target,qualification_target,str(filled.relative_to(registry.root)),fills,[],manifest_target)
    return {'project_facts':facts_result['path'],'qualification_ledger':qualification_result['path'],'filled_docx':str(filled.relative_to(registry.root)),'evidence_manifest':manifest['path'],'trusted_fields_filled':len(fills),'qualification_items':len(qualifications),'qualification_met':0}


def build_stage5_baseline(registry,tender_analysis_path,filled_docx_path):
    """Create conservative scoring and commitment ledgers from verified tender rows."""
    project=_project(registry);analysis_file=registry.path(tender_analysis_path);analysis=json.loads(analysis_file.read_text())
    filled=registry.path(filled_docx_path)
    if analysis.get('project_id')!=project['project_id'] or analysis.get('source_sha256')!=project['source_sha256']:
        raise ValueError('Tender analysis belongs to another project/source')
    if filled.suffix.lower()!='.docx' or not filled.is_file():raise ValueError('Stage 4 filled DOCX is required')
    source=None
    for candidate in sorted(registry.path('input').glob('*.pdf')):
        if _sha(candidate)==project['source_sha256']:source=candidate;break
    if source is None:raise ValueError('Hash-locked tender PDF is missing')
    source_rel=str(source.relative_to(registry.root))
    def ref(item):return {'path':source_rel,'sha256':project['source_sha256'],'physical_page':item['physical_page'],'location':item.get('source_location') or f"物理页{item['physical_page']}",'quote':item['source_quote']}
    scores=[]
    for item in analysis.get('items',[]):
        if item.get('category')!='scoring':continue
        quote=item['source_quote']
        if ('价格' in quote or '报价' in quote) and '分' in quote:kind='price'
        elif any(word in quote for word in ('评委','综合评价','优得','良得','一般得','方案完整','方案合理')):kind='subjective'
        else:kind='objective'
        scores.append({'scoring_id':item['requirement_id'],'kind':kind,'tender_source':ref(item),'evidence':[],
                       'claimed_points':0 if kind=='objective' else None,'calculated_points':None,'awarded_points':None,
                       'price_approved':False,'evidence_completeness':'missing','response_location':'',
                       'attainable_ceiling':None,'formula':'','calculation':None})
    if not scores:raise ValueError('Tender analysis contains no scoring requirements')
    scoring_target=str(_next(registry,'analysis/step5','scoring-ledger','.json').relative_to(registry.root))
    scoring=build_scoring(registry,scores,scoring_target)
    commitments=[]
    for item in analysis.get('items',[]):
        if item.get('category')!='business':continue
        commitments.append({'commitment_id':item['requirement_id'],'tender_source':ref(item),
                            'commencement':'按本条招标原文所载条件起算；尚未形成业务承诺','time_limit':'',
                            'scope':item['source_quote'],'responsibility':'','exceptions':'','business_approved':False})
    if not commitments:
        item=next(x for x in analysis.get('items',[]) if x.get('category')=='scoring')
        commitments=[{'commitment_id':'NO_BUSINESS_COMMITMENT','tender_source':ref(item),'commencement':'不适用；当前分析无独立商务承诺项','time_limit':'','scope':'当前项目未识别独立商务承诺项','responsibility':'','exceptions':'','business_approved':False}]
    commitments_target=str(_next(registry,'analysis/step5','commitments-ledger','.json').relative_to(registry.root))
    commitment_result=build_commitments(registry,commitments,commitments_target)
    commercial=_next(registry,'analysis/step5','commercial-scoring-draft','.docx');shutil.copy2(filled,commercial)
    placements=[{'ledger_kind':'scoring','item_id':x['scoring_id'],'quote':x['tender_source']['quote'],'docx_location':f"评分要求/{x['scoring_id']}"} for x in scores]
    manifest_target=str(_next(registry,'analysis/step5','commercial-manifest','.json').relative_to(registry.root))
    manifest=build_stage5_commercial_manifest(registry,scoring_target,commitments_target,str(commercial.relative_to(registry.root)),placements,manifest_target)
    return {'scoring_ledger':scoring['path'],'commitments_ledger':commitment_result['path'],'commercial_docx':str(commercial.relative_to(registry.root)),'commercial_manifest':manifest['path'],'scoring_items':len(scores),'commitments_pending':len(commitments),'business_approved_commitments':0}


def build_qualification(registry,items,target):
    allowed={'met','not_met','missing','conflict','dynamic_pending','not_applicable'};seen=set();rows=[]
    if not items:raise ValueError('Qualification items required')
    for raw in items:
        ident=str(raw.get('requirement_id') or '').strip()
        if not ident or ident in seen:raise ValueError('qualification requirement_id must be unique')
        seen.add(ident);status=raw.get('status')
        if status not in allowed:raise ValueError(ident+': invalid qualification status')
        tender=_source(registry,raw.get('tender_source') or {})
        evidence=[]
        for value in raw.get('evidence',[]):
            ref=_source(registry,value)
            applicability=value.get('applicability')
            if applicability not in {'applicable','not_applicable','conflict','unresolved'}:raise ValueError(ident+': invalid evidence applicability')
            evidence_type=str(value.get('evidence_type') or 'original_document')
            if evidence_type not in {'original_document','certificate','contract','invoice','technical_agreement','web_query'}:raise ValueError(ident+': invalid evidence type')
            subject=str(value.get('subject') or '').strip()
            certificate_number=str(value.get('certificate_number') or '').strip();validity=str(value.get('validity') or '').strip();issuer=str(value.get('issuer') or '').strip()
            if not subject:raise ValueError(ident+': evidence subject is required')
            if evidence_type=='certificate' and not all((certificate_number,validity,issuer)):raise ValueError(ident+': certificate evidence requires number, validity and issuer')
            evidence.append({**ref,'applicability':applicability,'subject':str(value.get('subject') or ''),
                             'evidence_type':evidence_type,'combination_id':str(value.get('combination_id') or ''),
                             'certificate_number':certificate_number,'validity':validity,'issuer':issuer})
        if status=='met' and (not evidence or any(x['physical_page'] is None or x['applicability']!='applicable' for x in evidence)):
            raise ValueError(ident+': met requires page-bound applicable original evidence')
        rows.append({'requirement_id':ident,'source_quote':tender['quote'],'tender_source':tender,'status':status,
                     'subject':str(raw.get('subject') or ''),'evidence':evidence,'reason':str(raw.get('reason') or '')})
    result=_write(registry,target,{'version':'qualification-ledger-v1','items':rows},'analysis/step4/')
    return {**result,'items':len(rows),'met':sum(x['status']=='met' for x in rows)}


def build_scoring(registry,items,target):
    kinds={'objective','subjective','price','formula'};seen=set();rows=[]
    if not items:raise ValueError('Scoring items required')
    for raw in items:
        ident=str(raw.get('scoring_id') or '').strip();kind=raw.get('kind')
        if not ident or ident in seen:raise ValueError('scoring_id must be unique')
        seen.add(ident)
        if kind not in kinds:raise ValueError(ident+': invalid scoring kind')
        tender=_source(registry,raw.get('tender_source') or {})
        evidence=[_source(registry,x) for x in raw.get('evidence',[])]
        awarded=raw.get('awarded_points');calculated=raw.get('calculated_points');approved=raw.get('price_approved') is True
        if kind=='subjective' and awarded not in (None,0):raise ValueError('Subjective points cannot be self-awarded')
        if kind=='price' and not approved and calculated not in (None,0):raise ValueError('Price points require approved price')
        if kind=='objective' and raw.get('claimed_points') not in (None,0) and not evidence:raise ValueError('Objective score claim requires evidence')
        rows.append({'scoring_id':ident,'kind':kind,'source_quote':tender['quote'],'tender_source':tender,
                     'evidence':evidence,'claimed_points':raw.get('claimed_points'),'calculated_points':calculated,
                     'awarded_points':awarded,'price_approved':approved,'evidence_completeness':raw.get('evidence_completeness'),
                     'response_location':str(raw.get('response_location') or ''),'attainable_ceiling':raw.get('attainable_ceiling'),
                     'formula':str(raw.get('formula') or ''),'calculation':raw.get('calculation')})
    result=_write(registry,target,{'version':'scoring-ledger-v1','items':rows},'analysis/step5/')
    return {**result,'items':len(rows)}


def build_commitments(registry,items,target):
    seen=set();rows=[]
    if not items:raise ValueError('Commitment items required')
    for raw in items:
        ident=str(raw.get('commitment_id') or '').strip()
        if not ident or ident in seen:raise ValueError('commitment_id must be unique')
        seen.add(ident);tender=_source(registry,raw.get('tender_source') or {})
        commencement=str(raw.get('commencement') or '').strip();scope=str(raw.get('scope') or '').strip()
        if not commencement or not scope:raise ValueError(ident+': commencement and scope are required')
        rows.append({'commitment_id':ident,'source_quote':tender['quote'],'tender_source':tender,
                     'commencement':commencement,'time_limit':str(raw.get('time_limit') or ''),'scope':scope,
                     'responsibility':str(raw.get('responsibility') or ''),'exceptions':str(raw.get('exceptions') or ''),
                     'business_approved':raw.get('business_approved') is True})
    result=_write(registry,target,{'version':'commitments-ledger-v1','items':rows},'analysis/step5/')
    return {**result,'items':len(rows),'business_approved':sum(x['business_approved'] for x in rows)}


def build_stage5_commercial_manifest(registry,scoring_ledger_path,commitments_ledger_path,commercial_docx_path,placements,target):
    scoring_file=registry.path(scoring_ledger_path);commitments_file=registry.path(commitments_ledger_path);docx=registry.path(commercial_docx_path)
    scoring=json.loads(scoring_file.read_text());commitments=json.loads(commitments_file.read_text())
    if scoring.get('version')!='scoring-ledger-v1' or commitments.get('version')!='commitments-ledger-v1':raise ValueError('Unsupported Stage 5 ledger version')
    if docx.suffix.lower()!='.docx' or not docx.is_file():raise ValueError('A real commercial DOCX is required')
    from docx import Document
    document=Document(docx);text='\n'.join([p.text for p in document.paragraphs]+[c.text for t in document.tables for r in t.rows for c in r.cells])
    score_map={x['scoring_id']:x for x in scoring.get('items',[])};commitment_map={x['commitment_id']:x for x in commitments.get('items',[])}
    expected={('scoring',x) for x in score_map}|{('commitment',x) for x,v in commitment_map.items() if v.get('business_approved') is True}
    actual=set();normalized=[]
    for raw in placements or []:
        kind=str(raw.get('ledger_kind') or '');ident=str(raw.get('item_id') or '');quote=str(raw.get('quote') or '').strip();location=str(raw.get('docx_location') or '').strip()
        key=(kind,ident)
        if key in actual or key not in expected:raise ValueError('Stage 5 placement is duplicated, unknown, or not approved: '+kind+'/'+ident)
        if not quote or quote not in text or not location:raise ValueError('Stage 5 placement quote/location is absent from the commercial DOCX')
        row=score_map[ident] if kind=='scoring' else commitment_map[ident]
        if kind=='scoring':
            allowed=[row.get('source_quote','')]+[x.get('quote','') for x in row.get('evidence',[])]
            if not any(x and x in quote for x in allowed):raise ValueError(ident+': scoring placement is not anchored to its requirement or evidence')
            if row.get('kind')=='subjective' and row.get('awarded_points') not in (None,0):raise ValueError(ident+': subjective score cannot be published as awarded')
        else:
            structured=[row.get(x,'') for x in ('commencement','time_limit','scope','responsibility','exceptions')]
            if not any(str(x).strip() and str(x).strip() in quote for x in structured):raise ValueError(ident+': commitment placement does not contain its approved structured terms')
        actual.add(key);normalized.append({'ledger_kind':kind,'item_id':ident,'docx_location':location,'quote':quote})
    if actual!=expected:raise ValueError('Stage 5 commercial DOCX placement coverage is incomplete')
    payload={'version':'stage5-commercial-manifest-v1','scoring_ledger_path':scoring_ledger_path,'scoring_ledger_sha256':_sha(scoring_file),
             'commitments_ledger_path':commitments_ledger_path,'commitments_ledger_sha256':_sha(commitments_file),
             'commercial_docx_path':commercial_docx_path,'commercial_docx_sha256':_sha(docx),'placements':normalized,
             'expected_placements':len(expected),'placed_items':len(actual),'coverage':1}
    result=_write(registry,target,payload,'analysis/step5/')
    return {**result,'placements':len(actual),'coverage':1}


def build_evidence_queue(registry,atomization_path,configuration_lock_path,configuration_lock_version,target):
    atom_file=registry.path(atomization_path);atom=json.loads(atom_file.read_text());meta=_project(registry)
    if atom.get('project_id') not in (None,meta['project_id']) or atom.get('source_sha256') not in (None,meta['source_sha256']):
        raise ValueError('Atomization belongs to another project')
    lock=load_configuration_lock(registry,configuration_lock_path,configuration_lock_version)
    rows=[];seen=set()
    for raw in atom.get('rows',[]):
        if raw.get('kind')!='requirement':continue
        ident=str(raw.get('requirement_id') or raw.get('id') or '').strip()
        if not ident or ident in seen:raise ValueError('Requirement IDs are empty or duplicated')
        seen.add(ident);rows.append({'task_id':ident,'requirement_id':ident,'source_text_sha256':hashlib.sha256(str(raw.get('source_text') or raw.get('text') or '').encode()).hexdigest(),
                                     'status':'pending','attempts':0,'last_error':None})
    if not rows:raise ValueError('No requirement rows to queue')
    batches=[{'batch_id':f'B{i//20+1:04d}','task_ids':[x['task_id'] for x in rows[i:i+20]],'status':'pending'} for i in range(0,len(rows),20)]
    payload={'version':'evidence-task-queue-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],
             'atomization_path':atomization_path,'atomization_sha256':_sha(atom_file),
             'configuration_lock_path':configuration_lock_path,'configuration_lock_version':lock['lock_version'],
             'tasks':rows,'batches':batches,'task_count':len(rows),'batch_size':20,'continuation_policy':'item_failure_records_and_continues'}
    result=_write(registry,target,payload,'analysis/step6/')
    return {**result,'tasks':len(rows),'batches':len(batches)}


def build_stage6_gap_baseline(registry,atomization_path):
    """Finish Stage 6 truthfully when this project has no approved offered model.

    A missing business selection is a data gap, not a reason to guess a model or
    pause the Agent.  Every atomic requirement is still visited and receives a
    durable ``no_applicable_evidence`` result.  A later approved selection
    creates a new immutable lock and invalidates this gap-only ledger.
    """
    meta=_project(registry);atom_file=registry.path(atomization_path);atom=json.loads(atom_file.read_text())
    requirements=[x for x in atom.get('rows',[]) if x.get('kind')=='requirement']
    ids=[str(x.get('requirement_id') or x.get('id') or '').strip() for x in requirements]
    if not ids or any(not x for x in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Stage 6 baseline requires unique atomic requirement IDs')
    selection_target=_next(registry,'analysis/step6','report-selection','.json')
    selection={'version':'report-selection-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],
               'selection_authority':'business_approved_selection_only','procurement_items':[
                 {'procurement_id':'PROCUREMENT-UNRESOLVED','name':'当前招标技术要求适用采购项',
                  'status':'gap_missing_business_selection','selected_model':'','submodel':'','report_number':'','report_version':'',
                  'parent_procurement_id':'','evidence_paths':[],'applicable_requirement_ids':ids,
                  'gap':'本项目尚无业务批准的拟供车型、子型号和成套检验报告；禁止由Agent猜测选型'}]}
    _atomic_new(selection_target,selection)
    lock_target=_next(registry,'analysis/step6','configuration-lock','.json')
    lock=create_configuration_lock(registry,str(selection_target.relative_to(registry.root)),str(lock_target.relative_to(registry.root)))
    queue_target=_next(registry,'analysis/step6','evidence-queue','.json')
    queue=build_evidence_queue(registry,atomization_path,lock['path'],lock['lock_version'],str(queue_target.relative_to(registry.root)))
    queue_data=json.loads(queue_target.read_text());batch_paths=[]
    reason='缺少本项目业务批准的拟供车型及其成套报告，未执行跨车型猜测或证据拼接'
    for batch in queue_data['batches']:
        results=[{'task_id':task_id,'item_status':'processed','result':{
                    'evaluation_status':'no_applicable_evidence','gap_reason':reason,
                    'deviation_conclusion':None,'deviation_conclusion_allowed':False,
                    'mathematical_relation':None,'locked_configuration':None}}
                 for task_id in batch['task_ids']]
        payload={'version':'evidence-batch-result-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],
                 'request_task_ids':batch['task_ids'],'results':results,'all_items_visited':True,
                 'manual_response_or_deviation_cells_written':False,'execution_status':'completed_with_item_issues',
                 'summary':{'visited':len(results),'no_applicable_evidence':len(results),'item_errors':0}}
        target=_next(registry,'analysis/step6/evidence-batches','gap-batch','.json')
        _atomic_new(target,payload);batch_paths.append(str(target.relative_to(registry.root)))
    ledger_target=_next(registry,'analysis/step6','technical-evidence-ledger','.json')
    ledger=build_technical_evidence_ledger(registry,queue['path'],batch_paths,str(ledger_target.relative_to(registry.root)))
    return {'report_selection':str(selection_target.relative_to(registry.root)),
            'configuration_lock':lock['path'],'configuration_lock_version':lock['lock_version'],
            'evidence_queue':queue['path'],'technical_evidence_ledger':ledger['path'],
            'requirements_visited':len(ids),'batch_results':batch_paths,
            'selection_gap':True,'manual_columns_written':False}


def build_stage7_gap_baseline(registry,atomization_path,technical_evidence_ledger_path):
    """Render the unreviewed eight-column table from the same atomic row object.

    When Stage 6 has no approved vehicle selection, historical answers are
    explicitly not applicable and column 5 remains empty.  The four hierarchy
    columns disclose unresolved attribution without reordering tender rows.
    """
    atom_file=registry.path(atomization_path);atom=json.loads(atom_file.read_text())
    ledger_file=registry.path(technical_evidence_ledger_path);ledger=json.loads(ledger_file.read_text())
    meta=_project(registry)
    if ledger.get('project_id')!=meta['project_id'] or ledger.get('source_sha256')!=meta['source_sha256']:
        raise ValueError('Technical evidence ledger belongs to another project/source')
    source_index_path=str(atom.get('source_index_path') or '').strip()
    source_index=registry.path(source_index_path)
    if not source_index.is_file() or atom.get('source_index_sha256')!=_sha(source_index):
        raise ValueError('Atomization source index is missing or stale')
    rows=[];history=[]
    for raw in atom.get('rows',[]):
        kind=raw.get('kind');source_ids=raw.get('source_ids') or []
        if kind=='heading':
            rows.append({'kind':'heading','text':str(raw.get('source_text') or raw.get('text') or ''),'source_ids':source_ids});continue
        if kind!='requirement':continue
        rid=str(raw.get('requirement_id') or '').strip();text=str(raw.get('source_text') or '')
        if not rid or not text or not source_ids:raise ValueError('Stage 7 requires bound source text for every requirement')
        applicable=str(raw.get('applicable_object') or 'object_unresolved').strip()
        if applicable=='object_unresolved':classification=['车型待业务锁定','对象归属待解析','对象归属待解析','条款核对']
        else:
            parts=[x.strip() for x in re.split(r'[/>→|]',applicable) if x.strip()]
            classification=(['车型待业务锁定']+parts+['条款核对']*4)[:4]
        rows.append({'kind':'requirement','requirement_id':rid,'classification':classification,
                     'sources':[{'id':source_ids[0],'quote':text}], 'source_joiner':'',
                     'removed_source_prefix':'','display_number':'','tender_requirement':text,
                     'bidder_response':'','deviation':''})
        history.append({'requirement_id':rid,'status':'not_applicable','reason':'当前项目尚无业务批准的拟供车型，不允许跨车型套用历史答案'})
    if not history:raise ValueError('No requirement rows are available for Stage 7')
    history_target=_next(registry,'analysis/step7','history-match-log','.json')
    _atomic_new(history_target,{'version':'history-match-log-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],'items':history})
    spec_target=_next(registry,'analysis/step7','eight-column-spec','.json')
    spec={'version':'deta-eight-column-internal-review-v3','atomization_path':atomization_path,
          'source_index_path':source_index_path,'history_match_path':str(history_target.relative_to(registry.root)),
          'evidence_batch_paths':[x['path'] for x in ledger.get('batch_results',[])],'rows':rows}
    _atomic_new(spec_target,spec)
    version=_next_stage7_composite_version(registry)
    rendered=registry.tools['build_eight_column_internal_review'].handler(str(spec_target.relative_to(registry.root)),f'eight-column-internal-review-v{version}')
    return {'eight_column_docx':rendered['outputs']['docx'],'eight_column_xlsx':rendered['outputs']['xlsx'],
            'eight_column_manifest':rendered['manifest'],'eight_column_traceability':rendered['traceability_path'],
            'history_match_log':str(history_target.relative_to(registry.root)),'spec_path':str(spec_target.relative_to(registry.root)),
            'requirement_rows':len(history),'historical_answers_filled':0,'manual_columns_written':False}


def build_stage8_baseline(registry,stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path):
    """Compute Stage 8 gates from bound files; semantic authority remains flags-only."""
    manifest_file=registry.path(stage4_manifest_path);manifest=json.loads(manifest_file.read_text())
    facts_file=registry.path(manifest.get('project_facts_path',''));docx=registry.path(manifest.get('filled_docx_path',''))
    facts=json.loads(facts_file.read_text());checks=[]
    for fact in facts.get('facts',[]):
        value=fact.get('value')
        if fact.get('state') not in {'source_verified','business_provided','derived_verified'} or value in (None,''):continue
        value=str(value)
        checks.append({'check_id':'FACT-'+fact['field_code'],'subject':fact.get('label') or fact['field_code'],
                       'left':{'path':str(facts_file.relative_to(registry.root)),'sha256':_sha(facts_file),'physical_page':None,
                               'location':'facts/'+fact['field_code'],'quote':value,'value':value},
                       'right':{'path':str(docx.relative_to(registry.root)),'sha256':_sha(docx),'physical_page':None,
                                'location':'基础字段表','quote':value,'value':value},'not_applicable_reason':''})
    if not checks:checks=[{'check_id':'FACT-NONE','subject':'已知项目字段','not_applicable_reason':'当前阶段没有可用于跨文件比对的可信已知字段'}]
    cross_target=_next(registry,'analysis/step8','cross-document-review','.json')
    cross=build_cross_document_review(registry,checks,str(cross_target.relative_to(registry.root)))
    quality_target=_next(registry,'analysis/step8','content-quality-review','.json')
    quality=build_deterministic_quality_review(registry,stage4_manifest_path,technical_evidence_ledger_path,
                                                eight_column_docx_path,eight_column_xlsx_path,cross['path'],[],
                                                str(quality_target.relative_to(registry.root)))
    return {'cross_document_review':cross['path'],'content_quality_review':quality['path'],
            'checks':cross['checks'],'issues':quality['issues'],'semantic_authority':'flags_only_no_pass_award'}


def build_stage9_baseline(registry,commercial_docx_path,eight_column_docx_path,eight_column_xlsx_path):
    """Create and publish the exact three website outputs plus an internal PDF."""
    from docx import Document
    source=registry.path(commercial_docx_path)
    if source.suffix.lower()!='.docx' or not source.is_file():raise ValueError('Stage 5 commercial DOCX is required')
    candidate=_next(registry,'analysis/step9','final-bid-candidate','.docx');shutil.copy2(source,candidate)
    document=Document(candidate)
    # Slot identifiers and source-location labels belong to the internal
    # ledgers.  They must never leak into a user-facing candidate.  Keep blank
    # response cells blank so missing business input remains truthful.
    slot_pattern=re.compile(r'^slot_[0-9a-f]{8,}$')
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if slot_pattern.fullmatch(cell.text.strip()):cell.text=''
    for paragraph_item in list(document.paragraphs):
        if re.fullmatch(r'来源：物理页\d+[，,].*',paragraph_item.text.strip()):
            paragraph_item._element.getparent().remove(paragraph_item._element)
    paragraph=document.paragraphs[0].insert_paragraph_before() if document.paragraphs else document.add_paragraph()
    paragraph.text='【Agent范围完成、待业务核对、不可递交】'
    if paragraph.runs:paragraph.runs[0].bold=True
    document.save(candidate)
    pdf=_next(registry,'analysis/step9','internal-final-render','.pdf')
    rendered=registry.tools['render_pdf'].handler(source=str(candidate.relative_to(registry.root)),target=str(pdf.relative_to(registry.root)))
    title=str(_project(registry).get('title') or '项目');safe_title=re.sub(r'[\\/:*?"<>|\x00-\x1f]+','_',title).strip(' .')[:80] or '项目'
    published=registry.tools['publish_final_website_outputs'].handler(
        final_bid_candidate=str(candidate.relative_to(registry.root)),
        eight_column_docx_candidate=eight_column_docx_path,
        eight_column_xlsx_candidate=eight_column_xlsx_path,
        internal_render_pdf_path=str(pdf.relative_to(registry.root)),
        final_bid_filename=safe_title+'-完整标书-Agent范围完成.docx',
        eight_column_docx_filename=safe_title+'-八列内部核对表.docx',
        eight_column_xlsx_filename=safe_title+'-八列内部核对表.xlsx')
    return {'final_bid_docx':published['final_bid_docx'],'eight_column_docx':published['eight_column_docx'],
            'eight_column_xlsx':published['eight_column_xlsx'],'internal_render_pdf':published['internal_render_pdf'],
            'output_manifest':published['output_manifest'],'candidate_docx':str(candidate.relative_to(registry.root)),
            'render_result':rendered,'website_output_count':3,'submission_ready':False}


def build_quality_review(registry,gates,semantic_findings,target):
    names={'known_fields','evidence','object_parameter','cross_document','manual_column_boundary'}
    if set(gates)!=names:raise ValueError('Exactly five content gates are required')
    normalized={}
    for name in sorted(names):
        value=gates[name]
        status=value.get('status')
        if status not in {'pass','issues_disclosed'}:raise ValueError(name+': invalid status')
        method=value.get('method')
        if name=='object_parameter' and method not in {'deterministic','hybrid_rule_model_flags_only'}:
            raise ValueError('Semantic model cannot independently award a pass')
        normalized[name]={'status':status,'method':method,'issues':value.get('issues',[]),'evidence':value.get('evidence',[])}
    if not isinstance(semantic_findings,list):raise ValueError('semantic_findings must be an array')
    payload={'version':'content-quality-review-v1','gates':normalized,'semantic_findings':semantic_findings,
             'semantic_authority':'flags_only_no_pass_award','submission_ready':False}
    result=_write(registry,target,payload,'analysis/step8/')
    return {**result,'issues':sum(len(x['issues']) for x in normalized.values())+len(semantic_findings)}


def build_deterministic_quality_review(registry,stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path,cross_document_review_path,semantic_findings,target):
    """Recompute Stage 8 gates from prior server-bound artifacts.

    Semantic findings remain flags only; callers cannot submit pass statuses.
    """
    from docx import Document
    from openpyxl import load_workbook
    refs={name:registry.path(path) for name,path in {
        'stage4_manifest':stage4_manifest_path,'technical_evidence_ledger':technical_evidence_ledger_path,
        'eight_column_docx':eight_column_docx_path,'eight_column_xlsx':eight_column_xlsx_path,
        'cross_document_review':cross_document_review_path}.items()}
    if any(not x.is_file() for x in refs.values()):raise ValueError('Stage 8 source artifact is missing')
    stage4=json.loads(refs['stage4_manifest'].read_text());technical=json.loads(refs['technical_evidence_ledger'].read_text());cross=json.loads(refs['cross_document_review'].read_text())
    if stage4.get('version')!='stage4-evidence-manifest-v1' or technical.get('version')!='technical-evidence-ledger-v1' or cross.get('version')!='cross-document-review-v1':raise ValueError('Unsupported Stage 8 source artifact version')
    known_issues=[]
    if stage4.get('fill_rate')!=1 or stage4.get('filled_slots')!=stage4.get('expected_fill_slots'):known_issues.append('可信已知字段未填满全部允许槽位')
    evidence_issues=[];object_issues=[]
    for row in technical.get('items',[]):
        conclusion=row.get('machine_conclusion')
        if conclusion in {'不可判定','无适用证据','证据冲突'}:evidence_issues.append(f"{row.get('requirement_id')}:{conclusion}")
        if conclusion in {'正偏离','无偏离','负偏离'} and not row.get('comparison'):object_issues.append(f"{row.get('requirement_id')}:结论缺少结构化比较")
    headers=['车型','子系统模块','部件模块','功能模块','标准车型答案（历史投标响应）','本次招标要求','投标人响应','偏离说明']
    doc=Document(refs['eight_column_docx']);manual_issues=[]
    if len(doc.tables)!=1 or [x.text for x in doc.tables[0].rows[0].cells]!=headers:manual_issues.append('DOCX八列表结构不正确')
    elif any(r.cells[6].text or r.cells[7].text for r in doc.tables[0].rows[1:]):manual_issues.append('DOCX第7或第8列非空')
    book=load_workbook(refs['eight_column_xlsx'],read_only=False,data_only=False)
    try:
        if '八列内部核对表' not in book.sheetnames:manual_issues.append('XLSX八列表工作表缺失')
        else:
            sheet=book['八列内部核对表']
            if [sheet.cell(1,c).value for c in range(1,9)]!=headers:manual_issues.append('XLSX八列表结构不正确')
            elif any(sheet.cell(r,7).value not in (None,'') or sheet.cell(r,8).value not in (None,'') for r in range(2,sheet.max_row+1)):manual_issues.append('XLSX第7或第8列非空')
    finally:book.close()
    cross_issues=[f"{x.get('check_id')}:{x.get('status')}" for x in cross.get('checks',[]) if x.get('status') in {'conflict_disclosed','missing_disclosed'}]
    if not isinstance(semantic_findings,list):raise ValueError('semantic_findings must be an array')
    gates={
      'known_fields':{'status':'issues_disclosed' if known_issues else 'pass','method':'deterministic','issues':known_issues,'evidence':[stage4_manifest_path]},
      'evidence':{'status':'issues_disclosed' if evidence_issues else 'pass','method':'deterministic','issues':evidence_issues,'evidence':[technical_evidence_ledger_path]},
      'object_parameter':{'status':'issues_disclosed' if object_issues or semantic_findings else 'pass','method':'hybrid_rule_model_flags_only','issues':object_issues,'evidence':[technical_evidence_ledger_path]},
      'cross_document':{'status':'issues_disclosed' if cross_issues else 'pass','method':'deterministic','issues':cross_issues,'evidence':[cross_document_review_path]},
      'manual_column_boundary':{'status':'issues_disclosed' if manual_issues else 'pass','method':'deterministic','issues':manual_issues,'evidence':[eight_column_docx_path,eight_column_xlsx_path]}}
    bindings={name:{'path':str(path.relative_to(registry.root)),'sha256':_sha(path)} for name,path in refs.items()}
    payload={'version':'content-quality-review-v2','gates':gates,'semantic_findings':semantic_findings,'semantic_authority':'flags_only_no_pass_award',
             'source_bindings':bindings,'submission_ready':False}
    result=_write(registry,target,payload,'analysis/step8/')
    return {**result,'issues':sum(len(x['issues']) for x in gates.values())+len(semantic_findings)}


def build_fill_evidence_manifest(registry,project_facts_path,qualification_ledger_path,filled_docx_path,fills,evidence_placements,target):
    facts_file=registry.path(project_facts_path);qualification_file=registry.path(qualification_ledger_path);docx=registry.path(filled_docx_path)
    facts=json.loads(facts_file.read_text());qualification=json.loads(qualification_file.read_text());meta=_project(registry)
    for data,name in ((facts,'project facts'),(qualification,'qualification ledger')):
        if data.get('project_id')!=meta['project_id'] or data.get('source_sha256')!=meta['source_sha256']:raise ValueError(name+' belongs to another project/source')
    if docx.suffix.lower()!='.docx' or not docx.is_file():raise ValueError('A real filled DOCX is required')
    from docx import Document
    document=Document(docx);text='\n'.join([p.text for p in document.paragraphs]+[c.text for t in document.tables for r in t.rows for c in r.cells])
    fact_map={x['field_code']:x for x in facts.get('facts',[])};expected={(x['field_code'],x['slot_id']) for x in facts.get('auto_fill_expected_slots',[])}
    actual=set();normalized_fills=[]
    for item in fills:
        key=(item.get('field_code'),item.get('slot_id'))
        if key in actual or key not in expected:raise ValueError('Fill record is duplicated or outside the trusted denominator')
        fact=fact_map[key[0]];value=item.get('value')
        if value!=fact.get('value'):raise ValueError('Filled value differs from the trusted project fact: '+key[0])
        if str(value) not in text:raise ValueError('Filled value is not present in the real DOCX: '+key[0])
        actual.add(key);normalized_fills.append({'field_code':key[0],'slot_id':key[1],'value':value,'state':fact['state'],'source_count':len(fact.get('sources',[]))})
    if actual!=expected:raise ValueError('Known trusted fields were not filled into every allowed current slot')
    qual_map={x['requirement_id']:x for x in qualification.get('items',[])};placement_keys=set();normalized_placements=[]
    for item in evidence_placements:
        rid=item.get('requirement_id');index=item.get('evidence_index');row=qual_map.get(rid)
        if row is None or type(index) is not int or index<0 or index>=len(row.get('evidence',[])):raise ValueError('Qualification evidence placement points outside the ledger')
        key=(rid,index)
        if key in placement_keys:raise ValueError('Qualification evidence placement is duplicated')
        location=str(item.get('document_location') or '').strip()
        if not location:raise ValueError('Qualification evidence placement requires a document location')
        placement_keys.add(key);evidence=row['evidence'][index];normalized_placements.append({'requirement_id':rid,'evidence_index':index,'document_location':location,'source_path':evidence['path'],'source_sha256':evidence['sha256'],'physical_page':evidence['physical_page']})
    expected_placements={(x['requirement_id'],i) for x in qualification.get('items',[]) if x.get('status')=='met' for i,_ in enumerate(x.get('evidence',[]))}
    if not expected_placements.issubset(placement_keys):raise ValueError('A qualification marked met has evidence that was not placed in the filled DOCX')
    payload={'version':'stage4-evidence-manifest-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],
             'project_facts_path':project_facts_path,'project_facts_sha256':_sha(facts_file),'qualification_ledger_path':qualification_ledger_path,'qualification_ledger_sha256':_sha(qualification_file),
             'filled_docx_path':filled_docx_path,'filled_docx_sha256':_sha(docx),'fills':normalized_fills,'expected_fill_slots':len(expected),'filled_slots':len(actual),'fill_rate':1,
             'qualification_evidence_placements':normalized_placements,'required_qualification_placements':len(expected_placements)}
    result=_write(registry,target,payload,'analysis/step4/')
    return {**result,'filled_slots':len(actual),'qualification_evidence_placements':len(normalized_placements)}


def build_technical_evidence_ledger(registry,evidence_queue_path,batch_result_paths,target):
    queue_file=registry.path(evidence_queue_path);queue=json.loads(queue_file.read_text());meta=_project(registry)
    if queue.get('project_id')!=meta['project_id'] or queue.get('source_sha256')!=meta['source_sha256']:raise ValueError('Evidence queue belongs to another project/source')
    expected=[x['task_id'] for x in queue.get('tasks',[])];collected=[];batch_bindings=[]
    for rel in batch_result_paths:
        path=registry.path(rel);data=json.loads(path.read_text())
        if data.get('version')!='evidence-batch-result-v1' or data.get('project_id')!=meta['project_id'] or data.get('source_sha256')!=meta['source_sha256']:raise ValueError('Evidence batch belongs to another project/source')
        if data.get('all_items_visited') is not True or data.get('manual_response_or_deviation_cells_written') is not False:raise ValueError('Evidence batch is incomplete or crossed the manual-column boundary')
        request_ids=data.get('request_task_ids',[]);result_ids=[x.get('task_id') for x in data.get('results',[])]
        if request_ids!=result_ids:raise ValueError('Evidence batch result order differs from its request')
        collected.extend(data.get('results',[]));batch_bindings.append({'path':rel,'sha256':_sha(path),'task_ids':request_ids})
    if [x.get('task_id') for x in collected]!=expected or len(expected)!=len(set(expected)):raise ValueError('Evidence batches must cover every queued task exactly once in queue order')
    rows=[]
    for item in collected:
        rid=item['task_id'];result=item.get('result') or {};status=result.get('evaluation_status');conclusion=result.get('deviation_conclusion')
        if item.get('item_status')=='item_error':machine='不可判定';reason=item.get('error','item_error')
        elif conclusion in {'正偏离','无偏离','负偏离'} and result.get('deviation_conclusion_allowed') is True:machine=conclusion;reason=result.get('decision_basis','')
        elif status=='no_applicable_evidence':machine='无适用证据';reason=result.get('gap_reason','')
        elif status in {'evidence_conflict','conflict'}:machine='证据冲突';reason=status
        else:machine='不可判定';reason=status or 'no locked deviation conclusion'
        relation=result.get('mathematical_relation');comparison=relation if machine in {'正偏离','无偏离','负偏离'} else None
        rows.append({'requirement_id':rid,'machine_conclusion':machine,'comparison':comparison,'reason':reason,
                     'evidence':result.get('original_page_verification') or result.get('evidence_verification'),
                     'applicability':result.get('applicability'),'locked_configuration':result.get('locked_configuration'),
                     'manual_response_written':False,'manual_deviation_written':False})
    payload={'version':'technical-evidence-ledger-v1','project_id':meta['project_id'],'source_sha256':meta['source_sha256'],
             'evidence_queue_path':evidence_queue_path,'evidence_queue_sha256':_sha(queue_file),'batch_results':batch_bindings,
             'items':rows,'item_count':len(rows),'manual_columns_written':False,
             'conclusion_counts':{x:sum(r['machine_conclusion']==x for r in rows) for x in ['正偏离','无偏离','负偏离','不可判定','无适用证据','证据冲突']}}
    result=_write(registry,target,payload,'analysis/step6/')
    return {**result,'items':len(rows),'conclusion_counts':payload['conclusion_counts']}


def build_cross_document_review(registry,checks,target):
    rows=[];seen=set()
    if not checks:raise ValueError('Cross-document checks required')
    for raw in checks:
        ident=str(raw.get('check_id') or '').strip()
        if not ident or ident in seen:raise ValueError('Cross-document check_id must be unique')
        seen.add(ident);reason=str(raw.get('not_applicable_reason') or '').strip()
        if reason:
            rows.append({'check_id':ident,'subject':str(raw.get('subject') or ''),'status':'not_applicable','left':None,'right':None,'reason':reason});continue
        left=_source(registry,raw.get('left') or {});right=_source(registry,raw.get('right') or {})
        left_value=raw.get('left',{}).get('value');right_value=raw.get('right',{}).get('value')
        if left_value in (None,'') or right_value in (None,''):status='missing_disclosed'
        elif str(left_value).strip()==str(right_value).strip():status='consistent'
        else:status='conflict_disclosed'
        rows.append({'check_id':ident,'subject':str(raw.get('subject') or ''),'status':status,
                     'left':{**left,'value':left_value},'right':{**right,'value':right_value},'reason':''})
    result=_write(registry,target,{'version':'cross-document-review-v1','checks':rows,'status_counts':{x:sum(r['status']==x for r in rows) for x in ['consistent','conflict_disclosed','missing_disclosed','not_applicable']}},'analysis/step8/')
    return {**result,'checks':len(rows),'conflicts':sum(x['status']=='conflict_disclosed' for x in rows)}


def install_workflow_ledger_tools(registry):
    source={'type':'object','properties':{'path':STRING,'sha256':STRING,'physical_page':{},'location':STRING,'quote':STRING},'required':['path','sha256','physical_page','location','quote'],'additionalProperties':True}
    registry.add('a_finalize_stage2_baseline','服务器从已完成的逐页招标分析直接推导technical物理页清单，索引锁定原件，并一次生成逐字保守条款原子化JSON和六列内部审查XLSX；不让模型回读大数组、猜页码或手抄原文。',schema({'tender_analysis_path':STRING}),lambda tender_analysis_path:build_stage2_finalize(registry,tender_analysis_path),True)
    registry.add('build_qualification_ledger','建立逐条资格账本；只有绑定原件物理页且适用性通过的证据才能标记met。缺失、冲突和动态时点事项记录后继续。',schema({'items':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda items,target:build_qualification(registry,items,target),True)
    registry.add('a_build_stage4_baseline','服务器直接读取招标分析和第3步骨架，填写当前原件可复核的项目名称/编号；投标人及资格证据不足时如实记为missing并继续，一次生成第4步四项候选产物。',schema({'tender_analysis_path':STRING,'skeleton_bundle_path':STRING}),lambda tender_analysis_path,skeleton_bundle_path:build_stage4_baseline(registry,tender_analysis_path,skeleton_bundle_path),True)
    registry.add('a_build_stage5_baseline','服务器直接读取招标分析和第4步已填DOCX，保守建立评分与承诺账本：主观项不自授分、价格未批准不计算、承诺未经业务批准不进入正文，并一次生成第5步四项候选产物。',schema({'tender_analysis_path':STRING,'filled_docx_path':STRING}),lambda tender_analysis_path,filled_docx_path:build_stage5_baseline(registry,tender_analysis_path,filled_docx_path),True)
    registry.add('a_build_stage6_gap_baseline','当当前项目尚无业务批准的拟供车型与成套报告时，服务器不猜选型，而是生成明确缺口的不可变选型锁，访问全部原子条款并一次完成第6步队列、批次结果和技术证据账本。',schema({'atomization_path':STRING}),lambda atomization_path:build_stage6_gap_baseline(registry,atomization_path),True)
    registry.add('a_build_stage7_gap_baseline','当第6步明确缺少业务批准选型时，服务器从同一原子化数据对象生成八列内部核对表DOCX/XLSX和逐行追溯JSON；历史答案不跨车型套用，第5、7、8列保持空白，条款顺序不变。',schema({'atomization_path':STRING,'technical_evidence_ledger_path':STRING}),lambda atomization_path,technical_evidence_ledger_path:build_stage7_gap_baseline(registry,atomization_path,technical_evidence_ledger_path),True)
    registry.add('a_build_stage8_baseline','服务器从第4步字段证据清单、第6步证据账本和第7步八列DOCX/XLSX重新计算跨文件一致性与五类内容门禁；缺项如实披露，语义模型不能独立授予通过。',schema({'stage4_manifest_path':STRING,'technical_evidence_ledger_path':STRING,'eight_column_docx_path':STRING,'eight_column_xlsx_path':STRING}),lambda stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path:build_stage8_baseline(registry,stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path),True)
    registry.add('a_build_stage9_baseline','服务器从第5步候选标书和第7步八列表生成带有“Agent范围完成、待业务核对、不可递交”标识的最终候选DOCX，内部渲染PDF，并原子发布网站仅有的三项产物。',schema({'commercial_docx_path':STRING,'eight_column_docx_path':STRING,'eight_column_xlsx_path':STRING}),lambda commercial_docx_path,eight_column_docx_path,eight_column_xlsx_path:build_stage9_baseline(registry,commercial_docx_path,eight_column_docx_path,eight_column_xlsx_path),True)
    registry.add('build_scoring_ledger','建立动态评分账本。主观分只记录证据完整度和可争取上限，价格未批准时不得计算价格分。',schema({'items':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda items,target:build_scoring(registry,items,target),True)
    registry.add('build_commitments_ledger','结构化保存承诺的起算条件、时限、范围、责任和例外；不自动批准更优承诺。',schema({'items':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda items,target:build_commitments(registry,items,target),True)
    registry.add('build_stage5_commercial_manifest','在评分、承诺账本和商务评分DOCX完成后调用。服务器仅允许有来源的评分内容和已由业务批准的承诺进入正文，并要求逐项定位、完整覆盖和哈希绑定。',schema({'scoring_ledger_path':STRING,'commitments_ledger_path':STRING,'commercial_docx_path':STRING,'placements':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda scoring_ledger_path,commitments_ledger_path,commercial_docx_path,placements,target:build_stage5_commercial_manifest(registry,scoring_ledger_path,commitments_ledger_path,commercial_docx_path,placements,target),True)
    registry.add('create_evidence_task_queue','根据全部原子条款和真实选型锁建立持久化证据任务队列，每批最多20条；单项失败须记录并继续。',schema({'atomization_path':STRING,'configuration_lock_path':STRING,'configuration_lock_version':STRING,'target':STRING}),lambda atomization_path,configuration_lock_path,configuration_lock_version,target:build_evidence_queue(registry,atomization_path,configuration_lock_path,configuration_lock_version,target),True)
    registry.add('build_content_quality_review','建立五类内容门禁。语义模型仅可提出问题，不能独立授予满足、无偏离或通过。',schema({'gates':{'type':'object'},'semantic_findings':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda gates,semantic_findings,target:build_quality_review(registry,gates,semantic_findings,target),True)
    registry.add('build_deterministic_content_quality_review','从第4步填充证据清单、第6步技术证据账本、第7步八列表DOCX/XLSX和跨文件核对表重新计算五类内容门禁。模型只提交语义问题清单，不能提交或授予pass。',schema({'stage4_manifest_path':STRING,'technical_evidence_ledger_path':STRING,'eight_column_docx_path':STRING,'eight_column_xlsx_path':STRING,'cross_document_review_path':STRING,'semantic_findings':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path,cross_document_review_path,semantic_findings,target:build_deterministic_quality_review(registry,stage4_manifest_path,technical_evidence_ledger_path,eight_column_docx_path,eight_column_xlsx_path,cross_document_review_path,semantic_findings,target),True)
    registry.add('build_stage4_evidence_manifest','在项目事实和资格账本完成、真实DOCX已填写并装配资格证据后调用。服务器核对可信字段已填入每个允许槽位、值与事实一致、所有met证据均有文档位置，并哈希绑定filled_docx。',schema({'project_facts_path':STRING,'qualification_ledger_path':STRING,'filled_docx_path':STRING,'fills':{'type':'array','items':{'type':'object'}},'evidence_placements':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda project_facts_path,qualification_ledger_path,filled_docx_path,fills,evidence_placements,target:build_fill_evidence_manifest(registry,project_facts_path,qualification_ledger_path,filled_docx_path,fills,evidence_placements,target),True)
    registry.add('build_technical_evidence_ledger','汇总服务器持久化的证据批次结果，要求按队列顺序恰好覆盖全部任务，并只接受锁定配置门禁实际授予的正偏离/无偏离/负偏离；其余稳定归类为不可判定、无适用证据或证据冲突。机器结论不写八列表第7/8列。',schema({'evidence_queue_path':STRING,'batch_result_paths':{'type':'array','items':STRING},'target':STRING}),lambda evidence_queue_path,batch_result_paths,target:build_technical_evidence_ledger(registry,evidence_queue_path,batch_result_paths,target),True)
    registry.add('build_cross_document_review','由服务器比较DOCX、XLSX、PDF或账本中的明确值。每侧文件、哈希、页码/位置和值必须真实存在；状态由工具计算为一致、冲突披露、缺失披露或不适用，模型不能自行授予一致。',schema({'checks':{'type':'array','items':{'type':'object'}},'target':STRING}),lambda checks,target:build_cross_document_review(registry,checks,target),True)
