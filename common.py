"""
common.py -- shared pieces used by every other file:
settings, the command registry, follow-up handling, language-model helpers
and a few small utilities.
"""
import json
import os
import re
import subprocess
import urllib.request

# ----------------------------------------------------------------- config --
WAKE_WORD = "computer"
LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
SYSTEM_PROMPT = (
    "You are a helpful voice assistant. Answer in one to three "
    "plain spoken paragraphs. No markdown, lists, or emojis. "
    "Occasionally add a short insight, humorous remark, or quip about the content."
)

WHISPER_BIN = "whisper-cli"                       # whisper.cpp binary
WHISPER_MODEL = os.path.expanduser("~/models/ggml-base.en.bin")

PIPER_BIN = "piper"
PIPER_MODEL = os.path.expanduser("~/models/en_US-kathleen-low.onnx")
PIPER_RATE = 16000                                # match the voice's sample rate

TERMINAL = "foot"                                 # or alacritty, xterm, kitty...
BROWSER = "qutebrowser"
SEARCH_URL = "https://duckduckgo.com/?q="         # fallback if the search tool fails
STARDATE_YEAR_SHIFT = 400      # pretend it is 2426 for stardates; 0 = raw TNG formula
COMMS_HOST = os.environ.get("COMMS_HOST", "hp11")   # machine "open hailing frequencies" ssh's to ("" = none)
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
# Matches "code zero zero zero destruct zero", also as "code 000, destruct 0."
# (Whisper often writes digits and punctuation). Override with the env variable.
SELF_DESTRUCT_PATTERN = os.environ.get(
    "SELF_DESTRUCT_PATTERN",
    r"\bcode\W*(?:(?:zero|0)\W*){3}destruct\W*(?:zero|0)\b")

RATE = 16000
SILENCE_RMS = 1000          # raise if it never stops recording, lower if it never starts
SILENCE_SECONDS = 1.2
MAX_SECONDS = 15
FOLLOWUP_WAIT = 8           # seconds to keep listening (no wake word) after a reply
TEXT_FOLLOWUP_WAIT = 30     # seconds to wait for a typed answer to a follow-up
SAY_WORKING = True          # say "Working." before slow steps (searches, summaries, chat)

# Runtime switches (commands can change these while the assistant runs)
settings = {"tts": True}   # True = speak replies aloud, False = text only

# ------------------------------------------------------- command registry --
COMMANDS = []   # (compiled regex, function), checked in the order registered


def command(pattern: str):
    """Register a command. The function gets the regex match, returns a reply."""
    def deco(fn):
        COMMANDS.append((re.compile(pattern, re.I), fn))
        return fn
    return deco


# ------------------------------------------------------------- follow-ups --
_followup = None   # a function(text) -> reply, used to keep a conversation going


def set_followup(fn):
    """Ask the main loop to listen again (no wake word) and pass the answer to fn."""
    global _followup
    _followup = fn


def take_followup():
    """Return the pending follow-up function (or None) and clear it."""
    global _followup
    fn, _followup = _followup, None
    return fn


# ------------------------------------------------------- speaking helpers --
_speaker = print            # computer.py swaps in the real speak() at start-up
_working_said = False


def set_speaker(fn):
    global _speaker
    _speaker = fn


def say(text):
    """Speak (or print, if voice is off) from any module."""
    _speaker(text)


def new_request():
    """Called for each new thing the user says, so 'Working.' can be said again."""
    global _working_said
    _working_said = False


def working(text="Working."):
    """Say a short 'working' notice, once per request, before a slow step."""
    global _working_said
    if SAY_WORKING and not _working_said:
        _working_said = True
        say(text)


# ---------------------------------------------------------------- helpers --
def run(*args):
    """Start a program and carry on without waiting for it. Returns True if it started."""
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError as e:                     # program not installed, etc.
        print(f"[run error] {args[0]}: {e}")
        return False


UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}


def to_number(text: str) -> int | None:
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total, found = 0, False
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in UNITS: total += UNITS[w]; found = True
        elif w in TENS: total += TENS[w]; found = True
        elif w == "hundred": total = max(total, 1) * 100; found = True
    return total if found else None


# words for picking from a spoken list: "second", "number two", ...
ORDINALS = {"first": 1, "one": 1, "1": 1, "second": 2, "two": 2, "to": 2, "too": 2,
            "2": 2, "third": 3, "three": 3, "3": 3, "fourth": 4, "four": 4, "4": 4}

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen"]
TENS_WORDS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
              "eighty", "ninety"]


def two_digits(n):
    if n < 20:
        return ONES[n]
    return TENS_WORDS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")


def spoken_year(y):
    if y == 2000:
        return "two thousand"
    if 2000 < y < 2010:
        return f"two thousand {two_digits(y - 2000)}"
    hi, lo = divmod(y, 100)
    if lo == 0:
        return f"{two_digits(hi)} hundred"
    if lo < 10:
        return f"{two_digits(hi)} oh {two_digits(lo)}"
    return f"{two_digits(hi)} {two_digits(lo)}"


def naturalize(text):
    """Make years in any spoken text sound natural: 2267 -> twenty-two sixty-seven."""
    def year(m):
        y = int(m.group(1))
        if y % 1000 == 0:                    # leave 1000, 2000 alone
            return m.group(0)
        words = spoken_year(y)
        if m.group(2):                       # a decade like 2260s -> sixties
            last = words.split()[-1]
            plural = last[:-1] + "ies" if last.endswith("y") else last + "s"
            words = words[:-len(last)] + plural
        return words
    # skip numbers that are part of something bigger: 41153.7, 12000, $1500
    return re.sub(r"(?<![\d.,$])([12]\d{3})(s)?\b(?![.,]\d)", year, text)


# ---------------------------------------------------------------- the LLM --
history = []


def ask_llm(prompt: str) -> str:
    """Chat with the llama-server model, remembering recent turns."""
    history.append({"role": "user", "content": prompt})
    body = json.dumps({
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:],
        "temperature": 0.7, "max_tokens": 8192,
    }).encode()
    req = urllib.request.Request(LLAMA_URL, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            reply = json.load(r)["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"I couldn't reach the language model. {e}"
    history.append({"role": "assistant", "content": reply})
    return reply


def llm_once(system, user, timeout=120):
    """One-off question to the model (no chat history), e.g. for summaries."""
    body = json.dumps({"messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                        "temperature": 0.7, "max_tokens": 8192}).encode()
    req = urllib.request.Request(LLAMA_URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------- routing --
def chat_followup(text):
    return handle(text)      # commands still work mid-conversation


def handle(text: str) -> str:
    """Try each command in order; anything unmatched goes to the LLM."""
    for pattern, fn in COMMANDS:
        m = pattern.search(text)
        if m:
            reply = fn(m)
            if reply is not None:
                return reply
    set_followup(chat_followup)   # keep listening after an LLM answer
    working()
    return ask_llm(text)
