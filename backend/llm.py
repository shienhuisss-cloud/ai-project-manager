"""OpenAI 兼容模型调用：结构化输出、校验重试及总成本计量。"""

import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config  # noqa: F401 — 加载 .env
from .validators import ValidationError


def get_config():
    def price(name):
        try:
            value = float(os.getenv(name, ""))
            return value if value >= 0 else None
        except ValueError:
            return None

    return {
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", "").rstrip("/"),
        "model": os.getenv("LLM_MODEL", ""),
        "timeout": float(os.getenv("LLM_TIMEOUT", "60")),
        "format": os.getenv("LLM_RESPONSE_FORMAT", "prompt"),
        "input_price": price("LLM_INPUT_PRICE_PER_MILLION"),
        "output_price": price("LLM_OUTPUT_PRICE_PER_MILLION"),
        "currency": os.getenv("LLM_CURRENCY", "CNY"),
    }


def is_configured():
    c = get_config()
    return bool(c["api_key"] and c["base_url"] and c["model"])


def config_summary():
    return {"configured": is_configured(), "model": get_config()["model"] or None}


def _extract_json(text):
    if not isinstance(text, str):
        raise ValidationError("模型 content 必须为字符串")
    text = re.sub(r"^\x60\x60\x60[a-zA-Z]*\s*", "", text.strip())
    text = re.sub(r"\s*\x60\x60\x60$", "", text)
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("需要 JSON 对象")
        return result
    except (ValueError, TypeError) as e:
        raise ValidationError("模型返回内容不是 JSON 对象") from e


def chat_json(messages, retry=1, schema=None, validate=None):
    cfg = get_config()
    started = time.monotonic()
    attempts = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    usage_complete = True

    def result(ok, kind, data=None):
        # 任何一次未返回 usage 时不声称掌握总费用。
        cost = None
        if usage_complete and attempts and cfg["input_price"] is not None and cfg["output_price"] is not None:
            cost = (
                usage["prompt_tokens"] * float(cfg["input_price"])
                + usage["completion_tokens"] * float(cfg["output_price"])
            ) / 1000000
        return {
            "ok": ok,
            "kind": kind,
            "data": data,
            "model": cfg["model"] or None,
            "usage": usage if usage_complete and attempts else None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "attempts": attempts,
            "retried": attempts > 1,
            "cost": cost,
            "currency": cfg["currency"] if cost is not None else None,
        }

    if not is_configured():
        return result(False, "unconfigured")
    payload = {"model": cfg["model"], "messages": messages, "temperature": 0, "stream": False}
    if cfg["format"] == "json_schema" and schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "analysis", "strict": True, "schema": schema},
        }
    elif cfg["format"] == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif cfg["format"] != "prompt":
        return result(False, "invalid")
    url = cfg["base_url"]
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    kind = "invalid"
    for attempt in range(retry + 1):
        attempts += 1
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
                body = json.loads(response.read().decode())
            call_usage = body.get("usage")
            if isinstance(call_usage, dict) and all(isinstance(call_usage.get(k), int) for k in usage):
                for key in usage:
                    usage[key] += call_usage[key]
            else:
                usage_complete = False
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValidationError("缺少模型输出")
            content = (choices[0].get("message") or {}).get("content")
            data = _extract_json(content)
            if validate:
                validate(data)
            return result(True, "ok", data)
        except ValidationError as e:
            kind = "invalid"
            # 校验错误不包含原始模型输出或服务商错误体，避免泄漏敏感资料。
            payload["messages"] = messages + [
                {
                    "role": "user",
                    "content": "上一轮未通过格式或引用校验。请按 JSON Schema 重新输出，逐字引用材料。校验原因："
                    + str(e)[:1200],
                }
            ]
        except urllib.error.HTTPError as e:
            usage_complete = False
            kind = "http"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError):
            usage_complete = False
            kind = "timeout"
        except (ValueError, TypeError, AttributeError, KeyError, IndexError):
            usage_complete = False
            kind = "invalid"
        if attempt < retry and kind in ("http", "timeout"):
            time.sleep(min(0.25 * (2**attempt), 2))
    return result(False, kind)
