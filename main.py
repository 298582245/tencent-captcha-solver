#!/usr/bin/env python3
"""腾讯云 TCaptcha 图形点选验证 — 获取 ticket / randstr"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from tencent_captcha import solve_captcha
from tencent_captcha.solver import TENCENTCLOUD_DEMO_APPIDS, DEFAULT_ENTRY_URL


def main() -> int:
    parser = argparse.ArgumentParser(description="Solve Tencent TCaptcha click challenge")
    parser.add_argument("--appid", help="CaptchaAppId (默认按 challenge-type 选取演示页 appid)")
    parser.add_argument(
        "--challenge-type",
        choices=["word_click", "icon_click", "slider", "image_select"],
        default="image_select",
        help="演示页验证类型；建议先用 image_select 测通",
    )
    parser.add_argument("--entry-url", default=DEFAULT_ENTRY_URL, help="业务入口页 Referer")
    parser.add_argument("--ocr-url", default=None, help="ddddocr Flask 服务地址（默认 OCR_BASE_URL 或 http://127.0.0.1:7777）")
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = solve_captcha(
        appid=args.appid,
        challenge_type=args.challenge_type,
        entry_url=args.entry_url,
        ocr_base_url=args.ocr_url or os.environ.get("OCR_BASE_URL", "http://127.0.0.1:7777"),
        max_retries=args.retries,
    )

    if args.as_json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    elif result.ok:
        print(f"OK  type={result.challenge_type}")
        print(f"ticket  = {result.ticket}")
        print(f"randstr = {result.randstr}")
    else:
        print(f"FAIL  type={result.challenge_type}  code={result.error_code}  msg={result.error_msg}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
