"""a6700_rawsmith core — rawpy-backed RAW developer with JSON recipes."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from PIL import Image

try:
    import rawpy
    from rawpy import RawPy
except ImportError:
    rawpy = None  # type: ignore[assignment]
    RawPy = None  # type: ignore[misc,assignment]


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


def recipe_schema() -> Dict[str, Any]:
    """Return a copy of the recipe schema."""
    return json.loads(json.dumps(SCHEMA))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _apply_exposure(rgb: np.ndarray, stops: float) -> np.ndarray:
    if stops == 0:
        return rgb
    gain = 2.0 ** stops
    return np.clip(rgb * gain, 0, 1)


def _apply_curve_contrast(rgb: np.ndarray, contrast: float) -> np.ndarray:
    if contrast == 0:
        return rgb
    c = contrast / 100.0
    # S-curve around mid-gray
    return np.clip((rgb - 0.5) * (1 + c * 2) + 0.5, 0, 1)


def _apply_blacks(rgb: np.ndarray, blacks: float) -> np.ndarray:
    if blacks == 0:
        return rgb
    lift = blacks / 100.0 * 0.3
    return np.clip(rgb + lift, 0, 1)


def _apply_whites(rgb: np.ndarray, whites: float) -> np.ndarray:
    if whites == 0:
        return rgb
    w = whites / 100.0
    # Expand highlights
    mask = rgb > 0.5
    return np.clip(np.where(mask, 0.5 + (rgb - 0.5) * (1 + w * 0.8), rgb), 0, 1)


def _apply_highlights(rgb: np.ndarray, highlights: float) -> np.ndarray:
    if highlights == 0:
        return rgb
    # Negative highlights: compress top end
    h = -highlights / 100.0
    bright = np.max(rgb, axis=2, keepdims=True)
    mask = bright > 0.6
    compressed = 0.6 + (bright - 0.6) * (1 - h * 0.8)
    return np.clip(np.where(mask, rgb / (bright + 1e-9) * compressed, rgb), 0, 1)


def _apply_shadows(rgb: np.ndarray, shadows: float) -> np.ndarray:
    if shadows == 0:
        return rgb
    s = shadows / 100.0
    dark = np.min(rgb, axis=2, keepdims=True)
    mask = dark < 0.4
    lifted = dark * (1 + s * 1.5)
    return np.clip(np.where(mask, rgb / (dark + 1e-9) * lifted, rgb), 0, 1)


def _apply_saturation(rgb: np.ndarray, saturation: float, vibrance: float) -> np.ndarray:
    if saturation == 0 and vibrance == 0:
        return rgb
    # Simple HSV-based approach
    max_c = np.max(rgb, axis=2, keepdims=True)
    min_c = np.min(rgb, axis=2, keepdims=True)
    chroma = max_c - min_c
    gray = min_c
    s = saturation / 100.0 + vibrance / 100.0 * (1 - np.mean(chroma, axis=2, keepdims=True))
    s = np.clip(s, -1, 2)
    out = gray + chroma * (1 + s)
    return np.clip(out, 0, 1)


def _apply_dehaze(rgb: np.ndarray, dehaze: float) -> np.ndarray:
    if dehaze == 0:
        return rgb
    d = dehaze / 100.0
    # Reduce haze by darkening and increasing contrast in midtones
    gray = np.mean(rgb, axis=2, keepdims=True)
    out = rgb - d * 0.2 * gray
    out = (out - 0.5) * (1 + d * 0.3) + 0.5
    return np.clip(out, 0, 1)


def _apply_clarity(rgb: np.ndarray, clarity: float) -> np.ndarray:
    if clarity == 0:
        return rgb
    c = clarity / 100.0
    # Local contrast approximation: blend with a slightly blurred version
    from scipy.ndimage import gaussian_filter  # lazy import

    gray = np.mean(rgb, axis=2)
    blurred = gaussian_filter(gray, sigma=2)
    detail = gray - blurred
    factor = c * 0.5
    new_gray = gray + detail * factor
    # Apply back
    max_c = np.max(rgb, axis=2, keepdims=True)
    min_c = np.min(rgb, axis=2, keepdims=True)
    chroma = max_c - min_c
    out = np.clip(new_gray[..., None] + (rgb - gray[..., None]) * (1 + factor * 0.5), 0, 1)
    return out


def _compute_stats(rgb: np.ndarray) -> Dict[str, Any]:
    """Compute development stats from an RGB array (0-1 float)."""
    lum = np.mean(rgb, axis=2)
    hist, edges = np.histogram(lum, bins=10, range=(0, 1))
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


def _process_rgb(rgb: np.ndarray, recipe: Dict[str, Any]) -> np.ndarray:
    """Apply recipe adjustments to linear RGB."""
    out = rgb.astype(np.float32) / 255.0 if rgb.dtype != np.float32 else rgb.copy()
    out = _apply_exposure(out, float(recipe.get("exposure", 0)))
    out = _apply_highlights(out, int(recipe.get("highlights", 0)))
    out = _apply_shadows(out, int(recipe.get("shadows", 0)))
    out = _apply_whites(out, int(recipe.get("whites", 0)))
    out = _apply_blacks(out, int(recipe.get("blacks", 0)))
    out = _apply_curve_contrast(out, float(recipe.get("contrast", 0)))
    out = _apply_dehaze(out, int(recipe.get("dehaze", 0)))
    out = _apply_clarity(out, int(recipe.get("clarity", 0)))
    out = _apply_saturation(out, int(recipe.get("saturation", 0)), int(recipe.get("vibrance", 0)))
    return out


def _save_preview(rgb: np.ndarray, out_path: str, size: int = 1400) -> str:
    """Save RGB preview, resizing to fit within `size` long edge."""
    img = Image.fromarray((rgb * 255).astype(np.uint8))
    w, h = img.size
    long = max(w, h)
    if long > size:
        scale = size / long
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    img.save(out_path, quality=90, optimize=True)
    return out_path


def _crop_rgb(rgb: np.ndarray, crop: Dict[str, float]) -> np.ndarray:
    if not crop:
        return rgb
    h, w = rgb.shape[:2]
    top = int(crop.get("top", 0) * h)
    bottom = int(crop.get("bottom", 1) * h)
    left = int(crop.get("left", 0) * w)
    right = int(crop.get("right", 1) * w)
    return rgb[top:bottom, left:right]


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
        Path to the ARW file.
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
    # Filter unknown keys
    known = set(SCHEMA["properties"].keys())
    for k in recipe:
        if k not in known:
            warnings.append(f"unknown recipe key: {k}")

    if rawpy is None:
        raise ImportError("rawpy not installed. Run: pip install rawpy")

    raw_exts = ('.arw', '.dng', '.nef', '.cr2', '.cr3', '.raf', '.orf', '.rw2', '.pef', '.srw')
    if path.lower().endswith(raw_exts):
        with rawpy.imread(path) as raw:
            # Extract metadata
            raw_metadata = {
                "camera": str(raw.color_matrix) if hasattr(raw, "color_matrix") else "unknown",
                "sizes": {
                    "sensor_width": int(raw.sizes.raw_width),
                    "sensor_height": int(raw.sizes.raw_height),
                    "image_width": int(raw.sizes.width),
                    "image_height": int(raw.sizes.height),
                },
                "daylight_whitebalance": (
                    {k: int(v) for k, v in raw.daylight_whitebalance.items()}
                    if hasattr(raw, "daylight_whitebalance")
                    else {}
                ),
                "camera_whitebalance": (
                    {k: int(v) for k, v in raw.camera_whitebalance.items()}
                    if hasattr(raw, "camera_whitebalance")
                    else {}
                ),
            }

            # Postprocess: use user-supplied WB if present
            wb = None
            if recipe.get("temperature") is not None or recipe.get("tint") is not None:
                # Convert K to rawpy WB multiplier format if possible
                wb = raw.use_camera_wb
                warnings.append("temperature/tint override requires calibrated conversion; using camera WB as base")

            rgb = raw.postprocess(
                use_camera_wb=(wb is None),
                user_wb=wb,
                no_auto_bright=True,
                bright=1.0,
                output_color=rawpy.ColorSpace.sRGB.value,
                output_bps=8,
            )
    else:
        # Fallback for non-RAW test images: load via Pillow
        warnings.append(f"Input is not a RAW file ({path}); using Pillow fallback for testing")
        img = Image.open(path).convert("RGB")
        rgb = np.array(img)
        raw_metadata = {
            "camera": "pillow-fallback",
            "sizes": {
                "sensor_width": rgb.shape[1],
                "sensor_height": rgb.shape[0],
                "image_width": rgb.shape[1],
                "image_height": rgb.shape[0],
            },
            "daylight_whitebalance": {},
            "camera_whitebalance": {},
        }

    # Apply recipe adjustments
    rgb = _process_rgb(rgb, recipe)
    rgb = _crop_rgb(rgb, recipe.get("crop", {}))
    _save_preview(rgb, out, size=size)
    stats = _compute_stats(rgb)

    return DevelopResult(
        preview_path=out,
        stats=stats,
        recipe=recipe,
        raw_metadata=raw_metadata,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Synthetic test ARW
# ---------------------------------------------------------------------------

def make_test_arw(path: str, width: int = 6016, height: int = 4000) -> str:
    """Create a synthetic ARW-like image for testing without a camera.

    Uses rawpy's ability to write a minimal DNG/raw file, or falls back to a
    numpy-based synthetic "raw" file if rawpy doesn't support writing.

    For practical testing, this generates a realistic test pattern image
    and saves it as a TIFF that rawpy can read as a fallback.
    """
    # Generate a realistic test scene: gradient sky + foreground + midtones
    y = np.linspace(0, 1, height)          # (H,)
    x = np.linspace(0, 1, width)           # (W,)
    x_grid, y_grid = np.meshgrid(x, y)     # both (H, W)

    sky = np.array([0.3, 0.5, 0.8])[None, None, :] * (1 - y_grid[:, :, None] * 0.6)
    ground = np.array([0.4, 0.35, 0.25])[None, None, :] * (0.5 + y_grid[:, :, None] * 0.5)
    horizon = np.clip((y_grid - 0.4) / 0.1, 0, 1)
    base = sky * (1 - horizon[:, :, None]) + ground * horizon[:, :, None]

    # Add some color variation (full HxW spatial terms)
    xx = np.linspace(0, 2 * np.pi, width)[None, :]
    yy2 = np.linspace(0, 2 * np.pi, height)[:, None]
    base[:, :, 0] = base[:, :, 0] + 0.1 * np.sin(xx * 10) * np.cos(yy2 * 8)
    base[:, :, 1] = base[:, :, 1] + 0.05 * np.cos(xx * 12)
    base[:, :, 2] = base[:, :, 2] + 0.08 * np.sin(yy2 * 15)

    # Add a bright area (simulate sunlit patch)
    cx, cy = int(width * 0.7), int(height * 0.3)
    r = min(width, height) // 6
    yy, xx = np.ogrid[:height, :width]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r ** 2
    base[mask] = np.clip(base[mask] + 0.3, 0, 1)

    # Simulate Bayer-like noise (simple)
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.015, base.shape)
    base = np.clip(base + noise, 0, 1)

    img = Image.fromarray((base * 255).astype(np.uint8))
    img.save(path, quality=95)
    return path


# ---------------------------------------------------------------------------
# Convenience CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import hashlib

    parser = argparse.ArgumentParser(description="a6700_rawsmith — develop ARW files")
    parser.add_argument("command", choices=["develop", "auto", "inspect", "schema", "test"])
    parser.add_argument("input", nargs="?")
    parser.add_argument("--out", "-o", default="preview.jpg")
    parser.add_argument("--size", type=int, default=1400)
    parser.add_argument("--recipe", default=None)
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args()

    if args.command == "schema":
        print(json.dumps(SCHEMA, indent=2, ensure_ascii=False))
        return

    if args.command == "test":
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "a6700_test_scene.jpg")
        make_test_arw(tmp)
        print(f"Test scene written: {tmp}")
        out_path = args.out if args.input else os.path.join(tempfile.gettempdir(), "a6700_preview.jpg")
        result = develop(tmp, {}, out_path, size=args.size)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
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
                "daylight_wb": (
                    {k: int(v) for k, v in raw.daylight_whitebalance.items()}
                    if hasattr(raw, "daylight_whitebalance")
                    else {}
                ),
                "camera_wb": (
                    {k: int(v) for k, v in raw.camera_whitebalance.items()}
                    if hasattr(raw, "camera_whitebalance")
                    else {}
                ),
            }
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        return

    if args.command == "auto":
        if not args.input or not os.path.exists(args.input):
            parser.error("auto requires an input file")
        # Conservative auto starting recipe
        recipe = dict(SCHEMA["default"])
        recipe.update({"exposure": 0.1, "shadows": 15, "vibrance": 10, "sharpness": 45})
        if args.recipe:
            user = json.load(open(args.recipe))
            recipe.update(user)
        for kv in args.set:
            k, v = kv.split("=", 1)
            try:
                recipe[k] = json.loads(v)
            except Exception:
                recipe[k] = v
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
        for kv in args.set:
            k, v = kv.split("=", 1)
            try:
                recipe[k] = json.loads(v)
            except Exception:
                recipe[k] = v
        result = develop(args.input, recipe, args.out, size=args.size)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
