# a6700_rawsmith

AI-friendly RAW developer for Sony α6700 ARW files, inspired by [rawsmith](https://github.com/Lewin671/rawsmith).

## Why this exists

rawsmith only supports Sony A7 IV. This package targets the α6700's ARW files and adds a Python API, Pillow fallback for testing, and an agent-friendly JSON recipe system.

## Install

```bash
pip install a6700_rawsmith
```

Requires Python >= 3.10. On first run, `rawpy` will pull in `libraw` automatically.

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
make_test_arw("/tmp/test_scene.jpg", width=6016, height=4000)

# Develop with a recipe
result = develop(
    "/tmp/test_scene.jpg",
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

See `SCHEMA` or `recipe_schema()` for the full schema with all parameters and defaults.

## Testing without a camera

`make_test_arw()` generates a synthetic gradient scene at full α6700 resolution (6016×4000). For non-RAW files, `develop()` falls back to Pillow automatically, making it easy to test the full pipeline without rawpy.

```python
from a6700_rawsmith import make_test_arw, develop

test_path = "/tmp/synthetic.jpg"
make_test_arw(test_path, width=800, height=600)
result = develop(test_path, {"exposure": 0.5}, "/tmp/out.jpg")
```

## Integration with a6700_tool.py

For camera control (USB remote shooting), see `a6700_tool.py` in the same repo. The typical workflow:

1. Use `a6700_tool.py` to set camera parameters via gphoto2
2. Capture ARW files to disk
3. Use `a6700_rawsmith` to develop them with recipes

## Roadmap

- [ ] Real ARW file testing with α6700
- [ ] Fujifilm LUT 3D application pipeline (`.cube` LUTs)
- [ ] Temperature/tint calibration for α6700 sensor
- [ ] Batch develop command
- [ ] Recipe diff/merge tool

## License

MIT
