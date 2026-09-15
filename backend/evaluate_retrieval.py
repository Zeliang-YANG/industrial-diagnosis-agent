"""离线评估本地知识检索的 Hit@k、MRR 与无关问题拒绝率。"""
import argparse
import json
from pathlib import Path

from knowledge_base import search_knowledge, knowledge_revision


DEFAULT_CASES = Path(__file__).with_name("evals") / "retrieval_cases.json"


def evaluate(cases, top_k=3):
    rows = []
    positive_rr = []
    positive_hit1 = []
    positive_hitk = []
    negative_correct = []
    for case in cases:
        result = search_knowledge(case["query"], top_k=top_k)
        returned = [item["id"] for item in result["matches"]]
        if case.get("expect_no_match"):
            correct = result["status"] == "no_match"
            negative_correct.append(correct)
            rows.append({"id": case["id"], "passed": correct, "returned_ids": returned})
            continue
        expected = set(case["expected_ids"])
        rank = next((index for index, value in enumerate(returned, 1) if value in expected), None)
        hit1, hitk = rank == 1, rank is not None
        positive_hit1.append(hit1)
        positive_hitk.append(hitk)
        positive_rr.append(1.0 / rank if rank else 0.0)
        rows.append({"id": case["id"], "passed": hitk, "rank": rank,
                     "expected_ids": case["expected_ids"], "returned_ids": returned})

    average = lambda values: round(sum(values) / len(values), 4) if values else 0.0
    summary = {"cases": len(cases), "top_k": top_k,
               "hit_at_1": average(positive_hit1), "hit_at_k": average(positive_hitk),
               "mrr": average(positive_rr), "no_match_accuracy": average(negative_correct),
               "knowledge_revision": knowledge_revision()}
    passed = summary["hit_at_k"] >= 0.9 and summary["mrr"] >= 0.75 and summary["no_match_accuracy"] == 1.0
    return {"passed": passed, "summary": summary, "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    output = evaluate(cases, args.top_k)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
