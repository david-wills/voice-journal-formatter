import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from voice_journal_formatter.backends import BackendError
from voice_journal_formatter.processor import Processor, build_prompt, run_scan
from voice_journal_formatter.store import Lock

from support import StubBackend, make_config, response, write_capture


class RoutingTests(unittest.TestCase):
    def test_single_output_note_type_writes_and_archives(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            raw = write_capture(config, "2026-01-15_audio-journal_raw-slug.md")
            backend = StubBackend(response(slug="a-quiet-morning"))

            summary = run_scan(config, backend=backend)

            self.assertEqual(summary.processed, 1)
            self.assertFalse(raw.exists(), "raw capture should be moved out")
            formatted = config.vault_root / "capture/formatted"
            self.assertEqual(
                [p.name for p in formatted.iterdir()],
                ["2026-01-15_audio-journal-formatted_a-quiet-morning.md"],
            )
            archived = config.archive_dir / "2026-01-15_audio-journal_a-quiet-morning.md"
            self.assertTrue(archived.exists(), "raw should land in archive")

    def test_multi_output_note_type_writes_every_destination(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2026-01-15_thought_raw-slug.md")
            backend = StubBackend(response(slug="memory-and-forgetting"))

            summary = run_scan(config, backend=backend)

            self.assertEqual(summary.processed, 1)
            self.assertTrue(
                (
                    config.archive_dir
                    / "2026-01-15_thought-formatted_memory-and-forgetting.md"
                ).exists()
            )
            self.assertTrue(
                (
                    config.vault_root
                    / "thoughts"
                    / "2026-01-15 Memory And Forgetting.md"
                ).exists()
            )

    def test_output_is_named_from_the_model_slug_not_the_capture_slug(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2026-01-15_audio-journal_6415.md")
            run_scan(config, backend=StubBackend(response(slug="the-real-topic")))

            written = list((config.vault_root / "capture/formatted").iterdir())
            self.assertIn("the-real-topic", written[0].name)

    def test_written_body_is_the_formatted_markdown(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2026-01-15_audio-journal_x.md")
            run_scan(
                config, backend=StubBackend(response(markdown="## Real\n\nBody."))
            )
            written = next((config.vault_root / "capture/formatted").iterdir())
            self.assertEqual(written.read_text(encoding="utf-8"), "## Real\n\nBody.\n")

    def test_ignores_files_that_do_not_match_the_capture_contract(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "notes.md")
            write_capture(config, "2026-01-15_recipe_soup.md")
            backend = StubBackend()  # would raise if called

            summary = run_scan(config, backend=backend)

            self.assertEqual(summary.processed, 0)
            self.assertEqual(backend.prompts, [])

    def test_collisions_do_not_overwrite_existing_notes(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2026-01-15_audio-journal_one.md")
            write_capture(config, "2026-01-15_audio-journal_two.md")
            backend = StubBackend(response(slug="same-slug"), response(slug="same-slug"))

            summary = run_scan(config, backend=backend)

            self.assertEqual(summary.processed, 2)
            names = sorted(p.name for p in (config.vault_root / "capture/formatted").iterdir())
            self.assertEqual(
                names,
                [
                    "2026-01-15_audio-journal-formatted_same-slug-2.md",
                    "2026-01-15_audio-journal-formatted_same-slug.md",
                ],
            )


class DryRunTests(unittest.TestCase):
    def test_dry_run_calls_no_model_and_moves_nothing(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            raw = write_capture(config, "2026-01-15_thought_x.md")
            backend = StubBackend()  # would raise if called

            summary = run_scan(config, dry_run=True, backend=backend)

            self.assertEqual(summary.count("would-process"), 1)
            self.assertEqual(backend.prompts, [])
            self.assertTrue(raw.exists())


class FailureHandlingTests(unittest.TestCase):
    def test_transient_failure_is_retried_not_quarantined(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=3)
            raw = write_capture(config, "2026-01-15_thought_x.md")

            summary = run_scan(
                config, backend=StubBackend(BackendError("ollama not running"))
            )

            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.quarantined, 0)
            self.assertTrue(raw.exists(), "file stays put so the next run retries")

    def test_repeated_failure_is_quarantined_and_stops_retrying(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=2)
            raw = write_capture(config, "2026-01-15_thought_x.md")

            run_scan(config, backend=StubBackend(BackendError("boom")))
            summary = run_scan(config, backend=StubBackend(BackendError("boom")))

            self.assertEqual(summary.quarantined, 1)
            self.assertFalse(raw.exists())
            quarantined = config.failed_dir / "2026-01-15_thought_x.md"
            self.assertTrue(quarantined.exists())

            # The next run must not see it at all.
            third = run_scan(config, backend=StubBackend())
            self.assertEqual(third.outcomes, [])

    def test_quarantine_writes_a_readable_error_report(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=1)
            write_capture(config, "2026-01-15_thought_x.md")
            run_scan(config, backend=StubBackend(BackendError("model unavailable")))

            report = config.failed_dir / "2026-01-15_thought_x.md.error.txt"
            text = report.read_text(encoding="utf-8")
            self.assertIn("model unavailable", text)
            self.assertIn(str(config.raw_dir), text, "should say how to retry")

    def test_success_after_a_failure_clears_the_failure_count(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=3)
            write_capture(config, "2026-01-15_thought_x.md")

            run_scan(config, backend=StubBackend(BackendError("transient")))
            processor = Processor(config, backend=StubBackend())
            self.assertEqual(processor.state.attempts("2026-01-15_thought_x.md"), 1)

            run_scan(config, backend=StubBackend(response()))
            fresh = Processor(config, backend=StubBackend())
            self.assertEqual(fresh.state.attempts("2026-01-15_thought_x.md"), 0)

    def test_unparseable_model_output_is_treated_as_a_failure(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=3)
            summary_backend = StubBackend("I'm sorry, I can't help with that.")
            write_capture(config, "2026-01-15_thought_x.md")

            summary = run_scan(config, backend=summary_backend)

            self.assertEqual(summary.failed, 1)

    def test_one_bad_capture_does_not_block_the_others(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), max_attempts=3)
            write_capture(config, "2026-01-15_thought_aaa.md")
            write_capture(config, "2026-01-15_thought_bbb.md")
            backend = StubBackend(BackendError("boom"), response(slug="fine"))

            summary = run_scan(config, backend=backend)

            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.processed, 1)


class InstallMarkerTests(unittest.TestCase):
    def test_pre_existing_captures_are_ignored_by_default(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2020-01-01_thought_old.md")
            Processor(config, backend=StubBackend()).state.reset_install_marker()

            summary = run_scan(config, backend=StubBackend())

            self.assertEqual(summary.outcomes, [])

    def test_process_existing_picks_up_the_backlog(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            write_capture(config, "2020-01-01_thought_old.md")
            Processor(config, backend=StubBackend()).state.reset_install_marker()

            summary = run_scan(
                config, process_existing=True, backend=StubBackend(response())
            )

            self.assertEqual(summary.processed, 1)


class LockTests(unittest.TestCase):
    def test_a_second_run_refuses_to_start_while_one_is_active(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            config.state_dir.mkdir(parents=True, exist_ok=True)
            with Lock(config.state_dir / "processor.lock"):
                with self.assertRaises(RuntimeError):
                    run_scan(config, backend=StubBackend())

    def test_lock_is_released_on_exit(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            run_scan(config, backend=StubBackend())
            self.assertFalse((config.state_dir / "processor.lock").exists())


class PromptTests(unittest.TestCase):
    def test_prompt_includes_shared_rules_type_instruction_and_transcript(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            note_type = config.note_type("thought")
            prompt = build_prompt(config, note_type, "raw transcript text")

            self.assertIn("Clean up this transcript.", prompt)
            self.assertIn("Keep it exploratory.", prompt)
            self.assertIn("raw transcript text", prompt)
            self.assertIn("thought voice transcript", prompt)

    def test_prompt_includes_configured_name_corrections(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            prompt = build_prompt(config, config.note_type("audio-journal"), "text")
            self.assertIn("Known name corrections:", prompt)
            self.assertIn('Use "Niamh", not "Neve".', prompt)

    def test_note_type_without_an_instruction_omits_it(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            prompt = build_prompt(config, config.note_type("audio-journal"), "text")
            self.assertNotIn("Keep it exploratory.", prompt)


if __name__ == "__main__":
    unittest.main()
