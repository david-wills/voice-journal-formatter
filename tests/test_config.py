import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from voice_journal_formatter.config import ConfigError, load_config

from support import make_config

MINIMAL = """
[vault]
root = "{root}"

[[note_type]]
name = "thought"
outputs = [{{ dir = "thoughts", filename = "{{date}}_{{slug}}.md" }}]
"""


def write_config(tmp: Path, body: str) -> Path:
    path = tmp / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


class LoadConfigTests(unittest.TestCase):
    def test_loads_a_minimal_config_with_defaults(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = write_config(tmp_path, MINIMAL.format(root=tmp_path.as_posix()))
            config = load_config(path)
            self.assertEqual(config.vault_root, tmp_path)
            self.assertEqual(config.raw_dir, tmp_path / "capture/raw")
            self.assertEqual(config.llm.backend, "ollama")
            self.assertEqual(config.max_attempts, 3)
            self.assertEqual(len(config.note_types), 1)

    def test_expands_user_and_env_vars_in_vault_root(self):
        with TemporaryDirectory() as tmp:
            path = write_config(Path(tmp), MINIMAL.format(root="~/some-vault"))
            config = load_config(path)
            self.assertEqual(config.vault_root, Path.home() / "some-vault")

    def test_missing_file_names_the_path_and_the_env_var(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                load_config(Path(tmp) / "absent.toml")
            self.assertIn("VOICE_JOURNAL_CONFIG", str(ctx.exception))

    def test_malformed_toml_is_reported_clearly(self):
        with TemporaryDirectory() as tmp:
            path = write_config(Path(tmp), "[vault\nroot = 1")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("Could not parse", str(ctx.exception))

    def test_missing_vault_root_is_rejected(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                Path(tmp),
                '[vault]\n\n[[note_type]]\nname = "t"\n'
                'outputs = [{ dir = "d", filename = "f.md" }]\n',
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("root", str(ctx.exception))


class NoteTypeValidationTests(unittest.TestCase):
    def _load(self, tmp: str, note_type_block: str):
        body = f'[vault]\nroot = "{tmp}"\n\n{note_type_block}'
        return load_config(write_config(Path(tmp), body))

    def test_requires_at_least_one_note_type(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                self._load(tmp, "")
            self.assertIn("note_type", str(ctx.exception))

    def test_rejects_underscore_in_note_type_name(self):
        # '_' separates fields in the capture filename, so a name containing one
        # would make the filename ambiguous.
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                self._load(
                    tmp,
                    '[[note_type]]\nname = "audio_journal"\n'
                    'outputs = [{ dir = "d", filename = "f.md" }]\n',
                )
            self.assertIn("_", str(ctx.exception))

    def test_rejects_duplicate_note_type_names(self):
        with TemporaryDirectory() as tmp:
            block = (
                '[[note_type]]\nname = "thought"\n'
                'outputs = [{ dir = "d", filename = "f.md" }]\n'
                '[[note_type]]\nname = "thought"\n'
                'outputs = [{ dir = "d", filename = "g.md" }]\n'
            )
            with self.assertRaises(ConfigError) as ctx:
                self._load(tmp, block)
            self.assertIn("Duplicate", str(ctx.exception))

    def test_rejects_note_type_with_no_outputs(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                self._load(tmp, '[[note_type]]\nname = "thought"\noutputs = []\n')
            self.assertIn("outputs", str(ctx.exception))

    def test_rejects_output_missing_filename(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                self._load(
                    tmp,
                    '[[note_type]]\nname = "thought"\n'
                    'outputs = [{ dir = "d" }]\n',
                )
            self.assertIn("filename", str(ctx.exception))


class BackendValidationTests(unittest.TestCase):
    def test_rejects_unknown_backend_and_lists_the_valid_ones(self):
        with TemporaryDirectory() as tmp:
            body = (
                f'[vault]\nroot = "{tmp}"\n\n[llm]\nbackend = "gpt4all"\n\n'
                '[[note_type]]\nname = "t"\n'
                'outputs = [{ dir = "d", filename = "f.md" }]\n'
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(write_config(Path(tmp), body))
            self.assertIn("ollama", str(ctx.exception))


class LookupTests(unittest.TestCase):
    def test_note_type_lookup_and_path_resolution(self):
        with TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            self.assertIsNotNone(config.note_type("thought"))
            self.assertIsNone(config.note_type("recipe"))
            self.assertEqual(
                config.resolve("thoughts"), config.vault_root / "thoughts"
            )


if __name__ == "__main__":
    unittest.main()
