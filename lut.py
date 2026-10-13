"""Fujifilm .cube LUT 3D application pipeline for a6700_rawsmith.

Supports:
- .cube LUT parsing (3D LUT format)
- Trilinear interpolation
- S-Log3 input gamma handling (Fujifilm LUT convention)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# S-Log3 transfer function (Fujifilm)
# ---------------------------------------------------------------------------

def _slog3_to_linear(img: np.ndarray) -> np.ndarray:
    """Convert S-Log3 encoded image to linear light.

    S-Log3 is Fujifilm's logarithmic gamma. This approximation follows
    the official S-Log3 encoding/decoding curve.
    """
    # S-Log3 decoding (log -> linear)
    # Valid for values in [0, 1] representing normalized S-Log3 signal
    img = np.clip(img, 0, 1)
    # S-Log3 formula: linear = (10**((normalized * 0.9 - 0.386) / 0.428)) - 0.037
    # Clamp to avoid negative
    linear = np.power(10.0, (img * 0.9 - 0.386) / 0.428) - 0.037
    return np.clip(linear, 0, 1)


def _linear_to_slog3(img: np.ndarray) -> np.ndarray:
    """Convert linear light to S-Log3 encoded image."""
    img = np.clip(img, 0, 1)
    # S-Log3 encoding: log = (np.log10(linear + 0.037) * 0.428 + 0.386) / 0.9
    slog3 = (np.log10(img + 0.037) * 0.428 + 0.386) / 0.9
    return np.clip(slog3, 0, 1)


# ---------------------------------------------------------------------------
# .cube parser
# ---------------------------------------------------------------------------

@dataclass
class CubeLUT:
    """Parsed .cube 3D LUT."""
    title: str
    size: int
    domain_min: Tuple[float, float, float]
    domain_max: Tuple[float, float, float]
    data: np.ndarray  # shape (size, size, size, 3)

    @staticmethod
    def from_file(path: str) -> "CubeLUT":
        """Load a .cube LUT file."""
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        title = ""
        size = 0
        domain_min = (0.0, 0.0, 0.0)
        domain_max = (1.0, 1.0, 1.0)
        data_start = 0

        for i, line in enumerate(lines):
            lower = line.lower()
            if lower.startswith("title"):
                title = line.split(" ", 1)[1].strip().strip('"')
            elif lower.startswith("lut_3d_size"):
                size = int(line.split(" ")[1])
            elif lower.startswith("domain_min"):
                parts = line.split()
                domain_min = (float(parts[1]), float(parts[2]), float(parts[3]))
            elif lower.startswith("domain_max"):
                parts = line.split()
                domain_max = (float(parts[1]), float(parts[2]), float(parts[3]))
            else:
                # First non-keyword line marks start of data
                data_start = i
                break

        # Parse RGB triples
        entries = []
        for line in lines[data_start:]:
            parts = line.split()
            if len(parts) < 3:
                continue
            entries.append([float(parts[0]), float(parts[1]), float(parts[2])])

        expected = size * size * size
        if len(entries) != expected:
            raise ValueError(
                f"LUT data mismatch: expected {expected} entries for size {size}, got {len(entries)}"
            )

        arr = np.array(entries, dtype=np.float32).reshape(size, size, size, 3)
        # .cube stores rows with red changing fastest, so the reshape yields a
        # [B, G, R, channel] layout. The sampler indexes data[R, G, B], so
        # transpose the spatial axes. (Identity LUTs are symmetric and mask
        # this bug; asymmetric LUTs expose it.)
        arr = arr.transpose(2, 1, 0, 3)
        return CubeLUT(
            title=title,
            size=size,
            domain_min=domain_min,
            domain_max=domain_max,
            data=arr,
        )


# ---------------------------------------------------------------------------
# Trilinear interpolation
# ---------------------------------------------------------------------------

def _trilinear_sample(lut: CubeLUT, rgb: np.ndarray) -> np.ndarray:
    """Apply LUT using trilinear interpolation.

    Parameters
    ----------
    lut : CubeLUT
    rgb : np.ndarray
        Float RGB in [0, 1], shape (H, W, 3).

    Returns
    -------
    np.ndarray
        Transformed RGB, same shape, clipped to [0, 1].
    """
    size = lut.size
    data = lut.data
    dmin = np.array(lut.domain_min, dtype=np.float32)
    dmax = np.array(lut.domain_max, dtype=np.float32)

    # Normalize input to LUT index space
    scale = (size - 1) / (dmax - dmin)
    idx = (rgb - dmin) * scale

    # Clamp to valid range
    idx = np.clip(idx, 0, size - 1.001)

    i0 = np.floor(idx).astype(np.int32)
    i1 = np.minimum(i0 + 1, size - 1)
    frac = idx - i0  # fractional part
    # Keep the trailing channel axis so (H,W,3) weights broadcast against the
    # (H,W,3) corner samples; a bare (H,W) would fail to align.
    f0 = frac[..., 0:1]
    f1 = frac[..., 1:2]
    f2 = frac[..., 2:3]

    # Trilinear interpolation: 8 corners
    out = (
        data[i0[..., 0], i0[..., 1], i0[..., 2]] * (1 - f0) * (1 - f1) * (1 - f2)
        + data[i1[..., 0], i0[..., 1], i0[..., 2]] * f0 * (1 - f1) * (1 - f2)
        + data[i0[..., 0], i1[..., 1], i0[..., 2]] * (1 - f0) * f1 * (1 - f2)
        + data[i0[..., 0], i0[..., 1], i1[..., 2]] * (1 - f0) * (1 - f1) * f2
        + data[i1[..., 0], i1[..., 1], i0[..., 2]] * f0 * f1 * (1 - f2)
        + data[i1[..., 0], i0[..., 1], i1[..., 2]] * f0 * (1 - f1) * f2
        + data[i0[..., 0], i1[..., 1], i1[..., 2]] * (1 - f0) * f1 * f2
        + data[i1[..., 0], i1[..., 1], i1[..., 2]] * f0 * f1 * f2
    )
    return np.clip(out, 0, 1)


# ---------------------------------------------------------------------------
# High-level apply
# ---------------------------------------------------------------------------

def apply_lut(
    rgb: np.ndarray,
    lut_path: str,
    input_srgb: bool = True,
    output_srgb: bool = True,
) -> np.ndarray:
    """Apply a .cube LUT to an RGB image.

    Parameters
    ----------
    rgb : np.ndarray
        Input RGB, float in [0, 1] or uint8, shape (H, W, 3).
    lut_path : str
        Path to .cube LUT file.
    input_srgb : bool
        If True, assume input is sRGB and convert to linear before LUT.
        Set to False if input is already linear.
    output_srgb : bool
        If True, convert LUT output back to sRGB gamma.

    Returns
    -------
    np.ndarray
        Transformed RGB, float in [0, 1], shape (H, W, 3).
    """
    if not os.path.exists(lut_path):
        raise FileNotFoundError(f"LUT file not found: {lut_path}")

    lut = CubeLUT.from_file(lut_path)

    # Normalize to float [0, 1]
    if rgb.dtype == np.uint8:
        rgb = rgb.astype(np.float32) / 255.0
    else:
        rgb = rgb.astype(np.float32)
        rgb = np.clip(rgb, 0, 1)

    # Convert sRGB -> linear if needed
    if input_srgb:
        # Simple sRGB gamma decode
        mask = rgb >= 0.04045
        linear = np.zeros_like(rgb, dtype=np.float32)
        linear[mask] = ((rgb[mask] + 0.055) / 1.055) ** 2.4
        linear[~mask] = rgb[~mask] / 12.92
        rgb = linear

    # Apply S-Log3 conversion if LUT expects log input
    # Fujifilm LUTs often assume S-Log3 source; detect from domain_min
    if lut.domain_min[0] < -0.1 or lut.domain_min[1] < -0.1 or lut.domain_min[2] < -0.1:
        # Negative domain min suggests S-Log3 range
        rgb = _slog3_to_linear(rgb)

    # Apply LUT
    rgb = _trilinear_sample(lut, rgb)

    # Convert linear -> sRGB if needed
    if output_srgb:
        mask = rgb >= 0.0031308
        srgb = np.zeros_like(rgb, dtype=np.float32)
        srgb[mask] = 1.055 * (rgb[mask] ** (1.0 / 2.4)) - 0.055
        srgb[~mask] = 12.92 * rgb[~mask]
        rgb = srgb

    return np.clip(rgb, 0, 1)
