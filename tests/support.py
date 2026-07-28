"""Shared test helpers: a throwaway vault and a scripted stand-in for a model."""

from __future__ import annotations

import json
from pathlib import Path

from voice_journal_formatter.config import Config, load_config
from voice_journal_formatter.store import State

CONFIG_TEMPLATE = """
[vault]
root = "{root}"
raw = "capture/raw"
archive = "archive"
failed = "capture/failed"

[runtime]
state_dir = "{state}"
min_file_age_seconds = 0
settle_seconds = 0
max_attempts = {max_attempts}

[llm]
backend = "ollama"
model = "test-model"

[prompt]
cleanup = "Clean up this transcript."
name_corrections = ["Use \\"Niamh\\", not \\"Neve\\"."]

[[note_type]]
name = "audio-journal"
outputs = [
  {{ dir = "capture/formatted", filename = "{{date}}_{{type}}-formatted_{{slug}}.md" }},
]

[[note_type]]
name = "thought"
instruction = "Keep it exploratory."
outputs = [
  {{ dir = "archive", filename = "{{date}}_{{type}}-formatted_{{slug}}.md" }},
  {{ dir = "thoughts", filename = "{{date}} {{title}}.md" }},
]
"""


def make_config(tmp: Path, max_attempts: int = 3) -> Config:
    """Build a real config file in a temp dir and load it."""
    vault = tmp / "vault"
    (vault / "capture" / "raw").mkdir(parents=True, exist_ok=True)
    config_path = tmp / "config.toml"
    config_path.write_text(
        CONFIG_TEMPLATE.format(
            root=vault.as_posix(),
            state=(tmp / "state").as_posix(),
            max_attempts=max_attempts,
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    # Establish the install marker now, so captures written by a test afterwards
    # look "new" the way real captures do.
    State(config.state_dir).reset_install_marker()
    return config


def write_capture(config: Config, name: str, text: str = "um so anyway") -> Path:
    path = config.raw_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def response(slug: str = "memory-and-forgetting", title: str | None = None,
             markdown: str = "## Heading\n\nCleaned text.") -> str:
    return json.dumps(
        {
            "slug": slug,
            "title": title if title is not None else "Memory And Forgetting",
            "formatted_markdown": markdown,
        }
    )


class StubBackend:
    """Returns scripted responses in order; raises any response that is an
    exception. Records prompts so tests can assert on prompt assembly."""

    name = "stub"

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("StubBackend ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)
