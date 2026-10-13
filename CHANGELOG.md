# Changelog

## 0.2.0 — 2026-10-13 (the birthday release 🎂)

A correctness pass: the package now actually develops real α6700 files, and
every recipe knob does something.

- **Fixed: crash on real ARW.** `develop`/`inspect` raised `AttributeError`
  because rawpy returns white-balance as a list, not a dict (`.items()`). The
  whole test suite had been passing only via the Pillow fallback, so this was
  invisible until a real CC0 α6700 sample was decoded.
- **Fixed: dead parameters.** `sharpness`, `denoise`, `hsl`, and `grade` were
  declared in the schema but silently ignored by the pipeline. All four are now
  implemented (unsharp mask, chroma denoise, per-band HSL, split toning) and
  guarded by regression tests that fail if any knob stops moving pixels.
- **Scene-linear pipeline.** rawpy now decodes to 16-bit linear (gamma 1.0);
  tone/colour run in linear light; sRGB gamma is applied only at the end.
- **White balance from temperature/tint is real.** A monotonic, bounded gain
  anchored on the camera daylight WB (the old version was a fixed channel
  shift with a warning). Fallback and RAW paths now behave identically.
- **Fujifilm LUT actually works.** Fixed a broadcast bug in the trilinear
  sampler; LUT failures are now reported in `warnings` instead of being
  swallowed by a bare `except: pass`. Ships a working Classic-Chrome-style
  `.cube` in `examples/luts/`.
- **rawsmith recipe interop.** `to-rawsmith` / `from-rawsmith` convert recipes
  between the two projects (shared keys round-trip losslessly).
- **Real-sample testing.** `scripts/fetch_sample.sh` pulls a CC0 α6700 ARW from
  raw.pixls.us; real-RAW tests run when the sample is present and skip (not
  fail) otherwise, so CI stays green without a 43 MB download.
- **Metadata.** `inspect`/`develop` now report ISO / shutter / aperture / focal
  length / timestamp (numbers an AI assistant can reason about).
- **Docs.** Bilingual README with a before/after comparison from a real file;
  honest limits section; roadmap updated.

## 0.1.0 — 2026-10-13

- Initial package structure with `pyproject.toml` and tests.
- Core RAW development via `rawpy` with Pillow fallback for testing.
- JSON recipe system with schema validation.
- Synthetic test scene generator `make_test_arw()`.
- `.cube` LUT 3D parsing and trilinear interpolation.
- CLI commands: `develop`, `auto`, `inspect`, `schema`, `test`.
