"""a6700_rawsmith core — rawpy-backed RAW developer with JSON recipes.

A Python sister project to rawsmith (https://github.com/Lewin671/rawsmith).
Where rawsmith is a from-scratch Rust engine that deliberately avoids LibRaw,
this implementation is built on ``rawpy`` (a LibRaw binding) and is positioned
as a fast, readable, LibRaw-backed reference for the Sony α6700 — with a
Fujifilm-style LUT look that rawsmith does not cover.

The develop pipeline runs in *scene-linear* float space (values proportional to
light) and only maps to sRGB gamma at the very end, so exposure, tone and
colour behave the way a photographer expects.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

try:
    from lut import apply_lut  # type: ignore[import]
except ImportError:
    try:
        from .lut import apply_lut  # type: ignore[import]
    except ImportError:
        apply_lut = None  # type: ignore[assignment]

try:
    import rawpy
except ImportError:
    rawpy = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# sRGB transfer functions
# ---------------------------------------------------------------------------

def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    """Decode sRGB gamma to scene-linear. Input/float in [0, 1]."""
    rgb = np.clip(rgb, 0.0, 1.0)
    low = rgb / 12.92
    high = ((rgb + 0.055) / 1.055) ** 2.4
    return np.where(rgb <= 0.04045, low, high).astype(np.float32)


def linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    """Encode scene-linear to sRGB gamma. Input/float in [0, 1]."""
    rgb = np.clip(rgb, 0.0, 1.0)
    low = rgb * 12.92
    high = 1.055 * (rgb ** (1.0 / 2.4)) - 0.055
    return np.where(rgb <= 0.0031308, low, high).astype(np.float32)


# ---------------------------------------------------------------------------
# Recipe schema
# ---------------------------------------------------------------------------

SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "A6700 RAW Recipe",
    "type": "object",
    "properties": {
        "exposure": {"type": "number", "minimum": -5, "maximum": 5, "default": 0},
        "temperature": {"type": ["number", "null"], "minimum": 2000, "maximum": 10000, "default": None},
        "tint": {"type": ["number", "null"], "minimum": -100, "maximum": 100, "default": None},
        "highlights": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "shadows": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "whites": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "blacks": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "contrast": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "clarity": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "dehaze": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "saturation": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "vibrance": {"type": "integer", "minimum": -100, "maximum": 100, "default": 0},
        "sharpness": {"type": "integer", "minimum": 0, "maximum": 100, "default": 40},
        "denoise": {"type": "integer", "minimum": 0, "maximum": 100, "default": 10},
        "rotate": {"type": "number", "minimum": -45, "maximum": 45, "default": 0},
        "crop": {
            "type": "object",
            "properties": {
                "top": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                "bottom": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
                "left": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                "right": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
            },
            "default": {},
        },
        "hsl": {
            "type": "object",
            "default": {},
            "properties": {
                band: {
                    "type": "object",
                    "properties": {
                        "hue": {"type": "integer", "minimum": -100, "maximum": 100},
                        "saturation": {"type": "integer", "minimum": -100, "maximum": 100},
                        "luminance": {"type": "integer", "minimum": -100, "maximum": 100},
                    },
                }
                for band in ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
            },
        },
        "grade": {
            "type": "object",
            "properties": {
                "shadows": {"type": "object", "properties": {"hue": {"type": "number"}, "sat": {"type": "number"}}},
                "highlights": {"type": "object", "properties": {"hue": {"type": "number"}, "sat": {"type": "number"}}},
            },
        },
        "a6700": {
            "type": "object",
            "description": "A6700-specific metadata (written into recipe, not applied here)",
            "properties": {
                "creative_look": {"type": "string", "enum": ["ST", "PT", "NT", "VV", "VV2", "FL", "IN", "SH", "BW", "SE"]},
                "picture_profile": {"type": "string", "pattern": "^PP([1-6]|10|11)$"},
                "fuji_lut_path": {"type": "string"},
            },
        },
    },
    "additionalProperties": False,
    "default": {
        "exposure": 0,
        "temperature": None,
        "tint": None,
        "highlights": 0,
        "shadows": 0,
        "whites": 0,
        "blacks": 0,
        "contrast": 0,
        "clarity": 0,
        "dehaze": 0,
        "saturation": 0,
        "vibrance": 0,
        "sharpness": 40,
        "denoise": 10,
        "rotate": 0,
        "crop": {},
        "hsl": {},
        "grade": {},
        "a6700": {},
    },
}

# Keys shared verbatim with rawsmith's recipe format (see docs/recipes/*.json).
RAWSMITH_SHARED_KEYS = {
    "exposure", "highlights", "shadows", "whites", "blacks",
    "contrast", "clarity", "dehaze", "vibrance", "crop",
}


def recipe_schema() -> Dict[str, Any]:
    """Return a copy of the recipe schema."""
    return json.loads(json.dumps(SCHEMA))


# ---------------------------------------------------------------------------
# rawsmith recipe interoperability
# ---------------------------------------------------------------------------

def to_rawsmith_recipe(recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an a6700 recipe into a rawsmith-compatible recipe.

    Only keys rawsmith understands are kept; a6700-specific extensions
    (temperature/tint, hsl, grade, a6700 block, sharpness/denoise/rotate)
    are dropped so the result can be fed straight into ``rawsmith develop``.
    """
    merged = dict(SCHEMA["default"], **recipe)
    out: Dict[str, Any] = {"rawsmith": "0.1.0"}
    for key in sorted(RAWSMITH_SHARED_KEYS):
        val = merged.get(key)
        if key == "crop":
            if val:
                out["crop"] = {k: v for k, v in val.items() if k in ("top", "bottom", "left", "right")}
        elif val not in (None, 0, {}, []):
            out[key] = val
    return out


def from_rawsmith_recipe(rs: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a rawsmith recipe into an a6700 recipe (fills defaults)."""
    out = dict(SCHEMA["default"])
    for key in RAWSMITH_SHARED_KEYS:
        if key in rs:
            out[key] = rs[key]
    return out


# ---------------------------------------------------------------------------
# Colour helpers (vectorised)
# ---------------------------------------------------------------------------

def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """RGB [0,1] (H,W,3) -> HSV, H in [0,1]."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    v = maxc
    s = np.where(maxc > 1e-9, (maxc - minc) / np.maximum(maxc, 1e-9), 0.0)
    diff = maxc - minc
    diff = np.where(diff < 1e-9, 1.0, diff)  # avoid /0 where achromatic
    rc = (maxc - r) / diff
    gc = (maxc - g) / diff
    bc = (maxc - b) / diff
    h = np.where(maxc == r, bc - gc,
        np.where(maxc == g, 2.0 + rc - bc, 4.0 + gc - rc))
    h = (h / 6.0) % 1.0
    h = np.where(maxc == minc, 0.0, h)  # achromatic -> hue 0
    return np.stack([h, s, v], axis=-1).astype(np.float32)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    """HSV (H,W,3), H in [0,1] -> RGB [0,1]."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = (h * 6.0).astype(np.int32) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1).astype(np.float32)


# Hue centre of each HSL band, in [0,1] hue units.
_HSL_BANDS: Dict[str, float] = {
    "red": 0.0, "orange": 0.0833, "yellow": 0.1667, "green": 0.3333,
    "aqua": 0.5, "blue": 0.6667, "purple": 0.8333, "magenta": 0.9167,
}


def _apply_hsl(rgb: np.ndarray, hsl: Dict[str, Any]) -> np.ndarray:
    """Per-band hue/saturation/luminance adjustment, band-weighted by hue distance."""
    if not hsl:
        return rgb
    hsv = _rgb_to_hsv(np.clip(rgb, 0, 1))
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    out_h, out_s, out_v = h.copy(), s.copy(), v.copy()
    for band, adj in hsl.items():
        if band not in _HSL_BANDS or not isinstance(adj, dict):
            continue
        centre = _HSL_BANDS[band]
        # circular distance in hue, weight falls off to 0 at 1/12 turn away
        dist = np.abs(h - centre)
        dist = np.minimum(dist, 1.0 - dist)
        weight = np.clip(1.0 - dist / (1.0 / 12.0), 0, 1)
        if "hue" in adj:
            out_h += weight * (float(adj["hue"]) / 100.0) * 0.16
        if "saturation" in adj:
            out_s = out_s * (1.0 + weight * (float(adj["saturation"]) / 100.0))
        if "luminance" in adj:
            out_v = out_v * (1.0 + weight * (float(adj["luminance"]) / 100.0) * 0.5)
    out_h = out_h % 1.0
    out_s = np.clip(out_s, 0, 1)
    out_v = np.clip(out_v, 0, 1)
    return _hsv_to_rgb(np.stack([out_h, out_s, out_v], axis=-1))


def _hue_to_rgb(hue_deg: float) -> np.ndarray:
    """Convert a hue in degrees to a unit RGB tint vector."""
    h = (hue_deg % 360.0) / 360.0
    return _hsv_to_rgb(np.array([[[h, 1.0, 1.0]]], dtype=np.float32))[0, 0]


def _apply_grade(rgb: np.ndarray, grade: Dict[str, Any]) -> np.ndarray:
    """Colour grade (split toning): tint shadows and highlights independently."""
    if not grade:
        return rgb
    lum = np.mean(rgb, axis=2, keepdims=True)
    out = rgb.copy()
    shadow_t = grade.get("shadows")
    highlight_t = grade.get("highlights")
    if isinstance(shadow_t, dict) and "hue" in shadow_t:
        sat = float(shadow_t.get("sat", 0.3))
        tint = _hue_to_rgb(float(shadow_t["hue"])) * sat
        mask = np.clip(1.0 - lum / 0.5, 0, 1)
        out = out + tint * mask * 0.25
    if isinstance(highlight_t, dict) and "hue" in highlight_t:
        sat = float(highlight_t.get("sat", 0.3))
        tint = _hue_to_rgb(float(highlight_t["hue"])) * sat
        mask = np.clip((lum - 0.5) / 0.5, 0, 1)
        out = out + tint * mask * 0.25
    return np.clip(out, 0, 1)


def _apply_sharpness(rgb: np.ndarray, amount: float) -> np.ndarray:
    """Unsharp mask in luminance, preserving colour."""
    if amount <= 0:
        return rgb
    from scipy.ndimage import gaussian_filter
    k = amount / 100.0 * 0.8
    gray = np.mean(rgb, axis=2)
    blur = gaussian_filter(gray, sigma=1.0)
    detail = gray - blur
    out = rgb + detail[..., None] * k
    return np.clip(out, 0, 1)


def _apply_denoise(rgb: np.ndarray, amount: float) -> np.ndarray:
    """Chroma-only smoothing that preserves luminance detail."""
    if amount <= 0:
        return rgb
    from scipy.ndimage import gaussian_filter
    sigma = 0.5 + (amount / 100.0) * 1.5
    gray = np.mean(rgb, axis=2, keepdims=True)
    chroma = rgb - gray
    smoothed = np.stack([gaussian_filter(chroma[..., c], sigma=sigma) for c in range(3)], axis=2)
    return np.clip(gray + smoothed, 0, 1)


# ---------------------------------------------------------------------------
# Tone operators (scene-linear)
# ---------------------------------------------------------------------------

def _apply_exposure(rgb: np.ndarray, stops: float) -> np.ndarray:
    if stops == 0:
        return rgb
    return np.clip(rgb * (2.0 ** stops), 0, 1)


def _apply_tone_zone(rgb: np.ndarray, value: float, centre: float, width: float, sign: int) -> np.ndarray:
    """Lift/compress a luminance zone around ``centre`` (linear light)."""
    if value == 0:
        return rgb
    lum = np.mean(rgb, axis=2, keepdims=True)
    weight = np.exp(-0.5 * ((lum - centre) / width) ** 2)
    gain = 1.0 + sign * (value / 100.0) * 0.6 * weight
    return np.clip(rgb * gain, 0, 1)


def _apply_highlights(rgb: np.ndarray, highlights: float) -> np.ndarray:
    return _apply_tone_zone(rgb, highlights, centre=0.75, width=0.25, sign=-1)


def _apply_shadows(rgb: np.ndarray, shadows: float) -> np.ndarray:
    return _apply_tone_zone(rgb, shadows, centre=0.2, width=0.2, sign=1)


def _apply_whites(rgb: np.ndarray, whites: float) -> np.ndarray:
    return _apply_tone_zone(rgb, whites, centre=0.85, width=0.3, sign=1)


def _apply_blacks(rgb: np.ndarray, blacks: float) -> np.ndarray:
    return _apply_tone_zone(rgb, blacks, centre=0.1, width=0.2, sign=-1)


def _apply_contrast(rgb: np.ndarray, contrast: float) -> np.ndarray:
    if contrast == 0:
        return rgb
    c = contrast / 100.0
    pivot = 0.18  # middle grey in linear light
    return np.clip(pivot + (rgb - pivot) * (1 + c), 0, 1)


def _apply_dehaze(rgb: np.ndarray, dehaze: float) -> np.ndarray:
    if dehaze == 0:
        return rgb
    d = dehaze / 100.0
    gray = np.mean(rgb, axis=2, keepdims=True)
    out = rgb - d * 0.15 * gray
    return np.clip(out, 0, 1)


def _apply_clarity(rgb: np.ndarray, clarity: float) -> np.ndarray:
    if clarity == 0:
        return rgb
    from scipy.ndimage import gaussian_filter
    c = clarity / 100.0
    gray = np.mean(rgb, axis=2)
    blurred = gaussian_filter(gray, sigma=8)
    detail = gray - blurred
    factor = c * 0.6
    new_gray = gray + detail * factor
    out = np.clip(new_gray[..., None] + (rgb - gray[..., None]) * (1 + factor * 0.3), 0, 1)
    return out


def _apply_saturation(rgb: np.ndarray, saturation: float, vibrance: float) -> np.ndarray:
    if saturation == 0 and vibrance == 0:
        return rgb
    hsv = _rgb_to_hsv(np.clip(rgb, 0, 1))
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vib = vibrance / 100.0 * (1.0 - s)  # vibrance spares already-saturated pixels
    s2 = np.clip(s * (1.0 + saturation / 100.0 + vib), 0, 1)
    return _hsv_to_rgb(np.stack([h, s2, v], axis=-1))


# ---------------------------------------------------------------------------
# White balance from colour temperature
# ---------------------------------------------------------------------------

def _wb_adjustment(kelvin: Optional[float], tint: Optional[float]) -> Tuple[np.ndarray, Optional[str]]:
    """Relative RGB gains versus a ~6500K daylight reference, from temperature/tint.

    A warmer target (lower Kelvin) trims red and boosts blue; ``tint`` shifts
    green. Monotonic and bounded — never diverges. Returns (gain[3], warning).
    """
    gains = np.ones(3, dtype=np.float64)
    if kelvin is None and tint is None:
        return gains, None
    if kelvin is not None:
        w = float(np.clip((6500.0 - float(kelvin)) / 6500.0, -1.0, 1.0))
        gains[0] *= (1.0 - 0.45 * w)
        gains[2] *= (1.0 + 0.45 * w)
    if tint:
        gains[1] *= (1.0 + float(np.clip(tint, -100, 100)) / 100.0 * 0.3)
    return np.maximum(gains, 1e-3), None


def _kelvin_to_wb(kelvin: Optional[float], tint: Optional[float],
                  base_wb: List[float]) -> Tuple[Optional[List[float]], Optional[str]]:
    """Map a target temperature/tint to rawpy ``user_wb`` multipliers.

    Anchored on the camera's daylight white balance (treated as ~6500K).
    Returns (user_wb, warning_or_None); None means "use camera WB".
    """
    if kelvin is None and tint is None:
        return None, None
    if not base_wb or len(base_wb) < 3:
        return None, "no daylight WB available; ignoring temperature/tint"
    factor, _ = _wb_adjustment(kelvin, tint)
    gains = np.array(base_wb[:3], dtype=np.float64) * factor
    gains = np.maximum(gains, 1e-3)
    return [float(gains[0]), float(gains[1]), float(gains[2]), 1.0], None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _compute_stats(rgb: np.ndarray) -> Dict[str, Any]:
    """Compute development stats from an RGB array (0-1 float)."""
    lum = np.mean(rgb, axis=2)
    hist, _ = np.histogram(lum, bins=10, range=(0, 1))
    tones = {
        "p50": float(np.percentile(lum, 50) * 100),
        "p95": float(np.percentile(lum, 95) * 100),
        "spread": float((np.percentile(lum, 95) - np.percentile(lum, 5)) * 100),
    }
    clipped_high = float(np.mean(lum > 0.98) * 100)
    clipped_low = float(np.mean(lum < 0.02) * 100)
    zones = []
    for row in np.array_split(lum, 3, axis=0):
        for col in np.array_split(row, 3, axis=1):
            zones.append(float(np.mean(col) * 100))
    return {
        "tones": tones,
        "histogram": [float(x) for x in hist],
        "clipped_highlights_pct": round(clipped_high, 2),
        "crushed_shadows_pct": round(clipped_low, 2),
        "zones": [round(z, 1) for z in zones],
    }


# ---------------------------------------------------------------------------
# Core develop function
# ---------------------------------------------------------------------------

@dataclass
class DevelopResult:
    preview_path: str
    stats: Dict[str, Any]
    recipe: Dict[str, Any]
    raw_metadata: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preview_path": self.preview_path,
            "stats": self.stats,
            "recipe": self.recipe,
            "raw_metadata": self.raw_metadata,
            "warnings": self.warnings,
        }


def _process_rgb_linear(rgb: np.ndarray, recipe: Dict[str, Any]) -> np.ndarray:
    """Apply tone/colour recipe to a scene-linear float array in [0, 1].

    Every operator defaults to a neutral no-op when its key is absent, so an
    empty recipe is the identity transform.
    """
    out = np.clip(rgb.astype(np.float32), 0, 1)
    out = _apply_exposure(out, float(recipe.get("exposure", 0)))
    out = _apply_highlights(out, float(recipe.get("highlights", 0)))
    out = _apply_shadows(out, float(recipe.get("shadows", 0)))
    out = _apply_whites(out, float(recipe.get("whites", 0)))
    out = _apply_blacks(out, float(recipe.get("blacks", 0)))
    out = _apply_contrast(out, float(recipe.get("contrast", 0)))
    out = _apply_dehaze(out, float(recipe.get("dehaze", 0)))
    out = _apply_clarity(out, float(recipe.get("clarity", 0)))
    out = _apply_denoise(out, float(recipe.get("denoise", 0)))
    out = _apply_saturation(out, float(recipe.get("saturation", 0)), float(recipe.get("vibrance", 0)))
    out = _apply_hsl(out, recipe.get("hsl", {}))
    out = _apply_grade(out, recipe.get("grade", {}))
    out = _apply_sharpness(out, float(recipe.get("sharpness", 0)))
    return out


def _process_rgb(rgb: np.ndarray, recipe: Dict[str, Any]) -> np.ndarray:
    """Backwards-compatible entry point: treat input as scene-linear float."""
    return _process_rgb_linear(rgb, recipe)


def _resize_to_size(rgb: np.ndarray, size: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    long = max(w, h)
    if long <= size or size <= 0:
        return rgb
    scale = size / long
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def _crop_rgb(rgb: np.ndarray, crop: Dict[str, float]) -> np.ndarray:
    if not crop:
        return rgb
    h, w = rgb.shape[:2]
    top = int(crop.get("top", 0) * h)
    bottom = int(crop.get("bottom", 1) * h)
    left = int(crop.get("left", 0) * w)
    right = int(crop.get("right", 1) * w)
    return rgb[top:bottom, left:right]


def _apply_rotate(rgb: np.ndarray, angle_deg: float) -> np.ndarray:
    if angle_deg == 0:
        return rgb
    from scipy.ndimage import rotate
    return rotate(rgb, angle_deg, reshape=False, order=1, mode="nearest")


def _save_preview(rgb: np.ndarray, out_path: str, size: int = 1400) -> str:
    """Save RGB preview (float 0-1), resizing to fit within `size` long edge."""
    rgb = _resize_to_size(rgb, size) if size and max(rgb.shape[:2]) > size else rgb
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    img.save(out_path, quality=90, optimize=True)
    return out_path


def _wb_list(raw: Any, attr: str) -> List[float]:
    """Safely read a rawpy white-balance attribute (list, not dict)."""
    try:
        val = getattr(raw, attr)
    except Exception:
        return []
    if isinstance(val, dict):
        return [float(v) for v in val.values()]
    try:
        return [float(v) for v in val]
    except Exception:
        return []


def _exif_camera(path: str) -> str:
    """Best-effort camera make+model from EXIF (rawpy may not expose it)."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            # 0x010f Make, 0x0110 Model
            make = exif.get(0x010F)
            model = exif.get(0x0110)
            name = " ".join(str(x) for x in (make, model) if x).strip()
            return name or "unknown"
    except Exception:
        return "unknown"


def develop(
    path: str,
    recipe: Dict[str, Any],
    out: str,
    size: int = 1400,
) -> DevelopResult:
    """Develop a RAW file into a preview JPEG.

    Parameters
    ----------
    path : str
        Path to the RAW (ARW/DNG/...) or, for testing, a raster image.
    recipe : dict
        Adjustment parameters. Unknown keys are ignored with a warning.
    out : str
        Output preview path (JPEG or PNG).
    size : int
        Long-edge preview size in pixels.

    Returns
    -------
    DevelopResult
    """
    warnings: List[str] = []
    recipe = dict(SCHEMA["default"], **recipe)
    known = set(SCHEMA["properties"].keys())
    for k in recipe:
        if k not in known:
            warnings.append(f"unknown recipe key: {k}")

    raw_exts = ('.arw', '.dng', '.nef', '.cr2', '.cr3', '.raf', '.orf', '.rw2', '.pef', '.srw')
    is_raw = path.lower().endswith(raw_exts)

    if is_raw:
        if rawpy is None:
            raise ImportError("rawpy not installed. Run: pip install rawpy")
        with rawpy.imread(path) as raw:
            daylight_wb = _wb_list(raw, "daylight_whitebalance")
            camera_wb = _wb_list(raw, "camera_whitebalance")
            # rawpy (this version) does not expose make/model; try PIL EXIF,
            # and always surface the shooting parameters an assistant can reason
            # about (ISO / shutter / aperture / focal length / timestamp).
            other = getattr(raw, "other", None)
            shooting: Dict[str, Any] = {}
            if other is not None:
                for attr in ("iso_speed", "shutter_speed", "aperture", "focal_length", "timestamp"):
                    val = getattr(other, attr, None)
                    if val is not None:
                        shooting[attr] = str(val) if attr == "timestamp" else float(val)
            raw_metadata = {
                "camera": _exif_camera(path),
                "shooting": shooting,
                "sizes": {
                    "sensor_width": int(raw.sizes.raw_width),
                    "sensor_height": int(raw.sizes.raw_height),
                    "image_width": int(raw.sizes.width),
                    "image_height": int(raw.sizes.height),
                },
                "daylight_whitebalance": daylight_wb,
                "camera_whitebalance": camera_wb,
            }
            user_wb, wb_warn = _kelvin_to_wb(
                recipe.get("temperature"), recipe.get("tint"), daylight_wb
            )
            if wb_warn:
                warnings.append(wb_warn)
            # Decode to 16-bit *linear* scene values (gamma 1,1 = no tone curve).
            rgb = raw.postprocess(
                use_camera_wb=(user_wb is None),
                user_wb=user_wb,
                no_auto_bright=True,
                output_color=rawpy.ColorSpace.sRGB,
                output_bps=16,
                gamma=(1.0, 1.0),
            )
        rgb_linear = rgb.astype(np.float32) / 65535.0
    else:
        warnings.append(f"Input is not a RAW file ({path}); using Pillow fallback for testing")
        img = Image.open(path).convert("RGB")
        rgb_u8 = np.asarray(img, dtype=np.float32) / 255.0
        rgb_linear = srgb_to_linear(rgb_u8)
        # Fallback path has no sensor WB, so apply the temperature/tint gain
        # directly in linear space (the RAW path does this via user_wb instead).
        wb_factor, wb_warn = _wb_adjustment(recipe.get("temperature"), recipe.get("tint"))
        if wb_warn:
            warnings.append(wb_warn)
        rgb_linear = np.clip(rgb_linear * wb_factor[None, None, :], 0, 1)
        raw_metadata = {
            "camera": "pillow-fallback",
            "sizes": {
                "sensor_width": rgb_u8.shape[1],
                "sensor_height": rgb_u8.shape[0],
                "image_width": rgb_u8.shape[1],
                "image_height": rgb_u8.shape[0],
            },
            "daylight_whitebalance": [],
            "camera_whitebalance": [],
        }

    # Work at preview resolution for speed; tone/colour are resolution-safe.
    rgb_linear = _resize_to_size(rgb_linear, size)

    # Adjustments in scene-linear space.
    rgb_linear = _process_rgb_linear(rgb_linear, recipe)

    # Fujifilm LUT look (a6700 extension) — errors are surfaced, not swallowed.
    a6700 = recipe.get("a6700") if isinstance(recipe.get("a6700"), dict) else {}
    lut_path = a6700.get("fuji_lut_path")
    if lut_path:
        if apply_lut is None:
            warnings.append("fuji_lut_path set but lut module unavailable; LUT skipped")
        else:
            try:
                rgb_linear = apply_lut(rgb_linear, lut_path, input_srgb=False, output_srgb=False)
            except Exception as exc:
                warnings.append(f"LUT application failed: {exc}")

    # Map to sRGB gamma only at the end.
    rgb_srgb = linear_to_srgb(rgb_linear)

    # Geometry last (on gamma-encoded pixels is fine for preview).
    rgb_srgb = _apply_rotate(rgb_srgb, float(recipe.get("rotate", 0)))
    rgb_srgb = _crop_rgb(rgb_srgb, recipe.get("crop", {}))

    _save_preview(rgb_srgb, out, size=size)
    stats = _compute_stats(rgb_srgb)

    return DevelopResult(
        preview_path=out,
        stats=stats,
        recipe=recipe,
        raw_metadata=raw_metadata,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Synthetic test image
# ---------------------------------------------------------------------------

def make_test_image(path: str, width: int = 6016, height: int = 4000) -> str:
    """Create a synthetic raster test scene (JPEG/PNG) for camera-free testing.

    This is NOT a real RAW file — it exercises the Pillow fallback path and the
    tone/colour operators. For real RAW decoding tests, supply an actual ARW
    (see tests/ and the A6700_SAMPLE env var).
    """
    y = np.linspace(0, 1, height)
    x = np.linspace(0, 1, width)
    x_grid, y_grid = np.meshgrid(x, y)

    sky = np.array([0.3, 0.5, 0.8])[None, None, :] * (1 - y_grid[:, :, None] * 0.6)
    ground = np.array([0.4, 0.35, 0.25])[None, None, :] * (0.5 + y_grid[:, :, None] * 0.5)
    horizon = np.clip((y_grid - 0.4) / 0.1, 0, 1)
    base = sky * (1 - horizon[:, :, None]) + ground * horizon[:, :, None]

    xx = np.linspace(0, 2 * np.pi, width)[None, :]
    yy2 = np.linspace(0, 2 * np.pi, height)[:, None]
    base[:, :, 0] = base[:, :, 0] + 0.1 * np.sin(xx * 10) * np.cos(yy2 * 8)
    base[:, :, 1] = base[:, :, 1] + 0.05 * np.cos(xx * 12)
    base[:, :, 2] = base[:, :, 2] + 0.08 * np.sin(yy2 * 15)

    cx, cy = int(width * 0.7), int(height * 0.3)
    r = min(width, height) // 6
    yy, xx = np.ogrid[:height, :width]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r ** 2
    base[mask] = np.clip(base[mask] + 0.3, 0, 1)

    rng = np.random.default_rng(42)
    base = np.clip(base + rng.normal(0, 0.015, base.shape), 0, 1)

    img = Image.fromarray((base * 255).astype(np.uint8))
    img.save(path, quality=95)
    return path


# Backwards-compatible alias (historical name; it always produced a raster).
make_test_arw = make_test_image


# ---------------------------------------------------------------------------
# Convenience CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="a6700_rawsmith — develop ARW files")
    parser.add_argument("command", choices=["develop", "auto", "inspect", "schema", "test", "to-rawsmith", "from-rawsmith"])
    parser.add_argument("input", nargs="?")
    parser.add_argument("--out", "-o", default="preview.jpg")
    parser.add_argument("--size", type=int, default=1400)
    parser.add_argument("--recipe", default=None)
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args()

    def _parse_set(recipe: Dict[str, Any]) -> None:
        for kv in args.set:
            k, v = kv.split("=", 1)
            try:
                recipe[k] = json.loads(v)
            except Exception:
                recipe[k] = v

    if args.command == "schema":
        print(json.dumps(SCHEMA, indent=2, ensure_ascii=False))
        return

    if args.command == "test":
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "a6700_test_scene.jpg")
        make_test_image(tmp)
        print(f"Test scene written: {tmp}")
        out_path = args.out if args.input else os.path.join(tempfile.gettempdir(), "a6700_preview.jpg")
        result = develop(tmp, {}, out_path, size=args.size)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        return

    if args.command in ("to-rawsmith", "from-rawsmith"):
        if not args.input or not os.path.exists(args.input):
            parser.error(f"{args.command} requires a recipe JSON file")
        data = json.load(open(args.input))
        conv = to_rawsmith_recipe(data) if args.command == "to-rawsmith" else from_rawsmith_recipe(data)
        print(json.dumps(conv, indent=2, ensure_ascii=False))
        return

    if args.command == "inspect":
        if not args.input or not os.path.exists(args.input):
            parser.error("inspect requires an input file")
        if rawpy is None:
            parser.error("rawpy not installed")
        with rawpy.imread(args.input) as raw:
            meta = {
                "file": args.input,
                "size": {
                    "sensor": f"{raw.sizes.raw_width}x{raw.sizes.raw_height}",
                    "image": f"{raw.sizes.width}x{raw.sizes.height}",
                },
                "daylight_wb": _wb_list(raw, "daylight_whitebalance"),
                "camera_wb": _wb_list(raw, "camera_whitebalance"),
            }
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        return

    if args.command == "auto":
        if not args.input or not os.path.exists(args.input):
            parser.error("auto requires an input file")
        recipe = dict(SCHEMA["default"])
        recipe.update({"exposure": 0.1, "shadows": 15, "vibrance": 10, "sharpness": 45})
        if args.recipe:
            recipe.update(json.load(open(args.recipe)))
        _parse_set(recipe)
        result = develop(args.input, recipe, args.out, size=args.size)
        recipe_path = args.input + ".a6700_rawsmith.json"
        with open(recipe_path, "w") as f:
            json.dump(result.recipe, f, ensure_ascii=False, indent=2)
        print(json.dumps(
            {"preview": result.preview_path, "recipe": recipe_path, "stats": result.stats, "warnings": result.warnings},
            indent=2, ensure_ascii=False, default=str,
        ))
        return

    if args.command == "develop":
        if not args.input or not os.path.exists(args.input):
            parser.error("develop requires an input file")
        recipe = dict(SCHEMA["default"])
        if args.recipe:
            recipe.update(json.load(open(args.recipe)))
        _parse_set(recipe)
        result = develop(args.input, recipe, args.out, size=args.size)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
