# -*- coding: utf-8 -*-
"""SQLite-backed API request audit log."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_default_db = Path(__file__).resolve().parent.parent / "data" / "requests.db"


def _db_path() -> Path:
    raw = os.environ.get("REQUEST_LOG_DB", "").strip()
    return Path(raw) if raw else _default_db


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock:
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    userid TEXT NOT NULL DEFAULT '',
                    client_ip TEXT NOT NULL DEFAULT '',
                    method TEXT NOT NULL DEFAULT 'GET',
                    aid TEXT NOT NULL DEFAULT '',
                    type_id INTEGER NOT NULL DEFAULT 1,
                    entry_url TEXT NOT NULL DEFAULT '',
                    js_path TEXT NOT NULL DEFAULT '',
                    ocr_url TEXT NOT NULL DEFAULT '',
                    retries INTEGER NOT NULL DEFAULT 4,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    http_status INTEGER NOT NULL DEFAULT 200,
                    response_code INTEGER NOT NULL DEFAULT 0,
                    response_msg TEXT NOT NULL DEFAULT '',
                    success INTEGER NOT NULL DEFAULT 0,
                    response_json TEXT NOT NULL DEFAULT '{}',
                    duration_ms INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_request_logs_userid
                    ON request_logs(userid);
                CREATE INDEX IF NOT EXISTS idx_request_logs_created
                    ON request_logs(created_at DESC);
                """
            )
            conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def insert_log(
    *,
    userid: str,
    client_ip: str,
    method: str,
    params: Dict[str, Any],
    http_status: int,
    response_body: Dict[str, Any],
    duration_ms: int,
) -> int:
    data = response_body if isinstance(response_body, dict) else {}
    success = 1 if data.get("code") == 200 else 0
    row_id = 0
    with _lock:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO request_logs (
                    created_at, userid, client_ip, method,
                    aid, type_id, entry_url, js_path, ocr_url, retries,
                    request_json, http_status, response_code, response_msg,
                    success, response_json, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    userid or "",
                    client_ip or "",
                    method or "GET",
                    str(params.get("aid", "")),
                    int(params.get("type_id", 1)),
                    str(params.get("entry_url", "")),
                    str(params.get("js_path") or ""),
                    str(params.get("ocr_url", "")),
                    int(params.get("retries", 8)),
                    json.dumps(params, ensure_ascii=False),
                    int(http_status),
                    int(data.get("code", 0) or 0),
                    str(data.get("msg", "") or ""),
                    success,
                    json.dumps(data, ensure_ascii=False),
                    int(duration_ms),
                ),
            )
            conn.commit()
            row_id = int(cur.lastrowid or 0)
    return row_id


def fetch_logs(
    *,
    userid: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where = ""
    args: List[Any] = []
    if userid:
        where = "WHERE userid = ?"
        args.append(userid)

    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM request_logs " + where, args
        ).fetchone()["c"]
        rows = conn.execute(
            """
            SELECT id, created_at, userid, client_ip, method,
                   aid, type_id, entry_url, js_path, ocr_url, retries,
                   request_json, http_status, response_code, response_msg,
                   success, response_json, duration_ms
            FROM request_logs
            """
            + where
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["success"] = bool(item["success"])
        try:
            item["request"] = json.loads(item.pop("request_json") or "{}")
        except json.JSONDecodeError:
            item["request"] = {}
        try:
            item["response"] = json.loads(item.pop("response_json") or "{}")
        except json.JSONDecodeError:
            item["response"] = {}
        items.append(item)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def fetch_stats(userid: Optional[str] = None) -> Dict[str, Any]:
    where = ""
    args: List[Any] = []
    if userid:
        where = "WHERE userid = ?"
        args.append(userid)

    with _connect() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail_count
            FROM request_logs
            """
            + where,
            args,
        ).fetchone()
        if userid:
            by_user = conn.execute(
                """
                SELECT userid,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok_count
                FROM request_logs
                WHERE userid = ?
                GROUP BY userid
                """,
                [userid],
            ).fetchall()
        else:
            by_user = conn.execute(
                """
                SELECT userid,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok_count
                FROM request_logs
                GROUP BY userid
                ORDER BY total DESC
                LIMIT 20
                """
            ).fetchall()

    total = int(summary["total"] or 0)
    ok_count = int(summary["ok_count"] or 0)
    return {
        "total": total,
        "ok_count": ok_count,
        "fail_count": int(summary["fail_count"] or 0),
        "success_rate": round(ok_count / total * 100, 1) if total else 0.0,
        "by_user": [dict(r) for r in by_user],
    }
