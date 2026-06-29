# -*- coding: utf-8 -*-
"""Node.js + jsdom bridge to run official tdc.js and produce collect/eks."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional, Tuple
from pathlib import Path

from .platform_bin import resolve_executable
from .trajectory import Trajectory

log = logging.getLogger(__name__)

TDC_JS_DIR = Path(__file__).resolve().parent.parent / "tdc" / "js"


class TdcBridge:
    def __init__(self, js_dir: Optional[Path] = None, node_path: Optional[str] = None, timeout: float = 60.0) -> None:
        self.js_dir = js_dir or TDC_JS_DIR
        self.node_path = node_path or resolve_executable("node") or "node"
        self.timeout = timeout
        self.executor = self.js_dir / "tdc_executor.js"

    def ensure_ready(self) -> None:
        if not self.executor.exists():
            raise FileNotFoundError(
                f"缺少 {self.executor}。请先运行: python scripts/setup_tdc.py"
            )
        profile = self.js_dir / "profile.js"
        if not profile.exists():
            raise FileNotFoundError(
                f"缺少 {profile}。请运行: python scripts/setup_tdc.py（会下载 profile.js）"
            )
        node_modules = self.js_dir / "node_modules"
        if not node_modules.exists():
            raise FileNotFoundError(
                f"缺少 {node_modules}。请在 {self.js_dir} 下执行: npm install"
            )

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
        proc = subprocess.run(
            [self.node_path, str(self.executor)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(self.js_dir),
            timeout=self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"tdc_executor failed: {proc.stderr[-800:]}")
        data = json.loads(proc.stdout.strip())
        collect = data.get("collect", "")
        eks = data.get("eks", "")
        if not collect:
            raise RuntimeError("tdc_executor returned empty collect")
        log.info("TDC collect_len=%d eks_len=%d", len(collect), len(eks))
        return collect, eks
