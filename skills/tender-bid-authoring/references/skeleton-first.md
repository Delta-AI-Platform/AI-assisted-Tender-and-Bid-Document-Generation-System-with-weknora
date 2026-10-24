# 空白骨架、槽位和冻结结构

## 强制顺序

完整标书和重制任务执行：`招标要求解析 → 空白骨架DOCX → 骨架渲染与校验 → 冻结骨架 → 按槽位检索 → 填充副本 → 结构回验 → 最终目录页码与逐页终审`。

空白骨架只承担“先确定结构和格式”的作用，不是本技能的交付终点。用户要求制作标书时，骨架门禁通过后必须无缝继续资料检索、正文生成、证据附件归位、目录页码更新和逐页终审；不能把骨架文件、仅填基础字段的骨架或缺少正文与证明附件的文件命名为候选标书或最终标书。若后续资料不足，仍应在既有证据范围内形成完整候选卷并同步输出缺项清单，而不是停止在骨架阶段。

第1步未通过骨架门禁时，不得开始企业资料、历史章节、证书、参数或承诺内容的正式召回与填充。解析时可以读取客户模板判断招标格式的复刻方法，但不得提前把历史项目内容写进骨架。

骨架生成前必须按[骨架文档格式标准与模板](skeleton-format-standard.md)确定本项目使用的格式来源、`style_profile`和模板哈希。招标原表有明确样式时克隆原表；没有完整样式时，才使用权威库默认模板。格式参数和项目级例外随骨架一起冻结。

冻结的是章节、固定表式和分页规则，不是最终物理页数。正文、图片和附件在指定槽位内可以自然扩页；章、节、小节另起页、标题与首个内容同页、分节方向、页边距、表格结构和附件锚点不得随填充改变。

## 第1步产物

至少形成：

- 唯一章节树和要求覆盖矩阵；
- 商务空白骨架DOCX；
- 技术空白骨架DOCX；
- 项目明确要求合卷时的合卷骨架；
- `skeleton_bundle_manifest.json`；
- `slot_manifest.json`；
- `technical_requirement_manifest.json`；
- `skeleton.lock.json`；
- `skeleton_validation.json`；
- 每份骨架的Office渲染PDF、页图和`skeleton_render_manifest.json`。
- 本次实际采用的`skeleton_style_profile.json`及其来源、版本和哈希。

骨架必须实际打开和渲染。JSON中的格式说明、目录清单或表格坐标不能替代真实DOCX。

`skeleton_tools.py build`的卷规格可用`cover`定义封面固定标题与固定行。投标人等待填写字段必须定义为带`slot_id`的封面行，生成不可见内容控件并进入同一槽位清单；不得用空格或下划线冒充无约束占位。

## 骨架可见内容

骨架可见内容仅限本项目招标要求或已确认格式决定的：封面字段标签、分部扉页、目录节点、章节目标题、固定正文、表名、表头、招标要求列、证书/报告/材料名称、注释、下划线、签章位和附件位置。

骨架不得包含：

- 企业实际值、技术响应、偏离结论、评分主张或履约承诺；
- 历史客户、项目、车型、日期、金额、人员、签章和证书原件；
- “待补充”“模型生成”“检索结果”等内部提示；
- 为占满页面而编造的示例文字或虚构资料名。

证书和材料槽位以招标要求及确认章节树命名。原件尚未匹配时保持空槽并列入缺项，不删除其要求位置，也不提前放入相近材料。

## 槽位契约

每个可变位置有稳定`slot_id`，在Word中使用不可见内容控件`BID_SLOT:<slot_id>`定位。用户看到的骨架保持空白，不显示内部标签。

`slot_manifest.json`每项至少记录：

- `slot_id、volume_id、section_id、display_title、slot_type`；
- 对应`requirement_ids`和`scoring_ids`；
- `allowed_material_types`和适用主体、客户、车型、年度、版本条件；
- 精确标题、子级目录名及同义检索词；
- `allow_blank、allow_repeat、natural_overflow`；
- `allowed_stages、required_by_stage`；
- Word内容控件标签或表格坐标。

后续资料检索只能从当前槽位的标题、要求、评分对象和适用条件发起。召回结果必须登记槽位、资料版本、来源、适用判断和内容哈希。未绑定槽位的内容不得追加到文末、“其他资料”或临时新建章节。

填充始终复制冻结骨架，不修改骨架原件。人工编辑也应发生在槽位内；需要新增章节、表格、材料标题或附件锚点时，退回第1步重建并冻结新骨架版本。

## 技术响应空表

第1步从招标正文及引用附件的第一项要求连续提取到最后一项，生成空白技术响应表：

- 原样保留编号、星号、括号、数值、单位、限定语和投标时提交要求；
- 复合条款可拆分内部判断，但保留原编号、原顺序和父子关系；
- 每行绑定稳定`requirement_id`、原文位置、表格行及响应/偏离槽位；
- “招标人要求”列写原文，响应、证据和偏离填写区保持空白；
- v1.38将详细技术响应及偏离槽位标记为用户指定`allow_blank=true`、原因为`business_directed_blank`；其他技术评分和附件槽位不得一并放宽；
- 先校验表头、列序、列宽、合并、方向、重复表头和自然跨页，再冻结。

第7步仅填独立横向技术对标XLSX；正式DOCX详细技术表的响应及偏离槽位持续为空。Excel少行、多行、换序、改写要求列、添加无来源要求或删除复合条款均阻断通过。Word原文转Excel及报告级模块映射见[技术对标工作簿](technical-excel-v138.md)。

## 确定性工具

使用`scripts/skeleton_tools.py`：

```powershell
python scripts/skeleton_tools.py build --spec skeleton_spec.json --output-dir skeleton
python scripts/skeleton_tools.py validate --bundle skeleton/skeleton_bundle_manifest.json --slots skeleton/slot_manifest.json --technical skeleton/technical_requirement_manifest.json --output skeleton/skeleton_validation.json
python scripts/skeleton_tools.py freeze --bundle skeleton/skeleton_bundle_manifest.json --slots skeleton/slot_manifest.json --technical skeleton/technical_requirement_manifest.json --output skeleton/skeleton.lock.json
python scripts/skeleton_tools.py compare --candidate filled.docx --lock skeleton/skeleton.lock.json --volume-id business
python scripts/skeleton_tools.py create-style-template --profile assets/skeleton-format-standard.json --output assets/skeleton-style-template.docx
```

`build`适用于可用结构规范确定性重建的页面。招标提供DOCX时优先克隆原表；只有PDF时使用Documents/PDF能力重建骨架，再以相同槽位契约和`freeze`门禁锁定。不能因脚本不能复刻复杂页面就简化或重设计强制格式。

Rulebase规则：

- `docx_skeleton_contract`：骨架卷、槽位和技术要求关系完整；
- `docx_technical_blank_table`：技术原文、顺序和空白响应区正确；
- `docx_requirement_coverage`：每项要求映射实际槽位；
- `docx_slot_fill`：只在授权阶段填授权槽位并绑定来源；
- `docx_structure_frozen`：填充后结构与冻结骨架一致。
- `docx_skeleton_format_standard`：字体、字号、字重、颜色及必需样式符合本项目冻结的格式档案。

结构比较忽略`BID_SLOT`内容，锁定其余正文OOXML、标题、表格、分节、样式、编号、页眉页脚和设置。目录域显示页码、渲染分页标记及槽位内容允许按最终文件更新；槽位外变化必须阻断。

## 版本与失效

骨架锁保存技能版本、工具版本、骨架ID、槽位清单哈希、技术要求清单哈希、格式档案ID/哈希、模板样式哈希及各卷结构哈希。

- 招标、澄清、目录、强制表单、评分要求或格式变化：第1步及全部下游`stale`；
- 新材料只改变既有槽位内容：从对应填充步骤失效，骨架保持有效；
- 新材料要求新增章节、表格或材料位置：从第1步失效；
- 填充文档的槽位外结构变化：当前步骤`blocked`；
- 最终目录、页码或渲染更新不授权改变冻结结构。

第9步只装配冻结骨架的填充副本、更新目录页码并渲染。第10步的逐页记录必须绑定最终文件哈希和当前`skeleton_id`。

第9步装配门禁还必须提交`final_content_completion_manifest.json`和`attachment_placement_manifest.json`，确认：正式章节有实际正文或明确的业务留空/资料缺失依据；该卷全部槽位均已登记最终状态；技术要求已逐行形成正式响应；所有已选证书、报告、图片和证明材料已经进入对应附件锚点；目录页码来自当前最终渲染结果。任一条件未满足时只能保持填充中或缺项状态，不得以“骨架已通过”替代完整标书完成。详细字段见[完整标书制作主流程](full-authoring-workflow.md)。
