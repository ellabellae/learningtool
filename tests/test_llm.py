"""The API seam: lean schema, grammar-size fallback, fenced-JSON tolerance."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from learningtool.llm import AnthropicLLM, Block, LLMError, Reply, lean_schema, parse_json
from learningtool.schema import draft_json_schema


def test_lean_schema_drops_constraints_and_keeps_shape():
    lean = lean_schema(anthropic.transform_schema(draft_json_schema()))
    text = json.dumps(lean)
    for noise in ("pattern", "minLength", "minItems", "title", "description", "default"):
        assert f'"{noise}"' not in text, noise
    scene = lean["$defs"]["Scene"]
    assert scene["properties"]["role"]["enum"][0] == "prereq"
    assert scene["additionalProperties"] is False
    assert "headline" in scene["required"]
    # roughly half the size of the full schema
    assert len(text) < len(json.dumps(draft_json_schema())) * 0.7


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here it is:\n{"a": 1}\nDone.', '```\n{"a": 1}```'],
)
def test_parse_json_tolerates_fences_and_prose(text):
    assert parse_json(Reply(text=text, stop_reason="end_turn"), "x") == {"a": 1}


class _FakeStream:
    def __init__(self, text):
        self._text = text

    def __iter__(self):
        return iter([SimpleNamespace(type="message_start"), SimpleNamespace(type="content_block_delta")])

    def get_final_message(self):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            stop_reason="end_turn",
            model="fake-model",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
            stop_details=None,
        )


class _FakeMessages:
    """First call with a format refuses on grammar size; a call without a format succeeds."""

    def __init__(self):
        self.calls = []

    @contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if "format" in kwargs["output_config"]:
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            resp = httpx.Response(400, request=req, json={"type": "error", "error": {"type": "invalid_request_error", "message": "The compiled grammar is too large, which would cause performance issues."}})
            raise anthropic.BadRequestError("The compiled grammar is too large", response=resp, body=None)
        yield _FakeStream('{"hook_question": "ok"}')


def test_grammar_too_large_falls_back_to_prompt_guided_json():
    fake = _FakeMessages()
    llm = AnthropicLLM(model="fake-model", client=SimpleNamespace(messages=fake))
    reply = llm.structured("sys", [Block(text="paper", cache=True), Block(text="profile")], {"type": "object", "properties": {"hook_question": {"type": "string", "minLength": 1}}, "required": ["hook_question"]})
    assert reply.text == '{"hook_question": "ok"}' and llm.last_mode == "prompt-guided"
    assert len(fake.calls) == 2
    first, second = fake.calls
    assert "format" in first["output_config"] and "minLength" not in json.dumps(first["output_config"]["format"])
    assert "format" not in second["output_config"]
    tail = second["messages"][0]["content"][-1]["text"]
    assert "no code fences" in tail and '"hook_question"' in tail
    assert second["messages"][0]["content"][0].get("cache_control") == {"type": "ephemeral"}


def test_other_bad_requests_are_not_retried_without_schema():
    class Refusing(_FakeMessages):
        @contextmanager
        def stream(self, **kwargs):
            self.calls.append(kwargs)
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            resp = httpx.Response(400, request=req, json={"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens must be positive"}})
            raise anthropic.BadRequestError("max_tokens must be positive", response=resp, body=None)
            yield  # pragma: no cover

    fake = Refusing()
    llm = AnthropicLLM(model="fake-model", client=SimpleNamespace(messages=fake))
    with pytest.raises(LLMError, match="API error 400"):
        llm.structured("sys", [Block(text="x")], {"type": "object"})
    assert len(fake.calls) == 1
