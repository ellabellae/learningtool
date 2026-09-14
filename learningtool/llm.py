"""The one seam between the pipeline and the model.

    generate.py / repair.py / audit.py ──▶ LLM.structured(system, blocks, schema) ──▶ text, stop_reason, usage
                                              │
                                              ├── AnthropicLLM: client.messages.stream(...output_config.format=json_schema)
                                              └── FakeLLM: canned replies for tests and the LEARN_LLM_FAKE env var

Streaming is used so a lesson-sized output never trips the HTTP timeout; the
final message is read with get_final_message(). stop_reason is returned so the
caller can turn "max_tokens" into a clear message instead of a JSON error.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = os.environ.get("LEARN_MODEL", "claude-opus-5")
MAX_OUTPUT_TOKENS = 64000
TIMEOUT_SECONDS = 600
MAX_RETRIES = 3
EFFORT = os.environ.get("LEARN_EFFORT", "high")


@dataclass
class Block:
    """One content block of the user turn. `cache=True` marks the end of the stable prefix."""

    text: str
    cache: bool = False


@dataclass
class Reply:
    text: str
    stop_reason: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_details: dict | None = None
    elapsed: float = 0.0


class LLMError(Exception):
    """User-facing model failure; the message says what to do next."""


class Truncated(LLMError):
    pass


class Refused(LLMError):
    pass


class LLM(Protocol):
    def structured(self, system: str, blocks: list[Block], schema: dict, *, progress=None) -> Reply: ...


_SCHEMA_NOISE = {"title", "description", "default", "examples", "pattern", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "format"}


def lean_schema(schema):
    """Strip everything the API's grammar compiler does not need. Keeps the shape
    (type, properties, required, additionalProperties, enum, items, anyOf, $defs,
    $ref); drops titles, descriptions, and value constraints, which inflate the
    compiled grammar until the API refuses it. Pydantic re-checks the constraints
    on our side anyway."""
    if isinstance(schema, dict):
        return {k: lean_schema(v) for k, v in schema.items() if k not in _SCHEMA_NOISE}
    if isinstance(schema, list):
        return [lean_schema(v) for v in schema]
    return schema


PROMPT_GUIDED_SUFFIX = (
    "\n\nReply with one JSON object and nothing else: no prose before or after, no code fences. "
    "It must match this JSON schema exactly (every required key present, no extra keys):\n"
)


class AnthropicLLM:
    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic(max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS)
        self.last_mode = ""

    def structured(self, system: str, blocks: list[Block], schema: dict, *, progress=None) -> Reply:
        import anthropic

        content = []
        for b in blocks:
            item: dict = {"type": "text", "text": b.text}
            if b.cache:
                item["cache_control"] = {"type": "ephemeral"}
            content.append(item)
        lean = lean_schema(schema)
        t0 = time.monotonic()
        try:
            try:
                final = self._call(system, content, lean, progress, t0)
                self.last_mode = "grammar"
            except anthropic.BadRequestError as exc:
                if "grammar" not in str(exc).lower():
                    raise
                # The API refused to compile the schema. Ask for JSON in the prompt instead;
                # parse_json + Pydantic validate the reply on our side.
                guided = content + [{"type": "text", "text": PROMPT_GUIDED_SUFFIX + json.dumps(lean)}]
                final = self._call(system, guided, None, progress, t0)
                self.last_mode = "prompt-guided"
        except anthropic.AuthenticationError as exc:
            raise LLMError("the API rejected the key (AuthenticationError). Check ANTHROPIC_API_KEY, then re-run.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("rate limited after retries (RateLimitError). Wait a minute and re-run.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the API (APIConnectionError). Check the network and re-run.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code} after retries: {exc.message}. Re-run.") from exc
        text = "".join(b.text for b in final.content if b.type == "text")
        usage = final.usage
        return Reply(
            text=text, stop_reason=final.stop_reason or "", model=final.model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            stop_details=(final.stop_details.model_dump() if getattr(final, "stop_details", None) else None),
            elapsed=time.monotonic() - t0,
        )

    def _call(self, system: str, content: list[dict], schema: dict | None, progress, t0: float):
        output_config: dict = {"effort": EFFORT}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        with self.client.messages.stream(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config=output_config,
        ) as stream:
            for event in stream:
                if progress and event.type in ("content_block_delta", "message_start"):
                    progress(time.monotonic() - t0)
            return stream.get_final_message()


class FakeLLM:
    """Replays canned replies. Records every call so tests can assert on the prompt."""

    def __init__(self, replies: list[Reply | dict | str]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def structured(self, system: str, blocks: list[Block], schema: dict, *, progress=None) -> Reply:
        self.calls.append({"system": system, "blocks": blocks, "schema": schema})
        if not self.replies:
            raise LLMError("FakeLLM has no replies left")
        r = self.replies.pop(0)
        if isinstance(r, Reply):
            return r
        if isinstance(r, dict):
            return Reply(text=json.dumps(r), stop_reason="end_turn", model="fake")
        return Reply(text=r, stop_reason="end_turn", model="fake")


_FAKE: FakeLLM | None = None


def get_llm() -> LLM:
    """The real client, or a FakeLLM when LEARN_LLM_FAKE names a JSON file of replies.
    The fake is created once per process so a chain (generate, audit, repair) consumes replies in order."""
    global _FAKE
    fake = os.environ.get("LEARN_LLM_FAKE")
    if fake:
        if _FAKE is None:
            data = json.loads(Path(fake).read_text(encoding="utf-8"))
            replies = data if isinstance(data, list) else [data]
            _FAKE = FakeLLM([Reply(**r) if isinstance(r, dict) and "stop_reason" in r else r for r in replies])
        return _FAKE
    return AnthropicLLM()


def check_result(reply: Reply, what: str) -> None:
    if reply.stop_reason == "max_tokens":
        raise Truncated(
            f"{what} was cut off at {reply.output_tokens} output tokens (the model's reply ran past the limit). "
            "This paper is too long for one lesson; chunking arrives in v1.1."
        )
    if reply.stop_reason == "refusal":
        cat = (reply.stop_details or {}).get("category")
        raise Refused(f"the model declined to write {what}" + (f" (category: {cat})" if cat else "") + ".")


def parse_json(reply: Reply, what: str) -> dict:
    check_result(reply, what)
    text = reply.text.strip()
    # Prompt-guided replies sometimes arrive fenced or with a sentence around them.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    if not text.startswith("{"):
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b > a:
            text = text[a : b + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{what}: the model's reply was not valid JSON ({exc.msg} at char {exc.pos}). Re-run.") from exc


def ticker(label: str, stream=sys.stderr):
    """Returns a progress callback that rewrites one line: `<label>  waiting on model … 2:14`."""
    last = {"t": -5.0}

    def cb(elapsed: float) -> None:
        if elapsed - last["t"] < 1.0:
            return
        last["t"] = elapsed
        m, s = divmod(int(elapsed), 60)
        print(f"\r{label:<8} waiting on model … {m}:{s:02d}", end="", file=stream, flush=True)

    return cb
