"""Tests for a6700_rawsmith."""

import json
import os
import sys
import tempfile

import pytest

# Ensure package import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a6700_rawsmith import (  # noqa: E402
    SCHEMA,
    DevelopResult,
    _compute_stats,
    _process_rgb,
    develop,
    from_rawsmith_recipe,
    make_test_arw,
    make_test_image,
    recipe_schema,
    to_rawsmith_recipe,
)

# A real Sony α6700 ARW, if available locally. raw.pixls.us hosts CC0 samples;
# see scripts/fetch_sample.sh. When absent, RAW-decode tests are skipped so the
# suite still runs in CI without a 43 MB download.
SAMPLE_ARW = os.environ.get("A6700_SAMPLE", os.path.join(os.path.dirname(__file__), "fixtures", "ILCE-6700.ARW"))
HAS_SAMPLE = os.path.exists(SAMPLE_ARW)
needs_sample = pytest.mark.skipif(not HAS_SAMPLE, reason="real α6700 ARW sample not present (set A6700_SAMPLE or run scripts/fetch_sample.sh)")


def test_recipe_schema_returns_dict():
    schema = recipe_schema()
    assert isinstance(schema, dict)
    assert schema["title"] == "A6700 RAW Recipe"
    assert "exposure" in schema["properties"]
    assert "a6700" in schema["properties"]


def test_default_schema_has_required_keys():
    default = SCHEMA["default"]
    assert "exposure" in default
    assert "a6700" in default
    assert default["exposure"] == 0
    assert default["sharpness"] == 40


def test_make_test_arw_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.jpg")
        result = make_test_arw(path)
        assert os.path.exists(path)
        assert path == result


def test_compute_stats_returns_expected_keys():
    import numpy as np

    rgb = np.random.default_rng(0).random((100, 100, 3)).astype(np.float32)
    stats = _compute_stats(rgb)
    assert "tones" in stats
    assert "histogram" in stats
    assert "clipped_highlights_pct" in stats
    assert "crushed_shadows_pct" in stats
    assert "zones" in stats
    assert len(stats["histogram"]) == 10
    assert len(stats["zones"]) == 9


def test_process_rgb_identity():
    import numpy as np

    rgb = np.random.default_rng(1).random((50, 50, 3)).astype(np.float32)
    recipe = {
        "exposure": 0,
        "highlights": 0,
        "shadows": 0,
        "whites": 0,
        "blacks": 0,
        "contrast": 0,
        "dehaze": 0,
        "clarity": 0,
        "saturation": 0,
        "vibrance": 0,
    }
    out = _process_rgb(rgb, recipe)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_develop_with_synthetic_image():
    with tempfile.TemporaryDirectory() as tmp:
        test_img = os.path.join(tmp, "synthetic.jpg")
        make_test_arw(test_img, width=400, height=300)
        out_path = os.path.join(tmp, "preview.jpg")
        result = develop(test_img, {}, out_path, size=400)
        assert isinstance(result, DevelopResult)
        assert os.path.exists(out_path)
        assert "preview_path" in result.to_dict()
        assert "stats" in result.to_dict()
        assert "recipe" in result.to_dict()
        assert "warnings" in result.to_dict()
        assert result.preview_path == out_path


def test_develop_with_recipe_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        test_img = os.path.join(tmp, "synthetic.jpg")
        make_test_arw(test_img, width=400, height=300)
        out_path = os.path.join(tmp, "preview_recipe.jpg")
        recipe = {"exposure": 0.5, "contrast": 20, "shadows": 30}
        result = develop(test_img, recipe, out_path, size=400)
        assert result.recipe["exposure"] == 0.5
        assert result.recipe["contrast"] == 20
        assert result.recipe["shadows"] == 30


def test_develop_result_to_dict_serializable():
    result = DevelopResult(
        preview_path="/tmp/test.jpg",
        stats={"tones": {"p50": 50.0}},
        recipe={"exposure": 0},
        raw_metadata={},
        warnings=["test warning"],
    )
    d = result.to_dict()
    assert isinstance(d, dict)
    assert d["warnings"] == ["test warning"]
    # Ensure JSON serializable
    json.dumps(d)


# ---------------------------------------------------------------------------
# Dead-parameter regression: every schema knob must actually move pixels.
# Guards against the pre-rewrite bug where sharpness/denoise/hsl/grade were
# declared in SCHEMA but silently ignored by the pipeline.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,recipe", [
    ("sharpness", {"sharpness": 100}),
    ("denoise", {"denoise": 100}),
    ("hsl", {"hsl": {"red": {"saturation": 100}}}),
    ("grade", {"grade": {"shadows": {"hue": 200, "sat": 0.6}}}),
    ("temperature", {"temperature": 3000}),
])
def test_recipe_knob_changes_output(key, recipe):
    """Each adjustment must produce a different image than the neutral recipe."""
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        src = make_test_image(os.path.join(tmp, "s.jpg"), width=320, height=240)
        base = os.path.join(tmp, "base.jpg")
        changed = os.path.join(tmp, "chg.jpg")
        develop(src, {}, base, size=320)
        develop(src, recipe, changed, size=320)
        a = np.asarray(Image.open(base), dtype=np.float32)
        b = np.asarray(Image.open(changed), dtype=np.float32)
        assert not np.array_equal(a, b), f"{key} is a no-op (dead parameter regression)"


def test_make_test_image_alias_exists():
    """make_test_arw stays as a backwards-compatible alias for make_test_image."""
    assert make_test_arw is make_test_image


# ---------------------------------------------------------------------------
# White balance: monotonic in colour temperature, no divergence.
# ---------------------------------------------------------------------------

def test_white_balance_monotonic():
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        src = make_test_image(os.path.join(tmp, "s.jpg"), width=240, height=160)
        rbs = []
        for k in (2500, 4500, 6500, 8500):
            out = os.path.join(tmp, f"wb{k}.jpg")
            develop(src, {"temperature": k}, out, size=240)
            px = np.asarray(Image.open(out), dtype=np.float32)
            rbs.append(float(px[..., 0].mean() - px[..., 2].mean()))
        # R-B must increase monotonically with temperature (warm target -> cooler image)
        assert all(rbs[i] < rbs[i + 1] for i in range(len(rbs) - 1)), f"WB not monotonic: {rbs}"


# ---------------------------------------------------------------------------
# rawsmith recipe interoperability.
# ---------------------------------------------------------------------------

def test_to_rawsmith_recipe_only_shared_keys():
    recipe = {"exposure": 0.5, "shadows": 30, "vibrance": 25, "crop": {"top": 0.1, "bottom": 0.9},
              "temperature": 3200, "hsl": {"red": {"hue": 10}}, "a6700": {"picture_profile": "PP11"}}
    rs = to_rawsmith_recipe(recipe)
    assert rs["rawsmith"] == "0.1.0"
    assert rs["exposure"] == 0.5
    assert rs["shadows"] == 30
    assert "crop" in rs and rs["crop"]["top"] == 0.1
    # a6700-only extensions must not leak into a rawsmith recipe
    assert "temperature" not in rs
    assert "hsl" not in rs
    assert "a6700" not in rs


def test_recipe_roundtrip_preserves_shared():
    original = {"exposure": 0.3, "highlights": -20, "shadows": 40, "contrast": 10, "vibrance": 15}
    back = from_rawsmith_recipe(to_rawsmith_recipe(original))
    for k, v in original.items():
        assert back[k] == v, f"{k} lost in round-trip"


# ---------------------------------------------------------------------------
# Real α6700 RAW decode (skipped unless a sample is present).
# ---------------------------------------------------------------------------

@needs_sample
def test_real_arw_develops():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "real.jpg")
        result = develop(SAMPLE_ARW, {"exposure": 0.2, "vibrance": 15}, out, size=800)
        assert os.path.exists(out)
        assert result.raw_metadata["sizes"]["image_width"] > 4000
        assert result.warnings == []


@needs_sample
def test_real_arw_inspect_no_crash():
    """Regression: develop/inspect crashed on real ARW because rawpy returns
    white-balance as a list, not a dict (.items() raised AttributeError)."""
    result = develop(SAMPLE_ARW, {}, "/tmp/_real_inspect.jpg", size=400)
    assert isinstance(result.raw_metadata["daylight_whitebalance"], list)


def test_lut_axis_order_asymmetric():
    """A .cube that swaps R and G must map input [1,0,0] -> output [0,1,0].

    Guards the [B,G,R] reshape vs data[R,G,B] indexing bug: an identity LUT is
    symmetric and cannot catch it, so use an asymmetric one.
    """
    import numpy as np
    from lut import apply_lut
    N = 17
    lines = ["LUT_3D_SIZE %d" % N]
    for b in range(N):
        for g in range(N):
            for r in range(N):
                lines.append("%.6f %.6f %.6f" % (g / (N - 1), r / (N - 1), b / (N - 1)))
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "swap.cube")
        with open(p, "w") as f:
            f.write("\n".join(lines) + "\n")
        out = apply_lut(np.array([[[1.0, 0.0, 0.0]]], dtype=np.float32), p,
                        input_srgb=False, output_srgb=False)
        assert abs(out[0, 0, 1] - 1.0) < 0.05 and abs(out[0, 0, 0]) < 0.05
