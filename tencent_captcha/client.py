# -*- coding: utf-8 -*-
"""HTTP client for TCaptcha 2.0 three-phase protocol."""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as _http

    _USE_CURL_CFFI = True
except ImportError:
    import requests as _http

    _USE_CURL_CFFI = False

log = logging.getLogger(__name__)

_JSONP_RE = re.compile(r"^\s*\w+\s*\(\s*(.*)\s*\)\s*;?\s*$", re.DOTALL)

# 国内站 cloud.tencent.com → turing.captcha.qcloud.com
# 国际站 tencentcloud.com → ca.turing.captcha.qcloud.com（页面加载 TJNCaptcha-global.js）
BASE_URL_CN = "https://turing.captcha.qcloud.com"
BASE_URL_GLOBAL = "https://ca.turing.captcha.qcloud.com"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
IMPERSONATE_CANDIDATES = ("chrome131", "chrome124", "chrome120", "edge101", "chrome110")


def resolve_base_url(entry_url: str, base_url: Optional[str] = None) -> str:
    if base_url:
        return base_url.rstrip("/")
    if "tencentcloud.com" in entry_url or "intl" in entry_url:
        return BASE_URL_GLOBAL
    return BASE_URL_CN


def resolve_js_path(base_url: str) -> str:
    if "ca.turing" in base_url:
        return "/tgJNCap-global.203d0ca0.js"
    return "/tgJCap.627c7f42.js"


def resolve_image_referer(base_url: str) -> str:
    if "ca.turing" in base_url:
        return "https://global.turing.captcha.gtimg.com/"
    return "https://turing.captcha.gtimg.com/"


@dataclass
class SpriteElem:
    elem_id: str
    sprite_pos: tuple[int, int]
    size_2d: tuple[int, int]


@dataclass
class FgElem(SpriteElem):
    init_pos: tuple[int, int] = (0, 0)

@dataclass
class PrehandleResp:
    sess: str
    img_url: str
    sprite_url: str
    bg_width: int
    bg_height: int
    instruction: str
    show_type: str
    data_type: list[str]
    fg_elem_list: list[FgElem]
    ins_elem_list: list[SpriteElem]
    tdc_path: str
    pow_prefix: str
    pow_md5: str
    raw: dict[str, Any] = field(repr=False)


@dataclass
class VerifyResp:
    ok: bool
    ticket: str
    randstr: str
    error_code: int
    error_msg: str


def parse_jsonp(raw: str) -> dict[str, Any]:
    m = _JSONP_RE.match(raw)
    body = m.group(1) if m else raw
    return json.loads(body)


def _browser_headers(*, referer: str, fetch_site: str = "same-site") -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
        "Sec-Fetch-Dest": "script",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": fetch_site,
    }


class TCaptchaClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_UA,
        entry_url: str = "",
        base_url: Optional[str] = None,
        js_path: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        impersonate: Optional[str] = None,
    ) -> None:
        self.user_agent = user_agent
        self.ua_b64 = base64.b64encode(user_agent.encode()).decode()
        self.entry_url = entry_url
        self.base_url = resolve_base_url(entry_url, base_url)
        self.js_path = js_path or resolve_js_path(self.base_url)
        self.image_referer = resolve_image_referer(self.base_url)
        self.proxy = proxy
        self.timeout = timeout
        self.impersonate = impersonate or IMPERSONATE_CANDIDATES[0]
        self._session = self._create_session(self.impersonate)
        log.info("TCaptcha client base_url=%s js=%s impersonate=%s", self.base_url, self.js_path, self.impersonate)

    def _create_session(self, impersonate: str):
        if _USE_CURL_CFFI:
            return _http.Session(impersonate=impersonate)
        log.warning("curl_cffi not installed; using requests without TLS impersonate")
        return _http.Session()

    def _network_kwargs(self) -> Dict[str, Any]:
        if not self.proxy:
            return {}
        if _USE_CURL_CFFI:
            return {"proxy": self.proxy}
        return {"proxies": {"http": self.proxy, "https": self.proxy}}

    def warmup(self) -> None:
        """先访问业务页，拿 cookie / 降低风控拦截概率。"""
        if not self.entry_url:
            return
        try:
            self._session.get(
                self.entry_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                },
                timeout=self.timeout,
                **self._network_kwargs(),
            )
        except Exception as exc:
            log.warning("warmup skipped: %s", exc)

    def prehandle(self, aid: str) -> PrehandleResp:
        last_error: Optional[Exception] = None
        for impersonate in ([self.impersonate] + [x for x in IMPERSONATE_CANDIDATES if x != self.impersonate]):
            try:
                if impersonate != self.impersonate:
                    log.info("retry prehandle with impersonate=%s", impersonate)
                    self._session = self._create_session(impersonate)
                    self.impersonate = impersonate
                self.warmup()
                return self._prehandle_once(aid)
            except RuntimeError as exc:
                last_error = exc
                if "403" not in str(exc):
                    raise
        raise last_error or RuntimeError("prehandle failed")

    def _prehandle_once(self, aid: str) -> PrehandleResp:
        callback = f"_aq_{random.randint(100000, 999999)}"
        params = {
            "aid": aid,
            "protocol": "https",
            "accver": "1",
            "showtype": "popup",
            "ua": self.ua_b64,
            "noheader": "1",
            "fb": "1",
            "aged": "0",
            "enableAged": "0",
            "enableDarkMode": "0",
            "grayscale": "1",
            "clientype": "2",
            "cap_cd": "",
            "uid": "",
            "lang": "zh-cn",
            "entry_url": self.entry_url,
            "elder_captcha": "0",
            "js": self.js_path,
            "login_appid": "",
            "wb": "1",
            "subsid": "1",
            "callback": callback,
            "sess": "",
        }
        referer = self.entry_url or self.base_url
        headers = _browser_headers(referer=referer, fetch_site="cross-site" if self.entry_url else "same-site")
        url = f"{self.base_url}/cap_union_prehandle"
        r = self._session.get(
            url, params=params, headers=headers, timeout=self.timeout, **self._network_kwargs()
        )
        if r.status_code != 200:
            raise RuntimeError(f"prehandle HTTP {r.status_code}: {r.text[:300]}")
        data = parse_jsonp(r.text)
        dyn = data["data"]["dyn_show_info"]
        comm = data["data"]["comm_captcha_cfg"]

        if "bg_elem_cfg" not in dyn:
            raise RuntimeError(
                "prehandle 仅返回占位配置（无验证码图片）。"
                f" appid={aid} 在国际站 TJCaptcha 2.0 下需先触发「I am human」复选框，"
                " 再请求 cap_union_new_show 才能拿到题目。"
                " 测试建议：先用 --appid 189925386（Select in order 点选，一次 prehandle 即有图）"
                " 或 --appid 189918153（滑块）。"
                f" 当前 dyn_show_info keys={list(dyn.keys())}"
            )

        bg_cfg = dyn["bg_elem_cfg"]
        bg_size = bg_cfg.get("size_2d")
        if isinstance(bg_size, list):
            bg_w, bg_h = bg_size[0], bg_size[1]
        else:
            bg_w = bg_cfg.get("width", 672)
            bg_h = bg_cfg.get("height", 480)

        fg_list: list[FgElem] = []
        for elem in dyn.get("fg_elem_list", []):
            init = elem.get("init_pos", [0, 0])
            fg_list.append(
                FgElem(
                    elem_id=str(elem["id"]),
                    sprite_pos=(elem["sprite_pos"][0], elem["sprite_pos"][1]),
                    size_2d=(elem["size_2d"][0], elem["size_2d"][1]),
                    init_pos=(init[0], init[1]),
                )
            )

        ins_list: list[SpriteElem] = []
        for elem in dyn.get("ins_elem_cfg", []) or []:
            ins_list.append(
                SpriteElem(
                    elem_id=str(elem["id"]),
                    sprite_pos=(elem["sprite_pos"][0], elem["sprite_pos"][1]),
                    size_2d=(elem["size_2d"][0], elem["size_2d"][1]),
                )
            )

        pow_cfg = comm.get("pow_cfg", {})
        click_cfg = bg_cfg.get("click_cfg", {})
        log.info(
            "prehandle ok show_type=%s instruction=%r fg_elems=%d ins_elems=%d",
            dyn.get("show_type", ""),
            dyn.get("instruction", ""),
            len(fg_list),
            len(ins_list),
        )
        return PrehandleResp(
            sess=data.get("sess", ""),
            img_url=bg_cfg["img_url"],
            sprite_url=dyn.get("sprite_url", ""),
            bg_width=bg_w,
            bg_height=bg_h,
            instruction=dyn.get("instruction", ""),
            show_type=dyn.get("show_type", ""),
            data_type=click_cfg.get("data_type", []),
            fg_elem_list=fg_list,
            ins_elem_list=ins_list,
            tdc_path=comm.get("tdc_path", ""),
            pow_prefix=pow_cfg.get("prefix", ""),
            pow_md5=pow_cfg.get("md5", ""),
            raw=data,
        )

    def _abs_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    def get_image(self, img_url: str) -> bytes:
        full = self._abs_url(img_url)
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": self.image_referer,
        }
        r = self._session.get(full, headers=headers, timeout=self.timeout, **self._network_kwargs())
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"image download failed HTTP {r.status_code}")
        return r.content

    def get_fg_image_url(self, bg_img_url: str) -> str:
        full = self._abs_url(bg_img_url)
        parsed = urllib.parse.urlparse(full)
        qs = urllib.parse.parse_qs(parsed.query)
        qs["img_index"] = ["0"]
        new_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    def verify(
        self,
        sess: str,
        *,
        ans: str,
        pow_answer: str,
        pow_calc_time: int,
        collect: str,
        eks: str,
    ) -> VerifyResp:
        body = {
            "ans": ans,
            "sess": sess,
            "pow_answer": pow_answer,
            "pow_calc_time": str(pow_calc_time),
            "collect": collect,
            "tlg": str(len(urllib.parse.unquote(collect))),
            "eks": eks,
        }
        origin = (
            urllib.parse.urlsplit(self.entry_url)._replace(path="", query="", fragment="").geturl()
            if self.entry_url
            else self.base_url
        )
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": self.entry_url or self.base_url,
            "Origin": origin,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }
        url = f"{self.base_url}/cap_union_new_verify"
        r = self._session.post(
            url,
            data=urllib.parse.urlencode(body),
            headers=headers,
            timeout=self.timeout,
            **self._network_kwargs(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"verify HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        err = d.get("errorCode", -1)
        return VerifyResp(
            ok=str(err) == "0",
            ticket=d.get("ticket", ""),
            randstr=d.get("randstr", ""),
            error_code=int(err) if str(err).lstrip("-").isdigit() else -1,
            error_msg=d.get("errMessage", d.get("errMsg", "")),
        )
