import io
import json
import urllib.error

from backend import llm, validators, prompts
from backend.schemas import ExtractResult, output_schema


def configured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-test-only")
    monkeypatch.setenv("LLM_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "json_schema")
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION", "2")


def response(data):
    return io.BytesIO(
        json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            }
        ).encode()
    )


def test_sends_schema_and_retries_invalid_evidence(monkeypatch):
    configured(monkeypatch)
    captured = []
    candidate = {"title": "官网", "intent": "requested", "evidence": [{"source_id": "s", "quote": "伪造"}]}
    bad = {"candidates": [candidate], "questions": []}
    good = {
        "candidates": [{**candidate, "evidence": [{"source_id": "s", "quote": "做官网"}]}],
        "questions": [],
    }

    def fake_urlopen(request, **kwargs):
        captured.append(json.loads(request.data))
        return response(bad if len(captured) == 1 else good)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    out = llm.chat_json(
        prompts.build_extract_messages({}, [{"source_id": "s", "text": "做官网"}]),
        schema=output_schema(ExtractResult),
        validate=lambda data: validators.validate_extract(data, [{"id": "s", "text": "做官网"}]),
    )
    assert out["ok"] and out["attempts"] == 2
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    assert "JSON Schema" in captured[0]["messages"][0]["content"]
    assert out["usage"]["total_tokens"] == 240
    assert abs(out["cost"] - 0.00028) < 1e-9


def test_provider_failure_does_not_echo_sensitive_body(monkeypatch):
    configured(monkeypatch)

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("url", 401, "secret-key-and-customer-data", {}, io.BytesIO(b"secret"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fail)
    out = llm.chat_json([], schema=output_schema(ExtractResult))
    assert not out["ok"] and out["kind"] == "http"
    assert out["attempts"] == 1 and out["cost"] is None
    assert "secret" not in json.dumps(out)


def test_unsupported_provider_can_use_prompt_only(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "prompt")

    def fake(request, **kwargs):
        assert "response_format" not in json.loads(request.data)
        return response({"changes": []})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    assert llm.chat_json([])["ok"] is True


def test_type_error_and_empty_changes_handled():
    import pytest

    with pytest.raises(validators.ValidationError):
        validators.validate_extract(
            {"candidates": [{"title": 123, "intent": "requested", "evidence": []}]}, []
        )
    assert validators.validate_compare({"changes": []}, {"requirements": []}, []) == []
