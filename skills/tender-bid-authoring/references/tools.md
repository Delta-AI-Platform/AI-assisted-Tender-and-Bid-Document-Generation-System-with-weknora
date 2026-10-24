# 通用工具契约

使用当前可用Python及python-docx；提取PDF书签另需pypdf。运行`docx_tools.py --help`查看参数。路径均作为独立shell参数安全传入，输出到项目工作目录，不把内部文本或个人信息公开。

## 统一Rulebase运行与追溯

十步项目必须先由`scripts/workflow_engine.py`建立并维护状态。`rulebase_runner.py`只产生单阶段确定性结果，不能独立授予阶段通过或交付资格。其结果必须使用`workflow_engine.py record-rulebase`登记，混合阶段还需登记当前Agent任务与结果，最后以`complete`提交阶段产物清单。完整状态、失效和交付规则见[十步强制工作流状态机](workflow-state-machine.md)。

Rulebase 1.4.0保留既有商务规则，并使用`docx_skeleton_contract`、`docx_skeleton_format_standard`、`docx_technical_blank_table`、`docx_requirement_coverage`、`docx_slot_fill`和`docx_structure_frozen`。第1步验证真实空白DOCX、权威版式、要求覆盖和技术空表；后续步骤验证槽位来源及冻结结构；工作流3.2在第9步交叉核对内容清单、实际槽位、技术响应和DOCX内附件部件，空骨架或仅靠清单声明不能升级为候选稿。

确定性阶段必须通过代码执行，不用模型口头声明代替。统一入口：

`python scripts/rulebase_runner.py PROJECT_STAGE_SPEC.json --out RUN_RESULT.json`

规范文件使用UTF-8 JSON，至少包含`project_id`、`stage`、`inputs`、`outputs`和`rules`。每个文件有稳定`id`和路径，可选预期哈希；每条规则有稳定`id`、`type`和`severity`。支持的基础规则包括文件存在、哈希一致、DOCX ZIP有效、必含/禁含文字、表格行列、锁定列原文、节设置一致、JSON集合一致和附件实际存在。

运行结果固定记录Rulebase版本、脚本SHA-256、任务规范SHA-256、UTC时间、输入/输出路径/体量/哈希、逐规则结果及`pass/blocked`状态。`severity=error`失败时退出码为2，候选不得升级为有效成果；告警可保留但必须进入阶段记录。规范和运行结果保存在项目工作目录，不进入应标文件。

专项工具仍可执行更细检查，但必须把其输入、版本、结果或输出哈希登记到同一阶段记录。Agent不得修改运行结果或把失败解释为通过；新增确定性规则优先扩展该入口或已登记专项工具，不散落为不可追溯的一次性命令。

## 空白骨架工具

`scripts/skeleton_tools.py`提供`build、inspect、freeze、validate、compare、fill`。`build`从版本化结构规范创建带固定封面/扉页、章节、原表式、材料标题和技术空表的商务及技术DOCX，可变位置内部以`BID_SLOT`内容控件定位；`freeze`保存每卷结构哈希；`compare`允许槽位内容自然扩页，但阻止槽位外标题、表格、分节、样式和页眉页脚变化。

招标提供复杂原表时先用当前Documents/PDF能力克隆或重建，再以槽位清单和`freeze`锁定；不得为了适配通用`build`规范而简化原表。每份骨架必须经Office渲染，渲染清单与`skeleton_id`及DOCX哈希绑定。命令和清单要求见[空白骨架、槽位和冻结结构](skeleton-first.md)。

## 权威库资产与召回

技术参数的首查入口是[技术响应](technical-response.md)所指定的长期维护工作簿。先用只读 `technical_report_lookup.py` 列出具体报告，再回看所选报告原页；本节权威库召回用于补查未覆盖字段和成熟应标表达，不能仅凭同车型命中覆盖报告锁定值。用户提供的旧横向表只作版式参考，不能覆盖报告级标准数据。

v1.38 的招标Word→内部Excel使用 `python scripts/technical_alignment.py build --source 技术要求.docx --start 技术要求标题 --scope 本项目车型范围.json --output 技术对标空表.xlsx --manifest source_manifest.json`；填充后用 `python scripts/technical_alignment.py validate --source 技术要求.docx --workbook 技术对标.xlsx --manifest source_manifest.json --require-responses` 回验原文、行数、车型范围、分组和次序。范围清单写 `layout`（`combined` 或 `separate`）、`models`（仅本项目车型的 `model_id`、`tender_model`）和按原文顺序完整分段的 `sections`（`source_heading`、`first_source_order`、`last_source_order`、`model_ids`）。合写要求保持一个共同区间；分写要求保持招标原来的各车型区间。所选标准型号、子型号及报告ID在填充时另记证据索引，不能把长期总表全部车型复制进来。自动编号、页图、文本框或复杂合并无法可靠抽取时须回看原Word/渲染页，不得把脚本输出当已无遗漏的最终结论。正式DOCX详细技术响应填写区保持空白，另交XLSX；正/负差异的段落间距和审查标识依[技术对标工作簿](technical-excel-v138.md)。

`python scripts/technical_report_lookup.py --model WLR-10B(E)` 只列候选报告ID，不自动合并同车型报告；确认实际配置和原件后用 `python scripts/technical_report_lookup.py --report-id ir-...` 取得该份报告的字段级来源。可用 `--workbook PATH` 或 `BID_TECH_REPORT_WORKBOOK` 指向明确的更新版。输出属于内部候选数据，不等于符合招标条件。

技能内置 `authority_assets_check.py`、`authority_recall.py` 和参数语义配置，不再依赖难以发现的项目临时脚本。权威库正文和索引资产仍是外部只读数据，通过 `--authority-dir DIR` 或环境变量 `BID_AUTHORITY_DIR` 明确指定；脚本不得暗猜其他项目目录。

开始制作前运行：

`python scripts/authority_assets_check.py --authority-dir AUTHORITY_DIR`

缺少四件套、版本不一致或体量与登记基线不同即停止依赖该副本召回，先同步或改用经核实的数据源。召回示例：

`python scripts/authority_recall.py --authority-dir AUTHORITY_DIR --model WLR-19 --param 离地间隙 --ledger LEDGER.json`

召回结果只提供候选及成熟答复，当前项目证据、配置和招标比较仍决定最终事实与正偏离。

原权威库工具用于底层来源和事实回查，不再将blocks按`section_title`聚合为完整章节。

## 模块化权威库

模块库使用`--module-dir DIR`或环境变量`BID_AUTHORITY_MODULE_DIR`指定。制作前执行：

`python scripts/authority_module_assets_check.py --module-dir MODULE_DIR`

完整章节召回示例：

`python scripts/authority_module_recall.py --module-dir MODULE_DIR --customer 山西焦煤集团有限责任公司 --domain technical --module-type chapter_module --title 质量保证 --limit 5`

车型逐条响应召回示例：

`python scripts/authority_module_recall.py --module-dir MODULE_DIR --domain technical --module-type response_module --model WLR-19 --limit 20`

默认只返回`reuse_eligibility=eligible`。`--include-review`仅供模块边界修复检查，输出不得直接进入正式投标文件。召回输出保留完整模块正文、模块ID、来源路径、来源哈希和替换字段；不得只取搜索摘要后重新概述。

## 客户模板库

客户模板库优先使用`--template-dir DIR`或环境变量`BID_CUSTOMER_TEMPLATE_DIR`显式指定；两者均未设置时，工具检查当前用户的`.codex/data/bid-customer-template-library/current`标准位置。制作前执行：

`python scripts/customer_template_assets_check.py --template-dir TEMPLATE_DIR`

精确召回示例：

`python scripts/customer_template_recall.py --template-dir TEMPLATE_DIR --customer 山西焦煤集团有限责任公司 --domain technical --scoring 质量保证`

未精确命中客户时默认停止，不自动使用同集团或其他客户模板。只有显式加`--allow-alias`才使用已登记别名；同集团和同业务类型降级由项目复用台账人工确认，不由脚本静默完成。输出完整章节文本和来源，不重新概括。

## inspect / compare

`python scripts/docx_tools.py inspect INPUT.docx --table 0`

返回哈希、顶层表索引/行列/表头、正文、节设置、媒体哈希及实际图片引用顺序；指定表时返回物理行文本。合并单元格可能使列表重复，嵌套表不列为顶层；不能将物理行数直接说成要求条数。

`python scripts/docx_tools.py compare BEFORE.docx AFTER.docx --table 0`

默认只允许格式变化，检查文本不变、表结构、节和图片保全。明确仅修订第三列答复时可加`--allow-text-columns 2`，此时只检查选中表其他列文本及节/媒体/顶层表尺寸，**不证明其他正文未改、答复数字正确或偏离判断正确**。语义修订仍须逐项核对数字单位、条件、事实和未知项；ZIP CRC不等于所有OOXML引用均有效。

## style

仅调整已明确绑定的表格段落区间颜色/加粗，不生成响应、删除前缀或修改文本。表达改写先在当前副本内审核完成，再绑定该副本。保持原run字体字号等属性，不猜识别义务，不自动将整段染色。

项目目录配置格式：
```json
{
  "source_sha256": "当前源文件的完整SHA-256",
  "paragraphs": [
    {
      "table": 0, "row": 1, "column": 2, "paragraph": 0,
      "expected_text": "1.1 示例项：实际答复。",
      "spans": [{"start": 0, "end": 3, "color": "00B0F0", "bold": true}]
    }
  ]
}
```

索引从0开始，含表头；区间为Python字符索引左闭右开。必须对照实际文本计算，不复制示例索引。先将分层优先级解析为互不重叠的区间，强制材料黑色加粗优先于否决蓝色。需要恢复普通黑色的区间也明确配置。

`python scripts/docx_tools.py style SOURCE.docx BINDINGS.json NEW.docx`

源哈希或预期文本不一致、区间重叠、同一合并段落重复绑定、含超链接/域/图片/书签等复杂结构时拒绝。输出必须新文件，失败候选不得交付。工具不会自动清理失败文件或覆盖旧成品；复杂OOXML按Documents流程处理，不删结构来绕过拒绝。

## pages

`python scripts/docx_tools.py pages RENDERED.pdf`

返回全部PDF书签与1起算物理页码；没有书签不代表没有章节。可用于回填已关联章节清单，但不自动按相似标题匹配，不写预计页码，不直接改DOCX目录。回填后须重新渲染检查。

视觉格式对齐依赖渲染链：经 `bid.ps1` 调用 `render_docx.py`（DOCX → PDF → PNG 页图），或 soffice 转 PDF 后由 pypdfium2 出页图；必须走 `bid.ps1` 入口，否则定位不到 LibreOffice。`docx_tools.py` 只做结构检查、保全对比与页码读取，不出页图，也不做视觉验收。

这些辅助工具不负责云端检索、OCR、模型生成、Word渲染或视觉验收；由对应任务及现行Documents/PDF能力执行。不得把工具运行成功称为标书内容完整或能直接递交。

## 成品轻量预检

`bid_preflight.py`提供现有流程所需的可执行检查，不替代逐页视觉与内容核验：

- `python scripts/bid_preflight.py docx OUTPUT.docx`：检查DOCX可读、目次标记、至少两个标题层级和结构指标。招标明确禁止增设目录或另定目录格式时，加 `--allow-toc-exception` 并在项目记录中写明依据。
- `python scripts/bid_preflight.py attachments ATTACHMENTS.json`：核对引用附件清单中的文件实际存在。
- `python scripts/bid_preflight.py positive POSITIVE.json`：v1.38核对同一条款ID在内部横向Excel、优于说明和真实证据索引三处一致，同时确认正式偏差表不自动回填。
- `python scripts/bid_preflight.py volume CURRENT.docx REFERENCE.docx`：与适用历史成熟卷比较字符、段落、表格、行和图片体量，默认低于任一参考指标35%即报警；报警用于查漏，不能靠填充无关内容追平。

附件清单格式为 `{"attachments":[{"id":"A1","path":"原件.pdf"}]}`；v1.38优于项核对清单格式为 `{"skill_version":"v1.38","technical_alignment":["1.2"],"positive_section":["1.2"],"evidence_index":["1.2"],"formal_deviation_blank":true}`。所有清单属于项目内部记录，不进入应标正文。
