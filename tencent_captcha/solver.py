# -*- coding: utf-8 -*-
"""End-to-end TCaptcha click solver using ddddocr Flask API."""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from PIL import Image

from .client import TCaptchaClient, PrehandleResp, resolve_base_url, BASE_URL_GLOBAL
from .ocr_client import DdddOcrClient
from .pow import solve_pow
from .tdc_bridge import TdcBridge
from .trajectory import build_image_select_trajectory, build_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)

# tencentcloud.com 演示页内置 CaptchaAppId（来自 main.e7a75ff1.js）
TENCENTCLOUD_DEMO_APPIDS = {
    # 189910271 文字点选：prehandle 只有占位，需浏览器点复选框后再 show（协议未完全打通）
    "word_click": "189910271",
    "icon_click": "189918153",      # 实际返回滑块题
    "image_select": "189925386",    # Select in order —— 推荐先用这个测通流程
    "slider": "189916011",          # 语音验证码
}
DEFAULT_ENTRY_URL = "https://www.tencentcloud.com/zh/products/captcha"

# Known business CaptchaAppId presets (when caller only passes aid)
AID_PRESETS = {
    "2039519451": {
        "entry_url": "https://app.rainyun.com/account/reward/earn#",
        "js_path": "/tcaptcha-frame.91efdf16.js",
    },
}


def _apply_aid_preset(
    aid: str,
    entry_url: str,
    js_path: Optional[str],
) -> tuple[str, Optional[str]]:
    preset = AID_PRESETS.get(aid)
    if not preset:
        return entry_url, js_path
    if entry_url == DEFAULT_ENTRY_URL:
        entry_url = preset.get("entry_url", entry_url)
    if js_path is None:
        js_path = preset.get("js_path")
    return entry_url, js_path


@dataclass
class CaptchaResult:
    ok: bool
    ticket: str
    randstr: str
    challenge_type: str
    error_code: int = -1
    error_msg: str = ""


def _parse_target_chars(instruction: str) -> list[str]:
    after = instruction.split("：", 1)[1] if "：" in instruction else instruction
    return re.findall(r"[\u4e00-\u9fff]", after)


def _resolve_tdc_url(tdc_path: str, base_url: str = BASE_URL_GLOBAL) -> str:
    if not tdc_path:
        return ""
    if tdc_path.startswith("http"):
        return tdc_path
    return f"{base_url}{tdc_path}"


def _build_ans_json(coords: list[tuple[int, int]]) -> str:
    ans_list = [
        {"elem_id": i + 1, "type": "DynAnswerType_POS", "data": f"{x},{y}"}
        for i, (x, y) in enumerate(coords)
    ]
    return json.dumps(ans_list)


def _detect_challenge_type(pre: PrehandleResp) -> str:
    instr = (pre.instruction or "").lower()
    if pre.ins_elem_list or "select in order" in instr:
        return "image_select"
    if pre.fg_elem_list:
        return "icon_click"
    if "请依次点击" in (pre.instruction or "") or _parse_target_chars(pre.instruction or ""):
        return "word_click"
    if "slide" in (pre.show_type or "").lower():
        return "slider"
    return "word_click"


def _solve_coords(
    pre: PrehandleResp,
    bg_bytes: bytes,
    client_http: TCaptchaClient,
    ocr: DdddOcrClient,
) -> list[tuple[int, int]]:
    challenge = _detect_challenge_type(pre)

    if challenge == "image_select":
        if not pre.sprite_url:
            raise RuntimeError("image_select 缺少 sprite_url")
        if not pre.ins_elem_list:
            raise RuntimeError("image_select 缺少 ins_elem_cfg")
        sprite_bytes = client_http.get_image(pre.sprite_url)
        return ocr.match_select_in_order(bg_bytes, sprite_bytes, pre.ins_elem_list)

    if challenge == "icon_click":
        fg_url = client_http.get_fg_image_url(pre.img_url)
        fg_bytes = client_http.get_image(fg_url)
        fg_img = Image.open(io.BytesIO(fg_bytes))
        hints: list[bytes] = []
        for elem in pre.fg_elem_list:
            px, py = elem.sprite_pos
            pw, ph = elem.size_2d
            crop = fg_img.crop((px, py, px + pw, py + ph))
            buf = io.BytesIO()
            crop.save(buf, "PNG")
            hints.append(buf.getvalue())
        return ocr.match_icon_click(bg_bytes, hints)

    targets = _parse_target_chars(pre.instruction)
    if not targets:
        raise RuntimeError(f"无法从 instruction 解析目标文字: {pre.instruction!r}")
    return ocr.match_word_click(bg_bytes, targets)


def solve_once(
    *,
    appid: str,
    entry_url: str = DEFAULT_ENTRY_URL,
    ocr_base_url: str = "http://127.0.0.1:7777",
    user_agent: Optional[str] = None,
    js_path: Optional[str] = None,
    http: Optional[TCaptchaClient] = None,
    ocr: Optional[DdddOcrClient] = None,
    tdc: Optional[TdcBridge] = None,
) -> CaptchaResult:
    if http is None:
        http = TCaptchaClient(entry_url=entry_url, user_agent=user_agent, js_path=js_path) if user_agent else TCaptchaClient(
            entry_url=entry_url, js_path=js_path
        )
    if ocr is None:
        ocr = DdddOcrClient(base_url=ocr_base_url)
    if tdc is None:
        tdc = TdcBridge()

    pre = http.prehandle(appid)
    challenge = _detect_challenge_type(pre)
    bg_bytes = http.get_image(pre.img_url)
    coords = _solve_coords(pre, bg_bytes, http, ocr)

    traj_segments = []
    if challenge == "image_select":
        for cx, cy in coords:
            traj_segments.append(
                build_image_select_trajectory(cx, cy, canvas_w=pre.bg_width, canvas_h=pre.bg_height)
            )
    else:
        for cx, cy in coords:
            traj_segments.append(
                build_click_trajectory(cx, cy, canvas_w=pre.bg_width, canvas_h=pre.bg_height)
            )
    trajectory = merge_trajectories(traj_segments)

    pow_answer, pow_calc_time = solve_pow(pre.pow_prefix, pre.pow_md5)
    base = resolve_base_url(entry_url)
    collect, eks = tdc.collect(
        _resolve_tdc_url(pre.tdc_path, base),
        trajectory,
        http.user_agent,
        captcha_origin=base,
    )
    ans = _build_ans_json(coords)
    log.info("verify payload ans=%s coords=%s", ans, coords)

    verify = http.verify(
        pre.sess,
        ans=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=collect,
        eks=eks,
    )
    return CaptchaResult(
        ok=verify.ok,
        ticket=verify.ticket,
        randstr=verify.randstr,
        challenge_type=challenge,
        error_code=verify.error_code,
        error_msg=verify.error_msg,
    )


def solve_captcha(
    *,
    appid: Optional[str] = None,
    challenge_type: str = "image_select",
    entry_url: str = DEFAULT_ENTRY_URL,
    ocr_base_url: str = "http://127.0.0.1:7777",
    js_path: Optional[str] = None,
    max_retries: int = 4,
) -> CaptchaResult:
    aid = appid or TENCENTCLOUD_DEMO_APPIDS.get(challenge_type, TENCENTCLOUD_DEMO_APPIDS["word_click"])
    entry_url, js_path = _apply_aid_preset(aid, entry_url, js_path)
    http = TCaptchaClient(entry_url=entry_url, js_path=js_path)
    ocr = DdddOcrClient(base_url=ocr_base_url)
    tdc = TdcBridge()
    last: Optional[CaptchaResult] = None
    for attempt in range(1, max_retries + 1):
        log.info("solve attempt %d/%d appid=%s", attempt, max_retries, aid)
        try:
            result = solve_once(
                appid=aid,
                entry_url=entry_url,
                ocr_base_url=ocr_base_url,
                js_path=js_path,
                http=http,
                ocr=ocr,
                tdc=tdc,
            )
            last = result
            if result.ok:
                return result
            log.warning("verify failed: code=%s msg=%s", result.error_code, result.error_msg)
        except Exception as exc:
            log.exception("attempt %d failed: %s", attempt, exc)
            last = CaptchaResult(ok=False, ticket="", randstr="", challenge_type=challenge_type, error_msg=str(exc))
    return last or CaptchaResult(ok=False, ticket="", randstr="", challenge_type=challenge_type, error_msg="unknown")
