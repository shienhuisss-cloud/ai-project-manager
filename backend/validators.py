# -*- coding: utf-8 -*-
"""
后端校验（M3）——模型输出的结构化校验 + 证据/ID 归属检查。

规则（任务书）：
- 证据引用必须包含程序提供的 source_id 和原文片段，且引用文字确实存在。
- 模型只能引用后端传入的 ID；跨项目或伪造的 source_id / requirement_id 会被拒绝。
- 候选类型、字段约束由确定性代码校验，不信任模型自报。
"""
import json

from fastapi import HTTPException

ALLOWED_INTENTS = {"requested", "hypothetical", "excluded", "unclear"}
ALLOWED_KINDS = {"added", "modified", "removed", "clarification", "unchanged", "uncertain"}


class ValidationError(Exception):
    pass


def _valid_evidence(evidence, valid_source_ids):
    """校验证据数组：非空、source_id 属于本项目、quote 真实存在。"""
    if not isinstance(evidence, list) or not evidence:
        raise ValidationError("至少需要一条证据")
    cleaned = []
    for e in evidence:
        if not isinstance(e, dict):
            raise ValidationError("证据必须是对象")
        sid = str(e.get("source_id", ""))
        quote = (e.get("quote") or "").strip()
        if sid not in valid_source_ids:
            raise ValidationError(f"证据引用了不存在或不属于本项目的 source_id：{sid}")
        if not quote:
            raise ValidationError("证据缺少原文片段 quote")
        src_text = valid_source_ids[sid]
        if quote not in src_text:
            raise ValidationError(f"证据原文片段在 source {sid} 中不存在（伪造引文）")
        cleaned.append({"source_id": sid, "quote": quote})
    return cleaned


def validate_extract(data, sources):
    """
    校验提取结果。sources: [{id, text}, ...]
    返回清洗后的 {requirements, questions}。
    """
    if not isinstance(data, dict):
        raise ValidationError("提取结果不是对象")
    candidates = data.get("candidates", data.get("requirements", []))
    if not isinstance(candidates, list) or not candidates:
        raise ValidationError("提取结果缺少 candidates 或为空")

    valid_source_ids = {s["id"]: s["text"] for s in sources}
    requirements = []
    for c in candidates:
        if not isinstance(c, dict):
            raise ValidationError("候选必须是对象")
        title = (c.get("title") or "").strip()
        if not title:
            raise ValidationError("候选缺少标题 title")
        intent = c.get("intent")
        if intent not in ALLOWED_INTENTS:
            intent = "unclear"
        stated = c.get("stated_acceptance_criteria") or []
        if not isinstance(stated, list):
            stated = []
        evidence = _valid_evidence(c.get("evidence"), valid_source_ids)
        questions = c.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        requirements.append({
            "title": title,
            "description": (c.get("description") or "").strip(),
            "intent": intent,
            "stated_acceptance_criteria": [str(x) for x in stated],
            "evidence": evidence,
            "questions": [str(q) for q in questions],
        })

    questions = data.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    return {"requirements": requirements, "questions": [str(q) for q in questions]}


def validate_compare(data, baseline_snapshot, sources):
    """
    校验变更比较结果。baseline_snapshot: 基线版本快照（含稳定 requirement_id）。
    返回清洗后的 changes 列表（含 change_index）。
    """
    if not isinstance(data, dict):
        raise ValidationError("比较结果不是对象")
    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValidationError("比较结果缺少 changes 或为空")

    valid_source_ids = {s["id"]: s["text"] for s in sources}
    valid_req_ids = {r.get("id") for r in baseline_snapshot.get("requirements", [])}

    cleaned = []
    for i, c in enumerate(changes):
        if not isinstance(c, dict):
            raise ValidationError("变更候选必须是对象")
        kind = c.get("kind")
        if kind not in ALLOWED_KINDS:
            raise ValidationError(f"未知的变更类型：{kind}")
        rel = c.get("related_requirement_ids") or []
        if not isinstance(rel, list):
            rel = []
        for rid in rel:
            if rid not in valid_req_ids:
                raise ValidationError(f"变更引用了基线中不存在的 requirement_id：{rid}")
        evidence = _valid_evidence(c.get("evidence"), valid_source_ids)
        proposed_after = (c.get("proposed_after") or "").strip()
        # 类型约束：removed 需要有明确取消依据（体现在 reason/evidence），
        # added/modified 需要 proposed_after 非空
        if kind in ("added", "modified") and not proposed_after:
            raise ValidationError(f"{kind} 类型缺少 proposed_after")
        cleaned.append({
            "change_index": i,
            "kind": kind,
            "related_requirement_ids": [str(x) for x in rel],
            "proposed_after": proposed_after,
            "reason": (c.get("reason") or "").strip(),
            "evidence": evidence,
            "questions": [str(q) for q in (c.get("questions") or [])],
            "title": (c.get("title") or "").strip(),
        })
    return cleaned


def _err(message):
    return HTTPException(status_code=422, detail=f"模型输出校验失败：{message}")
