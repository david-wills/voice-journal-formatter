"""LLM backends.

A backend takes a prompt and returns raw model text. Parsing and validation
happen upstream, so adding a backend means implementing one method.

Backend errors deliberately never include the prompt. The prompt contains the
user's transcript, and error text ends up in logs.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .config import LLMConfig
from .parsing import response_schema

_STDERR_EXCERPT_CHARS = 800


class BackendError(RuntimeError):
    """Raised when a backend fails to produce a response."""


class Backend(Protocol):
    """Anything that can turn a prompt into model text."""

    name: str

    def generate(self, prompt: str) -> str: ...


class OllamaBackend:
    """Local inference via the ollama HTTP API. Nothing leaves the machine."""

    name = "ollama"

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self._config.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": self._config.think,
        }
        request = urllib.request.Request(
            self._config.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._config.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise BackendError(
                f"Could not reach ollama at {self._config.url} ({exc.reason}). "
                "Is `ollama serve` running?"
            ) from exc

        if body.get("error"):
            raise BackendError(f"ollama returned an error: {body['error']}")
        return str(body.get("response", ""))


class CodexBackend:
    """Optional backend shelling out to the codex CLI.

    Runs with approvals and sandboxing disabled so it can work unattended, which
    is a real trade-off: it grants the CLI full access to the machine for the
    duration of the call. Prefer the ollama backend unless you specifically want
    a hosted model.
    """

    name = "codex"

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def generate(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="vjf-") as workdir:
            schema_path = Path(workdir) / "schema.json"
            output_path = Path(workdir) / "last-message.json"
            schema_path.write_text(json.dumps(response_schema()), encoding="utf-8")

            command = [
                self._config.codex_command,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--cd",
                workdir,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--dangerously-bypass-approvals-and-sandbox",
                "--color",
                "never",
            ]
            if self._config.codex_model:
                command += ["--model", self._config.codex_model]
            command.append("-")

            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self._config.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise BackendError(
                    f"codex command {self._config.codex_command!r} not found on PATH"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BackendError(
                    f"codex timed out after {self._config.timeout_seconds}s"
                ) from exc

            if completed.returncode != 0:
                # stdout is excluded on purpose: codex echoes the prompt, and the
                # prompt contains the transcript.
                stderr = (completed.stderr or "").strip()[-_STDERR_EXCERPT_CHARS:]
                raise BackendError(
                    f"codex exited {completed.returncode}. stderr tail:\n{stderr}"
                )
            if not output_path.exists():
                raise BackendError("codex produced no output file")
            return output_path.read_text(encoding="utf-8")


def build_backend(config: LLMConfig) -> Backend:
    """Construct the configured backend."""
    if config.backend == "ollama":
        return OllamaBackend(config)
    if config.backend == "codex":
        return CodexBackend(config)
    raise BackendError(f"Unknown backend: {config.backend!r}")
