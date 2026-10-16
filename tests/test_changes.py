from copy import deepcopy

from backend.changes import apply_decisions


def fixture():
    return {
        "requirements": [
            {
                "id": "REQ-1",
                "title": "查询",
                "description": "查询订单",
                "intent": "requested",
                "stated_acceptance_criteria": ["两秒内返回"],
                "questions": ["如何登录？", "支持哪些日期？"],
                "evidence": [{"source_id": "old", "quote": "原始需求"}],
            }
        ],
        "questions": ["如何登录？", "支持哪些日期？"],
    }


def change(kind, **fields):
    return {
        "change_index": 0,
        "kind": kind,
        "related_requirement_ids": ["REQ-1"],
        "proposed_after": "查询订单并按日期筛选",
        "title": "订单查询",
        "intent": "requested",
        "questions": [],
        "stated_acceptance_criteria": None,
        "evidence": [{"source_id": "new", "quote": "新沟通"}],
        **fields,
    }


def test_clarification_resolves_only_answered_questions():
    original = fixture()
    before = deepcopy(original)
    result = apply_decisions(
        original,
        [change("clarification", resolved_questions=["如何登录？"])],
        [{"change_index": 0, "decision": "accept"}],
    )
    assert result["questions"] == ["支持哪些日期？"]
    assert result["requirements"][0]["questions"] == ["支持哪些日期？"]
    assert result["requirements"][0]["stated_acceptance_criteria"] == ["两秒内返回"]
    assert original == before


def test_explicit_empty_acceptance_criteria_can_clear_old_conditions():
    result = apply_decisions(
        fixture(),
        [change("modified", stated_acceptance_criteria=[])],
        [{"change_index": 0, "decision": "accept"}],
    )
    assert result["requirements"][0]["stated_acceptance_criteria"] == []


def test_unchanged_does_not_add_questions_or_change_snapshot():
    original = fixture()
    assert (
        apply_decisions(original, [change("unchanged")], [{"change_index": 0, "decision": "accept"}])
        == original
    )


def test_uncertain_manual_revision_becomes_pending_question():
    original = fixture()
    result = apply_decisions(
        original,
        [change("uncertain", related_requirement_ids=[], questions=["原问题？"])],
        [{"change_index": 0, "decision": "accept", "revised_after": "人工问题：价格一致吗？"}],
    )
    assert result["requirements"] == original["requirements"]
    assert result["questions"][-1] == "人工问题：价格一致吗？"
    assert "原问题？" not in result["questions"]
