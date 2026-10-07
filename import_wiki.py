#!/usr/bin/env python3
"""
import_wiki.py -- build a local, searchable copy of Memory Alpha or Memory Beta
from a Fandom XML dump, so lookups work offline and don't hit the website.

Usage:
    python3 import_wiki.py alpha path/to/memory-alpha_pages_current.xml.7z
    python3 import_wiki.py beta  path/to/memory-beta_pages_current.xml.gz
    python3 import_wiki.py --info

Getting a dump: open <wiki>/wiki/Special:Statistics (for example
https://memory-alpha.fandom.com/wiki/Special:Statistics) and look for the
"Database dumps" section. Get the *current pages* dump, not the full history
(which is much bigger). Fandom only lets wiki admins request fresh dumps, so
the available one may be old; the assistant falls back to the live wiki for
anything the local copy lacks.

Accepted files: .xml, .xml.gz, .xml.7z (needs the `7z` program, e.g.
`apk add 7zip`, or the py7zr package; or just unpack the 7z yourself first).

The database is written to the data/ folder next to this script. Do not
commit it: the wikis' text is under Creative Commons licenses and is large.
"""
import argparse
import gzip
import html
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from common import WIKI_DB_DIR

MAX_PARAS = 60          # most paragraphs kept per article
MAX_CHARS = 30000       # most characters kept per article
MIN_PARA_LEN = 60       # shorter lines are not read aloud

# Sections that are lists of links and references, not prose.
CUT_HEADINGS = re.compile(
    r"^=+\s*(?:appendices|see also|external links|references|connections|"
    r"related stories|links and references|apocrypha)\s*=+\s*$", re.I | re.M)

COMMENT = re.compile(r"<!--.*?-->", re.S)
REF_SELF = re.compile(r"<ref\b[^>]*/>", re.I)
REF_PAIR = re.compile(r"<ref\b[^>]*>.*?</ref>", re.S | re.I)
BLOCK_TAGS = re.compile(r"<(gallery|timeline|poem|math|noinclude|references)\b.*?</\1>",
                        re.S | re.I)
TABLE = re.compile(r"^\{\|.*?^\|\}", re.S | re.M)
EXT_LINK = re.compile(r"\[(?:https?:)?//\S+(?:\s+([^\]]+))?\]")
NAMED_ARG = re.compile(r"^\s*[\w .-]+=")
NAMESPACE_LINK = re.compile(
    r"\s*:?\s*(?:file|image|media|category|special|template|help)\s*:", re.I)


# ---------------------------------------------------------- wikitext cleanup --
def split_args(body):
    """Split on '|' that are not inside {{ }} or [[ ]]."""
    parts, depth, last = [], 0, 0
    for m in re.finditer(r"\{\{|\}\}|\[\[|\]\]|\|", body):
        tok = m.group()
        if tok in ("{{", "[["):
            depth += 1
        elif tok in ("}}", "]]"):
            depth = max(0, depth - 1)
        elif depth == 0:
            parts.append(body[last:m.start()])
            last = m.end()
    parts.append(body[last:])
    return parts


# Citation templates like {{TNG|Sins of the Father}}: reading the bare episode
# name in the middle of a sentence sounds odd, so they are dropped.
SERIES_TEMPLATES = {"tos", "tas", "tng", "ds9", "voy", "ent", "dis", "pic", "ld",
                    "pro", "snw", "sfa", "sto", "tmp", "twok", "tsfs", "tvh",
                    "tff", "tuc", "gen", "fc", "ins", "nem", "st09", "stid", "stb"}


def template_text(body):
    """What an inline template contributes to the sentence: its first plain argument."""
    args = split_args(body)
    name = args[0].strip().lower()
    if name in SERIES_TEMPLATES:
        return ""
    for arg in args[1:]:
        if NAMED_ARG.match(arg):
            continue
        shown = strip_templates(arg, inline=True).strip()
        return f"USS {shown}" if name == "uss" else shown
    return ""


def strip_templates(text, inline=False):
    """Remove {{templates}}. Block templates (infoboxes, sidebars) vanish;
    templates inside a sentence are replaced by their first argument."""
    out, pos, depth, start = [], 0, 0, 0
    for m in re.finditer(r"\{\{|\}\}", text):
        if m.group() == "{{":
            if depth == 0:
                out.append(text[pos:m.start()])
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                body = text[start + 2:m.start()]
                at_line_start = start == 0 or text[start - 1] == "\n"
                nxt = text[m.end():m.end() + 1]
                is_block = at_line_start and ("\n" in body or nxt in ("", "\n", "{"))
                if inline or not is_block:
                    out.append(template_text(body))
                pos = m.end()
    if depth == 0:
        out.append(text[pos:])
    return "".join(out)          # an unterminated template is dropped


def link_text(body):
    """What a [[link]] shows: its label, or its target. Files and categories vanish."""
    if NAMESPACE_LINK.match(body):
        return ""
    parts = split_args(body)
    shown = parts[-1] if len(parts) > 1 else parts[0].split("#")[0]
    return strip_links(shown.lstrip(": "))


def strip_links(text):
    out, pos, depth, start = [], 0, 0, 0
    for m in re.finditer(r"\[\[|\]\]", text):
        if m.group() == "[[":
            if depth == 0:
                out.append(text[pos:m.start()])
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out.append(link_text(text[start + 2:m.start()]))
                pos = m.end()
    if depth == 0:
        out.append(text[pos:])
    return "".join(out)


def clean_wikitext(text):
    """Turn raw wikitext into a list of plain-text paragraphs, ready to read aloud."""
    text = COMMENT.sub("", text)
    cut = CUT_HEADINGS.search(text)
    if cut:
        text = text[:cut.start()]
    text = REF_SELF.sub("", text)
    text = REF_PAIR.sub("", text)
    text = BLOCK_TAGS.sub("", text)
    text = TABLE.sub("", text)
    text = strip_templates(text)
    text = strip_links(text)
    text = EXT_LINK.sub(lambda m: m.group(1) or "", text)
    text = re.sub(r"'{2,5}", "", text)                  # bold / italics
    text = re.sub(r"__[A-Z]+__", "", text)              # __NOTOC__ etc.
    text = re.sub(r"<[^>]+>", "", text)                 # leftover html tags
    text = html.unescape(text)

    paras, total = [], 0
    for line in text.split("\n"):
        line = line.strip()
        if not line or line[0] in "*#:;|!=":            # lists, headings, leftovers
            continue
        line = re.sub(r"\(\s*[,;]?\s*\)", "", line)     # "( )" left by removed templates
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < MIN_PARA_LEN:
            continue
        paras.append(line)
        total += len(line)
        if len(paras) >= MAX_PARAS or total >= MAX_CHARS:
            break
    return paras


# ----------------------------------------------------------------- reading --
def open_dump(path):
    """Return (stream, cleanup): a binary stream of the dump's XML, and a function to call afterwards."""
    lower = path.lower()
    nothing = lambda: None
    if lower.endswith(".gz"):
        f = gzip.open(path, "rb")
        return f, f.close
    if lower.endswith(".7z"):
        for exe in ("7zz", "7z", "7za", "7zr"):
            if shutil.which(exe):
                proc = subprocess.Popen([exe, "e", "-so", path],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                return proc.stdout, proc.stdout.close
        try:
            import py7zr
        except ImportError:
            sys.exit("Can't open .7z: install the 7z program (apk add 7zip), "
                     "or pip install py7zr, or unpack the file and pass the .xml.")
        print("(using py7zr: unpacking to a temporary folder first)")
        tmp = tempfile.mkdtemp(prefix="wikidump-", dir=WIKI_DB_DIR)
        with py7zr.SevenZipFile(path) as z:
            z.extractall(path=tmp)
        xml_path = next((os.path.join(root, n) for root, _, names in os.walk(tmp)
                         for n in names if n.endswith(".xml")), None)
        if not xml_path:
            shutil.rmtree(tmp, ignore_errors=True)
            sys.exit("No .xml file found inside the 7z archive.")
        f = open(xml_path, "rb")
        return f, lambda: (f.close(), shutil.rmtree(tmp, ignore_errors=True))
    f = open(path, "rb")
    return f, f.close


def local(tag):
    return tag.rsplit("}", 1)[-1]


def page_fields(page):
    title = ns = text = redirect = None
    for c in page:
        t = local(c.tag)
        if t == "title":
            title = c.text
        elif t == "ns":
            ns = c.text
        elif t == "redirect":
            redirect = c.get("title")
        elif t == "revision":              # the last revision in the file is the newest
            for r in c:
                if local(r.tag) == "text":
                    text = r.text or ""
    return title, ns, redirect, text


def iter_pages(stream):
    context = ET.iterparse(stream, events=("start", "end"))
    _, root = next(context)
    for event, elem in context:
        if event == "end" and local(elem.tag) == "page":
            yield page_fields(elem)
            root.clear()                   # keep memory flat on huge dumps


# ----------------------------------------------------------------- writing --
def build(wiki, dump_path, use_fts=True):
    os.makedirs(WIKI_DB_DIR, exist_ok=True)
    final = os.path.join(WIKI_DB_DIR, f"memory_{wiki}.db")
    tmp = final + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE pages (id INTEGER PRIMARY KEY, title TEXT, title_lc TEXT, body TEXT);
        CREATE TABLE redirects (from_lc TEXT PRIMARY KEY, to_title TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    started = time.time()
    pages = redirects = skipped = 0
    batch_p, batch_r = [], []

    def flush():
        con.executemany("INSERT OR REPLACE INTO pages(title, title_lc, body) VALUES (?,?,?)", batch_p)
        con.executemany("INSERT OR REPLACE INTO redirects VALUES (?,?)", batch_r)
        batch_p.clear(); batch_r.clear()

    stream, cleanup = open_dump(dump_path)
    for title, ns, redirect, text in iter_pages(stream):
        if not title or ns != "0":                       # articles only
            continue
        if redirect is None and text and text.lstrip()[:9].upper() == "#REDIRECT":
            m = re.search(r"\[\[([^\]|#]+)", text)
            redirect = m.group(1).strip() if m else None
        if redirect:
            batch_r.append((title.lower(), redirect.split("#")[0].strip()))
            redirects += 1
        else:
            paras = clean_wikitext(text or "")
            if not paras:
                skipped += 1
            else:
                batch_p.append((title, title.lower(), "\n".join(paras)))
                pages += 1
        if len(batch_p) + len(batch_r) >= 1000:
            flush()
        if (pages + redirects) and (pages + redirects) % 5000 == 0:
            print(f"  {pages} articles, {redirects} redirects ...", flush=True)
    flush()
    cleanup()

    con.execute("CREATE INDEX idx_title_lc ON pages(title_lc)")
    fts = False
    if use_fts:
        try:
            con.executescript("""
                CREATE VIRTUAL TABLE pages_fts USING fts5(
                    title, body, content='pages', content_rowid='id');
                INSERT INTO pages_fts(pages_fts) VALUES('rebuild');
            """)
            fts = True
        except sqlite3.OperationalError as e:
            print(f"(full-text search unavailable: {e}; title search only)")
    meta = {"wiki": wiki, "source": os.path.basename(dump_path),
            "imported": time.strftime("%Y-%m-%d %H:%M"), "articles": str(pages),
            "redirects": str(redirects), "fts": "yes" if fts else "no"}
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.close()
    os.replace(tmp, final)
    mb = os.path.getsize(final) / 1e6
    print(f"Done in {time.time() - started:.0f}s: {pages} articles, {redirects} redirects, "
          f"{skipped} empty pages skipped, {mb:.1f} MB -> {final}")


def show_info():
    found = False
    for wiki in ("alpha", "beta"):
        path = os.path.join(WIKI_DB_DIR, f"memory_{wiki}.db")
        if os.path.exists(path):
            found = True
            con = sqlite3.connect(path)
            meta = dict(con.execute("SELECT key, value FROM meta"))
            con.close()
            print(f"Memory {wiki.title()}: {meta.get('articles')} articles, "
                  f"imported {meta.get('imported')} from {meta.get('source')}, "
                  f"full-text search: {meta.get('fts')}")
    if not found:
        print(f"No local databases found in {WIKI_DB_DIR}")


def main():
    ap = argparse.ArgumentParser(description="Import a Memory Alpha/Beta dump")
    ap.add_argument("wiki", nargs="?", choices=("alpha", "beta"))
    ap.add_argument("dump", nargs="?", help=".xml, .xml.gz or .xml.7z dump file")
    ap.add_argument("--info", action="store_true", help="show what is imported")
    ap.add_argument("--no-fts", action="store_true", help="skip the full-text index")
    args = ap.parse_args()
    if args.info:
        return show_info()
    if not (args.wiki and args.dump):
        ap.error("give a wiki (alpha or beta) and a dump file, or use --info")
    build(args.wiki, args.dump, use_fts=not args.no_fts)


if __name__ == "__main__":
    main()
