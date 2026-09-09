# -*- coding: utf-8 -*-
"""
本地 mock 模型服务（仅用于 M3 验收测试，不是产品的一部分）。

模拟一个 OpenAI 兼容的 /chat/completions 端点，根据请求里最后一条 user 消息的内容
返回预置的提取/比较 JSON。可模拟三种行为：
  1. 正常返回（提取 / 比较）
  2. 返回伪造 source_id（用于 T08 校验失败测试）
  3. 返回非 JSON（用于 invalid 测试）

用法：python mock_model.py --port 9001
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def extract_result(sources):
    # 从真实 source_id 取第一条，构造带真实 source_id 与真实引文的提取结果
    sid = sources[0]["source_id"] if sources else "SRC-xxx"
    text = sources[0]["text"] if sources else ""
    quote = text[:20]
    return {
        "candidates": [
            {"title": "预约挂号功能", "description": "患者可在线选医生预约。",
             "intent": "requested", "stated_acceptance_criteria": [],
             "evidence": [{"source_id": sid, "quote": quote}], "questions": []},
        ],
        "questions": ["是否需要在线支付挂号费？"],
    }


def compare_result(new_sources):
    sid = new_sources[0]["source_id"] if new_sources else "SRC-xxx"
    text = new_sources[0]["text"] if new_sources else ""
    quote = text[:20]
    return {
        "changes": [
            {"kind": "added", "related_requirement_ids": [],
             "proposed_after": "新增预约挂号功能。", "reason": "新材料提出预约挂号。",
             "evidence": [{"source_id": sid, "quote": quote}], "questions": [],
             "title": "预约挂号"},
        ]
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode("utf-8"))
        messages = body.get("messages", [])
        # 找最后一条 user 消息
        user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user = m.get("content", "")
                break

        # 根据 user 内容里的输入决定行为
        if isinstance(user, str) and "FORCE_BAD_SOURCE" in user:
            # T08：返回伪造 source_id
            content = json.dumps({"candidates": [
                {"title": "伪证据需求", "intent": "requested", "evidence": [
                    {"source_id": "SRC-NOT-EXIST", "quote": "不存在的内容"}], "questions": []}]})
        elif isinstance(user, str) and "FORCE_BAD_JSON" in user:
            content = "这不是 JSON"
        else:
            # 解析 user JSON 里的 sources / new_sources
            try:
                u = json.loads(user)
            except Exception:
                u = {}
            if "sources" in u:
                content = json.dumps(extract_result(u["sources"]), ensure_ascii=False)
            elif "new_sources" in u:
                content = json.dumps(compare_result(u["new_sources"]), ensure_ascii=False)
            else:
                content = json.dumps({"candidates": [], "questions": []}, ensure_ascii=False)

        resp = {
            "id": "mock-1", "object": "chat.completion",
            "model": body.get("model", "mock-model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9001)
    args = ap.parse_args()
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock model on http://127.0.0.1:{args.port}")
    srv.serve_forever()
