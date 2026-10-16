"""格式、引用和关联语义校验。"""

from pydantic import ValidationError as SchemaError

from .schemas import ExtractResult, CompareResult


class ValidationError(Exception):
    pass


def validate_extract(data, sources):
    if isinstance(data, dict) and "requirements" in data:
        data = {"candidates": data["requirements"], "questions": data.get("questions", [])}
    try:
        result = ExtractResult.model_validate(data).model_dump()
    except SchemaError as e:
        raise ValidationError(str(e)) from e
    for requirement in result["candidates"]:
        validate_evidence(requirement["evidence"], sources)
    return {"requirements": result["candidates"], "questions": result["questions"]}


def validate_evidence(evidence, sources):
    texts = {s["id"]: s["text"] for s in sources}
    for item in evidence:
        sid, quote = item["source_id"], item["quote"]
        if sid not in texts or not quote.strip() or quote not in texts[sid]:
            raise ValidationError("引文必须逐字存在于本次选中的材料中")


def validate_compare(data, baseline_snapshot, sources):
    try:
        changes = CompareResult.model_validate(data).model_dump()["changes"]
    except SchemaError as e:
        raise ValidationError(str(e)) from e
    valid_ids = {r["id"] for r in baseline_snapshot.get("requirements", [])}
    for i, change in enumerate(changes):
        related = change["related_requirement_ids"]
        if len(set(related)) != len(related) or any(r not in valid_ids for r in related):
            raise ValidationError("关联需求 ID 无效或重复")
        if change["kind"] in ("modified", "removed") and not related:
            raise ValidationError("修改、删除必须关联原需求")
        if change["kind"] == "added" and related:
            raise ValidationError("已有需求的范围变化应使用 modified")
        if change["kind"] in ("modified", "clarification") and len(related) > 1:
            raise ValidationError("请将多条需求的修改拆为独立变更")
        if change["kind"] in ("added", "modified", "clarification") and not change["proposed_after"].strip():
            raise ValidationError("新增、修改、澄清必须提供修改后内容")
        if change["kind"] == "uncertain" and not change["questions"]:
            raise ValidationError("待定项必须提供需要回答的问题")
        validate_evidence(change["evidence"], sources)
        change["change_index"] = i
    return changes
