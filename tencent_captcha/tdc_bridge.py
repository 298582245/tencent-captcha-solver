# -*- coding: utf-8 -*-
"""Node.js + jsdom bridge to run official tdc.js and produce collect/eks."""

from __future__ import annotations

import atexit
import json
import logging
import select
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

from .platform_bin import resolve_executable
from .trajectory import Trajectory

log = logging.getLogger(__name__)

TDC_JS_DIR = Path(__file__).resolve().parent.parent / "tdc" / "js"

_worker_lock = threading.Lock()
_worker: Optional["_TdcWorker"] = None


class _TdcWorker:
    def __init__(self, js_dir: Path, node_path: str, timeout: float) -> None:
        self.js_dir = js_dir
        self.timeout = timeout
        self._proc = subprocess.Popen(
            [node_path, str(js_dir / "tdc_worker.js")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(js_dir),
        )
        self._io_lock = threading.Lock()
        self._wait_worker_ready()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _wait_worker_ready(self, startup_timeout: float = 15.0) -> None:
        if self._proc.stderr is None:
            return

        readable, _, _ = select.select([self._proc.stderr], [], [], startup_timeout)
        if not readable:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"tdc worker start timeout: {err[-400:]}")
        ready = self._proc.stderr.readline()
        if self._proc.poll() is not None:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"tdc worker exited during start: {err[-400:]}")
        if ready:
            log.info("tdc worker: %s", ready.strip())

    def _drain_stderr(self) -> None:
        if not self._proc.stderr:
            return
        for line in self._proc.stderr:
            text = line.strip()
            if text:
                log.debug(text)

    def collect(self, payload: dict) -> Tuple[str, str]:
        if self._proc.poll() is not None:
            raise RuntimeError("tdc worker process exited")
        with self._io_lock:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("tdc worker returned no output")
        data = json.loads(line.strip())
        if data.get("error"):
            raise RuntimeError(f"tdc worker failed: {data['error']}")
        collect = data.get("collect", "")
        eks = data.get("eks", "")
        if not collect:
            raise RuntimeError("tdc worker returned empty collect")
        return collect, eks

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass


def _get_worker(js_dir: Path, node_path: str, timeout: float) -> _TdcWorker:
    global _worker
    with _worker_lock:
        if _worker is None or _worker._proc.poll() is not None:
            if _worker is not None:
                _worker.close()
            _worker = _TdcWorker(js_dir, node_path, timeout)
        return _worker


def _shutdown_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.close()
            _worker = None


atexit.register(_shutdown_worker)


class TdcBridge:
    def __init__(self, js_dir: Optional[Path] = None, node_path: Optional[str] = None, timeout: float = 60.0) -> None:
        self.js_dir = js_dir or TDC_JS_DIR
        self.node_path = node_path or resolve_executable("node") or "node"
        self.timeout = timeout
        self.executor = self.js_dir / "tdc_worker.js"

    def ensure_ready(self) -> None:
        if not (self.js_dir / "tdc_run.js").exists():
            raise FileNotFoundError(f"缺少 {self.js_dir / 'tdc_run.js'}")
        if not self.executor.exists():
            raise FileNotFoundError(f"缺少 {self.executor}。请先运行: python scripts/setup_tdc.py")
        profile = self.js_dir / "profile.js"
        if not profile.exists():
            raise FileNotFoundError(f"缺少 {profile}。请运行: python scripts/setup_tdc.py")
        node_modules = self.js_dir / "node_modules"
        if not node_modules.exists():
            raise FileNotFoundError(f"缺少 {node_modules}。请在 {self.js_dir} 下执行: npm install")

    def collect(
        self,
        tdc_url: str,
        trajectory: Trajectory,
        user_agent: str,
        *,
        captcha_origin: Optional[str] = None,
    ) -> Tuple[str, str]:
        self.ensure_ready()
        payload = {
            "tdc_url": tdc_url,
            "ua": user_agent,
            "captcha_origin": captcha_origin or "",
            "trajectory": {
                "kind": trajectory.kind,
                "points": [{"x": p.x, "y": p.y, "t": p.t} for p in trajectory.points],
                "total_ms": trajectory.total_ms,
            },
            "debug": False,
        }
        worker = _get_worker(self.js_dir, self.node_path, self.timeout)
        collect, eks = worker.collect(payload)
        log.info("TDC collect_len=%d eks_len=%d", len(collect), len(eks))
        return collect, eks

    def warm_up(self) -> None:
        """Start persistent Node worker early (optional)."""
        self.ensure_ready()
        _get_worker(self.js_dir, self.node_path, self.timeout)
