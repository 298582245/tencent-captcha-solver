# -*- coding: utf-8 -*-
# HTTP API for Tencent TCaptcha (Python 3.7+)

import argparse
import logging
import os
import sys

try:
    from flask import Flask, jsonify, request
except ImportError:
    sys.stderr.write("Missing flask. Run: pip install 'flask>=2.0,<3.0'\n")
    sys.exit(1)

try:
    from tencent_captcha.solver import DEFAULT_ENTRY_URL, solve_captcha
except ImportError as e:
    sys.stderr.write("Import failed: %s\n" % e)
    sys.stderr.write("Run from project root and: pip install -r requirements-py37.txt\n")
    sys.exit(1)

TYPE_MAP = {
    1: "image_select",
    2: "word_click",
    3: "icon_click",
    4: "slider",
}

app = Flask(__name__)
log = logging.getLogger("captcha_api")


def _cfg(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip()


def _parse_params():
    body = {}
    if request.method == "POST" and request.is_json:
        raw = request.get_json(silent=True)
        if isinstance(raw, dict):
            body = raw

    aid = str(
        body.get("aid") or body.get("appid") or request.args.get("aid") or request.args.get("appid") or ""
    ).strip()
    if not aid:
        return None, (jsonify({"code": 400, "msg": "missing aid", "data": None}), 400)

    type_raw = body.get("type", request.args.get("type", 1))
    try:
        type_id = int(type_raw)
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "invalid type", "data": None}), 400)

    entry_url = str(
        body.get("entry_url") or request.args.get("entry_url") or _cfg("CAPTCHA_ENTRY_URL", DEFAULT_ENTRY_URL)
    ).strip()
    js_path = str(
        body.get("js") or body.get("js_path") or request.args.get("js") or request.args.get("js_path") or ""
    ).strip()
    ocr_url = str(
        body.get("ocr_url") or request.args.get("ocr_url") or _cfg("OCR_BASE_URL", "http://124.222.179.175:7777")
    ).strip()

    retries_raw = body.get("retries", request.args.get("retries", _cfg("CAPTCHA_MAX_RETRIES", "8")))
    try:
        retries = int(retries_raw)
    except (TypeError, ValueError):
        retries = 3

    js_val = js_path if js_path else None

    return (
        {
            "aid": aid,
            "type_id": type_id,
            "challenge_type": TYPE_MAP.get(type_id, "image_select"),
            "entry_url": entry_url,
            "js_path": js_val,
            "ocr_url": ocr_url,
            "retries": retries,
        },
        None,
    )


def _run_solve(params):
    log.info("api solve aid=%s type=%s entry=%s", params["aid"], params["type_id"], params["entry_url"])
    try:
        result = solve_captcha(
            appid=params["aid"],
            challenge_type=params["challenge_type"],
            entry_url=params["entry_url"],
            ocr_base_url=params["ocr_url"],
            js_path=params["js_path"],
            max_retries=params["retries"],
        )
    except Exception as exc:
        log.exception("api solve error aid=%s", params["aid"])
        return jsonify({"code": 500, "msg": str(exc), "data": None}), 500

    if result.ok and result.ticket and result.randstr:
        return jsonify(
            {
                "code": 200,
                "msg": "ok",
                "data": {
                    "ticket": result.ticket,
                    "randstr": result.randstr,
                    "challenge_type": result.challenge_type,
                },
            }
        )

    err = result.error_msg or ("verify failed code=%s" % result.error_code)
    return jsonify({"code": 500, "msg": err, "data": None}), 500


@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "code": 200,
            "msg": "Tencent TCaptcha API",
            "data": {
                "solve": "/solve_captcha?aid=APPID&type=1",
                "health": "/health",
                "type": {"1": "image_select", "2": "word_click", "3": "icon_click", "4": "slider"},
            },
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"code": 200, "msg": "ok", "data": {"status": "up"}})


@app.route("/solve_captcha", methods=["GET", "POST"])
def solve_captcha_api():
    params, err = _parse_params()
    if err:
        return err
    if params is None:
        return jsonify({"code": 400, "msg": "missing aid", "data": None}), 400
    return _run_solve(params)


def main():
    parser = argparse.ArgumentParser(description="Tencent TCaptcha HTTP API")
    parser.add_argument("--host", default=_cfg("CAPTCHA_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_cfg("CAPTCHA_API_PORT", "8080")))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    log.info("API http://%s:%s/solve_captcha?aid=APPID&type=1", args.host, args.port)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
