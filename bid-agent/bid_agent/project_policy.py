"""Project-specific policy consistency, without legacy blank/performance defaults.

This is an independent diagnostic, not a replacement for evidence verification
or the workflow's delivery gate. Authority references require separate review.
"""


def audit_policy(policy):
    errors=[];open_items=[]
    if not isinstance(policy,dict):
        return {'errors':['Policy must be an object'],'quality_status':'not_verified'}
    if not policy.get('project_id'):errors.append('Explicit project_id required')
    method=policy.get('evaluation_method')
    if method not in {'qualitative','numeric'}:errors.append('Explicit project evaluation method required')
    performance=policy.get('performance')
    if not isinstance(performance,dict):
        errors.append('Explicit performance state required; never assume not submitted')
    else:
        for key in ('required','submitted'):
            if key not in performance or (performance[key] is not None and type(performance[key]) is not bool):
                errors.append('performance.'+key+' must be explicit boolean or null')
            elif performance[key] is None:open_items.append('performance.'+key+' is unresolved')
        status=performance.get('status')
        if status not in {'met','not_met','unresolved','not_applicable'}:errors.append('Explicit performance status required')
        if status=='met':
            if performance.get('required') is not True or performance.get('submitted') is not True:
                errors.append('Performance cannot be met without required/submitted both true')
            if not performance.get('evidence_refs'):errors.append('Met assertion needs evidence references')
            open_items.append('Performance evidence identity, scope and sufficiency require independent verification')
        if status=='not_applicable' and performance.get('required') is not False:
            errors.append('Not-applicable performance requires explicit not-required basis')
        if not performance.get('requirement_refs'):errors.append('Performance requirement state needs current-source references')
        points=performance.get('claimed_points')
        if method=='qualitative' and points is not None:
            errors.append('Qualitative evaluation cannot inherit invented numerical points')
        if method=='numeric' and points is not None:
            maximum=performance.get('maximum_points')
            if type(points) not in (int,float) or type(maximum) not in (int,float) or not 0<=points<=maximum:
                errors.append('Numerical points must be bounded by sourced maximum')
    forms=policy.get('forms')
    if not isinstance(forms,list):errors.append('Explicit form policies required; no global three-form blank default')
    else:
        ids=set()
        for form in forms:
            if not isinstance(form,dict):errors.append('Form policy must be an object');continue
            ident=form.get('form_id')
            if not isinstance(ident,str) or not ident or ident in ids:errors.append('Unique form_id required')
            else:ids.add(ident)
            mode=form.get('mode')
            if mode not in {'blank','fill_from_evidence','not_applicable','unresolved'}:errors.append('Explicit form mode required')
            if mode=='unresolved':open_items.append(str(ident)+': form policy unresolved')
            if mode!='unresolved' and not form.get('authority_refs'):
                errors.append(str(ident)+': current procurement or explicit project decision reference required')
            if mode=='blank' and form.get('procurement_requires_filling') is True:
                open_items.append(str(ident)+': blank policy conflicts with procurement requirement; submission readiness cannot be granted')
    return {'errors':errors,'open_items':open_items,'structurally_consistent':not errors,
            'quality_status':'not_verified','scope':'Explicit project-policy consistency only. References, actual form contents and business acceptance are not verified here.'}


def install_policy_tools(registry):
    from .tools import schema, STRING
    import json
    import hashlib

    def check_project_policy(path):
        source=registry.path(path)
        if source.stat().st_size>500000:raise ValueError('Policy exceeds size limit')
        raw=source.read_bytes();policy=json.loads(raw)
        result=audit_policy(policy)
        return {'path':path,'sha256':hashlib.sha256(raw).hexdigest(),**result}

    registry.add('check_project_policy',
                 '检查项目规则声明的一致性，禁止默认三类表留空或不提交业绩。JSON需project_id、evaluation_method(qualitative/numeric)、performance(required/submitted可为bool或null,status,requirement_refs,evidence_refs,claimed_points)、forms(form_id,mode,authority_refs)。不验证证据内容或授予递交通过。',
                 schema({'path':STRING}),check_project_policy)
