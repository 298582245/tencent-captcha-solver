# -*- coding: utf-8 -*-
"""Resolve node/npm on Windows where shutil.which('npm') often fails."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def resolve_executable(name: str) -> Optional[str]:
    """Find node/npm executable; Windows needs .cmd / where fallback."""
    env_override = os.environ.get(name.upper() + "_PATH") or os.environ.get(name.upper())
    if env_override:
        p = Path(env_override)
        if p.is_file():
            return str(p)

    for candidate in (name, f"{name}.cmd", f"{name}.exe"):
        path = shutil.which(candidate)
        if path and Path(path).is_file():
            return path

    if sys.platform == "win32":
        for p in _windows_candidates(name):
            if p.is_file():
                return str(p)

        try:
            proc = subprocess.run(
                ["cmd", "/c", "where", name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.strip().splitlines():
                    p = Path(line.strip().strip('"'))
                    if p.is_file() and p.suffix.lower() in {".cmd", ".exe", ".bat", ""}:
                        return str(p)
                    if p.is_dir():
                        for suffix in (".cmd", ".exe"):
                            inner = p / f"{name}{suffix}"
                            if inner.is_file():
                                return str(inner)
        except (OSError, subprocess.TimeoutExpired):
            pass

    return None


def _windows_candidates(name: str) -> list[Path]:
    out: list[Path] = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")

    for base in (Path(pf) / "nodejs", Path(pf86) / "nodejs"):
        for suffix in (".cmd", ".exe", ""):
            out.append(base / f"{name}{suffix}")

    if appdata:
        npm_dir = Path(appdata) / "npm"
        out.append(npm_dir / f"{name}.cmd")
        out.append(npm_dir / f"{name}.exe")
        out.append(npm_dir / name)

    if localappdata:
        out.append(Path(localappdata) / "Programs" / "nodejs" / f"{name}.cmd")

    return out
