import os
import sqlite3
import uuid
from datetime import datetime, timezone

from app.schemas import VocabHighlight


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    refined_text TEXT NOT NULL,
                    vsr_raw_text TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    audio_path TEXT,
                    duration_s REAL,
                    confidence REAL,
                    input_source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vocab (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    original_phrase TEXT NOT NULL,
                    refined_phrase TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    starred INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS demos (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    duration_s REAL,
                    screen_path TEXT NOT NULL,
                    audio_path TEXT,
                    export_path TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    warning TEXT
                );
                CREATE TABLE IF NOT EXISTS demo_utterances (
                    id TEXT PRIMARY KEY,
                    demo_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    start_s REAL NOT NULL,
                    end_s REAL NOT NULL,
                    original_text TEXT NOT NULL,
                    english_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    FOREIGN KEY (demo_id) REFERENCES demos(id)
                );
                """
            )

    def save_session(self, *, source_lang, target_lang, original_text, refined_text,
                     vsr_raw_text, video_path, audio_path, duration_s, confidence,
                     input_source) -> str:
        sid = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions (id, created_at, source_lang, target_lang,
                   original_text, refined_text, vsr_raw_text, video_path, audio_path,
                   duration_s, confidence, input_source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, _now(), source_lang, target_lang, original_text, refined_text,
                 vsr_raw_text, video_path, audio_path, duration_s, confidence, input_source),
            )
        return sid

    def update_vsr_text(self, session_id: str, vsr_text: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE sessions SET vsr_raw_text = ? WHERE id = ?",
                         (vsr_text, session_id))

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_vocab(self, session_id: str, highlights: list[VocabHighlight],
                   source_lang: str) -> None:
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO vocab (session_id, original_phrase, refined_phrase,
                   reason, source_lang, starred, created_at)
                   VALUES (?,?,?,?,?,0,?)""",
                [(session_id, h.original_phrase, h.refined_phrase, h.reason, source_lang, _now())
                 for h in highlights],
            )

    def list_vocab(self, starred: bool | None = None, q: str | None = None) -> list[dict]:
        sql = "SELECT * FROM vocab"
        clauses, params = [], []
        if starred:
            clauses.append("starred = 1")
        if q:
            clauses.append("(original_phrase LIKE ? OR refined_phrase LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._vocab_row(r) for r in rows]

    def set_vocab_starred(self, vocab_id: int, starred: bool) -> dict:
        with self._conn() as conn:
            conn.execute("UPDATE vocab SET starred = ? WHERE id = ?",
                         (1 if starred else 0, vocab_id))
            row = conn.execute("SELECT * FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        return self._vocab_row(row)

    @staticmethod
    def _vocab_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["starred"] = bool(d["starred"])
        return d

    def create_demo(self, *, source_lang, target_lang, duration_s, screen_path,
                    audio_path=None, status="saved", error=None, warning=None,
                    demo_id=None) -> str:
        did = demo_id or uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO demos (id, created_at, source_lang, target_lang,
                   duration_s, screen_path, audio_path, export_path, status, error, warning)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (did, _now(), source_lang, target_lang, duration_s, screen_path,
                 audio_path, None, status, error, warning),
            )
        return did

    def get_demo(self, demo_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM demos WHERE id = ?", (demo_id,)).fetchone()
            if row is None:
                return None
            utts = conn.execute(
                "SELECT * FROM demo_utterances WHERE demo_id = ? ORDER BY idx ASC",
                (demo_id,),
            ).fetchall()
        d = dict(row)
        d["utterances"] = [dict(u) for u in utts]
        return d

    def update_demo(self, demo_id: str, **fields) -> None:
        allowed = {"source_lang", "target_lang", "duration_s", "screen_path",
                   "audio_path", "export_path", "status", "error", "warning"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(demo_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE demos SET {', '.join(sets)} WHERE id = ?", vals)

    def replace_utterances(self, demo_id: str, utterances: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM demo_utterances WHERE demo_id = ?", (demo_id,))
            for idx, u in enumerate(utterances):
                conn.execute(
                    """INSERT INTO demo_utterances
                       (id, demo_id, idx, start_s, end_s, original_text, english_text, source_lang)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, demo_id, idx, u["start_s"], u["end_s"],
                     u["original_text"], u["english_text"], u["source_lang"]),
                )

    def update_utterance_texts(self, demo_id: str, edits: list[tuple[str, str]]) -> None:
        with self._conn() as conn:
            for uid, text in edits:
                conn.execute(
                    """UPDATE demo_utterances SET english_text = ?
                       WHERE id = ? AND demo_id = ?""",
                    (text, uid, demo_id),
                )
