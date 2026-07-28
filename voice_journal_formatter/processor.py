"""Orchestration: scan the raw folder, format each capture, route the output."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .backends import Backend, build_backend
from .config import Config, NoteType
from .naming import (
    build_capture_pattern,
    parse_capture_filename,
    render_filename,
    unique_path,
)
from .parsing import Result, parse_json_response, validate_result
from .store import Lock, State, atomic_write, is_stable, move_without_overwrite


@dataclass
class Outcome:
    filename: str
    status: str  # processed | failed | quarantined | would-process
    written: list[Path] = field(default_factory=list)
    error: str = ""


@dataclass
class ScanSummary:
    outcomes: list[Outcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def processed(self) -> int:
        return self.count("processed")

    @property
    def failed(self) -> int:
        return self.count("failed")

    @property
    def quarantined(self) -> int:
        return self.count("quarantined")


def build_prompt(config: Config, note_type: NoteType, transcript: str) -> str:
    """Assemble the full prompt: task framing, shared cleanup rules, per-type
    instruction, then the transcript."""
    display = note_type.name.replace("-", " ")

    sections = [
        f"You are processing a {display} voice transcript.",
        "",
        "Return only valid JSON with exactly these keys:",
        '- "slug": a concise lowercase hyphenated thesis slug, around five words',
        '- "title": the same thesis in Title Case',
        '- "formatted_markdown": the cleaned transcript in Markdown',
        "",
        "Do not include Markdown fences around the JSON.",
        "",
        config.cleanup_prompt,
    ]

    if config.name_corrections:
        sections.append("")
        sections.append("Known name corrections:")
        sections.append("")
        sections.extend(f"- {item}" for item in config.name_corrections)

    if note_type.instruction:
        sections.append("")
        sections.append(note_type.instruction)

    sections += ["", "Transcript:", "<<<", transcript, ">>>"]
    return "\n".join(sections)


class Processor:
    def __init__(self, config: Config, backend: Backend | None = None) -> None:
        self.config = config
        self.backend = backend if backend is not None else build_backend(config.llm)
        self.state = State(config.state_dir)
        self.pattern = build_capture_pattern(
            [note_type.name for note_type in config.note_types]
        )

    def generate(self, note_type: NoteType, transcript: str) -> Result:
        prompt = build_prompt(self.config, note_type, transcript)
        raw = self.backend.generate(prompt)
        return validate_result(parse_json_response(raw))

    def write_outputs(
        self, note_type: NoteType, capture_date: str, result: Result
    ) -> list[Path]:
        written = []
        for output in note_type.outputs:
            filename = render_filename(
                output.filename,
                date=capture_date,
                note_type=note_type.name,
                slug=result.slug,
                title=result.title,
            )
            path = unique_path(self.config.resolve(output.directory) / filename)
            atomic_write(path, result.markdown)
            written.append(path)
        return written

    def process_file(self, path: Path, dry_run: bool = False) -> Outcome | None:
        parsed = parse_capture_filename(path.name, self.pattern)
        if parsed is None:
            return None
        note_type = self.config.note_type(parsed.note_type)
        if note_type is None:  # pragma: no cover - pattern is built from config
            return None

        if dry_run:
            return Outcome(filename=path.name, status="would-process")

        transcript = path.read_text(encoding="utf-8")
        result = self.generate(note_type, transcript)
        written = self.write_outputs(note_type, parsed.date, result)

        if note_type.archive_raw:
            archived_name = f"{parsed.date}_{note_type.name}_{result.slug}.md"
            move_without_overwrite(path, self.config.archive_dir / archived_name)

        self.state.clear_failure(path.name)
        return Outcome(filename=path.name, status="processed", written=written)

    def quarantine(self, path: Path, error: str) -> Path:
        """Move a repeatedly failing capture out of the scan path.

        Without this, one bad file fails on every scheduled run forever. The
        error report is written beside the quarantined file so the failure is
        discoverable without digging through logs.
        """
        moved = move_without_overwrite(path, self.config.failed_dir / path.name)
        report = moved.with_suffix(moved.suffix + ".error.txt")
        atomic_write(
            report,
            f"voice_journal_formatter could not process this capture after "
            f"{self.config.max_attempts} attempts.\n\n"
            f"Fix the underlying problem, then move the .md file back to "
            f"{self.config.raw_dir} to retry.\n\n"
            f"Last error:\n{error}\n",
        )
        self.state.clear_failure(path.name)
        return moved

    def handle_failure(self, path: Path, error: str) -> Outcome:
        attempts = self.state.record_failure(
            path.name, error, self.config.max_error_chars
        )
        if attempts >= self.config.max_attempts:
            self.quarantine(path, error)
            return Outcome(filename=path.name, status="quarantined", error=error)
        return Outcome(filename=path.name, status="failed", error=error)

    def scan(
        self,
        dry_run: bool = False,
        process_existing: bool | None = None,
        min_age_seconds: int | None = None,
    ) -> ScanSummary:
        if process_existing is None:
            process_existing = self.config.process_existing
        if min_age_seconds is None:
            min_age_seconds = self.config.min_file_age_seconds

        marker = 0.0 if process_existing else self.state.install_marker()
        self.config.raw_dir.mkdir(parents=True, exist_ok=True)
        summary = ScanSummary()

        for path in sorted(self.config.raw_dir.glob("*.md"), key=lambda p: p.name):
            if parse_capture_filename(path.name, self.pattern) is None:
                continue
            try:
                if not process_existing and path.stat().st_mtime < marker:
                    continue
                if not is_stable(path, min_age_seconds, self.config.settle_seconds):
                    continue
                outcome = self.process_file(path, dry_run=dry_run)
            except Exception:
                summary.outcomes.append(
                    self.handle_failure(path, traceback.format_exc())
                )
                continue
            if outcome is not None:
                summary.outcomes.append(outcome)

        return summary


def run_scan(
    config: Config,
    dry_run: bool = False,
    process_existing: bool | None = None,
    min_age_seconds: int | None = None,
    backend: Backend | None = None,
) -> ScanSummary:
    """Run a single scan under a lock."""
    processor = Processor(config, backend=backend)
    with Lock(config.state_dir / "processor.lock"):
        return processor.scan(
            dry_run=dry_run,
            process_existing=process_existing,
            min_age_seconds=min_age_seconds,
        )
