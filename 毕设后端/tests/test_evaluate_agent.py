"""评估器本身的离线测试，不调用模型。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluate_agent import load_cases, score_result


class EvaluateAgentTests(unittest.TestCase):
    def test_case_file_is_valid(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 3)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))

    def test_scoring_checks_tools_and_text(self):
        case = {"id": "x", "question": "q", "required_tools": ["search_knowledge"],
                "forbidden_tools": ["query_kpi"], "required_text": ["soff", "Tnon_sch"],
                "forbidden_text": ["已确认根因"]}
        result = {"status": "ok", "answer": "soff 对应 Tnon_sch",
                  "trace": [{"name": "search_knowledge"}], "usage": {"total_tokens": 1},
                  "grounding": {"status": "verified"}}
        self.assertTrue(score_result(case, result)["passed"])
        result["answer"] = "soff，已确认根因"
        scored = score_result(case, result)
        self.assertFalse(scored["passed"])
        self.assertFalse(scored["checks"]["required_text"])
        self.assertFalse(scored["checks"]["forbidden_text"])

    def test_optional_grounding_check(self):
        case = {"id": "rag", "question": "q", "require_grounding": True}
        result = {"status": "ok", "answer": "有引用", "trace": [],
                  "grounding": {"status": "missing"}}
        scored = score_result(case, result)
        self.assertFalse(scored["passed"])
        self.assertFalse(scored["checks"]["grounding"])

    def test_tool_argument_and_budget_checks(self):
        case = {"id": "tool", "question": "q", "required_tool_arguments": [
                    {"name": "query_line_kpi", "arguments": {"line_id": 1, "date": "2026-09-08"}}],
                "max_tool_calls": 1, "max_total_tokens": 100}
        result = {"status": "ok", "answer": "ok", "usage": {"total_tokens": 80},
                  "trace": [{"name": "query_line_kpi", "arguments": {
                      "line_id": 1, "date": "2026-09-08"}}]}
        self.assertTrue(score_result(case, result)["passed"])
        result["trace"][0]["arguments"]["line_id"] = 2
        self.assertFalse(score_result(case, result)["checks"]["tool_arguments"])

    def test_regex_checks_distinguish_negation_from_wrong_unit(self):
        case = {"id": "unit", "question": "q",
                "required_patterns": [r"MTBF[^\n]{0,20}分钟"],
                "forbidden_patterns": [r"\|\s*MTBF\s*\|\s*[^|\n]*小时\s*\|"]}
        result = {"status": "ok", "answer": "| MTBF | 2.6 分钟 |\n未改写为小时", "trace": []}
        self.assertTrue(score_result(case, result)["passed"])
        result["answer"] = "| MTBF | 2.6 小时 |"
        self.assertFalse(score_result(case, result)["passed"])

    def test_timeline_window_and_evidence_checks(self):
        case = {"id": "timeline", "question": "q", "require_evidence_validation": True,
                "require_timeline_page_window": True}
        result = {"status": "ok", "answer": "当前页 00:00:00 至 00:00:53，依据 status_event_log:12。",
                  "evidence_validation": {"status": "verified"}, "trace": [{
                      "name": "query_line_timeline", "result": {"page_window": {
                          "start": "2026-09-08T00:00:00", "end": "2026-09-08T00:00:53"}}}]}
        self.assertTrue(score_result(case, result)["passed"])
        result["answer"] = "当前页到 00:00:37。"
        self.assertFalse(score_result(case, result)["checks"]["timeline_page_window"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
