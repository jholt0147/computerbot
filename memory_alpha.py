"""
memory_alpha.py -- Star Trek lookups from Memory Alpha (canon) and
Memory Beta (non-canon), with spoken follow-ups.
"""
import html
import json
import re
import urllib.parse
import urllib.request

from common import (command, run, set_followup, handle, llm_once, working,
                    ORDINALS, BROWSER)

WIKIS = {
    "alpha": "https://memory-alpha.fandom.com/api.php",   # canon
    "beta": "https://memory-beta.fandom.com/api.php",     # non-canon (novels, comics, games)
}
lore = {"wiki": None, "title": None, "url": None, "paras": [], "pos": 0}

SUMMARY_CHARS = 8000      # how much article text to hand the model per summary
SUMMARY_TIMEOUT = 300     # seconds to wait for the model before giving up


def forget_article():
    """Other modules call this so a bare 'more' doesn't resume an old article."""
    lore["paras"] = []


# ------------------------------------------------------------ wiki access --
def wiki_api(wiki, **params):
    params["format"] = "json"
    url = WIKIS[wiki] + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "computer-voice-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def wiki_search(wiki, query):
    d = wiki_api(wiki, action="query", list="search", srsearch=query,
                 srlimit=5, srnamespace=0)
    return [h["title"] for h in d["query"]["search"]]


def wiki_paragraphs(wiki, title):
    d = wiki_api(wiki, action="query", prop="extracts", explaintext=1,
                 exsectionformat="plain", redirects=1, titles=title)
    page = next(iter(d["query"]["pages"].values()))
    text = page.get("extract", "")
    if not text:  # fallback if the TextExtracts extension is missing
        d = wiki_api(wiki, action="parse", page=title, prop="text", redirects=1)
        h = d["parse"]["text"]["*"]
        text = html.unescape("\n".join(re.sub(r"<[^>]+>", "", p)
                                       for p in re.findall(r"<p>(.*?)</p>", h, re.S)))
    return [re.sub(r"\[\d+\]", "", p).strip()
            for p in text.split("\n") if len(p.strip()) > 60]


# ---------------------------------------------------------- reading aloud --
def read_lore(count=3):
    chunk = lore["paras"][lore["pos"]: lore["pos"] + count]
    if not chunk:
        return f"That's everything I have on {lore['title']}."
    lore["pos"] += count
    text = " ".join(chunk)
    working()
    try:
        return llm_once("Summarize this Star Trek reference text for speaking aloud "
                        "in two or three paragraphs. No markdown.",
                        text[:SUMMARY_CHARS], timeout=SUMMARY_TIMEOUT)
    except Exception as e:
        # The model failed or timed out: say so in the terminal and read the
        # article text itself instead of just its first paragraph.
        print(f"[summary error] {e!r} -- reading the article text directly")
        return text[:3000]


def open_article(wiki, title):
    # Construct the fandom article URL
    wiki_domain = "memory-alpha" if wiki == "alpha" else "memory-beta"
    encoded_title = urllib.parse.quote(title.replace(" ", "_"))
    article_url = f"https://{wiki_domain}.fandom.com/wiki/{encoded_title}"

    lore.update(wiki=wiki, title=title, url=article_url,
                paras=wiki_paragraphs(wiki, title), pos=0)

    if not lore["paras"]:
        return f"I found {title} but it has no readable text."

    set_followup(lore_followup)
    return f"{title}, from Memory {wiki.title()}. {read_lore()} Say continue, or say open browser."


def lore_followup(text):
    # Check if the user wants to open the page in qutebrowser
    if re.search(r"\b(?:open|launch|show)(?:\s+in)?\s+(?:the\s+)?browser\b|\bopen\s+it\b", text, re.I):
        if lore.get("url"):
            run(BROWSER, lore["url"])
            return f"Opening {lore['title']} in the browser."
        return "No article URL is currently loaded."

    # Check if the user wants to read more text
    if re.search(r"\b(more|continue|go on|keep going|yes|yeah|sure)\b", text, re.I):
        set_followup(lore_followup)
        return read_lore() + " Say continue, or say open browser."

    return handle(text)          # anything else is treated as a normal request


# ----------------------------------------------------------- finding pages --
def chooser(wiki, options):
    def pick(text):
        n = next((ORDINALS[w] for w in re.findall(r"[a-z0-9]+", text.lower())
                  if w in ORDINALS), None)
        if n is None:
            t = text.lower().strip(" .,!?")
            n = next((i for i, o in enumerate(options, 1)
                      if len(t) > 2 and (t in o.lower() or o.lower() in t)), None)
        if n is None or n > len(options):
            return "Sorry, I didn't catch which one."
        return open_article(wiki, options[n - 1])
    return pick


def memory_lookup(wiki, topic):
    working()
    try:
        titles = wiki_search(wiki, topic)
        if not titles:                       # try the other wiki before giving up
            other = "beta" if wiki == "alpha" else "alpha"
            titles = wiki_search(other, topic)
            if titles:
                wiki = other
        if not titles:
            return f"I couldn't find {topic}."
        exact = [t for t in titles if t.lower() == topic.lower()]
        if exact or len(titles) == 1:
            return open_article(wiki, (exact or titles)[0])
        options = titles[:4]
        set_followup(chooser(wiki, options))
        listing = ". ".join(f"{i}, {t}" for i, t in enumerate(options, 1))
        return f"I found several. {listing}. Which one?"
    except Exception as e:
        print(f"[wiki error] {e}")
        return f"I couldn't reach Memory {wiki.title()}."


# ---------------------------------------------------------------- commands --
@command(r"\bmemory\s+(alpha|beta)\b")
def memory_wiki(m):
    wiki = m.group(1).lower()
    topic = re.sub(r"\b(?:in|on|from|at|using|with)?\s*(?:the\s+)?memory\s+(?:alpha|beta)\b",
                   " ", m.string, flags=re.I)
    topic = re.sub(r"^\W*(?:please\s+)?(?:look\s*up|search(?:\s+for)?|find|tell me about|"
                   r"what is|what was|who is|who was|who were|read me|check)\s+"
                   r"(?:the\s+)?(?:entry\s+(?:on|for)\s+)?", "", topic.strip(), flags=re.I)
    topic = topic.strip(" .,?!")
    if not topic:
        set_followup(lambda t: memory_lookup(wiki, t.strip(" .,?!")))
        return f"What should I look up in Memory {wiki.title()}?"
    return memory_lookup(wiki, topic)


@command(r"^\W*(?:tell me\s+)?more\W*$|^\W*(?:keep going|go on|continue)\W*$")
def more(m):
    if not lore["paras"]:
        return None                          # not in a lookup: fall through to the LLM
    set_followup(lore_followup)
    return read_lore() + " Say continue, or say open browser."
