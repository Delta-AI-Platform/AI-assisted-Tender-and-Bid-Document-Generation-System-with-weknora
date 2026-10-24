import json
import subprocess
from pathlib import Path
from .tools import schema, STRING

from .runtime import APP_PYTHON as PYTHON


def install_document_tools(registry):
    definitions = {
        'import_pdf_table': ('从PDF物理页提取可编辑表格，表序号从0开始，保留文字和合并单元格。include_surrounding_text=true时同时提取单表页标题与注释，多表/含图片页面拒绝；仅排除原印刷页脚。使用Noto Serif CJK SC替代字体，表格字号按源文字加权主字号取半磅、无文字格用10.5磅；左右内边距1磅；同基线表外文字用制表位恢复。源绘制的填写横线未自动复刻，必须渲染并与原页检查，不保证原排版完全相同。', {'source':STRING,'source_sha256':STRING,'page':{'type':'integer'},'table_index':{'type':'integer'},'include_surrounding_text':{'type':'boolean'},'target':STRING}),
        'capture_requirements': ('按页码和唯一的原文起止短句，从PDF直接提取要求到新JSON，免去手工复制长引文。每个selection须含requirement_id,start_page,end_page,start_text,end_text,category,target_section。起止短句仅允许空白/换行差异，文字数字标点必须一致，含末尾短句；保存引文仍为原PDF全文；程序只保证所选文字准确，不保证选取完整或分类正确。', {'source':STRING,'source_sha256':STRING,'target':STRING,'selections':{'type':'array','items':{'type':'object'}}}),
        'edit_table_cells': ('按源哈希、零起始表/行/列坐标及原值修改指定表格单元格，保留其他结构和包内部件；复杂内容拒绝粗暴改写。', {'source':STRING,'source_sha256':STRING,'target':STRING,'edits':{'type':'array','items':{'type':'object'}}}),
        'assemble_docx': ('将可编辑DOCX章节按顺序装配，保留源正文和图片。header_policy必须明确填master：全卷使用主文档页眉页脚。', {'master': STRING, 'sources': {'type': 'array', 'items': STRING}, 'target': STRING, 'header_policy': {'type':'string','enum':['master']}}),
        'append_pdf_evidence': ('仅在DOCX文末追加指定PDF完整原页作为证据图；不支持章节定位。需要放在章节内时，先制作独立证据片段，再用insert_docx的before/after定位插入未追加这些证据的主卷，禁止把文末追加视为位置要求已满足。pdf_sha256必须来自预期证据来源记录，不能用另一个PDF的哈希替代；页码只对绑定来源有效，哈希不证明内容适用性。保留原页内容及来源哈希，不生成签章；必须渲染后逐页检查。', {'source': STRING,'source_sha256': STRING,'pdf': STRING,'pdf_sha256': STRING,'pages': {'type':'array','items':{'type':'integer'}},'target': STRING}),
        'create_docx': ('从明确给出的段落、标题和表格创建可编辑 DOCX；不自动生成事实或偏离结论。',
                        {'target': STRING, 'blocks': {'type': 'array', 'description':'按原文顺序。每块必须用type字段；标题type=heading且level为1/2/3，不能写heading1或block_type。', 'items': {'type': 'object', 'properties': {'type': {'type':'string','enum':['paragraph','heading','page_break','table']}, 'text': STRING, 'level': {'type':'integer','enum':[1,2,3]}, 'rows': {'type':'array','items':{'type':'array','items':STRING}}}, 'required':['type'],'additionalProperties':False}}}),
        'format_docx': ('仅调整明确选定的已有分节页面尺寸及四边距（毫米），不改变字体、正文、原表或未选分节。sections每项必含section_index,width_mm,height_mm,top_mm,bottom_mm,left_mm,right_mm；必须重新渲染。', {'source': STRING, 'source_sha256': STRING, 'target': STRING, 'sections': {'type':'array','items':{'type':'object'}}}),
        'edit_docx': ('按来源哈希修改单文字运行段落。edits每项必须为block、before、after；block是read_document返回的正文块索引（从0开始，包括表格），不是纯段落序号。before为精确完整原文，after为明确新文；不能用old_text/new_text。混合格式拒绝替换；已有空BID_SLOT请先inspect_skill_document检查槽位，再用fill_empty_slots填写。',
                      {'source': STRING, 'source_sha256': STRING, 'target': STRING, 'edits': {'type': 'array', 'minItems': 1, 'items': schema({'block': {'type':'integer','minimum':0}, 'before':STRING, 'after':STRING})}}),
        'render_pdf': ('用独立 LibreOffice 配置渲染 DOCX 至新 PDF；渲染成功不代表视觉检查通过。',
                       {'source': STRING, 'target': STRING}),
        'assemble_pdf': ('按指定顺序装订 PDF 完整原页并返回来源哈希与页码映射；不是可编辑主文件装配。',
                         {'sources': {'type': 'array', 'items': STRING}, 'target': STRING}),
        'page_image': ('将 PDF 指定页渲染成 PNG。仅生成图片，不代表模型已经查看。',
                       {'source': STRING, 'page': {'type': 'integer'}, 'target': STRING}),
    }
    definitions['annotate_technical_workbook'] = ('对已生成内部技术对标表写入明确批注；按工作簿和记录文件双哈希绑定。JSON edits每项含requirement_id,column,before（空格原值为null）,after；仅允许列1/2/3/5/6，要求原文列4及证据索引受保护。按稳定ID定位，拒绝原值不符；不自动认可响应或偏离。', {'source':STRING,'source_sha256':STRING,'target':STRING,'records_file':STRING,'records_sha256':STRING})
    definitions['create_technical_workbook'] = ('将带稳定ID、原文quote及source_physical_pages的requirements JSON按原顺序转成内部技术对标空表。无标准车型报告时不造标准答案列；分类、响应、偏离留空。只验证输入原文保存，不证明完整性或语义拆项正确；超出Excel单格容量拒绝输出。', {'source':STRING,'source_sha256':STRING,'target':STRING})
    definitions['tag_docx_slots'] = ('为明确指定的真正空白段落或表格单元格添加BID_SLOT稳定标识。slots每项含slot_id、kind(paragraph/cell)、零起始paragraph或table/row/column。拒绝覆盖固定文字、复杂内容及合并格重复位置；标记不等于骨架冻结或质量通过。', {'source':STRING,'source_sha256':STRING,'target':STRING,'slots':{'type':'array','items':{'type':'object'}}})
    definitions['fill_empty_slots'] = ('仅向已有空BID_SLOT写入values对象中的明确文本。调用固定版本技能脚本和专用解释器，核对结构、其他包部件及逐字内容；拒绝未知位置和覆盖已填内容。文本事实仍需来源核验。', {'source':STRING,'source_sha256':STRING,'target':STRING,'values':{'type':'object'}})
    definitions['insert_docx'] = ('placement明确选择replace替换、before前插或after后插，before/after保留锚点原文。section_policy必须明确指定master或preserve_fragment；含图片片段必须preserve_fragment，保留其末页分节并恢复后续主卷尺寸。将fragment可编辑DOCX插入主文档唯一顶层段落marker处；只有placement=replace才删除锚点段落。两源均须哈希绑定，header_policy明确master。核对全正文顺序与图片保留；分页、节和表格外观仍需渲染核验。适用于原表在正确章节中归位，禁止把真实必需正文当作可删除占位。', {'source':STRING,'source_sha256':STRING,'fragment':STRING,'fragment_sha256':STRING,'marker':STRING,'target':STRING,'header_policy':{'type':'string','enum':['master']},'section_policy':{'type':'string','enum':['master','preserve_fragment']},'placement':{'type':'string','enum':['replace','before','after']}})
    definitions['set_docx_styles'] = ('仅修改指定Word样式的字体属性，正文及其他包部件字节不变。updates每项必填style_id（如Heading1/Heading2/Heading3/Normal），可填color(六位RGB)、font_name、size_pt(6-72半磅步长)、bold(bool)。会移除被覆盖属性的主题字体/颜色引用。直接格式可能覆盖样式；不调整封面位置、页边距或分页，仍需渲染。', {'source':STRING,'source_sha256':STRING,'target':STRING,'updates':{'type':'array','items':{'type':'object'}}})
    definitions['tag_inline_slots'] = ('冻结前为空白行内字段建立填写位置。slots每项含paragraph,slot_id,start,end,before；零起始字符范围只能为空或空白，单段仅支持单一简单文字运行；固定文字必须完整保留，标记不等于冻结。', {'source':STRING,'source_sha256':STRING,'target':STRING,'slots':{'type':'array','items':{'type':'object'}}})
    definitions['partition_sections'] = ('冻结前将单节、无图片和无槽位DOCX按明确范围分节。sections每项含end_body_index及width_mm,height_mm,top_mm,bottom_mm,left_mm,right_mm；按递增零起始正文块索引覆盖全部正文；表格后补分节承载段落，不能用于已冻结或原件图片卷。', {'source':STRING,'source_sha256':STRING,'target':STRING,'sections':{'type':'array','items':{'type':'object'}}})
    definitions['replace_table_layouts'] = ('固定单元格文字与合并结构完全相同时，用指定单表片段替换版式；replacements每项含table_index,fragment,fragment_sha256。修改源表布局须重新渲染，不更改未选表、正文或其他包部件。', {'source':STRING,'source_sha256':STRING,'target':STRING,'replacements':{'type':'array','items':{'type':'object'}}})
    definitions['xlsx_spec_tables'] = ('将指定XLSX行列原值转为可编辑DOCX表，按连续分组保留全局行序；拒绝公式和错误值。columns每项含column和width_mm；原始格式、图片不复制，不代表技术证明或业务确认。明确指定纸宽纸高及四边相同页边距毫米值。', {'source':STRING,'source_sha256':STRING,'target':STRING,'sheet':STRING,'header_row':{'type':'integer'},'start_row':{'type':'integer'},'end_row':{'type':'integer'},'group_column':{'type':'integer'},'columns':{'type':'array','items':{'type':'object'}},'title':STRING,'page_width_mm':{'type':'number'},'page_height_mm':{'type':'number'},'margin_mm':{'type':'number'}})
    definitions['expand_template_table'] = ('冻结前按原表样式扩展空白明细表。保留header_rows行表头，克隆prototype_row行样式，替换其余空白示例行；各索引从0开始，records必须为每格明确字符串。拒绝覆盖已有业务值和合并/复杂单元格，不生成价格、承诺或偏离结论。须重新渲染检查。', {'source':STRING,'source_sha256':STRING,'target':STRING,'table_index':{'type':'integer'},'header_rows':{'type':'integer'},'prototype_row':{'type':'integer'},'sequence_column':{'type':'integer'},'records':{'type':'array','items':{'type':'array','items':STRING}}})
    definitions['expand_table_from_records'] = ('从指纹绑定的JSON记录文件直接扩展并填写空白原表，不需模型抄写大数组。JSON为records数组或含records数组的对象；每行每格须为显式字符串。其余约束同expand_template_table，拒绝覆盖非空业务值和复杂单元格；只保证数据保存，非报价批准。', {'source':STRING,'source_sha256':STRING,'target':STRING,'table_index':{'type':'integer'},'header_rows':{'type':'integer'},'prototype_row':{'type':'integer'},'sequence_column':{'type':'integer'},'records_file':STRING,'records_sha256':STRING})
    definitions['repeat_docx_blocks'] = ('按源哈希和逐块精确文本绑定，保留原文并将正文段落/表格范围复制为copies套（含原套）。start_block/end_block为含端点的正文块索引；expected_texts是各块所有w:t拼接文字。只用于明确需重复的模板；拒绝图片、字段、分节、编号及标识内容。保留原表合并和样式，不填事实；须重新渲染。', {'source':STRING,'source_sha256':STRING,'target':STRING,'start_block':{'type':'integer'},'end_block':{'type':'integer'},'copies':{'type':'integer'},'expected_texts':{'type':'array','items':STRING}})
    definitions['edit_table_cells_from_records'] = ('直接执行哈希绑定JSON文件中的edits数组，不需模型抄写长数组。每项严格为table,row,column,before,after；各索引从0开始。同时绑定source_sha256和records_sha256，沿用逐格原值检查、合并别名拒绝及复杂内容保护，不自动认可业务事实。', {'source':STRING,'source_sha256':STRING,'target':STRING,'records_file':STRING,'records_sha256':STRING})
    definitions['remove_redundant_empty_section'] = ('精确删除一个冗余空分节段落：必须无正文/图片/字段等内容，前一段已结束分节，空节与后续节设置完全相同（仅忽略编辑rsid）。任何设置差异均拒绝；需哈希及真实block索引，渲染验证。', {'source':STRING,'source_sha256':STRING,'target':STRING,'block':{'type':'integer'}})
    definitions['set_paragraph_pagination'] = ('按源哈希和正文段落索引精确设置段前分页。edits每项严格含block、before（完整文字）、page_break_before布尔值；拒绝表格、分节和受保护段落。不改正文或表格；用于每人简历从新页开始，仍须渲染确认整份是否放得下。', {'source':STRING,'source_sha256':STRING,'target':STRING,'edits':{'type':'array','items':{'type':'object'}}})
    definitions['set_table_pagination'] = ('仅调整指定正文表格的行分页属性：前header_rows行在续页重复，keep_rows_together控制是否避免行内跨页。索引从0开始；不改内容、列宽、字号、业务值或其他包部件。行高超过整页时仍可能拆页，必须渲染查看续页。', {'source':STRING,'source_sha256':STRING,'target':STRING,'table_index':{'type':'integer','minimum':0},'header_rows':{'type':'integer','minimum':0},'keep_rows_together':{'type':'boolean'}})
    quote_parameters = {'workbook': STRING, 'pdf': STRING, 'start_page': {'type': 'integer'},
                        'end_page': {'type': 'integer'}, 'target': STRING}
    definitions['write_reconciliation'] = ('核对27列报价模板与PDF报价表，严格匹配顺序、型号、名称、图号；输出逐行来源报告，不修改报价。', quote_parameters)
    definitions['restore_quotation'] = ('从黄金PDF复原历史报价到新XLSX，仅填原模板空白K/L列；131行以实际模板为准，任何匹配缺失或歧义都拒绝输出；不是新报价审批。', quote_parameters)
    for name, (description, parameters) in definitions.items():
        def handler(_operation=name, **args):
            public = dict(args)
            target = registry.path(args['target'])
            extension = '.xlsx' if _operation in {'restore_quotation','create_technical_workbook','annotate_technical_workbook'} else '.json' if _operation in {'write_reconciliation','capture_requirements'} else '.docx' if _operation in {'create_docx', 'edit_docx', 'edit_table_cells', 'import_pdf_table', 'format_docx', 'assemble_docx', 'append_pdf_evidence', 'tag_docx_slots', 'fill_empty_slots','insert_docx','set_docx_styles','tag_inline_slots','partition_sections','replace_table_layouts','xlsx_spec_tables','expand_template_table','expand_table_from_records','set_table_pagination','repeat_docx_blocks','edit_table_cells_from_records','set_paragraph_pagination','remove_redundant_empty_section'} else '.png' if _operation == 'page_image' else '.pdf'
            if target.suffix.lower() != extension:
                raise ValueError('Output extension must be ' + extension)
            if target.exists(): raise FileExistsError('Output already exists; choose a new version')
            target.parent.mkdir(parents=True, exist_ok=True)
            args['target'] = str(target)
            if 'source' in args:
                args['source'] = str(registry.path(args['source']))
            for key in ('workbook', 'pdf', 'master','fragment','records_file'):
                if key in args: args[key] = str(registry.path(args[key]))
            if 'replacements' in args:
                args['replacements'] = [{**item,'fragment':str(registry.path(item['fragment']))} for item in args['replacements']]
            if 'sources' in args:
                if not args['sources']: raise ValueError('At least one source is required')
                args['sources'] = [str(registry.path(p)) for p in args['sources']]
            result = subprocess.run([PYTHON, str(Path(__file__).with_name('document_ops.py'))],
                                    input=json.dumps({'operation': _operation, 'args': args}),
                                    capture_output=True, text=True, timeout=150)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                raise ValueError(f'Document worker returned invalid output (exit {result.returncode}): {result.stderr[:500]}') from None
            if not data['ok']: raise ValueError(data['error'])
            receipt = {'operation': _operation, 'output': public['target'],
                       'input_paths': ([public['master']] if 'master' in public else []) + public.get('sources', []) + [public[k] for k in ('source','workbook','pdf','fragment','records_file') if k in public],
                       **data['data'], 'quality_status': 'not_verified'}
            with registry.store.connect() as conn:
                registry.store.event(conn, registry.run_id, 'artifact_created', receipt)
            return receipt
        registry.add(name, description, schema(parameters), handler, True)
