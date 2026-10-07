"""
wiki_local.py -- search the local Memory Alpha / Memory Beta databases built
by import_wiki.py. If a database doesn't exist, available() is False and
memory_alpha.py uses the live wiki instead.
"""
import os
import re
import sqlite3

from common import WIKI_DB_DIR

_connections = {}


def _connect(wiki):
    if wiki not in _connections:
        path = os.path.join(WIKI_DB_DIR, f"memory_{wiki}.db")
        if os.path.exists(path):
            try:
                con = sqlite3.connect(path, check_same_thread=False)
                con.execute("SELECT 1 FROM pages LIMIT 1")
                _connections[wiki] = con
            except sqlite3.Error as e:
                print(f"[wiki] can't use {path}: {e}")
                _connections[wiki] = None
        else:
            _connections[wiki] = None
    return _connections[wiki]


def available(wiki):
    return _connect(wiki) is not None


def _like(text):
    """Escape LIKE wildcards in user text."""
    return re.sub(r"([\\%_])", r"\\\1", text)


def _resolve(con, lc):
    """Exact title match for a lowercase name, following one redirect. Returns title or None."""
    for _ in range(2):
        row = con.execute("SELECT title FROM pages WHERE title_lc=?", (lc,)).fetchone()
        if row:
            return row[0]
        red = con.execute("SELECT to_title FROM redirects WHERE from_lc=?", (lc,)).fetchone()
        if not red:
            return None
        lc = red[0].lower()
    return None


def search(wiki, query, limit=5):
    """Matching article titles, best first. An exact title (or redirect) returns just that one."""
    con = _connect(wiki)
    if con is None:
        return []
    lc = re.sub(r"\s+", " ", query.replace("_", " ")).strip().lower()
    if not lc:
        return []

    for candidate in (lc, re.sub(r"^(?:the|a|an)\s+", "", lc)):
        exact = _resolve(con, candidate)
        if exact:
            return [exact]

    results = []

    def add(rows):
        for (title,) in rows:
            if title not in results:
                results.append(title)

    esc = _like(lc)
    add(con.execute("SELECT title FROM pages WHERE title_lc LIKE ? ESCAPE '\\' "
                    "ORDER BY length(title) LIMIT ?", (esc + "%", limit)))
    if len(results) < limit:
        add(con.execute("SELECT title FROM pages WHERE title_lc LIKE ? ESCAPE '\\' "
                        "ORDER BY length(title) LIMIT ?", ("%" + esc + "%", limit)))
    if len(results) < limit:
        words = re.findall(r"[A-Za-z0-9]+", query)
        if words:
            fts_query = " ".join(f'"{w}"' for w in words)
            try:
                add(con.execute(
                    "SELECT p.title FROM pages_fts f JOIN pages p ON p.id = f.rowid "
                    "WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts, 10.0, 1.0) LIMIT ?",
                    (fts_query, limit)))
            except sqlite3.OperationalError:
                pass                      # no full-text index in this database
    return results[:limit]


def paragraphs(wiki, title):
    """The article's cleaned paragraphs, or None if it isn't in the local database."""
    con = _connect(wiki)
    if con is None:
        return None
    row = con.execute("SELECT body FROM pages WHERE title=?", (title,)).fetchone()
    if not row:
        found = _resolve(con, title.lower())
        if found:
            row = con.execute("SELECT body FROM pages WHERE title=?", (found,)).fetchone()
    return row[0].split("\n") if row else None
