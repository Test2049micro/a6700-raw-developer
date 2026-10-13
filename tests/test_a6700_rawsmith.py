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
    make_test_arw,
    recipe_schema,
)


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
