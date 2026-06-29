# -*- coding: utf-8 -*-
"""Check Python version and required packages before starting API."""

from __future__ import print_function

import sys


def main():
    print("python:", sys.version)
    if sys.version_info[0] < 3 or (sys.version_info[0] == 3 and sys.version_info[1] < 7):
        print("ERROR: need Python 3.7+")
        return 1

    missing = []
    for name in ("flask", "requests", "numpy", "PIL", "cv2"):
        try:
            __import__(name)
            print("ok:", name)
        except ImportError:
            missing.append(name)
            print("MISSING:", name)

    try:
        import tencent_captcha  # noqa: F401
        print("ok: tencent_captcha")
    except ImportError as e:
        print("MISSING: tencent_captcha (%s)" % e)
        missing.append("tencent_captcha")

    if missing:
        print("")
        print("Fix: pip install -r requirements-py37.txt")
        return 1

    print("")
    print("All OK. Start: python api_server.py --host 0.0.0.0 --port 8080")
    return 0


if __name__ == "__main__":
    sys.exit(main())
