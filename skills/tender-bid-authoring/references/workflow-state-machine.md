# 十步强制工作流状态机

## 适用范围

新建或重制项目由`scripts/workflow_engine.py`管理。v1.38仍采用十步状态模式3.2：第1步除骨架外还要求招标Word原文清单与横向Excel空表；第7步要求技术方案DOCX和独立横向对标XLSX；第8步要求Excel逐行复核而非技术偏离清单；第9步交叉核对完整技术正文、正式详细表留空、内部Excel覆盖及DOCX附件部件。Agent失败可形成`candidate`并继续生产候选文件，但不能通过正式交付门禁。v1.37项目保留原阶段产物契约，不自动升级为v1.38通过状态。

本引擎只管理状态、证据、依赖、哈希和交付资格，不替代各阶段的解析、生成、Rulebase或多模态Agent。各工具必须把结果提交给状态机，绕过状态机产生的文件一律为候选件。

## 状态

- `pending`：前置阶段尚未形成可继续使用的产物。
- `ready`：前置阶段均为当前有效的`passed`或`candidate`，可以启动。
- `running`：本阶段正在产生确定性或开放性结果。
- `awaiting_agent`：Rulebase已通过，等待当前任务包绑定的Agent结果。
- `verifying`：Rulebase和Agent结果已具备，等待产物门禁。
- `candidate`：确定性产物及阶段门禁已通过，但Agent语义或视觉复核仍有明确开放项；允许后续制作，不具备正式递交资格。
- `passed`：本阶段规则、Agent和必需产物均通过，且所有哈希仍匹配。
- `blocked`：必需规则、事实、证据或Agent结论失败，禁止进入下一步。
- `stale`：输入、规则、依赖或产物在通过后发生变化，必须重跑。

`blocked`和`stale`不能被后续专项检查覆盖。重跑阶段时，状态机会立即使其全部下游阶段失效。

## 项目目录

建议每个项目建立独立目录：

```text
project_workflow/
├─ workflow.json
├─ events.jsonl
├─ project_manifest.json
└─ stages/
   ├─ 01/
   │  ├─ stage_spec.json
   │  ├─ rulebase_result.json
   │  └─ artifact_manifest.json
   └─ 10/
      ├─ agent_task.json
      ├─ agent_result.json
      ├─ page_review_manifest.json
      ├─ historical_feedback_review.json
      ├─ delivery_decision.json
      └─ artifact_manifest.json
```

`workflow.json`是当前状态快照，`events.jsonl`是追加式状态事件。不得通过手改这两个文件解决阻塞；只能修复阶段输入后重新执行。

## 基本命令

初始化：

```powershell
python scripts/workflow_engine.py init `
  --workflow-dir PROJECT_WORKFLOW `
  --project-id H112 `
  --skill-version v1.38 `
  --rulebase-version 1.4.0
```

启动阶段：

```powershell
python scripts/workflow_engine.py begin `
  --workflow-dir PROJECT_WORKFLOW `
  --stage 2 `
  --spec PROJECT_WORKFLOW/stages/02/stage_spec.json
```

提交Rulebase结果：

```powershell
python scripts/workflow_engine.py record-rulebase `
  --workflow-dir PROJECT_WORKFLOW `
  --stage 2 `
  --result PROJECT_WORKFLOW/stages/02/rulebase_result.json
```

提交Agent任务和结果：

```powershell
python scripts/workflow_engine.py record-agent `
  --workflow-dir PROJECT_WORKFLOW `
  --stage 3 `
  --task PROJECT_WORKFLOW/stages/03/agent_task.json `
  --result PROJECT_WORKFLOW/stages/03/agent_result.json
```

阶段完成：

```powershell
python scripts/workflow_engine.py complete `
  --workflow-dir PROJECT_WORKFLOW `
  --stage 2 `
  --artifact-manifest PROJECT_WORKFLOW/stages/02/artifact_manifest.json
```

刷新状态和检查哈希：

```powershell
python scripts/workflow_engine.py status --workflow-dir PROJECT_WORKFLOW
```

最终交付门禁：

```powershell
python scripts/workflow_engine.py check-delivery --workflow-dir PROJECT_WORKFLOW
```

旧项目审计：

```powershell
python scripts/workflow_engine.py audit-legacy `
  --source OLD_PROJECT_WORKDIR `
  --out OLD_PROJECT_WORKDIR/legacy_workflow_audit.json
```

## Rulebase约束

十步全部需要当前有效的Rulebase结果。`rulebase_result.json`至少包含：

```json
{
  "stage": "02-base-fields",
  "status": "pass",
  "rulebase_version": "1.4.0",
  "script_sha256": "...",
  "spec_sha256": "...",
  "required_failure_count": 0,
  "results": [{"id": "input-exists", "type": "file_exists", "passed": true}]
}
```

状态机只接受`status=pass`作为确定性通过。`blocked`、缺失状态、文件缺失或哈希变化均阻断阶段。

专项脚本的结果必须作为阶段产物或Rulebase规则登记；不得单独运行专项脚本后口头宣布整个阶段通过。

## Agent任务与结果契约

步骤1、3、4、5、6、7、8、10必须提交Agent任务包。任务包至少包含：

```json
{
  "stage": 1,
  "input_sources": [{"path": "...", "sha256": "...", "location": "page 42-43"}],
  "role": "提取本项目评分、禁止情形和响应性条款，不改写锁定原文",
  "task_requirements": ["..."],
  "rulebase_locks": ["..."],
  "output_contract": {"type": "object", "required": ["observations"]}
}
```

Agent结果至少包含：

```json
{
  "stage": 1,
  "task_sha256": "任务包文件SHA-256",
  "status": "candidate",
  "execution_mode": "code_fallback",
  "open_items": ["评分语义需模型或人工复核"],
  "observations": [{"location": "page 42", "conclusion": "..."}]
}
```

结果必须绑定当前任务包哈希。`pass`表示Agent工作实际完成；`candidate`必须带受控`execution_mode`和非空`open_items`，允许后续候选生产但不算审核通过；`blocked`禁止继续。旧任务结果、泛化提示产生的结果及空观察列表均不接受。

## 阶段产物清单

每个`artifact_manifest.json`包含阶段号及产物列表：

```json
{
  "stage": 1,
  "artifacts": [
    {"id": "input", "kind": "input_manifest", "path": "input_manifest.json"},
    {"id": "format", "kind": "format_baseline", "path": "format_baseline.json"},
    {"id": "scoring", "kind": "scoring_ledger", "path": "scoring_ledger.json"},
    {"id": "prohibitions", "kind": "prohibition_ledger", "path": "prohibition_ledger.json"},
    {"id": "responsiveness", "kind": "responsiveness_ledger", "path": "responsiveness_ledger.json"},
    {"id": "fields", "kind": "form_inventory", "path": "form_inventory.json"},
    {"id": "sections", "kind": "section_tree", "path": "section_tree.json"},
    {"id": "coverage", "kind": "requirement_coverage_matrix", "path": "requirement_coverage_matrix.json"},
    {"id": "skeleton-bundle", "kind": "skeleton_bundle_manifest", "path": "skeleton_bundle_manifest.json"},
    {"id": "skeleton-business", "kind": "skeleton_docx", "path": "business_blank.docx"},
    {"id": "skeleton-technical", "kind": "skeleton_docx", "path": "technical_blank.docx"},
    {"id": "slots", "kind": "skeleton_slot_manifest", "path": "slot_manifest.json"},
    {"id": "technical-rows", "kind": "technical_requirement_manifest", "path": "technical_requirement_manifest.json"},
    {"id": "skeleton-lock", "kind": "skeleton_lock", "path": "skeleton.lock.json"},
    {"id": "skeleton-validation", "kind": "skeleton_validation", "path": "skeleton_validation.json"},
    {"id": "skeleton-render", "kind": "skeleton_render_manifest", "path": "skeleton_render_manifest.json"}
  ]
}
```

必需`kind`由状态机按步骤固定。文件必须存在，声明哈希时必须与实际一致。阶段通过后，任何产物、任务、结果、规范或依赖哈希变化都会使本阶段及其下游自动变为`stale`。

十步必需产物按[完整标书制作主流程](full-authoring-workflow.md)和[商务十步专项规则](business-ten-step.md)登记。步骤1校验骨架、槽位、技术空表、要求覆盖、骨架锁、商务/技术卷齐全及Office渲染清单。步骤2、4、5、7、8校验骨架绑定、槽位填充和结构未变；步骤6校验证据只指向已登记槽位；步骤9除最终各卷、每卷填充清单、渲染清单及冻结结构外，还必须提交`final_content_completion_manifest`和`attachment_placement_manifest`，证明正式章节已有实质内容或明确缺项、技术要求逐行响应、已选附件实际进入成品。原有资格、评分、承诺和三类留空原表检查继续执行。专项检查均在`complete`内执行，不能靠无关Rulebase `pass`跳过。

规则中`passed`表示该步文件、检查和所需Agent复核完成，不替代业务符合性结论；`candidate`表示文件可继续使用但仍有Agent开放项。两者都允许后续阶段启动，只有`passed`满足正式交付门禁。资格结论`not_met`可以随步骤3继续传递到后续制作，但最终`check-delivery`必须拒绝递交就绪。

## 版本失效规则

- 招标、澄清、评分、禁止情形、响应性条款、目录、强制表单或格式变化：从步骤1重跑并形成新骨架锁。
- 基础字段或业务留空决定变化：从步骤2重跑。
- 资格资料变化：若只更新既有槽位，从步骤3重跑；若需要新增章节或材料位置，从步骤1重跑。资格未满足是领域结论，不阻断其余章节制作。
- 商务评分或体系认证落位变化：既有槽位内更新从步骤4重跑；新增位置从步骤1重跑。
- 承诺函文字、禁止条款映射或响应性映射变化：从步骤5重跑。
- 车型配置及技术证明变化：既有技术槽位内更新从步骤6重跑；新增要求行、章节或附件位置从步骤1重跑。
- 技术评分响应变化：从步骤7重跑。
- 内部横向Excel拟响应、证据索引或正式留空表变化：从步骤7或最早受影响步骤重跑；不再依赖独立技术偏离清单。
- 最终卷、PDF、目录、页码或页图变化：从步骤9重跑；不得用步骤9改变冻结结构。
- 骨架、槽位清单或骨架锁变化：步骤1—10失效。
- 技能或Rulebase版本变化：步骤1—10失效。

使用`invalidate`明确登记业务变化的最早受影响步骤：

```powershell
python scripts/workflow_engine.py invalidate `
  --workflow-dir PROJECT_WORKFLOW `
  --stage 4 `
  --reason "投标车型由业务重新确认"
```

## 第十步逐页门禁

步骤9的`rendered_pages_manifest`必须逐卷绑定最终DOCX哈希，逐页记录真实页图路径及SHA-256，页码从1连续且覆盖全部最终卷。`page_review_manifest`必须绑定同一批最终文件和页图，并且逐页记录：

- 当前`skeleton_id`，且与步骤1骨架锁一致；

- `output_id`；
- `page`；
- `page_image_sha256`；
- `page_type`；
- `checklist_results`；
- `visual_result`；
- `visual_note`；
- `reviewer`；
- `review_method`。

每页必须至少有一条适用的历史问题清单结论，每条含`item_id、result、note`。页号必须完整覆盖`1..page_count`，不能重复或缺页，`page_image_sha256`必须与步骤9对应页图一致。程序测得的文本量、边界和空白信号可以作为输入，但不能代替多模态视觉结论。视觉模型不可用时使用`visual_result=unresolved`并写明原因，步骤10保持`candidate`，不得批量伪造通过。

禁止批量写入相同的“已检查、无问题”作为逐页观察。视觉记录应能说明本页是什么、检查了什么以及结论落在哪里。

## 交付状态

步骤10通过不自动等于正式递交就绪。`delivery_decision.json`必须明确：

```json
{
  "status": "ready",
  "blocking_items": [],
  "business_open_items": []
}
```

状态可为：

- `ready`：可正式递交；
- `candidate`：文件制作完成，但仍有价格、凭证、签章或业务批准事项；
- `blocked`：存在资格、否决、强制证明、负偏离或其他阻塞事项。

只有十步全部保持当前有效的`passed`，交付决定为`ready`、无阻塞事项，且步骤3资格结论为`met`时，`check-delivery`才输出`delivery_ready=true`。任一阶段为`candidate`时仍可下载候选文件，但不可标为递交就绪。

