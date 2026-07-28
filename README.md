# voice-journal-formatter

Turn raw voice transcripts into formatted Markdown notes, using a local LLM. Runs
unattended, writes into any folder of Markdown files, and sends nothing off your
machine.

Speech-to-text gives you a wall of text with filler words, false starts, and
mangled names. That's a faithful record and an unreadable note. voice-journal-formatter does
the light editing pass you'd otherwise do by hand — and, critically, stops short
of turning your thinking into an essay.

## What it does

**Before** — what the transcription actually produced:

> okay so um it's Tuesday I think and I just got back from the the ride out to the
> reservoir and I wanted to get this down before I I forget it. so the thing about
> riding in the morning is that um it's not actually about the exercise. like I
> tell myself it's about the exercise but honestly if it were about the exercise
> I'd just go to the gym which is like four blocks away and takes no planning at
> all. it's about the fact that nobody can reach me. that's that's the whole
> thing. for fifty minutes there's no there's no slack there's no phone and I
> think that's the only time in the week where I'm actually thinking instead of
> just reacting to things

**After** — same note, same voice, readable:

> ## The ride isn't about the exercise
>
> It's Tuesday, and I just got back from the ride out to the reservoir. I wanted
> to get this down before I forget it.
>
> The thing about riding in the morning is that it's not actually about the
> exercise. I tell myself it is, but honestly, if it were about the exercise I'd
> just go to the gym, which is four blocks away and takes no planning at all.
>
> It's about the fact that nobody can reach me. That's the whole thing. For fifty
> minutes there's no Slack, no phone. I think that's the only time in the week
> where I'm actually thinking instead of just reacting to things.

The file is also given a title and a slug derived from what it's actually about,
so `2026-01-15_audio-journal_6415.md` becomes
`2026-01-15_audio-journal-formatted_riding-is-about-being-unreachable.md`.

Full example: [`examples/`](examples/).

The editing brief is deliberately conservative — remove filler, fix transcription
errors, break up rambling sentences, add headings — while preserving order,
meaning, first-person voice, and uncertainty. Rambling that's *doing something*
stays. You can rewrite the whole brief in config.

## Install

Requires Python 3.11+ and [ollama](https://ollama.com). No Python dependencies.

```bash
git clone https://github.com/david-wills/voice-journal-formatter.git
cd voice-journal-formatter

ollama pull qwen3:8b

mkdir -p ~/.config/voice-journal-formatter
cp config.example.toml ~/.config/voice-journal-formatter/config.toml
$EDITOR ~/.config/voice-journal-formatter/config.toml    # set vault.root

python3 -m voice_journal_formatter --check               # validate config, print resolved paths
python3 -m voice_journal_formatter --dry-run             # list what would be processed
```

To run it on a schedule (macOS, every 5 minutes):

```bash
./install/install.sh      # ./install/uninstall.sh to remove
```

On Linux, skip the installer and call `voice-journal-formatter --scan` from a systemd timer or
cron entry. The installer itself is macOS-only; the tool isn't.

## Bring your own capture

voice-journal-formatter doesn't record or transcribe anything. It watches one folder for files
named:

```
YYYY-MM-DD_<note-type>_<slug>.md
```

Anything that can write that filename is a valid capture source — an iOS Shortcut
that transcribes a voice memo, a Whisper wrapper, a mobile Obsidian plugin, or you
typing into a text editor. The slug in the capture name is throwaway; voice-journal-formatter
replaces it with one derived from the content.

Files that don't match are ignored, so the folder can hold other things safely.

## Configuration

Everything personal lives in `config.toml` — vault location, model, editing brief,
and the name corrections your transcriber keeps getting wrong:

```toml
[prompt]
name_corrections = ['Use "Niamh", not "Neve".']
```

**Note types** are the main knob. A note type is a category of capture with its own
extra instruction and its own output destinations:

```toml
[[note_type]]
name = "thought"
instruction = "Keep this exploratory. Don't over-polish it into an essay."
outputs = [
  { dir = "archive",  filename = "{date}_{type}-formatted_{slug}.md" },
  { dir = "thoughts", filename = "{date} {title}.md" },
]
```

That routes one capture to two destinations with different naming conventions.
Define as many types as you like, or just one. The type name is the middle field
of the capture filename, which is how a capture selects its own handling.

See [`config.example.toml`](config.example.toml) for the annotated full set.

## Design notes

Most of this tool is not the LLM call. It's the handling around it, because an
unattended process writing into a synced folder full of irreplaceable personal
notes has a specific set of ways to ruin your day.

**Cloud-synced vaults hand you half a file.** iCloud and Dropbox materialize files
incrementally, so a naive watcher happily reads a transcript that's still arriving
and formats the first third of it. Captures are only processed once they're older
than a threshold *and* their size and mtime are unchanged across a re-stat.

**A crash mid-write leaves a truncated note.** Every write goes to a temp file in
the destination directory and is then renamed. Rename within a filesystem is
atomic, so a note is either fully there or not there at all.

**Slugs come from an LLM, so names collide.** Two captures on the same day can
easily produce the same slug. Nothing is ever overwritten; the second becomes
`-2`, and so on.

**Scheduled runs overlap.** A local model on a long transcript can exceed the scan
interval. A PID lockfile prevents a second run from starting, and a lock older
than six hours is treated as stale and reclaimed, so a killed run doesn't wedge
the pipeline permanently.

**Installing on an existing vault shouldn't stampede.** On first run voice-journal-formatter
records an install marker and ignores everything older, so pointing it at a vault
with years of captures doesn't dispatch hundreds of model calls. `--process-existing`
opts into the backfill deliberately.

**A permanently broken capture must not retry forever.** This one is from
experience: a misconfigured backend meant one file failed on every scheduled run
for weeks, re-logging itself each time, and nothing else in the folder made it
obvious. Failures are now counted per file; after `max_attempts` the capture is
moved to a `failed/` folder with a plain-language `.error.txt` beside it
explaining how to retry. Transient failures — ollama not running — still get their
retries. A success clears the counter.

**Errors must not leak the transcript.** Error text ends up in logs, and here the
prompt *is* someone's journal. Backend errors never include the prompt, the codex
backend excludes stdout because the CLI echoes its input, and stored error text is
truncated. State lives outside the vault so nothing derived from a failure gets
synced.

**Local models don't reliably return clean JSON.** They're asked for strict JSON
and mostly comply. Parsing handles reasoning traces in `<think>` tags, Markdown
fences, and conversational preamble around the object, then falls back to
brace-matching. A response missing a slug is recovered from the title and vice
versa; only a missing body is fatal.

## Tests

```bash
PYTHONPATH=.:tests python3 -m unittest discover -s tests
```

71 tests, no dependencies, no network. The model is replaced by a scripted stub,
which is what makes the interesting paths testable: quarantine-after-N-failures,
retry-then-succeed, collision handling, and the several shapes of malformed model
output.

## Limitations

- The scheduled-run installer is macOS/launchd only. The tool runs anywhere.
- Ollama runs locally, so quality and speed track your hardware and model. Small
  models sometimes over-edit; the editing brief is the first thing to tune, not the
  model.
- One capture folder per config. Multiple vaults means multiple configs.
- The codex backend runs that CLI with approvals and sandboxing disabled so it can
  work unattended. That's a real trade-off, it's opt-in, and ollama is the default.

## Why

I keep a lot of voice notes, and the friction was never recording them — it was
that a raw transcript is unpleasant enough to reread that I didn't. This is a small
piece of a larger interest in local-first tooling for personal data: the useful
version of a thinking archive is one you own, that runs on your own machine, and
that nothing else gets a copy of.

## License

MIT
