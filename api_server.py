# -*- coding: utf-8 -*-
# HTTP API for Tencent TCaptcha (Python 3.7+)

import argparse
import hashlib
import hmac
import logging
import os
import sys
import threading
import time
from datetime import timedelta
from functools import wraps

try:
    from flask import Flask, jsonify, redirect, render_template, request, session, url_for
except ImportError:
    sys.stderr.write("Missing flask. Run: pip install 'flask>=2.0,<3.0'\n")
    sys.exit(1)

try:
    from captcha_api.request_log import fetch_logs, fetch_stats, init_db, insert_log
    from tencent_captcha.solver import DEFAULT_ENTRY_URL, solve_captcha
except ImportError as e:
    sys.stderr.write("Import failed: %s\n" % e)
    sys.stderr.write("Run from project root and: pip install -r requirements.txt\n")
    sys.exit(1)

TYPE_MAP = {
    1: "image_select",
    2: "word_click",
    3: "icon_click",
    4: "slider",
}

app = Flask(__name__, template_folder="templates")
log = logging.getLogger("captcha_api")
_solve_semaphore = None  # type: Optional[threading.Semaphore]


def _get_solve_semaphore() -> threading.Semaphore:
    global _solve_semaphore
    if _solve_semaphore is None:
        try:
            n = int(_cfg("CAPTCHA_MAX_CONCURRENT", "1"))
        except (TypeError, ValueError):
            n = 1
        _solve_semaphore = threading.Semaphore(max(1, n))
    return _solve_semaphore


def _configure_app():
    secret = _cfg("WEBUI_SECRET", "")
    if not secret:
        pwd = _webui_password()
        secret = hashlib.sha256(("captcha-webui:" + pwd).encode("utf-8")).hexdigest() if pwd else os.urandom(24).hex()
    app.config["SECRET_KEY"] = secret
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _webui_password():
    return _cfg("WEBUI_PASSWORD", "")


def _webui_username():
    name = _cfg("WEBUI_USERNAME", "admin")
    return name or "admin"


def _webui_enabled():
    return bool(_webui_password())


def _check_webui_login(username, password):
    return hmac.compare_digest(username or "", _webui_username()) and hmac.compare_digest(
        password or "", _webui_password()
    )


def webui_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _webui_enabled():
            if request.path.startswith("/api/"):
                return jsonify({"code": 503, "msg": "WEBUI_PASSWORD not configured", "data": None}), 503
            return render_template("login_disabled.html"), 503
        if session.get("webui_authed"):
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"code": 401, "msg": "login required", "data": None}), 401
        return redirect(url_for("webui_login", next=request.path))

    return wrapped


def _cfg(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip()


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("webui")


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

    userid = str(
        body.get("userid") or body.get("user_id") or request.args.get("userid") or request.args.get("user_id") or ""
    ).strip()

    entry_url = str(
        body.get("entry_url") or request.args.get("entry_url") or _cfg("CAPTCHA_ENTRY_URL", DEFAULT_ENTRY_URL)
    ).strip()
    js_path = str(
        body.get("js") or body.get("js_path") or request.args.get("js") or request.args.get("js_path") or ""
    ).strip()
    ocr_url = str(
        body.get("ocr_url") or request.args.get("ocr_url") or _cfg("OCR_BASE_URL", "http://127.0.0.1:7777")
    ).strip()

    retries_raw = body.get("retries", request.args.get("retries", _cfg("CAPTCHA_MAX_RETRIES", "6")))
    try:
        retries = int(retries_raw)
    except (TypeError, ValueError):
        retries = 6

    js_val = js_path if js_path else None

    return (
        {
            "aid": aid,
            "type_id": type_id,
            "challenge_type": TYPE_MAP.get(type_id, "image_select"),
            "userid": userid,
            "entry_url": entry_url,
            "js_path": js_val,
            "ocr_url": ocr_url,
            "retries": retries,
        },
        None,
    )


def _run_solve(params):
    log.info(
        "api solve userid=%s aid=%s type=%s entry=%s",
        params.get("userid") or "-",
        params["aid"],
        params["type_id"],
        params["entry_url"],
    )
    started = time.time()
    response_body = {}
    http_status = 500
    with _get_solve_semaphore():
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
            response_body = {"code": 500, "msg": str(exc), "data": None}
            http_status = 500
            duration_ms = int((time.time() - started) * 1000)
            insert_log(
                userid=params.get("userid") or "",
                client_ip=_client_ip(),
                method=request.method,
                params=params,
                http_status=http_status,
                response_body=response_body,
                duration_ms=duration_ms,
            )
            return jsonify(response_body), http_status

        if result.ok and result.ticket and result.randstr:
            response_body = {
                "code": 200,
                "msg": "ok",
                "data": {
                    "ticket": result.ticket,
                    "randstr": result.randstr,
                    "challenge_type": result.challenge_type,
                },
            }
            http_status = 200
        else:
            err = result.error_msg or ("verify failed code=%s" % result.error_code)
            response_body = {"code": 500, "msg": err, "data": None}
            http_status = 500

    duration_ms = int((time.time() - started) * 1000)
    insert_log(
        userid=params.get("userid", ""),
        client_ip=_client_ip(),
        method=request.method,
        params=params,
        http_status=http_status,
        response_body=response_body,
        duration_ms=duration_ms,
    )
    return jsonify(response_body), http_status


@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "code": 200,
            "msg": "Tencent TCaptcha API",
            "data": {
                "solve": "/solve_captcha?aid=APPID&type=1&userid=USER",
                "health": "/health",
                "webui": "/ui (login required)",
                "logs_api": "/api/logs (login required)",
                "type": {"1": "image_select", "2": "word_click", "3": "icon_click", "4": "slider"},
            },
        }
    )


@app.route("/ui/login", methods=["GET", "POST"])
def webui_login():
    if not _webui_enabled():
        return render_template("login_disabled.html"), 503
    if session.get("webui_authed"):
        return redirect(_safe_next(request.args.get("next")))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if _check_webui_login(username, password):
            session.permanent = True
            session["webui_authed"] = True
            return redirect(_safe_next(request.form.get("next") or request.args.get("next")))
        return render_template(
            "login.html",
            error="用户名或密码错误",
            next=request.form.get("next") or request.args.get("next") or "",
        ), 401
    return render_template("login.html", error="", next=request.args.get("next") or "")


@app.route("/ui/logout", methods=["POST"])
def webui_logout():
    session.pop("webui_authed", None)
    return redirect(url_for("webui_login"))


@app.route("/ui", methods=["GET"])
@webui_login_required
def webui():
    return render_template("logs.html")


@app.route("/api/logs", methods=["GET"])
@webui_login_required
def api_logs():
    userid = request.args.get("userid", "").strip() or None
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    data = fetch_logs(userid=userid, limit=limit, offset=offset)
    return jsonify({"code": 200, "msg": "ok", "data": data})


@app.route("/api/logs/stats", methods=["GET"])
@webui_login_required
def api_logs_stats():
    userid = request.args.get("userid", "").strip() or None
    data = fetch_stats(userid=userid)
    return jsonify({"code": 200, "msg": "ok", "data": data})


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

    init_db()
    _configure_app()
    try:
        from tencent_captcha.tdc_bridge import TdcBridge

        TdcBridge().warm_up()
        log.info("TDC worker ready")
    except Exception as exc:
        log.warning("TDC warm-up skipped: %s", exc)
    log.info("request log db ready")
    log.info(
        "solve concurrency=%s retries=%s",
        _cfg("CAPTCHA_MAX_CONCURRENT", "1"),
        _cfg("CAPTCHA_MAX_RETRIES", "6"),
    )
    log.info("API http://%s:%s/solve_captcha?aid=APPID&type=1&userid=USER", args.host, args.port)
    if _webui_enabled():
        log.info("WebUI http://%s:%s/ui (user=%s)", args.host, args.port, _webui_username())
    else:
        log.warning("WebUI disabled: set WEBUI_PASSWORD to enable /ui")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
