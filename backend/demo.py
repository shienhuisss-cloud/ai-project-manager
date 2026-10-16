"""演示材料与固定结果；与真实模型共用证据校验。"""

import json


DEMO_SOURCE_001 = (
    "客户：做一个官网，展示我们的产品，让客户能联系我们。\n"
    "开发者：第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？\n"
    "客户：对。先不做在线支付，也不做会员。\n"
    "开发者：产品信息需要你们自己在后台维护吗？\n"
    "客户：这个还没想好，我们下次确定。"
)

DEMO_SOURCE_002 = (
    "客户：现在想加一个会员登录，老客户登录后能看到专属价格。\n"
    "客户：在线支付以后可能再考虑，这次先不要做。\n"
    "开发者：不同老客户看到的价格一样吗？\n"
    "客户：还没定，我问一下销售。"
)

DEMO_EXTRACTION = {
    "requirements": [
        {
            "title": "官网展示产品并可联系",
            "description": "做一个官网，展示产品，让客户能联系到我们。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [{"source_id": "SRC-001", "quote": "做一个官网，展示我们的产品，让客户能联系我们。"}],
            "questions": [],
        },
        {
            "title": "首页",
            "description": "官网第一版包含首页页面。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [
                {
                    "source_id": "SRC-001",
                    "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？",
                }
            ],
            "questions": [],
        },
        {
            "title": "产品列表",
            "description": "官网第一版包含产品列表页面。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [
                {
                    "source_id": "SRC-001",
                    "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？",
                }
            ],
            "questions": [],
        },
        {
            "title": "产品详情",
            "description": "官网第一版包含产品详情页面。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [
                {
                    "source_id": "SRC-001",
                    "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？",
                }
            ],
            "questions": [],
        },
        {
            "title": "联系我们",
            "description": "官网第一版包含联系我们页面。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [
                {
                    "source_id": "SRC-001",
                    "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？",
                }
            ],
            "questions": [],
        },
        {
            "title": "明确排除：在线支付",
            "description": "第一版不做在线支付。",
            "intent": "excluded",
            "stated_acceptance_criteria": [],
            "evidence": [{"source_id": "SRC-001", "quote": "对。先不做在线支付，也不做会员。"}],
            "questions": [],
        },
        {
            "title": "明确排除：会员",
            "description": "第一版不做会员功能。",
            "intent": "excluded",
            "stated_acceptance_criteria": [],
            "evidence": [{"source_id": "SRC-001", "quote": "对。先不做在线支付，也不做会员。"}],
            "questions": [],
        },
        {
            "title": "产品信息后台维护方式",
            "description": "产品信息是否由客户自己在后台维护，尚未确定。",
            "intent": "unclear",
            "stated_acceptance_criteria": [],
            "evidence": [{"source_id": "SRC-001", "quote": "这个还没想好，我们下次确定。"}],
            "questions": ["产品信息需要你们自己在后台维护吗？"],
        },
    ],
    "questions": ["产品信息需要你们自己在后台维护吗？"],
}


def _find_req_by_title(snapshot: dict, keyword: str):
    for r in snapshot.get("requirements", []):
        if keyword in (r.get("title") or ""):
            return r
    return None


def build_demo_comparison(baseline_snapshot: dict) -> list:
    """生成固定示例的变更候选，并把 related_requirement_ids 关联到基线版本真实 ID。"""
    member_req = _find_req_by_title(baseline_snapshot, "会员")
    payment_req = _find_req_by_title(baseline_snapshot, "在线支付")

    changes = [
        {
            "kind": "modified" if member_req else "added",
            "title": "会员登录与专属价格",
            "intent": "requested",
            "related_requirement_ids": [member_req["id"]] if member_req else [],
            "proposed_after": "新增会员登录功能，老客户登录后可查看专属价格。",
            "reason": "新材料提出会员登录，与 V1 中“明确排除：会员”存在冲突，需人工决定是否纳入本期范围。",
            "evidence": [
                {"source_id": "SRC-002", "quote": "客户：现在想加一个会员登录，老客户登录后能看到专属价格。"}
            ],
            "questions": [],
        },
        {
            "kind": "uncertain",
            "intent": "unclear",
            "related_requirement_ids": [],
            "proposed_after": "老客户专属价格规则待确认（不同老客户价格是否一致）。",
            "reason": "新材料提到专属价格，但价格规则尚未确定。",
            "evidence": [
                {
                    "source_id": "SRC-002",
                    "quote": "开发者：不同老客户看到的价格一样吗？\n客户：还没定，我问一下销售。",
                }
            ],
            "questions": ["不同老客户看到的价格是否一样？"],
        },
        {
            "kind": "unchanged",
            "related_requirement_ids": [payment_req["id"]] if payment_req else [],
            "proposed_after": "在线支付仍为明确排除项，不纳入本期。",
            "reason": "新材料说明“在线支付以后可能再考虑，这次先不要做”，维持排除。",
            "evidence": [{"source_id": "SRC-002", "quote": "客户：在线支付以后可能再考虑，这次先不要做。"}],
            "questions": [],
        },
    ]
    for i, c in enumerate(changes):
        c["change_index"] = i
    return changes


def extraction(source):
    result = json.loads(json.dumps(DEMO_EXTRACTION, ensure_ascii=False))
    for r in result["requirements"]:
        for e in r["evidence"]:
            e["source_id"] = source["id"]
    return result


def comparison(snapshot, source):
    changes = build_demo_comparison(snapshot)
    for c in changes:
        c.pop("change_index", None)
        for e in c["evidence"]:
            e["source_id"] = source["id"]
    return {"changes": changes}
