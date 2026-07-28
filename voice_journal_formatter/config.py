"""Configuration loading and validation.

Everything that is personal to one user - vault location, folder names, model
choice, note taxonomy, name spellings - lives in a TOML file, not in the source.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "voice-journal-formatter" / "config.toml"
CONFIG_ENV_VAR = "VOICE_JOURNAL_CONFIG"

DEFAULT_CLEANUP_PROMPT = """Lightly edit this voice transcript.

Preserve the original order, meaning, first-person voice, and exploratory tone.
Do not summarize it or turn it into an essay.

You may do light transcript cleanup:

- remove obvious filler words and false starts when they interrupt readability,
- fix transcription errors and awkward dictated fragments,
- split long rambling sentences into readable paragraphs,
- lightly smooth grammar while keeping the voice-note feel,
- add descriptive Markdown headings at major topic shifts,
- use short paragraphs, often one idea per paragraph,
- keep mundane details and uncertain or rambling sections if present.

Aim for a cleaned-up spoken-note transcript, not a verbatim transcription and
not polished prose.
"""


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class Output:
    """One file the processor writes for a given note type.

    `directory` is relative to the vault root. `filename` is a template
    supporting {date}, {type}, {slug}, and {title}.
    """

    directory: str
    filename: str


@dataclass(frozen=True)
class NoteType:
    """A user-defined category of capture, e.g. "audio-journal" or "thought".

    The name is both the taxonomy label and the middle field of the capture
    filename contract: YYYY-MM-DD_<name>_<slug>.md
    """

    name: str
    instruction: str
    outputs: tuple[Output, ...]
    archive_raw: bool = True


@dataclass(frozen=True)
class LLMConfig:
    backend: str
    model: str
    url: str
    timeout_seconds: int
    think: bool
    codex_command: str
    codex_model: str


@dataclass(frozen=True)
class Config:
    vault_root: Path
    raw_dir: Path
    archive_dir: Path
    failed_dir: Path
    state_dir: Path
    note_types: tuple[NoteType, ...]
    llm: LLMConfig
    cleanup_prompt: str
    name_corrections: tuple[str, ...]
    min_file_age_seconds: int
    settle_seconds: float
    max_attempts: int
    max_error_chars: int
    process_existing: bool

    def note_type(self, name: str) -> NoteType | None:
        for note_type in self.note_types:
            if note_type.name == name:
                return note_type
        return None

    def resolve(self, relative_dir: str) -> Path:
        """Resolve a vault-relative directory to an absolute path."""
        return self.vault_root / relative_dir


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def _require(table: dict[str, Any], key: str, where: str) -> Any:
    if key not in table:
        raise ConfigError(f"Missing required key '{key}' in [{where}]")
    return table[key]


def _parse_outputs(raw_outputs: Any, note_name: str) -> tuple[Output, ...]:
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise ConfigError(
            f"note_type '{note_name}' needs a non-empty 'outputs' list"
        )
    outputs = []
    for index, entry in enumerate(raw_outputs):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"note_type '{note_name}' output #{index + 1} must be a table"
            )
        directory = _require(entry, "dir", f"note_type.{note_name}.outputs")
        filename = _require(entry, "filename", f"note_type.{note_name}.outputs")
        outputs.append(Output(directory=str(directory), filename=str(filename)))
    return tuple(outputs)


def _parse_note_types(raw: Any) -> tuple[NoteType, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(
            "At least one [[note_type]] must be defined. A note type's name is "
            "the middle field of the capture filename: YYYY-MM-DD_<name>_<slug>.md"
        )
    note_types = []
    seen: set[str] = set()
    for entry in raw:
        name = str(_require(entry, "name", "note_type"))
        if not name or "_" in name:
            raise ConfigError(
                f"note_type name {name!r} must be non-empty and must not contain "
                "'_', because '_' separates fields in the capture filename"
            )
        if name in seen:
            raise ConfigError(f"Duplicate note_type name: {name!r}")
        seen.add(name)
        note_types.append(
            NoteType(
                name=name,
                instruction=str(entry.get("instruction", "")).strip(),
                outputs=_parse_outputs(entry.get("outputs"), name),
                archive_raw=bool(entry.get("archive_raw", True)),
            )
        )
    return tuple(note_types)


def _parse_llm(raw: dict[str, Any]) -> LLMConfig:
    codex = raw.get("codex", {})
    if not isinstance(codex, dict):
        raise ConfigError("[llm.codex] must be a table")
    backend = str(raw.get("backend", "ollama"))
    if backend not in {"ollama", "codex"}:
        raise ConfigError(
            f"Unknown llm.backend {backend!r}. Supported backends: ollama, codex"
        )
    return LLMConfig(
        backend=backend,
        model=str(raw.get("model", "qwen3:8b")),
        url=str(raw.get("url", "http://127.0.0.1:11434/api/generate")),
        timeout_seconds=int(raw.get("timeout_seconds", 900)),
        think=bool(raw.get("think", False)),
        codex_command=str(codex.get("command", "codex")),
        codex_model=str(codex.get("model", "")),
    )


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Find the config file: explicit path, then env var, then default location."""
    if explicit:
        return _expand(str(explicit))
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return _expand(from_env)
    return DEFAULT_CONFIG_PATH


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate configuration from a TOML file."""
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"No config file at {config_path}. Copy config.example.toml there "
            f"and edit it, or set {CONFIG_ENV_VAR}."
        )
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc

    vault = raw.get("vault", {})
    if not isinstance(vault, dict):
        raise ConfigError("[vault] must be a table")
    vault_root = _expand(str(_require(vault, "root", "vault")))

    runtime = raw.get("runtime", {})
    prompt = raw.get("prompt", {})

    name_corrections = prompt.get("name_corrections", [])
    if not isinstance(name_corrections, list):
        raise ConfigError("prompt.name_corrections must be a list of strings")

    state_dir_raw = runtime.get(
        "state_dir", str(Path.home() / ".local" / "state" / "voice-journal-formatter")
    )

    return Config(
        vault_root=vault_root,
        raw_dir=vault_root / str(vault.get("raw", "capture/raw")),
        archive_dir=vault_root / str(vault.get("archive", "archive")),
        failed_dir=vault_root / str(vault.get("failed", "capture/failed")),
        state_dir=_expand(str(state_dir_raw)),
        note_types=_parse_note_types(raw.get("note_type")),
        llm=_parse_llm(raw.get("llm", {})),
        cleanup_prompt=str(
            prompt.get("cleanup", DEFAULT_CLEANUP_PROMPT)
        ).strip(),
        name_corrections=tuple(str(item) for item in name_corrections),
        min_file_age_seconds=int(runtime.get("min_file_age_seconds", 60)),
        settle_seconds=float(runtime.get("settle_seconds", 1.0)),
        max_attempts=int(runtime.get("max_attempts", 3)),
        max_error_chars=int(runtime.get("max_error_chars", 2000)),
        process_existing=bool(runtime.get("process_existing", False)),
    )
