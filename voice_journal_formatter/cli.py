"""Command line interface."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .processor import Processor, run_scan
from .store import State


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-journal-formatter",
        description="Turn raw voice transcripts into formatted notes, locally.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.toml (default: $VOICE_JOURNAL_CONFIG, "
        "then ~/.config/voice_journal_formatter/config.toml)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan the raw capture folder once. This is the default action.",
    )
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Also process captures older than the install marker.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching captures without calling a model or moving files.",
    )
    parser.add_argument(
        "--init-state",
        action="store_true",
        help="Reset the install marker to now and exit. Existing captures are "
        "then ignored unless --process-existing is passed.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the config, print the resolved paths, and exit.",
    )
    parser.add_argument("--min-age-seconds", type=int, default=None)
    return parser


def print_check(config) -> None:
    print(f"vault root : {config.vault_root}")
    print(f"raw        : {config.raw_dir}")
    print(f"archive    : {config.archive_dir}")
    print(f"failed     : {config.failed_dir}")
    print(f"state      : {config.state_dir}")
    print(f"backend    : {config.llm.backend} ({config.llm.model})")
    print(f"note types : {len(config.note_types)}")
    for note_type in config.note_types:
        targets = ", ".join(
            f"{output.directory}/{output.filename}" for output in note_type.outputs
        )
        print(f"  - {note_type.name} -> {targets}")
    missing = [
        str(path) for path in (config.vault_root,) if not path.exists()
    ]
    if missing:
        print(f"\nWARNING: does not exist: {', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print_check(config)
        return 0

    if args.init_state:
        state = State(config.state_dir)
        state.reset_install_marker()
        print(f"Install marker reset. Captures older than now will be ignored.")
        return 0

    try:
        summary = run_scan(
            config,
            dry_run=args.dry_run,
            process_existing=args.process_existing or None,
            min_age_seconds=args.min_age_seconds,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for outcome in summary.outcomes:
        if outcome.status == "processed":
            for path in outcome.written:
                print(f"wrote {path}")
        elif outcome.status == "would-process":
            print(f"would process {outcome.filename}")
        elif outcome.status == "quarantined":
            print(
                f"quarantined {outcome.filename} after {config.max_attempts} "
                f"attempts -> {config.failed_dir}",
                file=sys.stderr,
            )
        elif outcome.status == "failed":
            print(f"failed {outcome.filename} (will retry)", file=sys.stderr)

    if args.dry_run:
        print(f"Would process {summary.count('would-process')} file(s).")
    else:
        print(
            f"Processed {summary.processed} file(s). "
            f"{summary.failed} failed, {summary.quarantined} quarantined."
        )
    return 1 if summary.quarantined else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
