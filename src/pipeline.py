import cv2
import numpy as np
from src.settings import AppSettings


def _apply_brightness_contrast(bgr: np.ndarray, brightness: float, contrast: float) -> np.ndarray:
    out = cv2.convertScaleAbs(bgr, alpha=float(contrast), beta=0)
    if abs(brightness - 1.0) > 1e-3:
        out = np.clip(out.astype(np.float32) * float(brightness), 0, 255).astype(np.uint8)
    return out


def _unsharp_mask(bgr: np.ndarray, amount: float) -> np.ndarray:
    amount = float(amount)
    if amount <= 0.01:
        return bgr
    blur = cv2.GaussianBlur(bgr, (0, 0), 1.2)
    sharp = cv2.addWeighted(bgr, 1.0 + amount, blur, -amount, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)


def _zoom_center(bgr: np.ndarray, zoom: float) -> np.ndarray:
    zoom = float(zoom)
    if zoom <= 1.01:
        return bgr
    h, w = bgr.shape[:2]
    new_w = max(2, int(w / zoom))
    new_h = max(2, int(h / zoom))
    x1 = (w - new_w) // 2
    y1 = (h - new_h) // 2
    crop = bgr[y1:y1 + new_h, x1:x1 + new_w]
    return cv2.resize(crop, (w, h), interpolation=cv2.INTER_CUBIC)


def _edge_outline(bgr: np.ndarray, strength: float, thickness: int) -> np.ndarray:
    strength = float(np.clip(strength, 0.0, 1.0))
    thickness = int(np.clip(thickness, 1, 4))
    if strength <= 0.01:
        return bgr

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0.8)

    edges = cv2.Canny(gray, 60, 160)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * thickness + 1, 2 * thickness + 1))
    edges = cv2.dilate(edges, k, iterations=1)

    mask = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

    # Bright outline effect
    overlay = cv2.addWeighted(bgr, 1.0, mask, 0.45, 0)

    out = cv2.addWeighted(bgr, 1.0 - strength, overlay, strength, 0)
    return out


def _rp_visual_filter(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    h, s, v = cv2.split(hsv)

    # mild highlight compression
    v = np.power(v / 255.0, 1.15) * 255.0
    v = np.clip(v, 0, 255)

    hsv = cv2.merge([h, s, v]).astype(np.uint8)
    out = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    # mild warm tone
    out = out.astype(np.float32)
    out[:, :, 2] *= 1.03  # R
    out[:, :, 0] *= 0.98  # B
    return np.clip(out, 0, 255).astype(np.uint8)


def _emergency_mode(bgr: np.ndarray) -> np.ndarray:
    out = _apply_brightness_contrast(bgr, brightness=1.25, contrast=1.20)
    out = _unsharp_mask(out, 0.65)
    return out


def _reading_mode(bgr: np.ndarray, s: AppSettings) -> np.ndarray:
    # zoom magnifier
    out = _zoom_center(bgr, s.zoom)

    # keep reading clean (use existing brightness/contrast from sliders)
    out = _apply_brightness_contrast(out, s.brightness, s.contrast)

    # extra reading sharpness (separate slider)
    out = _unsharp_mask(out, getattr(s, "reading_sharp", 0.9))

    # outline around edges (helps text boundaries)
    out = _edge_outline(
        out,
        getattr(s, "reading_edges", 0.55),
        getattr(s, "reading_edge_thickness", 2)
    )
    return out


def process_frame(frame_bgr: np.ndarray, s: AppSettings) -> np.ndarray:
    if frame_bgr is None:
        return frame_bgr

    # Emergency overrides all
    if getattr(s, "emergency_on", False):
        return _emergency_mode(frame_bgr)

    # Reading mode
    if getattr(s, "reading_on", False):
        return _reading_mode(frame_bgr, s)

    out = frame_bgr

    # Visual modes
    if getattr(s, "adaptive_on", False):
        out = _apply_brightness_contrast(out, s.brightness, s.contrast)

    if getattr(s, "filter_on", False):
        out = _rp_visual_filter(out)

    if getattr(s, "sharpness_on", False):
        out = _unsharp_mask(out, s.sharpness)

    return out
