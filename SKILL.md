# a6700_rawsmith — agent skill

AI-friendly RAW developer for **Sony α6700** ARW files. Python sister project to
[rawsmith](https://github.com/Lewin671/rawsmith) (a Rust engine that avoids
LibRaw); this one is **LibRaw-backed via rawpy** and adds a Fujifilm `.cube` LUT
look. Recipes are interchangeable with rawsmith.

## Every command prints JSON · 所有命令输出 JSON

```bash
a6700_rawsmith develop FILE.ARW -o preview.jpg [--recipe r.json] [--set exposure=0.3] [--size 1400]
a6700_rawsmith auto FILE.ARW -o preview.jpg          # conservative starting recipe
a6700_rawsmith inspect FILE.ARW                       # sizes, WB, ISO/shutter/aperture/focal
a6700_rawsmith schema                                 # full recipe schema + defaults
a6700_rawsmith to-rawsmith recipe.json                # → rawsmith-compatible recipe
a6700_rawsmith from-rawsmith rawsmith.json            # → a6700 recipe
a6700_rawsmith test                                   # synthetic scene, no camera needed
```

## The edit loop · 编辑循环

1. `auto` a file → read the returned `stats` and look at `preview.jpg`.
2. Change recipe values (or `--set key=value`), `develop` again.
3. Compare `stats.tones` / `clipped_highlights_pct` / `zones` to judge the edit.
4. Repeat until the picture is right. The RAW is never modified — only the
   recipe changes.

### What `stats` tells you · stats 的含义

- `tones.p50/p95/spread` — luminance percentiles (0–100); p50 near 18 ≈ mid-grey.
- `clipped_highlights_pct` / `crushed_shadows_pct` — % of pixels blown/crushed.
- `zones` — 3×3 brightness grid, top-left → bottom-right.

### Warnings are not hidden · 警告不隐藏

`result.warnings` lists unknown recipe keys, white-balance fallbacks, and
**LUT failures** (a bad `.cube` path is reported, never silently swallowed).
Always read `warnings` before trusting a preview.

## Recipe knobs · 可调参数

Tone: `exposure` (−5..5), `highlights`/`shadows`/`whites`/`blacks`/`contrast`
(−100..100). Local: `clarity`, `dehaze` (−100..100). Colour: `saturation`,
`vibrance`, `temperature` (2000..10000K), `tint`, `hsl` (per-band),
`grade` (split tone). Detail: `sharpness`, `denoise` (0..100). Geometry:
`rotate`, `crop`. Look: `a6700.fuji_lut_path` (`.cube`). See `a6700_rawsmith
schema` for the authoritative list.

## Limits to state honestly · 如实告知的边界

- White balance is a bounded approximation, not a full chromaticity solve.
- No lens distortion/vignetting correction.
- Adjustments run in scene-linear space; output is sRGB 8-bit preview.

## Testing without a camera · 无相机测试

`make_test_image()` returns a synthetic raster (not a real RAW). Real-α6700
decode tests skip unless `A6700_SAMPLE` points at an ARW (fetch a CC0 sample via
`scripts/fetch_sample.sh`).
