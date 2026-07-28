"""Filename parsing, slug generation, and collision-safe output paths.

The capture filename is the entire input contract for this tool:

    YYYY-MM-DD_<note-type>_<slug>.md

Anything that can write a file with that name - an iOS Shortcut, a Whisper
wrapper, a text editor - is a valid capture source.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_SLUG_LENGTH = 96

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class CaptureName:
    """A parsed capture filename."""

    def __init__(self, date: str, note_type: str, slug: str) -> None:
        self.date = date
        self.note_type = note_type
        self.slug = slug

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"CaptureName(date={self.date!r}, note_type={self.note_type!r}, "
            f"slug={self.slug!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CaptureName):
            return NotImplemented
        return (self.date, self.note_type, self.slug) == (
            other.date,
            other.note_type,
            other.slug,
        )


def build_capture_pattern(note_type_names: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    """Build the capture filename regex for the configured note types.

    Note type names are escaped and sorted longest-first so that a name which is
    a prefix of another (e.g. "note" and "note-long") still matches correctly.
    """
    if not note_type_names:
        raise ValueError("At least one note type name is required")
    ordered = sorted(note_type_names, key=len, reverse=True)
    alternation = "|".join(re.escape(name) for name in ordered)
    return re.compile(
        rf"^(?P<date>\d{{4}}-\d{{2}}-\d{{2}})_(?P<type>{alternation})_(?P<slug>.+)\.md$"
    )


def parse_capture_filename(
    filename: str, pattern: re.Pattern[str]
) -> CaptureName | None:
    """Parse a capture filename, or return None if it does not match."""
    if filename.startswith(".") or not filename.endswith(".md"):
        return None
    match = pattern.match(filename)
    if not match:
        return None
    return CaptureName(
        date=match.group("date"),
        note_type=match.group("type"),
        slug=match.group("slug"),
    )


def slugify(value: str) -> str:
    """Reduce arbitrary text to a lowercase ASCII hyphenated slug."""
    value = value.strip().lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", value)
    return "-".join(words)[:MAX_SLUG_LENGTH].strip("-")


def title_from_slug(slug: str) -> str:
    """Turn a hyphenated slug into a Title Case title."""
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def safe_filename_component(value: str) -> str:
    """Strip characters that are unsafe in a filename on macOS or Windows."""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", value).strip().strip(".")
    return cleaned or "untitled"


def render_filename(
    template: str, *, date: str, note_type: str, slug: str, title: str
) -> str:
    """Render an output filename template.

    Supported placeholders: {date}, {type}, {slug}, {title}.
    """
    try:
        rendered = template.format(
            date=date, type=note_type, slug=slug, title=title
        )
    except KeyError as exc:
        raise ValueError(
            f"Unknown placeholder {exc} in filename template {template!r}. "
            "Supported: {date}, {type}, {slug}, {title}"
        ) from exc
    return safe_filename_component(rendered)


def unique_path(path: Path, limit: int = 10_000) -> Path:
    """Return `path`, or the first available `name-2.md`, `name-3.md`, ... variant.

    Captures are named from LLM-generated slugs, so two notes on the same day can
    collide. Never overwrite; always take the next free name.
    """
    if not path.exists():
        return path
    for index in range(2, limit):
        candidate = path.parent / f"{path.stem}-{index}{path.suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find an unused filename for {path}")
