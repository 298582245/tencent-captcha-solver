# -*- coding: utf-8 -*-
"""Persist failed captcha attempts for offline analysis / future training."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def _failure_root() -> Path:
    raw = os.environ.get("CAPTCHA_FAILURE_DIR", "data/failures")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_failures_enabled() -> bool:
    val = os.environ.get("CAPTCHA_SAVE_FAILURES", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def save_failure_sample(
    *,
    aid: str,
    reason: str,
    bg_bytes: bytes,
    sprite_bytes: Optional[bytes] = None,
    hint_images: Optional[list[bytes]] = None,
    coords: Optional[list[tuple[int, int]]] = None,
    verify_code: Optional[int] = None,
    challenge_type: str = "",
    match_info: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Write one failure folder with images + meta.json. Returns folder path or None."""
    if not save_failures_enabled():
        return None
    if not bg_bytes:
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe_aid = "".join(ch if ch.isalnum() else "_" for ch in aid) or "unknown"
    folder = _failure_root() / ("%s_aid%s" % (ts, safe_aid))
    try:
        folder.mkdir(parents=True, exist_ok=False)
        folder.joinpath("bg.png").write_bytes(bg_bytes)
        if sprite_bytes:
            folder.joinpath("sprite.png").write_bytes(sprite_bytes)
        if hint_images:
            hints_dir = folder / "hints"
            hints_dir.mkdir(exist_ok=True)
            for idx, data in enumerate(hint_images, start=1):
                hints_dir.joinpath("hint_%d.png" % idx).write_bytes(data)

        meta = {
            "aid": aid,
            "reason": reason,
            "challenge_type": challenge_type,
            "verify_code": verify_code,
            "coords": [list(c) for c in coords] if coords else None,
            "match": match_info or {},
            "extra": extra or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        folder.joinpath("meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("failure sample saved: %s", folder)
        return folder
    except OSError as exc:
        log.warning("failure sample save failed: %s", exc)
        return None
