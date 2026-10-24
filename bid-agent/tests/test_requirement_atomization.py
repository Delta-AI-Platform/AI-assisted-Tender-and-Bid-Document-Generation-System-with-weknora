import json
import tempfile
import unittest
from pathlib import Path

from bid_agent.requirement_atomization import install_requirement_atomization_tools


class Registry:
    def __init__(self, root):
        self.root = Path(root); self.tools = {}
    def path(self, relative):
        return self.root / relative
    def add(self, name, description, parameters, handler, side_effect=False):
        self.tools[name] = handler


class RequirementAtomizationTest(unittest.TestCase):
    def fixture(self, root):
        folder = root / "analysis/step2"; folder.mkdir(parents=True)
        index = {"pages": [{"physical_page": 5,
            "text_blocks": [{"id": "B1", "bbox": [0, 10, 1, 11], "text": "5.1 矿用防爆指挥车 WLR-5"}],
            "tables": [{"bbox": [0, 20, 1, 30], "rows": [
                {"id": "R1", "cells": ["1", "参考型号", "WLR-5 自动挡", None], "text": "1 | 参考型号 | WLR-5 自动挡 | "},
                {"id": "R2", "cells": ["2", "额定载客人数", "≥5人", None], "text": "2 | 额定载客人数 | ≥5人 | "}]}]}]}
        spec = {"version": "deta-requirement-atomization-v2",
            "source_index_path": "analysis/step2/technical-source-index.json", "rows": [
                {"kind": "heading", "source_ids": ["B1"], "source_joiner": "", "source_text": "5.1 矿用防爆指挥车 WLR-5",
                 "hierarchy_number": "5.1", "clause_type": "章节标题", "applicable_object": "WLR-5",
                 "object_basis": {"mode": "explicit_source", "source_id": "B1", "quote": "WLR-5"}},
                {"kind": "requirement", "requirement_id": "REQ-1", "source_ids": ["R1"], "source_joiner": " ",
                 "hierarchy_number": "5.1.1", "clause_type": "表格条目", "applicable_object": "WLR-5", "source_text": "1 参考型号 WLR-5 自动挡",
                 "object_basis": {"mode": "explicit_source", "source_id": "B1", "quote": "矿用防爆指挥车 WLR-5"}},
                {"kind": "requirement", "requirement_id": "REQ-2", "source_ids": ["R2"], "source_joiner": " ",
                 "hierarchy_number": "5.1.2", "clause_type": "表格条目", "applicable_object": "WLR-5", "source_text": "2 额定载客人数 ≥5人",
                 "object_basis": {"mode": "explicit_source", "source_id": "B1", "quote": "矿用防爆指挥车 WLR-5"}}]}
        (folder / "technical-source-index.json").write_text(json.dumps(index, ensure_ascii=False))
        (folder / "draft.json").write_text(json.dumps(spec, ensure_ascii=False))
        return spec

    def test_preserves_each_original_table_row_and_object_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root); registry = Registry(root); install_requirement_atomization_tools(registry)
            result = registry.tools["build_requirement_atomization"]("analysis/step2/draft.json", "条款原子化-v1")
            self.assertEqual(result["stats"]["requirement_rows"], 2)
            self.assertEqual(result["validation"]["source_text_verbatim"], "passed")
            self.assertEqual(result["validation"]["object_attribution_semantic_review"], "source_anchored_machine_gate_passed")
            self.assertEqual(result["downstream_status"], "approved_for_eight_column")
            data = json.loads((root / result["path"]).read_text())
            self.assertEqual(data["rows"][1]["source_text"], "1 参考型号 WLR-5 自动挡")
            self.assertEqual(data["rows"][2]["applicable_object"], "WLR-5")

    def test_accepts_clear_source_index_alias_from_agent_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spec=self.fixture(root);spec['source_index']=spec.pop('source_index_path')
            (root/'analysis/step2/draft.json').write_text(json.dumps(spec,ensure_ascii=False))
            registry=Registry(root);install_requirement_atomization_tools(registry)
            result=registry.tools['build_requirement_atomization']('analysis/step2/draft.json','alias-v1')
            self.assertEqual(result['validation']['source_coverage'],'passed')

    def test_server_baseline_copies_every_source_element_without_model_retyping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);registry=Registry(root);install_requirement_atomization_tools(registry)
            result=registry.tools['build_requirement_atomization_baseline']('analysis/step2/technical-source-index.json','server-baseline')
            data=json.loads((root/result['path']).read_text())
            self.assertEqual(data['stats']['source_elements_used'],3)
            self.assertEqual([x['source_text'] for x in data['rows']],['5.1 矿用防爆指挥车 WLR-5','1 参考型号 WLR-5 自动挡','2 额定载客人数 ≥5人'])
            self.assertTrue(all(x['applicable_object']=='object_unresolved' for x in data['rows']))
            self.assertEqual(data['validation']['source_coverage'],'passed')

    def test_rejects_merging_two_original_table_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); spec = self.fixture(root)
            spec["rows"][1]["source_ids"] = ["R1", "R2"]
            (root / "analysis/step2/draft.json").write_text(json.dumps(spec, ensure_ascii=False))
            registry = Registry(root); install_requirement_atomization_tools(registry)
            with self.assertRaisesRegex(ValueError, "only one original table row"):
                registry.tools["build_requirement_atomization"]("analysis/step2/draft.json", "错误稿")

    def test_rejects_changed_wording_and_inferred_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); spec = self.fixture(root)
            spec["rows"][1]["source_text"] = "1 推荐型号 WLR-5 自动挡"
            spec["rows"][1]["object_basis"]["mode"] = "model_inferred"
            (root / "analysis/step2/draft.json").write_text(json.dumps(spec, ensure_ascii=False))
            registry = Registry(root); install_requirement_atomization_tools(registry)
            with self.assertRaisesRegex(ValueError, "object attribution may not be model-inferred"):
                registry.tools["build_requirement_atomization"]("analysis/step2/draft.json", "错误稿")

    def test_allows_non_overlapping_exact_segments_from_one_pdf_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / "analysis/step2"; folder.mkdir(parents=True)
            text = "机械：ISO 标准\n（四）设备运行环境条件"
            index = {"pages": [{"physical_page": 4, "tables": [], "text_blocks": [
                {"id": "B22", "bbox": [0, 10, 1, 11], "text": text}]}]}
            spec = {"version": "deta-requirement-atomization-v2", "source_index_path": "analysis/step2/technical-source-index.json", "rows": [
                {"kind": "requirement", "requirement_id": "REQ-1", "source_ids": ["B22"],
                 "source_segments": [{"id": "B22", "quote": "机械：ISO 标准", "start": 0}],
                 "hierarchy_number": "3.17", "clause_type": "正文条款", "applicable_object": "项目通用", "source_text": "机械：ISO 标准",
                 "object_basis": {"mode": "explicit_source", "source_id": "B22", "quote": "机械：ISO 标准"}},
                {"kind": "heading", "source_ids": ["B22"],
                 "source_segments": [{"id": "B22", "quote": "（四）设备运行环境条件", "start": 10}],
                 "hierarchy_number": "（四）", "clause_type": "章节标题", "applicable_object": "项目通用", "source_text": "（四）设备运行环境条件",
                 "object_basis": {"mode": "explicit_source", "source_id": "B22", "quote": "设备运行环境条件"}}]}
            (folder / "technical-source-index.json").write_text(json.dumps(index, ensure_ascii=False))
            (folder / "draft.json").write_text(json.dumps(spec, ensure_ascii=False))
            registry = Registry(root); install_requirement_atomization_tools(registry)
            result = registry.tools["build_requirement_atomization"]("analysis/step2/draft.json", "分块条款")
            self.assertEqual(result["stats"]["requirement_rows"], 1)


if __name__ == "__main__":
    unittest.main()
