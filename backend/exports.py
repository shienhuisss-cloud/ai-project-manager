"""仅导出目标版本及其祖先，不把后续沟通混进历史文档。"""

import json

INTENTS = {"requested": "提出", "excluded": "排除", "hypothetical": "设想", "unclear": "待澄清"}


def build_markdown(project, version, lineage, runs):
    lines = [
        f"# {project['name']} — 需求文档",
        "",
        f"- 版本：V{version['seq']}",
        f"- 确认时间：{version['confirmed_at']}",
        f"- 确认人：{version['confirmed_by']}",
        "",
        "> 此文档是人工确认的需求快照，不代表客户签署的合同。",
        "",
        "## 需求清单",
        "",
    ]
    for r in version["snapshot"]["requirements"]:
        lines += [
            f"### {r['id']} · {r['title']} [{INTENTS.get(r['intent'], r['intent'])}]",
            "",
            r["description"],
            "",
        ]
        for criterion in r.get("stated_acceptance_criteria", []):
            lines.append(f"- 验收条件：{criterion}")
        for e in r["evidence"]:
            lines += [f"- 来源：{e['source_id']}", "  > " + e["quote"].replace("\n", "\n  > ")]
        for q in r["questions"]:
            lines.append(f"- 待澄清：{q}")
        lines.append("")
    lines += ["## 项目级待澄清问题", ""] + [f"- {q}" for q in version["snapshot"]["questions"]]
    lines += ["", "## 版本链", ""] + [f"- V{v['seq']} · {v['confirmed_at']}" for v in reversed(lineage)]
    for run in runs:
        if run["demo_mode"]:
            lines += ["", "> 示例模式：该版本关联内置示例，非真实模型效果证明。"]
        decisions = {d["change_index"]: d for d in run["decisions"]}
        changes = json.loads(run["result_json"]).get("changes", [])
        if changes:
            lines += ["", "## 关联变更审核", ""]
        for c in changes:
            d = decisions.get(c["change_index"])
            label = {"accept": "采纳", "ignore": "忽略"}.get((d or {}).get("decision"), "未决定")
            text = (d or {}).get("revised_after") or c["proposed_after"]
            lines.append(f"- [{label}/{c['kind']}] {text}")
    return "\n".join(lines).rstrip() + "\n"
