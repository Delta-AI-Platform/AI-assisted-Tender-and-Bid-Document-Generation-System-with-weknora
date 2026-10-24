import unittest

from bid_agent.history_standard_tools import _classify, _model_for_object


class HistoryStandardToolsTest(unittest.TestCase):
    def query(self):
        return {"requirement_id": "AR1", "requested_model": "WLR-5", "query_model": "WLR-5C(E)",
                "match_scope": "approved_model_family", "family_rule_id": "WLR5-PLATFORM-001",
                "parameter": "最高车速", "aliases": ["最高速度"]}

    def record(self, ident, answer):
        return {"id": ident, "match_scope": "exact_model", "match_type": "explicit_alias",
                "matched_model": "WLR-5C(E)", "standard_answer": answer,
                "source_triplet": "项目｜WLR-5C(E)｜标书.docx", "source_ref": {"page": 1}}

    def test_model_scope_is_explicit_and_not_cross_model(self):
        self.assertEqual(_model_for_object("矿用防爆指挥车 WLR-5")["scope"], "approved_model_family")
        self.assertEqual(_model_for_object("矿用防爆升降车 WC5J（A）")["scope"], "exact_model")
        self.assertIsNone(_model_for_object("充电桩"))

    def test_unique_answer_is_direct_and_traceable(self):
        result = _classify(self.query(), {"results": [self.record("H1", "最高车速25km/h")]})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["standard_answer"], "最高车速25km/h")
        self.assertEqual(result["selected_record_id"], "H1")

    def test_different_authority_answers_are_conflict_and_blank(self):
        result = _classify(self.query(), {"results": [self.record("H1", "25km/h"), self.record("H2", "30km/h")]})
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["standard_answer"], "")
        self.assertIsNone(result["selected_record_id"])

    def test_returned_submodel_must_equal_resolved_query_model(self):
        wrong = self.record("H1", "16°")
        wrong.update(matched_model="WLR5B", match_scope="same_model_family")
        result = _classify(self.query(), {"results": [wrong]})
        self.assertEqual(result["status"], "not_found")

    def test_leading_clause_number_only_is_equivalent(self):
        a = self.record("H1", "1.11.3充电时间：快充≤1.5h")
        b = self.record("H2", "1.10.3 充电时间：快充≤1.5h")
        result = _classify(self.query(), {"results": [a, b]})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["standard_answer"], a["standard_answer"])
        self.assertIn("equivalent", result["reason"])


if __name__ == "__main__": unittest.main()
