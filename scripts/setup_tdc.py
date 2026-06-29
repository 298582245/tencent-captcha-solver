#!/usr/bin/env python3
"""Download TDC bridge files from crackTCaptcha (GPL-3.0) and npm install jsdom."""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tencent_captcha.platform_bin import resolve_executable

TDC_JS = ROOT / "tdc" / "js"
REPO = "https://raw.githubusercontent.com/olbb/crackTCaptcha/main/src/crack_tcaptcha/tdc/js"
FILES = [
    "package.json",
    "tdc_executor.js",
    "env_patch.js",
    "profile.js",
    "invariants.js",
    "event_dispatch.js",
]


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetch {dest.name}")
    urllib.request.urlretrieve(url, dest)


def main() -> int:
    print(f"TDC target: {TDC_JS}")
    TDC_JS.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        download_file(f"{REPO}/{name}", TDC_JS / name)

    npm = resolve_executable("npm")
    if not npm:
        print("ERROR: 未找到 npm。")
        print("  1) 确认 cmd 里 npm --version 能跑")
        print("  2) 或设置环境变量 NPM_PATH=完整路径\\npm.cmd 后重试")
        return 1

    print(f"npm install ... ({npm})")
    subprocess.run([npm, "install"], cwd=str(TDC_JS), check=True)
    print("TDC bridge ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
