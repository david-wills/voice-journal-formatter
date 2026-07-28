import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from voice_journal_formatter.naming import (
    build_capture_pattern,
    parse_capture_filename,
    render_filename,
    safe_filename_component,
    slugify,
    title_from_slug,
    unique_path,
)


class SlugifyTests(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        self.assertEqual(slugify("Does Self Knowledge Help"), "does-self-knowledge-help")

    def test_expands_ampersand(self):
        self.assertEqual(slugify("memory & forgetting"), "memory-and-forgetting")

    def test_strips_punctuation_and_accents_to_ascii(self):
        self.assertEqual(slugify("what's *really* going on?!"), "what-s-really-going-on")

    def test_truncates_long_input_without_trailing_hyphen(self):
        slug = slugify("word " * 60)
        self.assertLessEqual(len(slug), 96)
        self.assertFalse(slug.endswith("-"))

    def test_returns_empty_for_unusable_input(self):
        self.assertEqual(slugify("!!! ???"), "")


class TitleTests(unittest.TestCase):
    def test_builds_title_case_from_slug(self):
        self.assertEqual(title_from_slug("memory-and-forgetting"), "Memory And Forgetting")

    def test_ignores_empty_segments(self):
        self.assertEqual(title_from_slug("a--b"), "A B")


class CapturePatternTests(unittest.TestCase):
    def setUp(self):
        self.pattern = build_capture_pattern(["audio-journal", "thought"])

    def test_parses_a_valid_capture_name(self):
        parsed = parse_capture_filename(
            "2026-01-15_audio-journal_morning-walk.md", self.pattern
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.date, "2026-01-15")
        self.assertEqual(parsed.note_type, "audio-journal")
        self.assertEqual(parsed.slug, "morning-walk")

    def test_rejects_unknown_note_type(self):
        self.assertIsNone(
            parse_capture_filename("2026-01-15_recipe_soup.md", self.pattern)
        )

    def test_rejects_missing_date(self):
        self.assertIsNone(
            parse_capture_filename("audio-journal_morning-walk.md", self.pattern)
        )

    def test_rejects_dotfiles_and_non_markdown(self):
        self.assertIsNone(
            parse_capture_filename(".2026-01-15_thought_x.md", self.pattern)
        )
        self.assertIsNone(
            parse_capture_filename("2026-01-15_thought_x.txt", self.pattern)
        )

    def test_longest_note_type_wins_when_one_is_a_prefix_of_another(self):
        # "note" is a prefix of "note-long"; a naive alternation would match
        # "note" first and leave "-long_slug" as the slug.
        pattern = build_capture_pattern(["note", "note-long"])
        parsed = parse_capture_filename("2026-01-15_note-long_idea.md", pattern)
        self.assertEqual(parsed.note_type, "note-long")
        self.assertEqual(parsed.slug, "idea")

    def test_note_type_containing_regex_metacharacters_is_escaped(self):
        pattern = build_capture_pattern(["a.b"])
        self.assertIsNotNone(parse_capture_filename("2026-01-15_a.b_x.md", pattern))
        self.assertIsNone(parse_capture_filename("2026-01-15_axb_x.md", pattern))

    def test_requires_at_least_one_note_type(self):
        with self.assertRaises(ValueError):
            build_capture_pattern([])


class FilenameTemplateTests(unittest.TestCase):
    def test_renders_all_placeholders(self):
        rendered = render_filename(
            "{date}_{type}-formatted_{slug}.md",
            date="2026-01-15",
            note_type="thought",
            slug="memory-and-forgetting",
            title="Memory And Forgetting",
        )
        self.assertEqual(rendered, "2026-01-15_thought-formatted_memory-and-forgetting.md")

    def test_renders_title_style_template(self):
        rendered = render_filename(
            "{date} {title}.md",
            date="2026-01-15",
            note_type="thought",
            slug="x",
            title="Memory And Forgetting",
        )
        self.assertEqual(rendered, "2026-01-15 Memory And Forgetting.md")

    def test_rejects_unknown_placeholder_with_a_helpful_message(self):
        with self.assertRaises(ValueError) as ctx:
            render_filename(
                "{nope}.md", date="d", note_type="t", slug="s", title="T"
            )
        self.assertIn("Supported", str(ctx.exception))

    def test_strips_path_separators_from_rendered_names(self):
        rendered = render_filename(
            "{title}.md", date="d", note_type="t", slug="s", title="a/b:c"
        )
        self.assertEqual(rendered, "abc.md")


class SafeComponentTests(unittest.TestCase):
    def test_falls_back_when_everything_is_stripped(self):
        self.assertEqual(safe_filename_component("///"), "untitled")

    def test_strips_leading_and_trailing_dots(self):
        self.assertEqual(safe_filename_component("..hidden"), "hidden")


class UniquePathTests(unittest.TestCase):
    def test_returns_original_when_free(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "note.md"
            self.assertEqual(unique_path(target), target)

    def test_appends_suffix_on_collision(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "note.md"
            target.write_text("first", encoding="utf-8")
            self.assertEqual(unique_path(target).name, "note-2.md")

    def test_keeps_counting_past_the_first_collision(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "note.md").write_text("a", encoding="utf-8")
            (Path(tmp) / "note-2.md").write_text("b", encoding="utf-8")
            self.assertEqual(unique_path(Path(tmp) / "note.md").name, "note-3.md")

    def test_raises_when_no_name_is_available(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "note.md").write_text("a", encoding="utf-8")
            (Path(tmp) / "note-2.md").write_text("b", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                unique_path(Path(tmp) / "note.md", limit=3)


if __name__ == "__main__":
    unittest.main()
