"""SQLite persistence.

The database is the pipeline's memory: it is what stops the same story being
drafted twice, and it is where the evidence ledger for every decision lives so
a published post can be audited weeks later.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    link          TEXT NOT NULL,
    doi           TEXT,
    author        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    raw           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_items_source    ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);

CREATE TABLE IF NOT EXISTS analyses (
    item_id       TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    analysed_at   TEXT NOT NULL,
    binding       TEXT NOT NULL DEFAULT '{}',
    corroboration TEXT NOT NULL DEFAULT '{}',
    claims        TEXT NOT NULL DEFAULT '[]',
    hype          TEXT NOT NULL DEFAULT '{}',
    gate          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS drafts (
    item_id    TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    written_at TEXT NOT NULL,
    word_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stage      TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    ok         INTEGER,
    detail     TEXT NOT NULL DEFAULT '{}'
);
"""


def utcnow() -> str:
    """Timestamps are ISO-8601 UTC everywhere, with no local-time ambiguity."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Item:
    """One normalized entry from any feed."""

    id: str
    source_id: str
    title: str
    link: str
    summary: str = ""
    doi: str | None = None
    author: str | None = None
    published_at: str | None = None
    fetched_at: str = field(default_factory=utcnow)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Everything we are allowed to reason over: headline plus dek.

        The article body is paywalled and is never fetched. This is the
        entire textual surface the pipeline sees from Science itself.
        """
        return f"{self.title}. {self.summary}".strip()


class Store:
    """Thin, explicit SQLite wrapper. No ORM, no magic."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- items ---------------------------------------------------------

    def upsert_item(self, item: Item) -> bool:
        """Insert an item. Returns True when it was new to us.

        Existing rows are left untouched: a feed that re-titles an article
        after publication must not silently change what we already analysed.
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO items
                    (id, source_id, title, summary, link, doi, author,
                     published_at, fetched_at, raw)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, item.source_id, item.title, item.summary, item.link,
                    item.doi, item.author, item.published_at, item.fetched_at,
                    json.dumps(item.raw, ensure_ascii=False),
                ),
            )
            return cursor.rowcount > 0

    def _row_to_item(self, row: sqlite3.Row) -> Item:
        data = dict(row)
        data["raw"] = json.loads(data.get("raw") or "{}")
        return Item(**data)

    def get_item(self, item_id: str) -> Item | None:
        row = self._conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def items(self, *, source_id: str | None = None, limit: int | None = None) -> list[Item]:
        sql = "SELECT * FROM items"
        params: list[Any] = []
        if source_id:
            sql += " WHERE source_id = ?"
            params.append(source_id)
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_item(r) for r in self._conn.execute(sql, params)]

    def unanalysed(self, source_id: str, *, limit: int | None = None) -> list[Item]:
        sql = """
            SELECT i.* FROM items i
            LEFT JOIN analyses a ON a.item_id = i.id
            WHERE i.source_id = ? AND a.item_id IS NULL
            ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
        """
        params: list[Any] = [source_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_item(r) for r in self._conn.execute(sql, params)]

    # -- analyses ------------------------------------------------------

    def save_analysis(self, item_id: str, analysis: "Analysis") -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO analyses
                    (item_id, analysed_at, binding, corroboration, claims, hype, gate)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    analysed_at   = excluded.analysed_at,
                    binding       = excluded.binding,
                    corroboration = excluded.corroboration,
                    claims        = excluded.claims,
                    hype          = excluded.hype,
                    gate          = excluded.gate
                """,
                (
                    item_id, utcnow(),
                    json.dumps(analysis.binding, ensure_ascii=False),
                    json.dumps(analysis.corroboration, ensure_ascii=False),
                    json.dumps(analysis.claims, ensure_ascii=False),
                    json.dumps(analysis.hype, ensure_ascii=False),
                    json.dumps(analysis.gate, ensure_ascii=False),
                ),
            )

    def get_analysis(self, item_id: str) -> "Analysis | None":
        row = self._conn.execute(
            "SELECT * FROM analyses WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return Analysis(
            binding=json.loads(row["binding"]),
            corroboration=json.loads(row["corroboration"]),
            claims=json.loads(row["claims"]),
            hype=json.loads(row["hype"]),
            gate=json.loads(row["gate"]),
            analysed_at=row["analysed_at"],
        )

    def analysed_items(self, source_id: str) -> list[tuple[Item, "Analysis"]]:
        rows = self._conn.execute(
            """
            SELECT i.*, a.binding, a.corroboration, a.claims, a.hype, a.gate,
                   a.analysed_at
            FROM items i JOIN analyses a ON a.item_id = i.id
            WHERE i.source_id = ?
            ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
            """,
            (source_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = self._row_to_item(
                {k: row[k] for k in row.keys() if k in Item.__dataclass_fields__}
            )
            out.append((
                item,
                Analysis(
                    binding=json.loads(row["binding"]),
                    corroboration=json.loads(row["corroboration"]),
                    claims=json.loads(row["claims"]),
                    hype=json.loads(row["hype"]),
                    gate=json.loads(row["gate"]),
                    analysed_at=row["analysed_at"],
                ),
            ))
        return out

    # -- drafts --------------------------------------------------------

    def record_draft(self, item_id: str, path: Path, word_count: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO drafts (item_id, path, written_at, word_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    path = excluded.path,
                    written_at = excluded.written_at,
                    word_count = excluded.word_count
                """,
                (item_id, str(path), utcnow(), word_count),
            )

    def has_draft(self, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM drafts WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row is not None

    # -- runs ----------------------------------------------------------

    def start_run(self, stage: str) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (stage, started_at) VALUES (?, ?)", (stage, utcnow())
            )
            return int(cursor.lastrowid or 0)

    def end_run(self, run_id: int, *, ok: bool, detail: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE runs SET ended_at = ?, ok = ?, detail = ? WHERE id = ?",
                (utcnow(), 1 if ok else 0, json.dumps(detail, ensure_ascii=False), run_id),
            )

    def stats(self) -> dict[str, int]:
        def count(sql: str) -> int:
            return int(self._conn.execute(sql).fetchone()[0])

        return {
            "items": count("SELECT COUNT(*) FROM items"),
            "analysed": count("SELECT COUNT(*) FROM analyses"),
            "drafts": count("SELECT COUNT(*) FROM drafts"),
            "runs": count("SELECT COUNT(*) FROM runs"),
        }


@dataclass
class Analysis:
    """Everything the pipeline concluded about one item, and why."""

    binding: dict[str, Any] = field(default_factory=dict)
    corroboration: dict[str, Any] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    hype: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    analysed_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def passes(self) -> bool:
        return bool(self.gate.get("passes"))

    @property
    def verified_claims(self) -> list[dict[str, Any]]:
        return [c for c in self.claims if c.get("status") == "verified"]
