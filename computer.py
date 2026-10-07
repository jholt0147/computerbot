#!/usr/bin/env python3
"""
Voice assistant for Alpine Linux + llama.cpp.

Say "computer, <something>" or just type it. Known commands run directly;
anything else goes to your llama-server model and the answer is spoken
(if voice is on) and printed.

Run:
    python3 computer.py            voice + typing, replies spoken
    python3 computer.py --mute     voice + typing, replies text only
    python3 computer.py --text     typing only (no microphone at all)

Files (keep them all in the same folder):
    computer.py      this file: microphone, speech, typing, and the main loop
    common.py        settings, command registry, LLM helpers, utilities
    commands.py      local commands (volume, time, sway, voice on/off ...)
    cloud.py         internet tools (web search, news)
    memory_alpha.py  Star Trek lookups from Memory Alpha and Beta
    wiki_local.py    searches local copies of those wikis (optional)
    import_wiki.py   builds the local copies from a Fandom dump (run it once)

Needs (Alpine):  apk add python3 alsa-utils pamixer espeak-ng qutebrowser foot
Plus:            whisper.cpp (whisper-cli + a ggml model) for speech-to-text
Optional:        piper (neural, more natural voice) -- set PIPER_MODEL in common.py
No pip packages required.
"""
import argparse
import array
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave

from common import (WAKE_WORD, WHISPER_BIN, WHISPER_MODEL,
                    PIPER_BIN, PIPER_MODEL, PIPER_RATE, RATE, SILENCE_RMS,
                    SILENCE_SECONDS, MAX_SECONDS, FOLLOWUP_WAIT,
                    TEXT_FOLLOWUP_WAIT, settings,
                    handle, naturalize, take_followup, set_speaker, new_request,
                    load_history)

# Importing these files is what registers their commands. The order matters:
# commands are checked in the order they are registered, so specific ones
# (Memory Alpha, search) go before broad ones (time, volume, ...).
import memory_alpha  # noqa: F401
import cloud         # noqa: F401
import commands      # noqa: F401

HELP = """\
Type a request and press Enter, the same as saying "computer, ...".
  voice off / voice on   turn spoken replies off or on
  /help                  show this message
  /quit                  exit"""

# Two things can send requests: the microphone thread and the typing thread.
# They put (source, text) in this queue and one worker handles them in order.
requests = queue.Queue()
busy = threading.Event()   # set while a request is being handled; mic stays quiet


# --------------------------------------------------------------- speaking --
def speak(text: str) -> None:
    print(f"[say] {text}")
    if not settings["tts"]:                  # voice off: the printout is the reply
        return
    text = naturalize(text)                  # years etc. sound natural when spoken
    try:
        if shutil.which(PIPER_BIN) and os.path.exists(PIPER_MODEL):
            p = subprocess.Popen([PIPER_BIN, "--model", PIPER_MODEL, "--output-raw",
                                  "--sentence_silence", "0.5",
                                  "--length_scale", "1.15",
                                  "--noise_scale", "0.4"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
            a = subprocess.Popen(["aplay", "-q", "-r", str(PIPER_RATE), "-f", "S16_LE",
                                  "-t", "raw", "-"], stdin=p.stdout)
            p.stdin.write(text.encode()); p.stdin.close()
            a.wait()
        else:
            subprocess.run(["espeak-ng", "-s", "165", text])
    except OSError as e:                     # a speech program is missing: don't crash
        print(f"[speech error] {e}")


set_speaker(speak)          # lets the other files say things (e.g. "Working.")


# -------------------------------------------------------------- listening --
def record_utterance(wait: float | None = None, abort=None) -> str | None:
    """Wait for speech, record until silence, return path to a wav file.

    wait  -- give up (return None) if nobody speaks within this many seconds
    abort -- a threading.Event; if it gets set, stop and return None
    """
    proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE),
                             "-c", "1", "-t", "raw"], stdout=subprocess.PIPE)
    chunk = RATE // 10 * 2            # 100 ms of 16-bit audio
    frames, started, quiet, idle = [], False, 0, 0
    try:
        while True:
            if abort is not None and abort.is_set():
                return None
            data = proc.stdout.read(chunk)
            if not data:
                break
            samples = array.array("h", data)
            rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
            if rms > SILENCE_RMS:
                started, quiet = True, 0
            elif started:
                quiet += 1
            else:
                idle += 1
                if wait and idle >= wait * 10:
                    return None
            if started:
                frames.append(data)
                if quiet >= SILENCE_SECONDS * 10 or len(frames) >= MAX_SECONDS * 10:
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)       # make sure the microphone is free again
        except subprocess.TimeoutExpired:
            proc.kill()
    if not frames:
        return None
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    return path


def transcribe(path: str) -> str:
    out = subprocess.run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", path, "-nt", "-np"],
                         capture_output=True, text=True).stdout
    os.unlink(path)
    out = re.sub(r"\[.*?\]|\(.*?\)", "", out)       # drop [BLANK_AUDIO] etc.
    return out.strip()


def strip_wake_word(text: str) -> str:
    return re.sub(rf"^\W*{WAKE_WORD}\b[,.!?\s]*", "", text, flags=re.I).strip()


def voice_listener():
    """Background thread: listen for the wake word and queue what follows it."""
    while True:
        if busy.is_set():
            time.sleep(0.1)
            continue
        wav = record_utterance(abort=busy)
        if not wav:
            continue
        if busy.is_set():                    # a typed request took over: drop this clip
            try:
                os.unlink(wav)
            except OSError:
                pass
            continue
        text = transcribe(wav)
        if not text:
            continue
        print(f"[heard] {text}")
        m = re.search(rf"\b{WAKE_WORD}\b[,.!?\s]*(.*)", text, re.I)
        if m:
            busy.set()                       # claim the floor before queueing
            requests.put(("voice", m.group(1).strip()))


# ----------------------------------------------------------------- typing --
def text_listener():
    """Background thread: read typed requests from the terminal."""
    print(HELP)
    while True:
        try:
            line = input("> ").strip()
        except EOFError:                     # no terminal (e.g. started as a service)
            return
        if not line:
            continue
        if line.lower() in ("/quit", "/exit"):
            busy.set()
            requests.put(("quit", ""))
            return
        if line.lower() in ("/help", "help"):
            print(HELP)
            continue
        busy.set()
        requests.put(("text", strip_wake_word(line)))


# ---------------------------------------------------------------- handling --
def next_answer(source: str) -> str:
    """Get the user's next input for a follow-up, from the mic or the keyboard."""
    if source == "voice":
        wav = record_utterance(wait=FOLLOWUP_WAIT)
        return strip_wake_word(transcribe(wav)) if wav else ""
    try:
        src, text = requests.get(timeout=TEXT_FOLLOWUP_WAIT)
    except queue.Empty:
        return ""
    if src == "quit":
        raise SystemExit
    return text


def run_followups(source: str):
    """Keep the conversation going (no wake word needed) while a command wants an answer."""
    while (fn := take_followup()):
        text = next_answer(source)
        if not text:
            return
        if re.search(r"\b(cancel|never mind|stop|no thanks|that's all)\b", text, re.I):
            speak("Okay.")
            return
        new_request()
        try:
            reply = fn(text)
        except Exception as e:
            print(f"[error] {e!r}")
            reply = "Something went wrong with that command."
        speak(reply)


def process(source: str, request: str):
    if not request:                          # just the wake word
        speak("Yes?")
        request = next_answer(source)
        if not request:
            return
    new_request()
    try:
        reply = handle(request)
    except Exception as e:
        print(f"[error] {e!r}")
        reply = "Something went wrong with that command."
    speak(reply)
    run_followups(source)


def worker():
    """Handle queued requests one at a time, whether spoken or typed."""
    while True:
        source, request = requests.get()
        busy.set()                           # keeps the microphone quiet meanwhile
        try:
            if source == "quit":
                raise SystemExit
            process(source, request)
        finally:
            if requests.empty():
                busy.clear()


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description="Voice assistant")
    ap.add_argument("--text", action="store_true", help="typing only, no microphone")
    ap.add_argument("--mute", action="store_true", help="start with spoken replies off")
    args = ap.parse_args()

    if args.mute or args.text:
        settings["tts"] = False
    load_history()               # pick up the saved conversation, if any
    speak("Establishing secure connection. System Online. Memory Alpha Priority One. Access Granted.")
    if not args.text:
        threading.Thread(target=voice_listener, daemon=True).start()
    threading.Thread(target=text_listener, daemon=True).start()
    worker()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass
