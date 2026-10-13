# a6700_rawsmith

AI-friendly RAW developer for Sony α6700 ARW files, inspired by [rawsmith](https://github.com/Lewin671/rawsmith).

## Why this exists

rawsmith only supports Sony A7 IV. This package targets the α6700's ARW files and adds a Python API, Pillow fallback for testing, and an agent-friendly JSON recipe system.

## Install

```bash
pip install a6700_rawsmith
```

## Quick start

```bash
# Run the test suite
python -m pytest tests -q

# Generate a synthetic test image and develop it
python -m a6700_rawsmith test --size 1200

# Develop a real ARW file
python -m a6700_rawsmith develop DSC01234.ARW -o preview.jpg

# Auto-develop with a conservative starting recipe
python -m a6700_rawsmith auto DSC01234.ARW -o preview.jpg

# Inspect RAW metadata
python -m a6700_rawsmith inspect DSC01234.ARW

# Print the recipe schema
python -m a6700_rawsmith schema
```

## Python API

```python
from a6700_rawsmith import develop, make_test_arw, SCHEMA

# Generate a synthetic test image (no camera needed)
test_path = "/tmp/test_scene.jpg"
make_test_arw(test_path, width=6016, height=4000)

# Develop with a recipe
result = develop(
    test_path,
    recipe={"exposure": 0.3, "shadows": 20, "vibrance": 15},
    out="/tmp/preview.jpg",
    size=1400,
)

print(result.stats)
print(result.warnings)
```

## Recipe format

Recipes are JSON objects with adjustment parameters. Only changed parameters need to be specified; defaults are merged automatically.

```json
{
  "exposure": 0.2,
  "highlights": -30,
  "shadows": 25,
  "whites": 10,
  "blacks": -5,
  "contrast": 15,
  "clarity": 30,
  "dehaze": 10,
  "saturation": 5,
  "vibrance": 15,
  "sharpness": 50,
  "crop": {
    "top": 0.05,
    "bottom": 0.95,
    "left": 0.0,
    "right": 1.0
  },
  "a6700": {
    "creative_look": "ST",
    "picture_profile": "PP3"
  }
}
```

### Available parameters

| Parameter | Type | Range | Default |
|-----------|------|-------|---------|
| `exposure` | number | -5 to 5 | 0 |
| `temperature` | number/null | 2000-10000K | null |
| `tint` | number/null | -100 to 100 | null |
| `highlights` | integer | -100 to 100 | 0 |
| `shadows` | integer | -100 to 100 | 0 |
| `whites` | integer | -100 to 100 | 0 |
| `blacks` | integer | -100 to 100 | 0 |
| `contrast` | integer | -100 to 100 | 0 |
| `clarity` | integer | -100 to 100 | 0 |
| `dehaze` | integer | -100 to 100 | 0 |
| `saturation` | integer | -100 to 100 | 0 |
| `vibrance` | integer | -100 to 100 | 0 |
| `sharpness` | integer | 0 to 100 | 40 |
| `denoise` | integer | 0 to 100 | 10 |
| `rotate` | number | -45 to 45 | 0 |
| `crop` | object | normalized 0-1 | {} |
| `hsl` | object | per-band adjustments | {} |
| `grade` | object | shadows/highlights hue+sat | {} |
| `a6700.creative_look` | string | ST/PT/NT/VV/VV2/FL/IN/SH/BW/SE | - |
| `a6700.picture_profile` | string | PP1-6/10/11 | - |
| `rotate` | number | -45 to 45 degrees | 0 |
| `a6700.fuji_lut_path` | string | path to `.cube` LUT | - |

## Adjustments

### Rotation

Use `rotate` to correct horizon or composition without leaving the RAW pipeline:

```json
{
  "rotate": 1.5
}
```

The rotation is applied in linear RGB before preview save, using nearest-edge interpolation.

### Temperature and tint

`temperature` is expressed in Kelvin, and `tint` is a simplified green-magenta shift. These are lightweight channel-shift approximations, not full RAW processor WB curves:

```json
{
  "temperature": 6500,
  "tint": 10
}
```

### Fujifilm LUT

If you have a `.cube` LUT, point `a6700.fuji_lut_path` to it. The LUT is applied in sRGB space and failures are swallowed silently so recipes stay portable across machines:

```json
{
  "a6700": {
    "fuji_lut_path": "/Users/f/LUTs/FujiClassic.cube"
  }
}
```

## Testing without a camera

`make_test_arw()` generates a synthetic gradient scene at full α6700 resolution (6016×4000). For non-RAW files, `develop()` falls back to Pillow automatically, making it easy to test the full pipeline without rawpy.

```python
from a6700_rawsmith import make_test_arw, develop

test_path = "/tmp/synthetic.jpg"
make_test_arw(test_path, width=800, height=600)
result = develop(test_path, {"exposure": 0.5}, "/tmp/out.jpg")
```

## Integration with a6700_tool.py

For camera control (USB remote shooting), see `a6700_tool.py` in the same directory. The typical workflow:

1. Use `a6700_tool.py` to set camera parameters via gphoto2
2. Capture ARW files to disk
3. Use `a6700_rawsmith` to develop them with recipes

## Roadmap

- [ ] Real ARW file testing with α6700
- [ ] Fujifilm LUT 3D application pipeline (`.cube` LUTs)
- [ ] Temperature/tint calibration for α6700 sensor
- [ ] Batch develop command
- [ ] Recipe diff/merge tool

## Credits

Thanks to my friend for the skill that inspired parts of this project.
