"""Separate command invocations must share the RAW decode, not just Python calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest
from test_a6700_rawsmith import SAMPLE_ARW, needs_sample
ROOT=Path(__file__).resolve().parents[1]
CLIENT=ROOT/'a6700_session.py'

@pytest.fixture
def runtime():
    # macOS sockaddr_un paths have a small fixed size.
    with tempfile.TemporaryDirectory(prefix='a67-',dir='/tmp') as d:
        yield Path(d)/'run'
        if CLIENT.exists():
            subprocess.run([sys.executable,str(CLIENT),'--runtime-dir',str(Path(d)/'run'),'stop'],capture_output=True,timeout=20)

def command(runtime,*args,check=True):
    p=subprocess.run([sys.executable,str(CLIENT),'--runtime-dir',str(runtime),*args],capture_output=True,text=True,timeout=40)
    if check:assert p.returncode==0,p.stderr
    return p

@needs_sample
def test_separate_clients_reuse_worker_and_match_original_cli(runtime,tmp_path):
    paths=[];responses=[]
    for i,exposure in enumerate([0.1,0.4]):
        out=tmp_path/f'{i}.jpg';paths.append(out)
        responses.append(json.loads(command(runtime,'develop',SAMPLE_ARW,'-o',str(out),'--size','800','--set',f'exposure={exposure}').stdout))
    assert responses[0]['worker']['pid']==responses[1]['worker']['pid']
    assert responses[1]['worker']['raw_decodes']==1
    direct=tmp_path/'direct.jpg'
    p=subprocess.run([sys.executable,str(ROOT/'a6700_rawsmith.py'),'develop',SAMPLE_ARW,'-o',str(direct),'--size','800','--set','exposure=0.4'],capture_output=True,text=True,check=True)
    expected=json.loads(p.stdout)
    assert paths[1].read_bytes()==direct.read_bytes()
    assert responses[1]['result']['stats']==expected['stats']
    assert paths[0].read_bytes()!=paths[1].read_bytes()
    command(runtime,'clear')
    third=json.loads(command(runtime,'develop',SAMPLE_ARW,'-o',str(tmp_path/'third.jpg'),'--size','800').stdout)
    assert third['worker']['raw_decodes']==2
    command(runtime,'stop')
    assert json.loads(command(runtime,'status').stdout)['running'] is False


def test_status_does_not_start_worker_and_runtime_is_private(runtime):
    assert json.loads(command(runtime,'status').stdout)=={'running':False}
    assert not (runtime/'worker.sock').exists()
    assert runtime.stat().st_mode & 0o777 == 0o700

@needs_sample
def test_idle_exit_and_invalid_request_leave_worker_usable(runtime,tmp_path):
    import socket,time
    response=json.loads(command(runtime,'--idle-seconds','1','develop',SAMPLE_ARW,'-o',str(tmp_path/'out.jpg'),'--size','400').stdout)
    assert (runtime/'worker.sock').stat().st_mode & 0o777 == 0o600
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.connect(str(runtime/'worker.sock'));sock.sendall(b'not-json\n')
        assert json.loads(sock.makefile('rb').readline())['ok'] is False
    assert json.loads(command(runtime,'status').stdout)['running'] is True
    deadline=time.monotonic()+6
    while (runtime/'worker.sock').exists() and time.monotonic()<deadline:
        time.sleep(.1)
    assert json.loads(command(runtime,'status').stdout)=={'running':False}
    restart=json.loads(command(runtime,'develop',SAMPLE_ARW,'-o',str(tmp_path/'again.jpg'),'--size','400').stdout)
    assert restart['worker']['pid']!=response['worker']['pid']
    assert (tmp_path/'out.jpg').read_bytes()==(tmp_path/'again.jpg').read_bytes()


def test_insecure_runtime_is_rejected(tmp_path):
    tmp_path.chmod(0o755)
    p=command(tmp_path,'status',check=False)
    assert p.returncode != 0 and 'owner-only' in p.stderr

@needs_sample
def test_source_update_restarts_worker_before_next_edit(runtime,tmp_path):
    import shutil
    isolated=tmp_path/'source';isolated.mkdir()
    for name in ['a6700_session.py','a6700_rawsmith.py','lut.py']:
        shutil.copyfile(ROOT/name,isolated/name)
    def edit(name):
        p=subprocess.run([sys.executable,str(isolated/'a6700_session.py'),'--runtime-dir',str(runtime),'develop',SAMPLE_ARW,'--size','400','-o',str(tmp_path/name)],capture_output=True,text=True,timeout=40)
        assert p.returncode==0,p.stderr
        return json.loads(p.stdout)
    first=edit('first.jpg')
    with (isolated/'lut.py').open('a') as f:f.write('\n# test source revision\n')
    second=edit('second.jpg')
    assert first['worker']['pid']!=second['worker']['pid']
    assert (tmp_path/'first.jpg').read_bytes()==(tmp_path/'second.jpg').read_bytes()

@needs_sample
def test_concurrent_clients_start_one_worker(runtime,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    def edit(i):
        return json.loads(command(runtime,'develop',SAMPLE_ARW,'--size','400','-o',str(tmp_path/f'{i}.jpg')).stdout)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(edit,range(2)))
    assert len({r['worker']['pid'] for r in results})==1
    status=json.loads(command(runtime,'status').stdout)
    assert status['worker']['raw_decodes']==1
    assert (tmp_path/'0.jpg').read_bytes()==(tmp_path/'1.jpg').read_bytes()
