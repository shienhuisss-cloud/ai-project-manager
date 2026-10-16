"""检查评测集、调用真实模型、根据人工标注计分。默认不调用模型。"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import llm, prompts, validators
from backend.schemas import ExtractResult, CompareResult, output_schema

ROOT = Path(__file__).resolve().parents[1]


def validate_case(case, data):
    if case["task"] == "extract":
        return validators.validate_extract(data, case["sources"])
    return {"changes": validators.validate_compare(data, case["baseline"], case["sources"])}


def run_cases(dataset):
    if not llm.is_configured():
        raise ValueError("真实评测需要先配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL")
    results = []
    for case in dataset["cases"]:
        sources = [{"source_id": s["id"], "text": s["text"]} for s in case["sources"]]
        messages = (
            prompts.build_extract_messages({"project_name": case["name"]}, sources)
            if case["task"] == "extract"
            else prompts.build_compare_messages(case["baseline"], sources)
        )
        model = ExtractResult if case["task"] == "extract" else CompareResult
        response = llm.chat_json(
            messages, schema=output_schema(model), validate=lambda d: validate_case(case, d)
        )
        normalized = validate_case(case, response["data"]) if response["ok"] else {}
        field = "requirements" if case["task"] == "extract" else "changes"
        results.append(
            {
                "case_id": case["id"],
                "task": case["task"],
                "status": response["kind"],
                "predictions": normalized.get(field, []),
                **{
                    key: response.get(key)
                    for key in ("model", "usage", "duration_ms", "attempts", "cost", "currency")
                },
            }
        )
        print(f"{case['id']}: {response['kind']}")
    return {
        "dataset_version": dataset["dataset_version"],
        "prompt_version": prompts.PROMPT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }


def summarize(dataset, report, annotations=None):
    results = report["results"]
    durations = sorted(r["duration_ms"] for r in results)
    summary = {
        "cases": len(results),
        "success_rate": sum(r["status"] == "ok" for r in results) / len(results),
        "retry_rate": sum((r["attempts"] or 0) > 1 for r in results) / len(results),
        "latency_p50_ms": statistics.median(durations),
        "latency_p95_ms": durations[max(0, int(len(durations) * 0.95 + 0.999) - 1)],
        "total_cost": sum(r["cost"] for r in results)
        if all(r["cost"] is not None for r in results)
        else None,
        "currency": results[0]["currency"],
        "semantic_metrics": "待人工标注，不用 JSON 合法率代替语义准确率",
    }
    if annotations is None:
        return summary
    if report["dataset_version"] != dataset["dataset_version"]:
        raise ValueError("评测集版本不一致")
    cases = {c["id"]: c for c in dataset["cases"]}
    outputs = {r["case_id"]: r for r in results}
    matched = gold_total = predicted_total = accepted = supported = kind_right = kind_total = reviewed = 0
    for label in annotations["cases"]:
        if not label["reviewed"]:
            continue
        reviewed += 1
        c, out = cases[label["case_id"]], outputs[label["case_id"]]
        gold = c["expected"]["requirements" if c["task"] == "extract" else "changes"]
        predictions = out["predictions"]
        pairs = label["matches"]
        used_gold, used_pred = set(), set()
        for pair in pairs:
            gi, pi = pair["gold_index"], pair["prediction_index"]
            if (
                gi in used_gold
                or pi in used_pred
                or not 0 <= gi < len(gold)
                or not 0 <= pi < len(predictions)
            ):
                raise ValueError(f"{c['id']}: 匹配索引越界或重复")
            used_gold.add(gi)
            used_pred.add(pi)
            if c["task"] == "compare":
                kind_total += 1
                kind_right += gold[gi]["kind"] == predictions[pi]["kind"]
        for field in ("accepted_without_edit", "evidence_supported"):
            indexes = label[field]
            if len(set(indexes)) != len(indexes) or any(i < 0 or i >= len(predictions) for i in indexes):
                raise ValueError(f"{c['id']}: {field} 索引无效")
        matched += len(pairs)
        gold_total += len(gold)
        predicted_total += len(predictions)
        accepted += len(label["accepted_without_edit"])
        supported += len(label["evidence_supported"])

    def ratio(a, b):
        return a / b if b else None

    summary["semantic_metrics"] = {
        "reviewed_cases": reviewed,
        "recall": ratio(matched, gold_total),
        "precision": ratio(matched, predicted_total),
        "evidence_support_rate": ratio(supported, predicted_total),
        "direct_acceptance_rate": ratio(accepted, predicted_total),
        "matched_change_kind_accuracy": ratio(kind_right, kind_total),
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/cases.json")
    parser.add_argument("--run", action="store_true", help="显式调用配置模型，会消耗 API 额度")
    parser.add_argument("--report", type=Path, help="已有模型输出，仅读取计分")
    parser.add_argument("--annotations", type=Path, help="对应报告的人工语义标注")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    for case in dataset["cases"]:
        validate_case(case, case["expected"])
    print(f"参考答案通过结构与引文校验：{len(dataset['cases'])} 组（非模型效果）")
    if not args.run and not args.report:
        return
    if args.run and args.report:
        parser.error("--run 与 --report 不能同时使用")
    report = run_cases(dataset) if args.run else json.loads(args.report.read_text(encoding="utf-8"))
    annotations = json.loads(args.annotations.read_text(encoding="utf-8")) if args.annotations else None
    summary = summarize(dataset, report, annotations)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.run:
        args.output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        (args.output / f"{stamp}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        labels = {
            "cases": [
                {
                    "case_id": r["case_id"],
                    "reviewed": False,
                    "matches": [],
                    "accepted_without_edit": [],
                    "evidence_supported": [],
                }
                for r in report["results"]
            ]
        }
        (args.output / f"{stamp}.annotations.json").write_text(
            json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告与空白人工标注表已写入：{args.output}")


if __name__ == "__main__":
    main()
