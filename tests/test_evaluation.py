import json
from pathlib import Path

import pytest

from scripts.evaluate import summarize, validate_case


def test_all_synthetic_references_are_valid():
    dataset = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation/cases.json").read_text(encoding="utf-8")
    )
    assert len(dataset["cases"]) == 30
    assert len({c["id"] for c in dataset["cases"]}) == 30
    for case in dataset["cases"]:
        validate_case(case, case["expected"])


def test_metrics_require_manual_labels_and_reject_duplicate_matches():
    dataset = {
        "dataset_version": "test",
        "cases": [
            {"id": "c", "task": "extract", "expected": {"requirements": [{"title": "a"}, {"title": "b"}]}}
        ],
    }
    report = {
        "dataset_version": "test",
        "results": [
            {
                "case_id": "c",
                "status": "ok",
                "predictions": [{"title": "a"}],
                "duration_ms": 100,
                "attempts": 2,
                "cost": None,
                "currency": None,
            }
        ],
    }
    assert summarize(dataset, report)["semantic_metrics"].startswith("待人工标注")
    label = {
        "case_id": "c",
        "reviewed": True,
        "matches": [{"gold_index": 0, "prediction_index": 0}],
        "accepted_without_edit": [0],
        "evidence_supported": [0],
    }
    metrics = summarize(dataset, report, {"cases": [label]})["semantic_metrics"]
    assert metrics["recall"] == 0.5 and metrics["precision"] == 1
    label["matches"] *= 2
    with pytest.raises(ValueError, match="重复"):
        summarize(dataset, report, {"cases": [label]})
