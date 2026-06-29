#!/usr/bin/env python3
"""Quick diagnostic: test prehandle only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tencent_captcha.client import TCaptchaClient, BASE_URL_CN, BASE_URL_GLOBAL, resolve_base_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", default="189910271")
    parser.add_argument("--entry-url", default="https://www.tencentcloud.com/zh/products/captcha")
    parser.add_argument("--base-url", default="", help="force base url, e.g. https://ca.turing.captcha.qcloud.com")
    args = parser.parse_args()

    base = args.base_url or resolve_base_url(args.entry_url)
    print(f"entry_url = {args.entry_url}")
    print(f"base_url  = {base}")
    print(f"expected  = {BASE_URL_GLOBAL} (intl) / {BASE_URL_CN} (cn)")

    client = TCaptchaClient(entry_url=args.entry_url, base_url=base or None)
    pre = client.prehandle(args.appid)
    print("OK prehandle")
    print("instruction:", pre.instruction)
    print("show_type:", pre.show_type)
    print("sess:", pre.sess[:80], "...")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("FAIL:", exc)
        sys.exit(1)
