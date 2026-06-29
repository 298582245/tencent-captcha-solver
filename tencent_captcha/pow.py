# -*- coding: utf-8 -*-
"""TCaptcha PoW (proof-of-work) solver."""

from __future__ import annotations

import hashlib
import random
import time

_MAX_NONCE = 1_000_000


def solve_pow(prefix: str, target_md5: str, *, min_ms: int = 300, max_ms: int = 500) -> tuple[str, int]:
    t0 = time.perf_counter()
    for nonce in range(_MAX_NONCE):
        candidate = prefix + str(nonce)
        if hashlib.md5(candidate.encode()).hexdigest() == target_md5:
            calc_ms = int((time.perf_counter() - t0) * 1000)
            if min_ms > 0:
                target = random.randint(min_ms, max_ms) if max_ms > min_ms else min_ms
                if calc_ms < target:
                    time.sleep((target - calc_ms) / 1000.0)
                    calc_ms = target
            return candidate, calc_ms
    raise RuntimeError(f"PoW not solved within {_MAX_NONCE} iterations")
