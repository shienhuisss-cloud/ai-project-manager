# -*- coding: utf-8 -*-
"""
模型调用客户端。

走 OpenAI 兼容的 /chat/completions 接口，能接 DeepSeek、Kimi、GLM、通义等
任何提供这个端点的服务商。

环境变量（或 .env）：
  LLM_API_KEY    密钥
  LLM_BASE_URL   接口地址，例如 https://api.deepseek.com/v1（自动拼 /chat/completions）
  LLM_MODEL      模型名，例如 deepseek-chat
  LLM_TIMEOUT    超时秒数，默认 60

密钥只放后端环境，不进前端和源码。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

_ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_dotenv(path):
    # 简单解析 KEY=VALUE，忽略 # 注释
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                out[key] = value
    return out


_dotenv = _load_dotenv(_ENV_FILE)


def _env(name, default=""):
    return os.environ.get(name, _dotenv.get(name, default))


def get_config() -> dict:
    return {
        "api_key": _env("LLM_API_KEY", ""),
        "base_url": _env("LLM_BASE_URL", "").rstrip("/"),
        "model": _env("LLM_MODEL", ""),
        "timeout": float(_env("LLM_TIMEOUT", "60") or 60),
    }


def is_configured() -> bool:
    c = get_config()
    return bool(c["api_key"] and c["base_url"] and c["model"])


def config_summary() -> dict:
    c = get_config()
    return {"configured": is_configured(), "model": c["model"] or None}


class LLMError(Exception):
    def __init__(self, kind, message):
        self.kind = kind  # unconfigured / timeout / http / invalid
        self.message = message
        super().__init__(message)


def _endpoint(cfg) -> str:
    base = cfg["base_url"]
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json(text: str):
    # 模型有时会包一层 ``` 代码块，先去掉
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 实在不行就找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("invalid", "模型返回的内容不是有效 JSON")


def chat_json(messages, retry=1):
    # 返回 { ok, data, error, kind, model, usage, duration_ms, retried }
    cfg = get_config()
    if not is_configured():
        return {
            "ok": False, "kind": "unconfigured", "data": None,
            "error": "尚未配置模型。请在项目根目录创建 .env（参考 .env.example）或设置环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。",
            "model": None, "usage": None, "duration_ms": 0, "retried": False,
        }

    url = _endpoint(cfg)
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }

    last_error = None
    for attempt in range(retry + 1):
        start = time.time()
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + cfg["api_key"],
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            duration_ms = int((time.time() - start) * 1000)

            choice = body.get("choices", [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            usage = body.get("usage") or None

            try:
                data = _extract_json(content)
            except LLMError as e:
                last_error = e
                if attempt < retry:
                    # 把错误反馈给模型，让它重试一次
                    payload["messages"] = messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"上面的输出无法解析为 JSON（错误：{e.message}）。请重新只输出一个合法的 JSON 对象。"},
                    ]
                    continue
                return {
                    "ok": False, "kind": "invalid", "data": None,
                    "error": e.message,
                    "model": cfg["model"], "usage": usage, "duration_ms": duration_ms, "retried": attempt > 0,
                }

            return {
                "ok": True, "kind": "ok", "data": data,
                "error": None, "model": cfg["model"], "usage": usage,
                "duration_ms": duration_ms, "retried": attempt > 0,
            }

        except urllib.error.HTTPError as e:
            last_error = LLMError("http", f"模型服务返回 HTTP {e.code}")
            try:
                detail = e.read().decode("utf-8")[:300]
                last_error.message += f"：{detail}"
            except Exception:
                pass
            if attempt < retry and e.code in (429, 500, 502, 503, 504):
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = LLMError("timeout", f"模型请求超时或网络错误：{e}")
            if attempt < retry:
                continue
            break

    duration_ms = 0
    return {
        "ok": False,
        "kind": (last_error.kind if last_error else "error"),
        "data": None,
        "error": (last_error.message if last_error else "未知错误"),
        "model": cfg["model"], "usage": None, "duration_ms": duration_ms, "retried": True,
    }
