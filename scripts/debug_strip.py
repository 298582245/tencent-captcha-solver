#!/usr/bin/env python3
"""Fetch one Select-in-order captcha and save strip / hint crops for inspection."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tencent_captcha.client import TCaptchaClient
from tencent_captcha.ocr_client import DdddOcrClient
from tencent_captcha.solver import DEFAULT_ENTRY_URL, TENCENTCLOUD_DEMO_APPIDS

OUT = ROOT / "debug_captcha"
APPID = TENCENTCLOUD_DEMO_APPIDS["image_select"]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    http = TCaptchaClient(entry_url=DEFAULT_ENTRY_URL)
    ocr = DdddOcrClient(base_url="http://124.222.179.175:7777")
    pre = http.prehandle(APPID)
    bg = http.get_image(pre.img_url)
    sprite = http.get_image(pre.sprite_url)
    (OUT / "bg.png").write_bytes(bg)
    (OUT / "sprite.png").write_bytes(sprite)

    elem = pre.ins_elem_list[0]
    px, py = elem.sprite_pos
    pw, ph = elem.size_2d
    from PIL import Image
    import io

    sp = Image.open(io.BytesIO(sprite)).convert("RGB")
    strip = sp.crop((px, py, px + pw, py + ph))
    strip.save(OUT / "strip.png")

    hints = ocr.split_hint_strip(
        ocr._crop_png(sp, (px, py, px + pw, py + ph))
    )
    for i, h in enumerate(hints):
        (OUT / f"hint_{i+1}.png").write_bytes(h)

    print(f"saved to {OUT}")
    print(f"strip size={strip.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
