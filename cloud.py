"""
cloud.py -- tools that reach out over the internet: web search and news.
"""
import json
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import memory_alpha
from common import (command, run, set_followup, handle, working, ORDINALS,
                    BROWSER, SEARCH_URL)

# ------------------------------------------------------------- web search --
SEARCH_TOOL = "ddgr"          # or "googler"


def web_results(query, n=3):
    out = subprocess.check_output([SEARCH_TOOL, "--np", "-n", str(n), "--json", query],
                                  timeout=20)
    return json.loads(out)


def result_chooser(results):
    def pick(text):
        n = next((ORDINALS[w] for w in re.findall(r"[a-z0-9]+", text.lower())
                  if w in ORDINALS), None)
        if n is None or n > len(results):
            return "Sorry, which number?"
        run(BROWSER, results[n - 1]["url"])
        return "Working..."
    return pick


@command(r"^\W*(?:google|duckduckgo|search(?:\s+google)?\s+for|search\s+the\s+web\s+for)\W+(.+)")
def web_search_cmd(m):
    query = m.group(1).strip(" .,?!")
    working()
    try:
        results = web_results(query)
    except Exception as e:
        print(f"[search error] {e}")
        run(BROWSER, SEARCH_URL + urllib.parse.quote_plus(query))   # fall back to the browser
        return "The search tool failed, so I opened the browser instead."
    if not results:
        return f"I found nothing for {query}."
    spoken = ". ".join(f"{i}, {r.get('title', 'untitled')}"
                       for i, r in enumerate(results, 1))
    set_followup(result_chooser(results))
    return f"Top results. {spoken}. Say a number to open one."


# ------------------------------------------------------------------- news --
NEWS_FEEDS = {   # keyword you can say -> feed URL
    "top":      "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "world":    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "tech":     "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "science":  "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
    "business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
}
news = {"items": [], "pos": 0}


def fetch_headlines(url):
    req = urllib.request.Request(url, headers={"User-Agent": "computer-voice-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        root = ET.fromstring(r.read())
    # Strips the trailing ' - Publisher' from Google News RSS titles for spoken clarity
    return [i.findtext("title", "").rsplit(" - ", 1)[0].strip() for i in root.iter("item")]


def read_headlines(count=3):
    chunk = news["items"][news["pos"]: news["pos"] + count]
    if not chunk:
        return "That's all the headlines I have."
    news["pos"] += count
    set_followup(news_followup)
    return ". ".join(chunk) + ". Want more headlines?"


def news_followup(text):
    if re.search(r"\b(more|continue|go on|keep going|yes|yeah|sure)\b", text, re.I):
        return read_headlines()
    return handle(text)          # anything else is a normal request


@command(r"\b(?:news|headlines)\b")
def get_news(m):
    topic = next((k for k in NEWS_FEEDS if k in m.string.lower()), "top")
    working()
    try:
        news.update(items=fetch_headlines(NEWS_FEEDS[topic]), pos=0)
    except Exception as e:
        print(f"[news error] {e}")
        return "I couldn't reach the news feed."
    memory_alpha.forget_article()   # so a bare "more" doesn't resume an old wiki article
    return f"Here are the {topic} headlines. " + read_headlines()
