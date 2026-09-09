# -*- coding: utf-8 -*-
"""
模型调用客户端

通过「OpenAI 兼容」的 Chat Completions HTTP 接口调用任意中文/通用模型，
覆盖 DeepSeek、Moonshot(Kimi)、智谱 GLM、通义千问(DashScope)、OpenAI 等
绝大多数提供 OpenAI 兼容端点的服务商。

配置方式（二选一，环境变量优先于 .env）：
  1. 在项目根目录创建 .env 文件（内容见 .env.example）
  2. 在启动命令前设置环境变量

变量：
  LLM_API_KEY    必填，模型服务商提供的 API 密钥
  LLM_BASE_URL   必填，接口基础地址，例如 https://api.deepseek.com/v1
                 （程序会自动拼接 /chat/completions）
  LLM_MODEL      必填，模型名，例如 deepseek-chat
  LLM_TIMEOUT    可选，请求超时秒数，默认 60

密钥只保存在后端环境/文件中，绝不进入前端代码、导出文件或源码仓库。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

_ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_dotenv(path):
    """极简 .env 解析（KEY=VALUE 每行，忽略 # 注释）。"""
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


# ---------------------------------------------------------------------------
# 结构化调用
# ---------------------------------------------------------------------------

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
    """从模型返回文本中稳健提取 JSON 对象。"""
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("invalid", "模型返回的内容不是有效 JSON")


def chat_json(messages, retry=1):
    """
    调用模型并返回解析后的 JSON 对象（dict）。

    返回 dict: { "ok", "data", "error", "kind", "model", "usage", "duration_ms", "retried" }
    """
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
                    # 把错误反馈追加给模型，重试一次
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
