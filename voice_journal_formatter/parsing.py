"""Extracting a usable result from whatever the model actually returned.

Local models are asked for strict JSON and mostly comply. "Mostly" is the
problem: reasoning traces, Markdown fences, and leading prose all show up in
practice, so parsing is deliberately forgiving.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .naming import slugify, title_from_slug

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_FENCE = re.compile(r"^```(?:json)?\s*")
_CLOSE_FENCE = re.compile(r"\s*```$")


class ResultError(ValueError):
    """Raised when a model response cannot be turned into a usable note."""


@dataclass(frozen=True)
class Result:
    slug: str
    title: str
    markdown: str


def response_schema() -> dict:
    """JSON schema for backends that support structured output natively."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["slug", "title", "formatted_markdown"],
        "properties": {
            "slug": {
                "type": "string",
                "description": (
                    "A concise hyphenated thesis slug, around five words, "
                    "lowercase ASCII."
                ),
            },
            "title": {
                "type": "string",
                "description": "The same thesis as a short Title Case title.",
            },
            "formatted_markdown": {
                "type": "string",
                "description": "The cleaned transcript in Markdown.",
            },
        },
    }


def parse_json_response(output: str) -> dict:
    """Pull a JSON object out of a model response.

    Handles, in order: reasoning traces in <think> tags, Markdown code fences,
    and finally a brace-matched substring when the model wrapped the JSON in
    conversational text.
    """
    text = _THINK_BLOCK.sub("", output).strip()
    if text.startswith("```"):
        text = _CLOSE_FENCE.sub("", _OPEN_FENCE.sub("", text))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def validate_result(payload: dict) -> Result:
    """Turn a parsed response into a Result, deriving anything the model omitted.

    A missing slug is recoverable from the title and vice versa; missing body
    text is not, because there is nothing to write.
    """
    markdown = str(payload.get("formatted_markdown", "")).strip()
    if not markdown:
        raise ResultError("Model response did not include 'formatted_markdown'")

    slug = slugify(str(payload.get("slug") or payload.get("title") or ""))
    if not slug:
        raise ResultError("Model response did not include a usable slug or title")

    raw_title = str(payload.get("title") or "").strip()
    title = title_from_slug(slugify(raw_title) or slug)
    return Result(slug=slug, title=title, markdown=markdown)
