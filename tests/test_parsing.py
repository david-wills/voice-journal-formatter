import json
import unittest

from voice_journal_formatter.parsing import (
    ResultError,
    parse_json_response,
    validate_result,
)

PAYLOAD = {
    "slug": "memory-and-forgetting",
    "title": "Memory And Forgetting",
    "formatted_markdown": "## On forgetting\n\nSome cleaned text.",
}


class ParseJsonResponseTests(unittest.TestCase):
    """Local models are asked for strict JSON. These are the ways they don't."""

    def test_parses_clean_json(self):
        self.assertEqual(parse_json_response(json.dumps(PAYLOAD)), PAYLOAD)

    def test_tolerates_surrounding_whitespace(self):
        self.assertEqual(parse_json_response(f"\n\n{json.dumps(PAYLOAD)}\n"), PAYLOAD)

    def test_strips_markdown_fences(self):
        fenced = f"```json\n{json.dumps(PAYLOAD)}\n```"
        self.assertEqual(parse_json_response(fenced), PAYLOAD)

    def test_strips_bare_fences(self):
        fenced = f"```\n{json.dumps(PAYLOAD)}\n```"
        self.assertEqual(parse_json_response(fenced), PAYLOAD)

    def test_strips_reasoning_traces(self):
        traced = (
            "<think>The user wants cleanup. Let me plan.</think>\n"
            + json.dumps(PAYLOAD)
        )
        self.assertEqual(parse_json_response(traced), PAYLOAD)

    def test_strips_multiline_reasoning_traces(self):
        traced = f"<think>\nline one\nline two\n</think>\n\n{json.dumps(PAYLOAD)}"
        self.assertEqual(parse_json_response(traced), PAYLOAD)

    def test_extracts_json_wrapped_in_conversational_text(self):
        chatty = f"Sure! Here is the result:\n{json.dumps(PAYLOAD)}\nLet me know."
        self.assertEqual(parse_json_response(chatty), PAYLOAD)

    def test_raises_when_there_is_no_json_at_all(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_json_response("I'm sorry, I can't help with that.")


class ValidateResultTests(unittest.TestCase):
    def test_accepts_a_complete_payload(self):
        result = validate_result(PAYLOAD)
        self.assertEqual(result.slug, "memory-and-forgetting")
        self.assertEqual(result.title, "Memory And Forgetting")
        self.assertTrue(result.markdown.startswith("## On forgetting"))

    def test_derives_slug_from_title_when_missing(self):
        result = validate_result(
            {"title": "Memory And Forgetting", "formatted_markdown": "text"}
        )
        self.assertEqual(result.slug, "memory-and-forgetting")

    def test_derives_title_from_slug_when_missing(self):
        result = validate_result(
            {"slug": "memory-and-forgetting", "formatted_markdown": "text"}
        )
        self.assertEqual(result.title, "Memory And Forgetting")

    def test_normalizes_a_messy_title(self):
        result = validate_result(
            {"slug": "x-y", "title": "Memory: *and* forgetting!", "formatted_markdown": "t"}
        )
        self.assertEqual(result.title, "Memory And Forgetting")

    def test_rejects_missing_body(self):
        with self.assertRaises(ResultError):
            validate_result({"slug": "x", "title": "X"})

    def test_rejects_whitespace_only_body(self):
        with self.assertRaises(ResultError):
            validate_result({"slug": "x", "title": "X", "formatted_markdown": "   \n"})

    def test_rejects_payload_with_no_usable_name(self):
        with self.assertRaises(ResultError):
            validate_result({"slug": "!!!", "title": "???", "formatted_markdown": "t"})


if __name__ == "__main__":
    unittest.main()
