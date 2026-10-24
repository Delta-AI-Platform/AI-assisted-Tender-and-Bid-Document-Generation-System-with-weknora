"""Deterministic business checks; no semantic text rewriting or default compliance."""

def match_history(records, model, aliases=()):
    if not model or not model.strip():
        raise ValueError('An explicit model is required')
    allowed = {model, *aliases}
    matches = [r for r in records if r.get('model') in allowed]
    return {'matches': matches, 'selection': None,
            'status': 'multiple_candidates' if len(matches) > 1 else 'candidate' if matches else 'missing'}


def check_coverage(source_rows, response_rows):
    errors = []
    source_ids = [r['id'] for r in source_rows]
    response_ids = [r.get('id') for r in response_rows]
    if len(set(source_ids)) != len(source_ids):
        errors.append('Source contains duplicate IDs')
    if response_ids != source_ids:
        errors.append('Requirement sequence or completeness differs from source')
    for index, (source, response) in enumerate(zip(source_rows, response_rows)):
        if response.get('requirement') != source['requirement']:
            errors.append(f'Row {index + 1}: requirement text differs from source')
        if response.get('deviation') in ('无偏离', '正偏离') and not response.get('evidence'):
            errors.append(f'Row {index + 1}: compliance claim has no evidence reference')
    return {'ok': not errors, 'errors': errors, 'scope': 'exact_source_coverage_and_reference_presence',
            'evidence_content_verified': False}


def numbered_requirements(text, expected_count):
    import re
    matches=list(re.finditer(r'（([0-9]+)）',text))
    numbers=[int(m.group(1)) for m in matches]
    if numbers != list(range(1,expected_count+1)):
        raise ValueError('Source requirement numbering is incomplete or ambiguous')
    result=[]
    for i,match in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(text)
        # Printed footer is not part of the last requirement.
        body=text[match.start():end].strip()
        body=re.sub(r'\n\s*[—\-]?\s*\d+\s*[—\-]?\s*$', '', body)
        result.append(body)
    return result
