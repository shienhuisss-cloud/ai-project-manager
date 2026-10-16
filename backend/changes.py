"""纯函数：把人工审核后的变更应用到版本副本。"""

from copy import deepcopy

from fastapi import HTTPException


def apply_decisions(snapshot, changes, decisions):
    result = deepcopy(snapshot)
    by_index = {c["change_index"]: c for c in changes}
    seen, touched = set(), set()
    for decision in decisions:
        idx = decision["change_index"]
        if idx in seen or idx not in by_index:
            raise HTTPException(422, "变更索引无效或重复")
        seen.add(idx)
        if decision["decision"] == "ignore":
            continue
        c = by_index[idx]
        kind, related = c["kind"], c["related_requirement_ids"]
        proposed = decision.get("revised_after") or c["proposed_after"]
        if kind == "unchanged":
            continue
        if kind in ("modified", "removed", "clarification"):
            if touched.intersection(related):
                raise HTTPException(422, "多条采纳项修改同一需求，请分别审核")
            touched.update(related)
        reqs = result["requirements"]
        target = next((r for r in reqs if related and r.get("id") == related[0]), None)
        if kind == "added":
            if related:
                raise HTTPException(422, "与原范围冲突的需求应使用修改类型，请重新分析")
            reqs.append(
                {
                    "title": c.get("title") or proposed[:80],
                    "description": proposed,
                    "intent": c.get("intent", "requested"),
                    "stated_acceptance_criteria": c.get("stated_acceptance_criteria") or [],
                    "evidence": c["evidence"],
                    "questions": c["questions"],
                }
            )
        elif kind == "removed":
            result["requirements"] = [r for r in reqs if r["id"] not in related]
        elif kind in ("modified", "clarification"):
            if target is not None:
                target.update(
                    description=proposed,
                    intent=c.get("intent", "requested"),
                    evidence=target["evidence"] + [e for e in c["evidence"] if e not in target["evidence"]],
                    questions=list(
                        dict.fromkeys(
                            [q for q in target["questions"] if q not in c.get("resolved_questions", [])]
                            + c["questions"]
                        )
                    ),
                )
                if c.get("title"):
                    target["title"] = c["title"]
                if c.get("stated_acceptance_criteria") is not None:
                    target["stated_acceptance_criteria"] = c["stated_acceptance_criteria"]
            elif kind == "clarification":
                reqs.append(
                    {
                        "title": c.get("title") or proposed[:80],
                        "description": proposed,
                        "intent": c.get("intent", "unclear"),
                        "stated_acceptance_criteria": c.get("stated_acceptance_criteria") or [],
                        "evidence": c["evidence"],
                        "questions": c["questions"],
                    }
                )
        resolved = c.get("resolved_questions", []) if kind == "clarification" else []
        result["questions"] = [q for q in result["questions"] if q not in resolved]
        questions = (
            [decision["revised_after"]]
            if kind == "uncertain" and decision.get("revised_after")
            else c["questions"]
        )
        for q in questions:
            if q not in result["questions"]:
                result["questions"].append(q)
    return result
