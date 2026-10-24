# 商务十步专项规则

业务人员提供的十步实操顺序为主线；当前项目招标原文和有效澄清控制格式、资格及计分。历史真实标书用于选成熟章节与证明组织，不复制旧项目事实。本版业务决定适用于本次确认的制作流程，未获新业务确认前不擅自改回旧版业绩实填和正式偏差表自动回填。

## 十步产出

| 步骤 | 必需产物 | 阶段完成条件 |
|---|---|---|
| 1 项目模板与要求解析 | 输入清单、格式基线、唯一章节树、要求覆盖矩阵、评分/禁止/响应性台账、填空位清单、商务与技术空白骨架、技术响应空表、槽位清单、骨架锁及渲染记录 | 招标要求逐项绑定实际槽位；空白骨架经Office渲染、Rulebase及Agent检查并冻结，未混入企业事实或历史项目内容 |
| 2 基础字段填写 | 基础字段DOCX、实填清单、三类表留空决定、骨架绑定及槽位填充清单 | 只在步骤1冻结骨架的基础字段槽位实填；结构保持不变 |
| 3 资格资料 | 资格条款与证明矩阵、资格结论 | 真实列出缺项；业绩为资格必备而未提交时结论为`not_met`，其他步骤可继续 |
| 4 商务得分资料 | 商务评分矩阵、体系认证落位、商务候选DOCX、骨架绑定及槽位填充清单 | 非业绩评分项争取高档；业绩为`not_submitted`、主张分数0；认证填入对应冻结槽位 |
| 5 承诺与声明 | 承诺清单、承诺DOCX、覆盖记录、骨架绑定及槽位填充清单 | 每个适用禁止情形有具体承诺；只填承诺槽位，不新增章节或表单 |
| 6 技术资料接收 | 技术证据矩阵、证据—槽位映射 | 逐车型核对配置、参数、报告与证书，候选材料先绑定槽位再供步骤7使用 |
| 7 技术评分响应 | 技术评分矩阵、内部横向技术对标XLSX、技术方案DOCX、骨架绑定及槽位填充清单 | Excel要求列、行数和顺序锁定；正式详细技术表填写区留空，评分章节照常填充 |
| 8 响应性核对 | 响应检查、Excel逐行复核、正式留空表清单、商务候选DOCX、骨架绑定及槽位填充清单 | 内部拟响应与证据引用一致，正式原表固定内容完整且填写区空白，不另出技术偏离清单 |
| 9 装配与目录 | 最终卷清单、最终槽位清单、骨架绑定、最终版三类表清单、内容完成清单、附件装配清单、渲染卷清单、页码映射、页图清单 | 装配冻结骨架的完整填充副本；每个正式章节有实质内容或明确缺项，技术要求逐行响应，已选附件实际嵌入；更新目录页码并渲染，结构仍与骨架一致 |
| 10 逐页终审 | 逐页记录、历史反馈复查、交付决定 | 每页有观察结果；资格、评分与交付状态分别表达 |

步骤1内完成原十四步的项目身份、采购配置、资产检查、评分解析、空白骨架生成和冻结；骨架通过前不得正式召回企业内容。步骤3、4、6、7按槽位完成权威库和知识库双源检索、模块适用性分级与客户化改写。不得用检索命中代替证明适用、槽位绑定或实际附入。

## 三类原表留空

本次业绩表不填，业绩证明不附；商务偏差表、技术偏差表及正式详细技术响应表仅保留招标固定题名、说明、表头、列与行结构、签章位，投标人填写区保持空白。详细技术对标和优于说明在独立内部XLSX中完成，不另产技术偏离清单；正式表不接受自动回填。

实填清单中这三类填写位使用`business_directed_blank`，`expected`为空，记录本次业务决定来源。其他表仍按`required`、`allowed_blank`、`not_applicable`或`unresolved`核对。留空决定只豁免三类表的填写要求，不产生资格通过或评分得分。

步骤8、9使用`docx_intentional_blank_forms`确定性规则，且状态机`complete`直接调用同一检查。步骤8提交`blank_forms_manifest`绑定商务候选DOCX的表索引；步骤9提交`final_blank_forms_manifest`绑定最终DOCX的实际表索引，不能沿用装配前的索引。清单必须恰好包含`performance`、`commercial_deviation`、`technical_deviation`三类，每类列出原表文件、原表/目标表索引、题名、全部固定单元格与全部填写单元格。两组坐标必须完整覆盖表格且不交叉；表格尺寸与固定文字必须与原表一致，填写单元格必须为空。

示例：

```json
{
  "forms": [
    {
      "type": "performance",
      "source_file": "original_form.docx",
      "source_table": 0,
      "target_table": 0,
      "title": "近年具有的类似业绩",
      "fixed_cells": [[0,0],[0,1]],
      "response_cells": [[1,0],[1,1]]
    }
  ]
}
```

实际清单必须包含全部三类表和每个真实单元格；示例坐标不用于项目成品。当前确认的三类表均位于商务响应部分；商务候选和最终文件分别建立清单并检查。

## 资格、评分和体系认证

`qualification_decision.json`至少记录`performance_required`、`performance_submitted=false`和`overall_status`（`met/not_met/unresolved`）。业绩若为资格必备，`overall_status`不得为`met`。该结论不阻断步骤4—10制作，但`check-delivery`不能给出`delivery_ready=true`。

`commercial_scoring_matrix.json`记录`performance_points_max`、`performance_status=not_submitted`、`claimed_performance_points=0`及其他评分项的高档条件、响应和证据位置。业绩原本的“满分所需数量＋1”规则在本次业务决定下不执行；不把空表算作有效业绩。

`certification_placement.json`的`items`逐证书记录ID、来源文件、`scoring_related`、`scoring_section`、实际`section`和可见证书标题`caption`。用于评分时实际章节等于对应评分材料章节；不用于评分时为“其他商务资料”。步骤4与9在候选、最终DOCX中检查该标题位于声明章节之下。同一原件只附一次，其余适用章节用准确引用。

## 禁止情形与响应性评审承诺

`prohibition_ledger.json`逐条记录招标中与投标人资格、行为、履约有关的禁止情形：稳定ID、原文与位置、适用性、承诺函ID、具体回应文字。不能把不同情形合并为一句“均无”；允许在同一函内分项回应。招标要求独立格式时，记录`requires_separate_letter=true`并使用独立函。

`responsiveness_ledger.json`逐项记录响应性评审原文与位置、是否需要投标人回应、专项承诺函ID及具体回应文字。需要回应的项必须全部写进同一份“响应性评审承诺函”；承诺函不能仅写总括性的“完全响应”。`commitment_inventory.json`记录每份函的ID、题名和`responsiveness_letter_id`；检查结果必须与当前承诺DOCX和最终DOCX绑定。

Rulebase除原有商务规则外，步骤1必须使用`docx_skeleton_contract`、`docx_skeleton_format_standard`、`docx_technical_blank_table`和`docx_requirement_coverage`；填充步骤使用`docx_slot_fill`及`docx_structure_frozen`。所有键均为Rulebase规范中的文件ID。源招标、骨架、槽位、版式配置、台账、承诺文字或最终卷变更，必须按最早受影响步骤重跑。
