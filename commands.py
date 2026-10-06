"""
commands.py -- local commands: volume, terminals, time and date, stardate,
system status, and Sway window control.

To add a command, copy the pattern:

    @command(r"\\bmute\\b")
    def mute(m):
        subprocess.run(["pamixer", "--mute"])
        return "Muted."
"""
import datetime
import json
import os
import subprocess

from common import (command, run, to_number, spoken_year, settings,
                    BROWSER, TERMINAL, STARDATE_YEAR_SHIFT,
                    COMMS_HOST, SOUNDS_DIR, SELF_DESTRUCT_PATTERN)


# ------------------------------------------------------------ voice switch --
VOICE = r"(?:voice|speech|tts|text[\s-]to[\s-]speech)"


@command(rf"^\W*(?:turn\s+)?(?:the\s+)?{VOICE}\s+off\W*$"
         rf"|^\W*turn\s+off\s+(?:the\s+)?{VOICE}\W*$"
         r"|^\W*(?:text\s+only(?:\s+mode)?|stop\s+talking|be\s+quiet)\W*$")
def voice_off(m):
    settings["tts"] = False
    return "Voice off. I'll reply in text only."


@command(rf"^\W*(?:turn\s+)?(?:the\s+)?{VOICE}\s+(?:back\s+)?on\W*$"
         rf"|^\W*turn\s+on\s+(?:the\s+)?{VOICE}\W*$"
         r"|^\W*(?:start\s+talking|speak\s+again|you\s+can\s+talk(?:\s+again)?)\W*$")
def voice_on(m):
    settings["tts"] = True
    return "Voice on."


# ----------------------------------------------------------- sound & apps --
@command(r"\b(?:set\s+)?volume\b(?:.*?\bto\b)?\s*(.+)")
def set_volume(m):
    n = to_number(m.group(1))
    if n is None:
        return "What volume level?"
    n = max(0, min(100, n))
    subprocess.run(["pamixer", "--set-volume", str(n)])
    return f"Volume set to {n} percent."


@command(r"\b(?:open|launch|start)\b.*\bterminal\b")
def open_terminal(m):
    run(TERMINAL)
    return "Opening the terminal."


@command(r"\b(?:open communications|open hailing frequencies)\b")
def open_comms(m):
    if not COMMS_HOST:
        return "No communications host is set. Edit COMMS_HOST in common.py."
    run("footclient", "ssh", COMMS_HOST, "-t", "tmux", "a")
    return "Hailing frequencies open."


def play_sound(name, *options):
    """Play a sound effect from the sounds folder; skip quietly if it isn't there."""
    path = os.path.join(SOUNDS_DIR, name)
    if not os.path.exists(path):
        print(f"[sound] {path} not found -- skipping the sound effect")
        return False
    try:
        subprocess.run(["mpv", *options, path])
    except OSError as e:                     # mpv not installed
        print(f"[sound error] {e}")
        return False
    return True


@command(r"\b(?:red alert)\b")
def red_alert(m):
    play_sound("redalert.mp3", "--loop=2")
    return "Red alert. all hands to battlestations. red alert."

@command(SELF_DESTRUCT_PATTERN)
def self_destruct(m):
    play_sound("selfdestruct.wav")
    return "Self-destruct sequence initiated. Core breach imminent."


@command(r"\b(?:open|launch|start)\b.*\bqute\s*browser\b|\b(?:open|launch)\b.*\bbrowser\b")
def open_browser(m):
    run(BROWSER)
    return "Launching the browser."


# --------------------------------------------------------- time and dates --
def spoken_time(now):
    if now.hour == 0 and now.minute == 0:
        return "midnight"
    if now.hour == 12 and now.minute == 0:
        return "noon"
    hour = now.hour % 12 or 12
    if now.minute == 0:
        clock = f"{hour} o'clock"
    elif now.minute < 10:
        clock = f"{hour} oh {now.minute}"
    else:
        clock = f"{hour}:{now.minute}"
    h = now.hour
    part = ("in the morning" if h < 12 else "in the afternoon" if h < 18
            else "in the evening" if h < 22 else "at night")
    return f"{clock} {part}"


@command(r"\bwhat(?:['’]s|\s+is)?\s+the\s+(?:current\s+)?time\b|\bwhat\s+time\s+is\s+it\b|\bcurrent\s+time\b|^\W*(?:the\s+)?time\W*$")
def get_time(m):
    return f"The time is {spoken_time(datetime.datetime.now())}."


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@command(r"\bwhat(?:['’]s|\s+is)?\s+(?:the|today['’]s)\s+date\b|\bwhat\s+day\s+is\s+(?:it|today)\b|\bcurrent\s+date\b")
def get_date(m):
    now = datetime.date.today()
    return f"Today is {now:%A}, {now:%B} the {ordinal(now.day)}, {spoken_year(now.year)}."


@command(r"\bwhat\s+year\s+is\s+it\b|\bwhat(?:['’]s|\s+is)?\s+the\s+(?:current\s+)?year\b|\bcurrent\s+year\b")
def get_year(m):
    return f"It's {spoken_year(datetime.date.today().year)}."


def stardate(now, year_shift=0):
    start = datetime.datetime(now.year, 1, 1)
    end = datetime.datetime(now.year + 1, 1, 1)
    fraction = (now - start) / (end - start)
    return 1000 * (now.year + year_shift - 2323) + 1000 * fraction


def say_digits(text):
    words = {"0": "oh", ".": "point", "-": "negative"}
    return " ".join(words.get(c, c) for c in text)


@command(r"\bstardate\b")
def get_stardate(m):
    now = datetime.datetime.now()
    sd = stardate(now, STARDATE_YEAR_SHIFT)
    return (f"Stardate {say_digits(f'{sd:.1f}')}. "
            f"That's {now:%B} the {ordinal(now.day)}, {spoken_year(now.year)}, on Earth.")


# ----------------------------------------------------------------- system --
@command(r"\b(?:system status|resource usage|memory usage)\b")
def system_status(m):
    mem = subprocess.check_output(["free", "-m"]).decode().splitlines()[1].split()
    used_mb = mem[2]
    return f"Memory usage is currently at {used_mb} megabytes."


# -------------------------------------------------------------------- sway --
def screen_size():
    """Retrieve active monitor width and height using swaymsg."""
    try:
        out = subprocess.check_output(["swaymsg", "-t", "get_outputs"]).decode()
        for output in json.loads(out):
            if output.get("active"):
                rect = output["rect"]
                return rect["width"], rect["height"]
    except Exception as e:
        print(f"[swaymsg error] {e}")
    return 1920, 1080  # fallback dimensions


@command(r"\b(move|focus)\s+(?:the\s+)?(?:window\s+)?(up|down|left|right)\b")
def sway_window(m):
    action, direction = m.group(1).lower(), m.group(2).lower()
    subprocess.run(["swaymsg", action, direction])
    return f"Window {action} {direction}."


@command(r"\bset\b.*?\b(width|height)\b(?:.*?\bto\b\s*(.+))?")
def sway_resize(m):
    dim = m.group(1).lower()
    amount = to_number(m.group(2) or "")
    if amount is None:
        return f"To what {dim}?"
    amount = max(5, min(100, amount))        # keep it between 5 and 100 percent
    w, h = screen_size()
    px = round((w if dim == "width" else h) * amount / 100)
    subprocess.run(["swaymsg", "resize", "set", dim, str(px), "px"])
    return f"Window {dim} set to {amount} percent."
