# -*- coding: utf-8 -*-
"""Call local ddddocr Flask API for click-coordinate recognition."""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np
import requests
from PIL import Image

log = logging.getLogger(__name__)

# Minimum per-hint score to submit verify; below this we retry without verify.
SUBMIT_MIN_SCORE = 0.43
SUBMIT_EACH_MIN = 0.40
BBOX_MIN_SCORE = 0.43
EDGE_MARGIN = 15
EDGE_MIN_SCORE = 0.65


class DdddOcrClient:
    def __init__(self, base_url: str = "http://127.0.0.1:7777", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        r = self._session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _to_b64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode()

    def select(self, image_bytes: bytes) -> list[dict[str, list[int]]]:
        return self._post("/select", {"image": self._to_b64(image_bytes)})

    def detection(self, image_bytes: bytes) -> list[list[int]]:
        return self._post("/detection", {"image": self._to_b64(image_bytes)})["result"]

    def classification(self, image_bytes: bytes) -> str:
        return self._post("/classification", {"image": self._to_b64(image_bytes)})["result"]

    @staticmethod
    def _crop_png(img: Image.Image, box: tuple[int, int, int, int]) -> bytes:
        buf = io.BytesIO()
        img.crop(box).save(buf, "PNG")
        return buf.getvalue()

    @staticmethod
    def _decode_bgr(image_bytes: bytes) -> np.ndarray:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("无法解码图片")
        return img

    @staticmethod
    def _filter_icon_bboxes(bboxes: list[list[int]], *, min_side: int = 20) -> list[tuple[int, int, int, int]]:
        icons: list[tuple[int, int, int, int]] = []
        for raw in bboxes:
            x1, y1, x2, y2 = map(int, raw)
            w, h = x2 - x1, y2 - y1
            if w < min_side or h < min_side:
                continue
            if w > h * 3 or h > w * 3:
                continue
            icons.append((x1, y1, x2, y2))
        icons.sort(key=lambda b: b[0])
        return icons

    def _split_icon_bboxes_on_strip(
        self,
        strip: Image.Image,
        icon_start: int = 0,
    ) -> list[tuple[int, int, int, int]]:
        w, h = strip.size
        full_bytes = self._crop_png(strip, (0, 0, w, h))
        raw = self.detection(full_bytes)
        icons: list[tuple[int, int, int, int]] = []
        for x1, y1, x2, y2 in raw:
            cx = (x1 + x2) / 2
            bw, bh = x2 - x1, y2 - y1
            if cx < icon_start - 5:
                continue
            if bw < 8 or bh < 8:
                continue
            if bw > bh * 2.5:
                continue
            icons.append((int(x1), int(y1), int(x2), int(y2)))
        icons.sort(key=lambda b: b[0])
        return icons

    def _strip_boxes_by_components(self, strip: Image.Image) -> list[tuple[int, int, int, int]]:
        """Fallback: split strip icons via connected components on line art."""
        gray = np.array(strip.convert("L"))
        art = self._extract_line_art(gray)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        art = cv2.morphologyEx(art, cv2.MORPH_CLOSE, kernel, iterations=1)

        n_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(art, connectivity=8)
        boxes: list[tuple[int, int, int, int]] = []
        for i in range(1, n_labels):
            x, y, w, h, area = stats[i]
            if w < 10 or h < 10 or area < 50:
                continue
            if w > h * 3:
                continue
            boxes.append((int(x), int(y), int(x + w), int(y + h)))
        boxes.sort(key=lambda b: b[0])
        return boxes

    def _resolve_strip_icon_count(
        self,
        strip: Image.Image,
        detect_boxes: list[tuple[int, int, int, int]],
        component_boxes: list[tuple[int, int, int, int]],
    ) -> int:
        w, h = strip.size
        # Intl demo strip is fixed layout: 3 ordered icons in 170x50 sprite.
        if 160 <= w <= 180 and 45 <= h <= 55:
            return 3
        if len(detect_boxes) == 3:
            return 3
        if len(detect_boxes) == 4 and w > 180:
            return 4
        if len(component_boxes) == 3:
            return 3
        return 3

    def split_hint_strip(self, strip_bytes: bytes, count: Optional[int] = None) -> list:
        """Extract ordered hint icons from ins_elem_cfg sprite strip."""
        img = Image.open(io.BytesIO(strip_bytes)).convert("RGB")
        w, h = img.size

        detect_boxes = self._split_icon_bboxes_on_strip(img, icon_start=0)
        component_boxes = self._strip_boxes_by_components(img)
        n = count or self._resolve_strip_icon_count(img, detect_boxes, component_boxes)

        parts: list[bytes] = []
        min_icon_w = max(20, int(w / n / 2.0))
        detect_ok = len(detect_boxes) == n and all((b[2] - b[0]) >= min_icon_w for b in detect_boxes)
        if detect_ok:
            for x1, y1, x2, y2 in detect_boxes:
                pad = 2
                parts.append(
                    self._crop_png(
                        img,
                        (max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad)),
                    )
                )
        else:
            step = w / n
            for i in range(n):
                x1 = int(i * step)
                x2 = int((i + 1) * step) if i < n - 1 else w
                if x2 - x1 > 6:
                    parts.append(self._crop_png(img, (x1, 0, x2, h)))

        if not parts:
            raise RuntimeError("hint strip 拆分失败")
        sizes = [Image.open(io.BytesIO(p)).size for p in parts]
        log.info(
            "hint strip split: w=%d parts=%d sizes=%s detect=%d components=%d",
            w,
            len(parts),
            sizes,
            len(detect_boxes),
            len(component_boxes),
        )
        return parts

    @staticmethod
    def _extract_line_art(gray: np.ndarray) -> np.ndarray:
        """Keep dark stroke pixels; ignore magenta / photo background."""
        return np.where(gray < 115, 255, 0).astype(np.uint8)

    def _match_template_on_art(
        self,
        bg_art: np.ndarray,
        hint_art: np.ndarray,
        *,
        target_size: Optional[Tuple[int, int]] = None,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[Tuple[int, int], float, Tuple[int, int, int, int]]:
        """Match hint line-art on background line-art (already binarized)."""
        th0, tw0 = hint_art.shape[:2]
        if th0 < 2 or tw0 < 2:
            return (0, 0), -1.0, (0, 0, 0, 0)

        x_off, y_off = 0, 0
        search = bg_art
        if roi:
            x_off, y_off, x2, y2 = roi
            search = bg_art[y_off:y2, x_off:x2]

        best_score = -1.0
        best_center = (0, 0)
        best_box = (0, 0, 0, 0)

        scales: list[float] = []
        if target_size:
            tw_t, th_t = target_size
            if tw0 > 0 and th0 > 0:
                scales.extend([tw_t / tw0, th_t / th0, ((tw_t / tw0) + (th_t / th0)) / 2])
        scales.extend([3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 2.5, 2.0, 1.5, 1.0])
        seen: set[float] = set()

        for scale in scales:
            scale = round(scale, 2)
            if scale in seen or scale <= 0:
                continue
            seen.add(scale)
            tw = max(8, int(tw0 * scale))
            th = max(8, int(th0 * scale))
            if tw >= search.shape[1] - 2 or th >= search.shape[0] - 2:
                continue
            tpl = cv2.resize(hint_art, (tw, th), interpolation=cv2.INTER_NEAREST)
            res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                x1, y1 = max_loc
                x1 += x_off
                y1 += y_off
                best_score = float(max_val)
                best_center = (x1 + tw // 2, y1 + th // 2)
                best_box = (x1, y1, x1 + tw, y1 + th)

        return best_center, best_score, best_box

    def _match_gray_template(
        self,
        bg_gray: np.ndarray,
        hint_gray: np.ndarray,
        *,
        target_size: Optional[Tuple[int, int]] = None,
        roi: Optional[Tuple[int, int, int, int]] = None,
        search_mask: Optional[np.ndarray] = None,
    ) -> Tuple[Tuple[int, int], float, Tuple[int, int, int, int]]:
        x_off, y_off = 0, 0
        search = bg_gray
        if roi:
            x_off, y_off, x2, y2 = roi
            search = bg_gray[y_off:y2, x_off:x2]
        if search_mask is not None and search_mask.shape == search.shape:
            search = search.copy()
            search[search_mask == 0] = 255

        hh, hw = hint_gray.shape[:2]
        best_score = -1.0
        best_center = (0, 0)
        best_box = (0, 0, 0, 0)
        scales: list[float] = []
        if target_size:
            tw_t, th_t = target_size
            scales.extend([tw_t / hw, th_t / hh, ((tw_t / hw) + (th_t / hh)) / 2])
        scales.extend([4.0, 4.5, 5.0, 5.5, 6.0, 3.5, 3.0, 2.5])
        seen: set[float] = set()
        for scale in scales:
            scale = round(scale, 2)
            if scale in seen or scale <= 0:
                continue
            seen.add(scale)
            tw = max(8, int(hw * scale))
            th = max(8, int(hh * scale))
            if tw >= search.shape[1] - 2 or th >= search.shape[0] - 2:
                continue
            tpl = cv2.resize(hint_gray, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                x1, y1 = max_loc
                x1 += x_off
                y1 += y_off
                best_score = float(max_val)
                best_center = (x1 + tw // 2, y1 + th // 2)
                best_box = (x1, y1, x1 + tw, y1 + th)
        return best_center, best_score, best_box

    def _match_hint_on_canvas(
        self,
        hint_bytes: bytes,
        bg_bgr: np.ndarray,
        bg_gray: np.ndarray,
        bg_art: np.ndarray,
        *,
        target_size: Optional[Tuple[int, int]] = None,
        search_art: Optional[np.ndarray] = None,
    ) -> Tuple[Tuple[int, int], float, Tuple[int, int, int, int]]:
        hint_bgr = self._decode_bgr(hint_bytes)
        hint_gray = cv2.cvtColor(hint_bgr, cv2.COLOR_BGR2GRAY)
        hint_art = self._extract_line_art(hint_gray)
        art = search_art if search_art is not None else bg_art
        gray_search = bg_gray
        if search_art is not None:
            gray_search = bg_gray.copy()
            gray_search[search_art == 0] = 255

        c1, s1, b1 = self._match_template_on_art(art, hint_art, target_size=target_size)
        c2, s2, b2 = self._match_gray_template(gray_search, hint_gray, target_size=target_size)
        if s2 > s1:
            return c2, s2, b2
        return c1, s1, b1

    @staticmethod
    def _is_edge_suspicious(
        center: Tuple[int, int],
        score: float,
        canvas_w: int,
        canvas_h: int,
    ) -> bool:
        x, y = center
        near_edge = x < EDGE_MARGIN or y < EDGE_MARGIN
        near_far = (canvas_w and x > canvas_w - EDGE_MARGIN) or (canvas_h and y > canvas_h - EDGE_MARGIN)
        if (near_edge or near_far) and score < EDGE_MIN_SCORE:
            return True
        return False

    @staticmethod
    def _candidate_rank(scores: list[float]) -> tuple[float, float]:
        return (min(scores), sum(scores) / len(scores))

    @staticmethod
    def _scores_acceptable(scores: list[float]) -> bool:
        if not scores:
            return False
        if min(scores) < SUBMIT_EACH_MIN:
            return False
        return min(scores) >= SUBMIT_MIN_SCORE

    def _run_sequential_match(
        self,
        hint_images: list[bytes],
        bg: np.ndarray,
        bg_gray: np.ndarray,
        bg_art: np.ndarray,
        target_size: Optional[Tuple[int, int]],
    ) -> tuple[list[tuple[int, int]], list[float]]:
        h, w = bg_gray.shape[:2]
        search_art = bg_art.copy()
        seq_coords: list[tuple[int, int]] = []
        seq_scores: list[float] = []
        for idx, hint_bytes in enumerate(hint_images):
            center, score, box = self._match_hint_on_canvas(
                hint_bytes,
                bg,
                bg_gray,
                bg_art,
                target_size=target_size,
                search_art=search_art,
            )
            if self._is_edge_suspicious(center, score, w, h):
                self._mask_used_region(search_art, box)
                center2, score2, box2 = self._match_hint_on_canvas(
                    hint_bytes,
                    bg,
                    bg_gray,
                    bg_art,
                    target_size=target_size,
                    search_art=search_art,
                )
                if score2 >= score:
                    center, score, box = center2, score2, box2
                log.warning(
                    "hint#%d edge match adjusted score=%.3f->%.3f center=%s",
                    idx + 1,
                    score if score2 < score else score2,
                    score,
                    center,
                )
            if score < SUBMIT_EACH_MIN:
                log.warning("hint#%d low confidence score=%.3f center=%s", idx + 1, score, center)
            log.info(
                "hint#%d -> center=%s score=%.3f via=sequential box=%s",
                idx + 1,
                center,
                score,
                box,
            )
            self._mask_used_region(search_art, box)
            seq_coords.append(center)
            seq_scores.append(score)
        return seq_coords, seq_scores

    @staticmethod
    def _contour_similarity(hint_art: np.ndarray, cand_art: np.ndarray) -> float:
        hint_cnts, _ = cv2.findContours(hint_art, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cand_cnts, _ = cv2.findContours(cand_art, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not hint_cnts or not cand_cnts:
            return 0.0
        hint_main = max(hint_cnts, key=cv2.contourArea)
        if cv2.contourArea(hint_main) < 20:
            return 0.0
        best = 0.0
        for c in cand_cnts:
            if cv2.contourArea(c) < 20:
                continue
            dist = cv2.matchShapes(hint_main, c, cv2.CONTOURS_MATCH_I1, 0.0)
            best = max(best, 1.0 / (1.0 + dist))
        return best

    @staticmethod
    def _score_hint_in_bbox(hint_art: np.ndarray, cand_gray: np.ndarray) -> float:
        cand_art = DdddOcrClient._extract_line_art(cand_gray)
        best = -1.0
        hh, hw = hint_art.shape[:2]
        if hh < 2 or hw < 2:
            return best
        ch, cw = cand_art.shape[:2]
        if ch < 4 or cw < 4:
            return best
        for scale in (0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
            tw = max(4, int(hw * scale))
            th = max(4, int(hh * scale))
            if tw > cw or th > ch:
                tw = min(tw, cw)
                th = min(th, ch)
            tpl = cv2.resize(hint_art, (tw, th), interpolation=cv2.INTER_NEAREST)
            res = cv2.matchTemplate(cand_art, tpl, cv2.TM_CCOEFF_NORMED)
            tmpl_score = float(res.max()) if res.size else -1.0
            shape_score = DdddOcrClient._contour_similarity(tpl, cand_art)
            combined = 0.65 * tmpl_score + 0.35 * shape_score
            best = max(best, combined)
        return best

    @staticmethod
    def _orb_similarity(hint_gray: np.ndarray, patch_gray: np.ndarray) -> float:
        if hint_gray.size == 0 or patch_gray.size == 0:
            return 0.0
        th, tw = hint_gray.shape[:2]
        ph, pw = patch_gray.shape[:2]
        if th < 8 or tw < 8 or ph < 12 or pw < 12:
            return 0.0
        hint_rs = cv2.resize(hint_gray, (64, 64), interpolation=cv2.INTER_AREA)
        patch_rs = cv2.resize(patch_gray, (96, 96), interpolation=cv2.INTER_AREA)
        orb = cv2.ORB_create(nfeatures=400, scaleFactor=1.2, nlevels=4)
        kp1, des1 = orb.detectAndCompute(hint_rs, None)
        kp2, des2 = orb.detectAndCompute(patch_rs, None)
        if des1 is None or des2 is None or len(des1) < 4 or len(des2) < 4:
            return 0.0
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = bf.knnMatch(des1, des2, k=2)
        good = 0
        for pair in pairs:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.72 * n.distance:
                good += 1
        return min(1.0, good / max(len(kp1), 6))

    @staticmethod
    def _color_template_score(
        hint_gray: np.ndarray,
        patch_gray: np.ndarray,
        target_size: Optional[Tuple[int, int]] = None,
    ) -> float:
        hh, hw = hint_gray.shape[:2]
        ph, pw = patch_gray.shape[:2]
        if hh < 2 or hw < 2 or ph < 4 or pw < 4:
            return -1.0
        scales: list[float] = []
        if target_size:
            tw_t, th_t = target_size
            scales.extend([tw_t / hw, th_t / hh, ((tw_t / hw) + (th_t / hh)) / 2])
        scales.extend([3.5, 4.0, 4.5, 5.0, 5.5, 3.0, 2.5, 2.0])
        best = -1.0
        seen: set[float] = set()
        for scale in scales:
            scale = round(scale, 2)
            if scale in seen or scale <= 0:
                continue
            seen.add(scale)
            tw = max(6, int(hw * scale))
            th = max(6, int(hh * scale))
            if tw >= pw or th >= ph:
                tw = min(tw, pw - 1)
                th = min(th, ph - 1)
            if tw < 6 or th < 6:
                continue
            tpl = cv2.resize(hint_gray, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(patch_gray, tpl, cv2.TM_CCOEFF_NORMED)
            if res.size:
                best = max(best, float(res.max()))
        return best

    def _score_hint_against_bbox(
        self,
        hint_bytes: bytes,
        bg_bgr: np.ndarray,
        bg_gray: np.ndarray,
        bg_art: np.ndarray,
        bbox: Tuple[int, int, int, int],
        target_size: Optional[Tuple[int, int]],
    ) -> Tuple[float, Tuple[int, int], Tuple[int, int, int, int]]:
        x1, y1, x2, y2 = bbox
        pad = max(6, int(max(x2 - x1, y2 - y1) * 0.15))
        h, w = bg_gray.shape[:2]
        x1p, y1p = max(0, x1 - pad), max(0, y1 - pad)
        x2p, y2p = min(w, x2 + pad), min(h, y2 + pad)
        patch_gray = bg_gray[y1p:y2p, x1p:x2p]

        hint_bgr = self._decode_bgr(hint_bytes)
        hint_gray = cv2.cvtColor(hint_bgr, cv2.COLOR_BGR2GRAY)
        hint_art = self._extract_line_art(hint_gray)

        center, art_score, box = self._match_template_on_art(
            bg_art, hint_art, target_size=target_size, roi=(x1p, y1p, x2p, y2p)
        )
        tmpl_score = self._score_hint_in_bbox(hint_art, patch_gray)
        orb_score = self._orb_similarity(hint_gray, patch_gray)
        color_score = self._color_template_score(hint_gray, patch_gray, target_size)

        parts = [art_score, tmpl_score, orb_score, color_score]
        weights = [0.35, 0.30, 0.20, 0.15]
        combined = 0.0
        wsum = 0.0
        for val, wt in zip(parts, weights):
            if val < 0:
                continue
            combined += val * wt
            wsum += wt
        score = combined / wsum if wsum else -1.0
        return score, center, box

    def _assign_hints_to_bboxes(
        self,
        hint_images: list[bytes],
        bboxes: list[Tuple[int, int, int, int]],
        bg_bgr: np.ndarray,
        bg_gray: np.ndarray,
        bg_art: np.ndarray,
        target_size: Optional[Tuple[int, int]],
    ) -> Optional[tuple[list[tuple[int, int]], list[float]]]:
        if len(bboxes) < len(hint_images):
            return None

        n_h = len(hint_images)
        pairs: list[tuple[float, int, int, Tuple[int, int], Tuple[int, int, int, int]]] = []
        for hi, hint_bytes in enumerate(hint_images):
            for bi, bbox in enumerate(bboxes):
                score, center, box = self._score_hint_against_bbox(
                    hint_bytes, bg_bgr, bg_gray, bg_art, bbox, target_size
                )
                pairs.append((score, hi, bi, center, box))
        pairs.sort(key=lambda x: x[0], reverse=True)

        assigned_h: dict[int, int] = {}
        used_b: set[int] = set()
        details: dict[int, tuple[float, Tuple[int, int], Tuple[int, int, int, int], int]] = {}
        for score, hi, bi, center, box in pairs:
            if hi in assigned_h or bi in used_b:
                continue
            assigned_h[hi] = bi
            used_b.add(bi)
            details[hi] = (score, center, box, bi)
            if len(assigned_h) == n_h:
                break

        if len(assigned_h) < n_h:
            return None

        coords: list[tuple[int, int]] = []
        scores: list[float] = []
        for hi in range(n_h):
            score, center, box, bi = details[hi]
            hint_scores = sorted((s for s, h, b, _c, _bx in pairs if h == hi), reverse=True)
            if len(hint_scores) >= 2 and (hint_scores[0] - hint_scores[1]) < 0.06:
                raise RuntimeError(
                    "hint#%d 匹配歧义 (top=%.3f second=%.3f)，请重试"
                    % (hi + 1, hint_scores[0], hint_scores[1])
                )
            coords.append(center)
            scores.append(score)
            log.info(
                "hint#%d -> center=%s score=%.3f bbox_idx=%d box=%s via=bbox",
                hi + 1,
                center,
                score,
                bi,
                box,
            )
        if min(scores) < BBOX_MIN_SCORE:
            raise RuntimeError("bbox 匹配置信度过低 (min=%.3f)，请重试" % min(scores))
        return coords, scores

    def _bg_icon_target_size_from_bboxes(
        self,
        bboxes: list[tuple[int, int, int, int]],
        hint_count: int,
    ) -> Optional[Tuple[int, int]]:
        if not bboxes:
            return None
        picked = bboxes
        if len(picked) > hint_count:
            picked = sorted(
                picked,
                key=lambda b: abs((b[2] - b[0]) - (b[3] - b[1])),
            )[:hint_count]
        ws = [b[2] - b[0] for b in picked]
        hs = [b[3] - b[1] for b in picked]
        return int(np.median(ws)), int(np.median(hs))

    def _bg_icon_target_size(self, bg_bytes: bytes, hint_count: int) -> Optional[Tuple[int, int]]:
        bboxes = self._filter_icon_bboxes(self.detection(bg_bytes), min_side=22)
        return self._bg_icon_target_size_from_bboxes(bboxes, hint_count)

    def _mask_used_region(self, art: np.ndarray, box: Tuple[int, int, int, int], *, pad_ratio: float = 0.35) -> None:
        x1, y1, x2, y2 = box
        if x2 <= x1 or y2 <= y1:
            return
        pad = max(12, int(max(x2 - x1, y2 - y1) * pad_ratio))
        h, w = art.shape[:2]
        cv2.rectangle(
            art,
            (max(0, x1 - pad), max(0, y1 - pad)),
            (min(w, x2 + pad), min(h, y2 + pad)),
            0,
            -1,
        )

    def match_icons_correlation(
        self,
        bg_bytes: bytes,
        hint_images: list[bytes],
    ) -> list[tuple[int, int]]:
        """Match strip hints in order on the background canvas."""
        bg = self._decode_bgr(bg_bytes)
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        bg_art = self._extract_line_art(bg_gray)
        canvas_h, canvas_w = bg_gray.shape[:2]

        raw_boxes = self.detection(bg_bytes)
        bboxes = self._filter_icon_bboxes(raw_boxes, min_side=22)
        bboxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
        target_size = self._bg_icon_target_size_from_bboxes(bboxes[:10], len(hint_images))
        log.info(
            "bg detection: %d icon-like boxes, matching %d hints target_size=%s",
            len(bboxes),
            len(hint_images),
            target_size,
        )

        seq_coords, seq_scores = self._run_sequential_match(
            hint_images, bg, bg_gray, bg_art, target_size
        )

        bbox_coords: Optional[list[tuple[int, int]]] = None
        bbox_scores: Optional[list[float]] = None
        if len(bboxes) >= len(hint_images):
            try:
                bbox_result = self._assign_hints_to_bboxes(
                    hint_images, bboxes[:10], bg, bg_gray, bg_art, target_size
                )
                if bbox_result is not None:
                    bbox_coords, bbox_scores = bbox_result
            except RuntimeError as exc:
                log.info("bbox assignment skipped: %s", exc)

        candidates: list[tuple[str, list[tuple[int, int]], list[float]]] = []
        seq_edge_bad = any(
            self._is_edge_suspicious(c, s, canvas_w, canvas_h)
            for c, s in zip(seq_coords, seq_scores)
        )
        if self._scores_acceptable(seq_scores) and not seq_edge_bad:
            candidates.append(("sequential", seq_coords, seq_scores))
        elif seq_edge_bad:
            log.info("sequential rejected: edge suspicious (min=%.3f)", min(seq_scores))

        if bbox_coords and bbox_scores and self._scores_acceptable(bbox_scores):
            candidates.append(("bbox", bbox_coords, bbox_scores))

        if not candidates:
            raise RuntimeError(
                "图标匹配置信度过低 (sequential min=%.3f, bbox min=%s)，请重试"
                % (
                    min(seq_scores),
                    "%.3f" % min(bbox_scores) if bbox_scores else "n/a",
                )
            )

        method, coords, scores = max(
            candidates,
            key=lambda item: self._candidate_rank(item[2]),
        )
        log.info(
            "using %s path min=%.3f avg=%.3f scores=%s",
            method,
            min(scores),
            sum(scores) / len(scores),
            ["%.3f" % s for s in scores],
        )
        return coords

    def parse_select_result(self, raw: list[dict[str, list[int]]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for item in raw:
            for text, bbox in item.items():
                x1, y1, x2, y2 = map(int, bbox)
                parsed.append(
                    {
                        "text": re.sub(r"[^\u4e00-\u9fff]", "", text or ""),
                        "bbox": (x1, y1, x2, y2),
                        "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                    }
                )
        return parsed

    def match_word_click(self, bg_bytes: bytes, target_chars: list[str]) -> list[tuple[int, int]]:
        raw = self.select(bg_bytes)
        results = self.parse_select_result(raw)
        log.info("OCR select: %s", [(r["text"], r["bbox"]) for r in results])
        if not results:
            raise RuntimeError("ddddocr /select 未检测到任何文字区域")

        used: set[int] = set()
        coords: list[tuple[int, int]] = []
        for ch in target_chars:
            pick = -1
            for i, r in enumerate(results):
                if i in used:
                    continue
                if r["text"] == ch:
                    pick = i
                    break
            if pick < 0:
                for i, r in enumerate(results):
                    if i in used:
                        continue
                    if ch in r["text"]:
                        pick = i
                        break
            if pick < 0:
                for i in range(len(results)):
                    if i not in used:
                        pick = i
                        break
            if pick < 0:
                pick = 0
            used.add(pick)
            coords.append(results[pick]["center"])
            log.info("target=%r -> center=%s ocr=%r", ch, coords[-1], results[pick]["text"])
        return coords

    def match_select_in_order(
        self,
        bg_bytes: bytes,
        sprite_bytes: bytes,
        ins_elems: list[Any],
    ) -> list[tuple[int, int]]:
        """Select in order: strip hint icons → multi-scale match on background."""
        sprite = Image.open(io.BytesIO(sprite_bytes)).convert("RGB")
        hint_images: list[bytes] = []
        for elem in ins_elems:
            px, py = elem.sprite_pos
            pw, ph = elem.size_2d
            strip = self._crop_png(sprite, (px, py, px + pw, py + ph))
            hint_images.extend(self.split_hint_strip(strip))

        if not hint_images:
            raise RuntimeError("未能从 ins_elem_cfg sprite 中拆出提示图标")
        log.info("Select in order: %d hint icons", len(hint_images))
        return self.match_icons_correlation(bg_bytes, hint_images)

    def match_icon_click(self, bg_bytes: bytes, hint_images: list[bytes]) -> list[tuple[int, int]]:
        return self.match_icons_correlation(bg_bytes, hint_images)
