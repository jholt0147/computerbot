# computerbot

A Python voice assistant inspired by the computer from Star Trek. Say
"computer, ..." (or type) and it runs local commands, searches the web, reads
the news, looks things up on Memory Alpha and Memory Beta, or chats with a
local language model and answers out loud.

> **Fan project.** Not affiliated with, endorsed by, or sponsored by
> Paramount, CBS Studios, or the Star Trek franchise. Star Trek and related
> marks belong to their owners.

## About

This project is mostly for my own entertainment. I admit this was coded with
Claude's assistance. I'm sure there are bugs but so far it works well for me.
I would appreciate any constructive input on making it more accurate to the
series.

Developed and tested only on amd64 Alpine Linux (with Sway).

## What you can say

| Say                                                     | What happens |
|---------------------------------------------------------|--------------|
| "computer, set the volume to forty"                     | sets volume with `pamixer` |
| "open the terminal" / "open the browser"                | launches `foot` / qutebrowser |
| "open hailing frequencies"                              | opens a terminal that ssh's into `COMMS_HOST` and attaches tmux |
| "red alert" / the self-destruct code phrase             | plays a sound effect (you supply the audio, see `sounds/README.md`) |
| "what time is it" / "what's the date" / "what year is it" | spoken naturally |
| "stardate"                                              | stardate (TNG formula, shifted into the 2400s) plus the Earth date |
| "system status"                                         | memory usage |
| "move left", "focus right", "set the width to 50"       | Sway window control (width/height as percent of screen) |
| "voice off" / "voice on"                                | turn spoken replies off or on |
| "look up Worf in Memory Alpha" (or "Memory Beta")       | picks the article, summarizes it, then "continue" for more or "open browser" |
| "google best pizza"                                     | searches with `ddgr`/`googler`, reads the top three, say a number to open one |
| "what's the news" / "tech headlines"                    | reads headlines, "more" for the next ones |
| anything else                                           | goes to your local LLM, and it keeps listening for a follow-up |
| "clear history" / "forget everything"                  | erases the saved conversation |

Your conversation with the LLM is saved to `data/history.jsonl` and reloaded the next
time you start (settings: `PERSIST_HISTORY`, `HISTORY_LIMIT`, `HISTORY_CHARS` and
`HISTORY_KEEP` in `common.py`). The file is ignored by git.

You can also type requests at the `>` prompt. `/help` lists options, `/quit` exits.

## Requirements

- Python 3.10 or newer (no pip packages needed)
- `alsa-utils` (arecord/aplay), `pamixer`, `mpv`, `foot` (and `footclient` for the hailing-frequencies command), a web browser (tested with qutebrowser), `swaymsg` for the window commands
- **Speech to text:** [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (`whisper-cli` plus a ggml model, such as `ggml-base.en.bin`)
- **Speech:** [Piper](https://github.com/rhasspy/piper) for a natural voice and/or `espeak-ng` as a fallback
- **Language model:** a running [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` (any chat model; a small 2B-4B model works). If your model has a "thinking" mode, turn it off for faster replies.
- **Web search:** [ddgr](https://github.com/jarun/ddgr) and/or [googler](https://github.com/jarun/googler)

On Alpine: `apk add python3 alsa-utils pamixer mpv espeak-ng qutebrowser foot`

## Setup

1. Build whisper.cpp and download a model. Put the model somewhere like `~/models/`.
2. Install Piper and a voice (the `.onnx` file and its `.onnx.json` next to it). Piper's prebuilt binaries target glibc, so on Alpine you may need `gcompat` or a glibc container. The assistant falls back to `espeak-ng` if Piper isn't found.
3. Start your model server, for example: `llama-server -m your-model.gguf -c 4096`
4. Edit the settings at the top of `common.py` (model paths, voice, browser, terminal, `COMMS_HOST`).
5. Run it from this folder: `python3 computer.py`

Modes: `python3 computer.py` (voice + typing), `--mute` (starts with voice off), `--text` (typing only, no microphone).

## Offline Memory Alpha / Memory Beta (optional)

By default lookups use the live wikis. For instant, offline lookups you can
import a database dump once:

1. Open `https://memory-alpha.fandom.com/wiki/Special:Statistics` (and the same
   for `memory-beta`) and look for the "Database dumps" section. Download the
   **current pages** dump (not the full history). Fandom only lets wiki admins
   request fresh dumps, so the available one may be old.
2. Run `python3 import_wiki.py alpha path/to/dump.xml.7z` (or `beta`). It accepts
   `.xml`, `.xml.gz` and `.xml.7z` (a `7z` program, `apk add 7zip`, is needed
   for `.7z`, or unpack it yourself first).
3. `python3 import_wiki.py --info` shows what was imported.

The databases are written to `data/`, which is ignored by git. Don't commit or
redistribute them: the wikis' text is under Creative Commons licenses. When the
local copy has no match, the assistant asks the live wiki, so a stale dump is
not a problem. Set `USE_LOCAL_WIKI = False` in `common.py` to turn this off.

## Adding a command

Commands are regular expressions checked in order. Add one to `commands.py`:

```python
@command(r"\bmute\b")
def mute(m):
    subprocess.run(["pamixer", "--mute"])
    return "Muted."
```

The function returns what the assistant should say. Order matters: specific
commands are registered before broad ones (see the import order in
`computer.py`).

## Files

| File              | Purpose |
|-------------------|---------|
| `computer.py`     | microphone, speech, typing, and the main loop (run this) |
| `common.py`       | settings, command registry, LLM helpers |
| `commands.py`     | local commands |
| `cloud.py`        | web search and news |
| `memory_alpha.py` | Memory Alpha / Memory Beta lookups |
| `wiki_local.py`   | searches the optional local copies of those wikis |
| `import_wiki.py`  | builds the local copies from a Fandom dump (run once, optional) |
| `sounds/`         | put your own sound effects here (not included) |

## Credits and licensing

- Article text spoken during lookups comes from [Memory Alpha](https://memory-alpha.fandom.com) and [Memory Beta](https://memory-beta.fandom.com), community wikis whose content is available under Creative Commons licenses; see each site's licensing page. The text is fetched when you ask and is not stored or redistributed by this project.
- Built on [llama.cpp](https://github.com/ggml-org/llama.cpp), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [Piper](https://github.com/rhasspy/piper), and [ddgr/googler](https://github.com/jarun).
- This project's code is released under the MIT License (see `LICENSE`).
