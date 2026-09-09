# -*- coding: utf-8 -*-
"""结构化校验测试：拒绝伪造的 source_id、引文、requirement_id、非法类型。"""
import pytest

from backend import validators


SRC = {"id": "SRC-1", "text": "客户：做一个官网，展示我们的产品。"}
SRC2 = {"id": "SRC-2", "text": "客户：加一个会员登录。"}


def test_extract_accepts_valid():
    data = {
        "candidates": [{
            "title": "官网", "description": "", "intent": "requested",
            "stated_acceptance_criteria": [], "questions": [],
            "evidence": [{"source_id": "SRC-1", "quote": "做一个官网，展示我们的产品。"}],
        }],
        "questions": [],
    }
    out = validators.validate_extract(data, [SRC])
    assert out["requirements"][0]["title"] == "官网"


def test_extract_rejects_fake_source_id():
    data = {
        "candidates": [{
            "title": "官网", "intent": "requested",
            "evidence": [{"source_id": "SRC-NOT-EXIST", "quote": "做一个官网"}],
        }],
        "questions": [],
    }
    with pytest.raises(validators.ValidationError):
        validators.validate_extract(data, [SRC])


def test_extract_rejects_forged_quote():
    """引用文字在原文中不存在 → 拒绝（伪造引文）。"""
    data = {
        "candidates": [{
            "title": "官网", "intent": "requested",
            "evidence": [{"source_id": "SRC-1", "quote": "这段文字原文里根本没有"}],
        }],
        "questions": [],
    }
    with pytest.raises(validators.ValidationError):
        validators.validate_extract(data, [SRC])


def test_extract_rejects_cross_project_source():
    """source_id 不属于本次传入的 sources（跨项目）→ 拒绝。"""
    data = {
        "candidates": [{
            "title": "官网", "intent": "requested",
            "evidence": [{"source_id": "SRC-2", "quote": "加一个会员登录"}],
        }],
        "questions": [],
    }
    with pytest.raises(validators.ValidationError):
        validators.validate_extract(data, [SRC])


def test_compare_rejects_fake_requirement_id():
    data = {
        "changes": [{
            "kind": "modified", "related_requirement_ids": ["REQ-FAKE"],
            "proposed_after": "改", "reason": "", "questions": [],
            "evidence": [{"source_id": "SRC-2", "quote": "加一个会员登录"}],
        }],
    }
    baseline = {"requirements": [{"id": "REQ-0001", "title": "官网"}]}
    with pytest.raises(validators.ValidationError):
        validators.validate_compare(data, baseline, [SRC2])


def test_compare_rejects_unknown_kind():
    data = {
        "changes": [{
            "kind": "totally-made-up", "related_requirement_ids": [],
            "proposed_after": "x", "reason": "", "questions": [],
            "evidence": [{"source_id": "SRC-2", "quote": "加一个会员登录"}],
        }],
    }
    with pytest.raises(validators.ValidationError):
        validators.validate_compare(data, {"requirements": []}, [SRC2])


def test_compare_requires_proposed_after_for_added():
    data = {
        "changes": [{
            "kind": "added", "related_requirement_ids": [],
            "proposed_after": "", "reason": "", "questions": [],
            "evidence": [{"source_id": "SRC-2", "quote": "加一个会员登录"}],
        }],
    }
    with pytest.raises(validators.ValidationError):
        validators.validate_compare(data, {"requirements": []}, [SRC2])


def test_compare_accepts_valid_added():
    data = {
        "changes": [{
            "kind": "added", "related_requirement_ids": [],
            "proposed_after": "新增会员登录", "reason": "r", "questions": [],
            "evidence": [{"source_id": "SRC-2", "quote": "加一个会员登录"}],
        }],
    }
    out = validators.validate_compare(data, {"requirements": []}, [SRC2])
    assert out[0]["kind"] == "added"
