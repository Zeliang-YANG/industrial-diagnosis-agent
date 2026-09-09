"""运行并评分小型 Agent 评估集；默认会调用 DeepSeek。"""
import argparse
import json
import os
import re
from pathlib import Path

from sqlalchemy import create_engine
import db_models
from deepseek_agent import run_agent


DEFAULT_CASES = Path(__file__).with_name("evals") / "cases.json"


def score_result(case, result):
    answer = result.get("answer", "")
    called = [item.get("name") for item in result.get("trace", [])]
    checks = {
        "status": result.get("status") in case.get("accepted_statuses", ["ok"]),
        "required_tools": all(name in called for name in case.get("required_tools", [])),
        "forbidden_tools": all(name not in called for name in case.get("forbidden_tools", [])),
        "required_text": all(value in answer for value in case.get("required_text", [])),
        "forbidden_text": all(value not in answer for value in case.get("forbidden_text", [])),
    }
    if case.get("required_patterns"):
        checks["required_patterns"] = all(re.search(pattern, answer, re.IGNORECASE)
                                           for pattern in case["required_patterns"])
    if case.get("forbidden_patterns"):
        checks["forbidden_patterns"] = all(not re.search(pattern, answer, re.IGNORECASE)
                                            for pattern in case["forbidden_patterns"])
    expected_arguments = case.get("required_tool_arguments", [])
    if expected_arguments:
        checks["tool_arguments"] = all(any(
            item.get("name") == expected.get("name")
            and all(item.get("arguments", {}).get(key) == value
                    for key, value in expected.get("arguments", {}).items())
            for item in result.get("trace", [])
        ) for expected in expected_arguments)
    if "max_tool_calls" in case:
        checks["max_tool_calls"] = len(result.get("trace", [])) <= case["max_tool_calls"]
    if "max_total_tokens" in case:
        checks["max_total_tokens"] = result.get("usage", {}).get("total_tokens", 0) <= case["max_total_tokens"]
    if case.get("require_grounding"):
        checks["grounding"] = result.get("grounding", {}).get("status") == "verified"
    if case.get("require_evidence_validation"):
        checks["evidence_validation"] = result.get("evidence_validation", {}).get("status") == "verified"
    if case.get("require_timeline_page_window"):
        timeline = next((item.get("result", {}) for item in result.get("trace", [])
                         if item.get("name") == "query_line_timeline"), {})
        page_window = timeline.get("page_window", {})
        expected_times = [str(page_window.get(key, "")).split("T")[-1]
                          for key in ("start", "end")]
        checks["timeline_page_window"] = all(value and value in answer for value in expected_times)
    return {
        "id": case["id"], "passed": all(checks.values()), "checks": checks,
        "called_tools": called, "status": result.get("status"),
        "answer": answer, "usage": result.get("usage", {}),
        "grounding": result.get("grounding", {}),
        "latency_ms": result.get("latency_ms"),
    }


def load_cases(path=DEFAULT_CASES):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) and "id" in item and "question" in item for item in data):
        raise ValueError("评估集必须是包含 id 和 question 的 JSON array")
    return data


def use_database(database):
    """将评测工具绑定到指定数据库；默认使用隔离的 Agent 演示库。"""
    if not isinstance(database, str) or not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError("评测数据库名仅允许字母、数字和下划线")
    new_engine = create_engine(db_models.engine.url.set(database=database), pool_pre_ping=True)
    db_models.engine.dispose()
    db_models.engine = new_engine
    db_models.SessionLocal.configure(bind=new_engine)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--case", help="只运行指定 case id")
    parser.add_argument("--database", default=os.environ.get("AGENT_EVAL_DATABASE", "yzl_agent_demo"),
                        help="数据工具使用的 MySQL 数据库，默认 yzl_agent_demo")
    parser.add_argument("--output", help="可选，将完整 JSON 结果保存到指定文件")
    parser.add_argument("--summary-only", action="store_true", help="只打印汇总，仍执行全部评分")
    args = parser.parse_args()
    use_database(args.database)
    cases = load_cases(args.cases)
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error("未找到指定 case id")
    results = [score_result(case, run_agent(case["question"])) for case in cases]
    passed = sum(item["passed"] for item in results)
    total_tokens = sum(item.get("usage", {}).get("total_tokens", 0) for item in results)
    total_latency_ms = sum(item.get("latency_ms") or 0 for item in results)
    output = {"summary": {"passed": passed, "total": len(results),
                          "pass_rate": round(passed / len(results), 4) if results else 0,
                          "total_tokens": total_tokens,
                          "average_tokens": round(total_tokens / len(results), 2) if results else 0,
                          "total_latency_ms": round(total_latency_ms, 2)},
              "results": results}
    serialized = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2) if args.summary_only else serialized)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
