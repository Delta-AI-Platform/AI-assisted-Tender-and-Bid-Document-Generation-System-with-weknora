"""Bind table expansion to a complete JSON record snapshot, not model transcription."""
import hashlib,json
from pathlib import Path
try:
 from .expand_template_table import expand_template_table
except ImportError:
 from expand_template_table import expand_template_table

def expand_table_from_records(source,source_sha256,target,table_index,header_rows,prototype_row,sequence_column,records_file,records_sha256):
 raw=Path(records_file).read_bytes();actual=hashlib.sha256(raw).hexdigest()
 if actual!=records_sha256:raise ValueError('Records file version mismatch')
 def unique(pairs):
  result={}
  for k,v in pairs:
   if k in result:raise ValueError('Duplicate JSON key')
   result[k]=v
  return result
 data=json.loads(raw,object_pairs_hook=unique)
 records=data.get('records') if isinstance(data,dict) else data
 if not isinstance(records,list):raise ValueError('Expected JSON array or object with records array')
 result=expand_template_table(source,source_sha256,target,table_index,header_rows,prototype_row,sequence_column,records)
 return {**result,'records_file_sha256':actual,'records_source_mode':'exact JSON snapshot; no model transcription','limitations':['Source values are preserved, not commercially approved','Rendered review still required']}

def edit_table_cells_from_records(source, source_sha256, target, records_file, records_sha256):
 raw=Path(records_file).read_bytes();actual=hashlib.sha256(raw).hexdigest()
 if actual!=records_sha256:raise ValueError('Records file version mismatch')
 def unique(pairs):
  result={}
  for key,value in pairs:
   if key in result:raise ValueError('Duplicate JSON key')
   result[key]=value
  return result
 data=json.loads(raw,object_pairs_hook=unique)
 edits=data.get('edits') if isinstance(data,dict) else data
 if not isinstance(edits,list):raise ValueError('Expected JSON array or object with edits array')
 if any(not isinstance(e,dict) or set(e)!={'table','row','column','before','after'} for e in edits):
  raise ValueError('Each edit must have exactly table,row,column,before,after')
 try:
  from .table_edit import edit_table_cells
 except ImportError:
  from table_edit import edit_table_cells
 result=edit_table_cells(source,source_sha256,target,edits)
 return {**result,'records_file_sha256':actual,'records_source_mode':'exact JSON snapshot; no model transcription','limitations':['Each before value and source hash verified; source facts not approved','Rendered review still required']}
