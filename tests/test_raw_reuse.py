"""Real RAW reuse: repeated edits avoid decoding but never reuse wrong pixels."""
import os
from pathlib import Path
import numpy as np
import pytest
import a6700_rawsmith as m
from test_a6700_rawsmith import SAMPLE_ARW, needs_sample

@pytest.fixture(autouse=True)
def isolated_raw_cache():
    m.clear_raw_cache()
    yield
    m.clear_raw_cache()


@needs_sample
def test_repeated_raw_edits_decode_once_and_match_uncached(tmp_path, monkeypatch):
    original = m.rawpy.imread
    calls = []
    def read(*a, **kw):
        calls.append(a[0])
        return original(*a, **kw)
    monkeypatch.setattr(m.rawpy, 'imread', read)
    first = m.develop(SAMPLE_ARW, {}, str(tmp_path/'first.jpg'), size=800)
    second = m.develop(SAMPLE_ARW, {'exposure': 0.4}, str(tmp_path/'second.jpg'), size=800)
    assert len(calls) == 1, 'same RAW/size/WB must reuse decode across edits'
    m.clear_raw_cache()
    fresh = m.develop(SAMPLE_ARW, {'exposure': 0.4}, str(tmp_path/'fresh.jpg'), size=800)
    assert (tmp_path/'second.jpg').read_bytes() == (tmp_path/'fresh.jpg').read_bytes()
    assert second.stats == fresh.stats
    assert (tmp_path/'first.jpg').read_bytes() != (tmp_path/'second.jpg').read_bytes()
    assert first.raw_metadata == fresh.raw_metadata
    m.clear_raw_cache()

@needs_sample
def test_cache_invalidates_size_whitebalance_and_file_change(tmp_path, monkeypatch):
    import shutil
    source=tmp_path/'sample.ARW'; shutil.copyfile(SAMPLE_ARW,source)
    original=m.rawpy.imread; calls=[]
    def read(*a,**kw):
        calls.append(a[0]);return original(*a,**kw)
    monkeypatch.setattr(m.rawpy,'imread',read)
    for size,recipe in [(600,{}),(900,{}),(900,{'temperature':5000})]:
        m.develop(str(source),recipe,str(tmp_path/'out.jpg'),size=size)
    assert len(calls)==3
    stat=source.stat(); os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    m.develop(str(source),{'temperature':5000},str(tmp_path/'out.jpg'),size=900)
    assert len(calls)==4
    m.clear_raw_cache()

@needs_sample
def test_returned_metadata_cannot_poison_cached_result(tmp_path):
    one=m.develop(SAMPLE_ARW,{},str(tmp_path/'one.jpg'),size=400)
    width=one.raw_metadata['sizes']['image_width']
    one.raw_metadata['sizes']['image_width']=-1
    two=m.develop(SAMPLE_ARW,{},str(tmp_path/'two.jpg'),size=400)
    assert two.raw_metadata['sizes']['image_width']==width
    m.clear_raw_cache()

@needs_sample
def test_over_budget_preview_is_not_retained(tmp_path, monkeypatch):
    m.clear_raw_cache()
    monkeypatch.setattr(m, '_RAW_CACHE_LIMIT', 1024)
    original=m.rawpy.imread; calls=[]
    def read(*a,**kw):
        calls.append(a[0]); return original(*a,**kw)
    monkeypatch.setattr(m.rawpy,'imread',read)
    for i in range(2):
        m.develop(SAMPLE_ARW,{},str(tmp_path/f'{i}.jpg'),size=400)
    assert len(calls)==2
    assert (tmp_path/'0.jpg').read_bytes()==(tmp_path/'1.jpg').read_bytes()
    m.clear_raw_cache()


def test_chunked_u8_preserves_pixel_values_at_row_boundaries():
    for dtype in [np.float32,np.float64]:
        rgb=np.random.default_rng(19).uniform(-0.1,1.1,(259,131,3)).astype(dtype)
        expected=(np.clip(rgb,0,1)*255).astype(np.uint8)
        np.testing.assert_array_equal(m._rgb_to_u8(rgb),expected)


def test_different_raw_decodes_can_run_concurrently(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    sources=[tmp_path/'a.ARW',tmp_path/'b.ARW']
    for source in sources:
        source.write_bytes(b'fixture identity')
    barrier=threading.Barrier(2)
    def decode(path, recipe, size):
        barrier.wait(timeout=3)
        return np.zeros((2,2,3),dtype=np.float32), {'source':path}, []
    monkeypatch.setattr(m,'_decode_raw_preview',decode)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(m._load_raw_preview,str(source),{},400) for source in sources]
        results=[future.result(timeout=5) for future in futures]
    assert {r[1]['source'] for r in results}=={str(p) for p in sources}


def test_clear_during_decode_does_not_repopulate_cache(tmp_path, monkeypatch):
    source=tmp_path/'a.ARW';source.write_bytes(b'fixture identity')
    calls=[]
    def decode(path,recipe,size):
        calls.append(path)
        m.clear_raw_cache()
        return np.zeros((2,2,3),dtype=np.float32),{},[]
    monkeypatch.setattr(m,'_decode_raw_preview',decode)
    m._load_raw_preview(str(source),{},400)
    m._load_raw_preview(str(source),{},400)
    assert len(calls)==2
