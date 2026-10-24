# 模块化权威库

## 资产与入口

模块库是原权威库的只读派生层。使用`--module-dir DIR`或环境变量`BID_AUTHORITY_MODULE_DIR`指定；云端标准入口为`/srv/deta-bid/historical-bid-authority/modules-current`。目录至少包含：

- `authority.modules.meta.json`：版本、上游哈希、数量和资产哈希；
- `authority.modules.json.gz`：全部模块；
- `authority.chapter-modules.json.gz`、`authority.table-modules.json.gz`、`authority.response-modules.json.gz`、`authority.evidence-modules.json.gz`；
- `authority.module-index.json.gz`：轻量索引；
- `authority.module-relations.json.gz`：父子章节及章节—表格/响应关系；
- `authority.module-quality.json`：待复核、排除及无内容来源；
- `customers/*/module-manifest.json`：客户独立模块清单。

制作前运行`authority_module_assets_check.py`。校验只证明资产完整，不证明模块适用于当前项目。

## 类型与用途

- `chapter_module`：方案、服务、质量、培训、供货、运输、质保等完整章节，默认正文复用单位；
- `table_module`：完整表格及上下文，招标强制原表始终优先；
- `response_module`：逐条技术要求、历史响应、偏离和证明引用，用于参数应答；
- `evidence_module`：证书、报告、合同、业绩等证明材料关系，不作为叙述正文模板。

不得以若干句子或搜索摘要替代完整章节模块。章节包含的子标题、正文、表格、流程、措施和附件引用作为一个整体进入适用性判断。

## 复用资格

- `eligible`：可进入候选，仍须核对本项目适用性；
- `reference_only`：通常为招标/采购原文，只用于解析要求；
- `evidence_only`：只用于证明材料关联；
- `needs_review`：标题边界、结构或来源需复核，不进入默认召回；
- `excluded`：禁止复用。

默认召回仅返回`eligible`。不得通过`--include-review`把待复核内容直接投入正式正文；该参数只供修复模块边界时检查。

## 召回和排序

先按客户、领域和模块类型过滤，再按评分标题或车型检索。优先级为：

1. 同客户、同项目类型、同章节；
2. 同客户、相近项目、同章节；
3. 同集团且同业务类型；
4. 同车型或同功能的其他客户章节；
5. 合理检索后无适用模块才`Fresh Generate`。

技术参数候选还要比较投标车型、功率、电池、电压、尺寸、质量、载荷、三电系统和本项目关键配置。相同标题、相似文本或同车型名称不能替代业务参数核验。

## 模块台账

每个采用模块记录：

`目录节点ID｜模块ID｜模块类型｜来源客户｜来源项目｜来源文件及SHA-256｜原章节路径｜复用资格｜复用方式｜替换字段｜删除内容｜新增内容｜当前证据｜最终正文位置｜核验结论`。

复用方式仅为：

- `Exact Reuse`：客户、要求、配置和义务一致，只替换本项目固定字段；
- `Adapted Reuse`：完整模块作为基线，仅修改真实差异；
- `Reference Only`：不直接进入正文；
- `Fresh Generate`：无适用模块时按当前事实新写，并登记为项目新模块。

客户、项目、编号、标包、车型、参数、数量、期限、地点、人员、日期、价格、附件页码和专属承诺均须逐处核对。不能用全局替换代替章节语义检查。

## 目录驱动装配

评分目录节点必须绑定模块ID或明确标记`Fresh Generate`。同一模块支持多个评分点时正文只装配一次，其他节点准确交叉引用；不得重复堆入。同一目录节点绑定多个模块时先确定主模块，再将补充模块的适用子节合并，保留来源关系并清理重复标题。

目录结构召回以“成对项目”为优先单位：同一来源项目中同时存在`source_role=tender_reference`与`source_role=bid_submission`时，先从招标来源解析评分项，再检查投标来源如何形成目录和正文。内部记录来源项目、两份文件路径及哈希、评分项、目录节点、正文模块ID和证明材料模块ID。若只能定位单边文件，必须标为`unpaired_reference`，不得据此断言某种目录结构与评分规则相匹配。

成对案例用于学习映射方法，不直接决定当前目录。当前项目评分顺序、强制章节和评价对象优先；历史目录只提供层级深度、标题粒度、交叉引用和完整模块落位的参考。历史项目中将评分内容埋入兜底章节、遗漏评分项或沿用固定模板的做法不得继承。

模块装配顺序为：完整章节/表格基线 → 本项目字段替换 → 当前事实和逐条响应 → 证明材料 → 模块衔接和去重。不得先自由生成整卷，再反向为正文寻找模块来源。

## 最终检查

- 正文每个评分章节能反查模块ID或项目新写记录；
- 模块标题、全部正文、表格和页眉页脚无旧客户/旧项目信息；
- 当前期限、地点、车型、数量和责任与条款台账一致；
- `response_module`结论已用本项目证据重判；
- 引用的`evidence_module`对应原件已实际附入；
- `reference_only`、`needs_review`和`excluded`未进入投递正文；
- 目录、正文、附件索引和最终页码使用同一目录节点。
