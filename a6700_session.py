"""Private, idle-expiring RAW worker; lightweight clients share its RAM cache."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import resource
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
LIMIT = 1024 * 1024


def revision():
    h = hashlib.sha256()
    for name in ('a6700_session.py', 'a6700_rawsmith.py', 'lut.py'):
        h.update((ROOT / name).read_bytes())
    return h.hexdigest()


def prepare_runtime(runtime):
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = runtime.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError('Runtime must be an owner-only real directory (mode 700)')
    if len(os.fsencode(runtime / 'worker.sock')) >= 104:
        raise RuntimeError('Unix socket path too long; choose a shorter --runtime-dir')


def private_file(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
        os.close(fd)
        raise RuntimeError('Runtime file must be an owner-only regular file')
    return os.fdopen(fd, 'r+b', buffering=0)


def rpc(runtime, request, timeout=180):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(runtime / 'worker.sock'))
        payload = json.dumps(request, allow_nan=False).encode() + b'\n'
        if len(payload) > LIMIT:
            raise ValueError('Request too large')
        sock.sendall(payload)
        with sock.makefile('rb') as stream:
            raw = stream.readline(LIMIT + 1)
        if not raw.endswith(b'\n') or len(raw) > LIMIT:
            raise RuntimeError('Incomplete/oversized worker reply; do not blindly repeat an edit')
        reply = json.loads(raw)
        if not reply.get('ok'):
            raise RuntimeError(reply.get('error', 'Worker request failed'))
        return reply


def probe(runtime):
    try:
        return rpc(runtime, {'method': 'status'})
    except (FileNotFoundError, ConnectionRefusedError):
        return None


def ensure_worker(runtime, idle_seconds):
    # This lock covers discovery/startup only; no image data is stored here.
    with private_file(runtime / 'startup.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = probe(runtime)
        wanted = revision()
        if info and info['worker']['revision'] == wanted:
            return wanted
        if info:
            rpc(runtime, {'method': 'stop', 'revision': info['worker']['revision']})
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '--runtime-dir', str(runtime),
             '--idle-seconds', str(idle_seconds), '--worker'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            info = probe(runtime)
            if info and info['worker']['revision'] == wanted:
                return wanted
            if process.poll() is not None:
                raise RuntimeError('Worker failed to start; check runtime permissions and Python dependencies')
            time.sleep(0.05)
        raise RuntimeError('Worker startup not confirmed; no edit was submitted')


def serve(runtime, idle_seconds):
    with private_file(runtime / 'worker.lock') as lock:
        # Wait for an old revision to finish its graceful shutdown.
        fcntl.flock(lock, fcntl.LOCK_EX)
        address = runtime / 'worker.sock'
        if address.exists() or address.is_symlink():
            st = address.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                raise RuntimeError('Refusing to replace non-owned/non-socket runtime path')
            address.unlink()
        loaded_revision = revision()
        import a6700_rawsmith as engine
        if revision() != loaded_revision:
            raise RuntimeError("Source changed during worker startup")
        decodes = 0
        original = engine.rawpy.imread if engine.rawpy is not None else None
        if original:
            def read(*args, **kwargs):
                nonlocal decodes
                decodes += 1
                return original(*args, **kwargs)
            engine.rawpy.imread = read
        started = time.monotonic()
        last = started
        requests = 0
        stopping = False
        def metrics():
            entry = engine._RAW_CACHE
            return {'pid': os.getpid(), 'revision': loaded_revision,
                    'raw_decodes': decodes, 'requests': requests,
                    'cache_bytes': int(entry[1].nbytes) if entry else 0,
                    'idle_seconds': idle_seconds, 'uptime_s': time.monotonic()-started,
                    'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2 if sys.platform == 'darwin' else 1024)}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(address))
            os.chmod(address, 0o600)
            identity = address.stat().st_ino
            server.listen(8)
            server.settimeout(min(0.2, idle_seconds))
            try:
                while not stopping and time.monotonic()-last < idle_seconds:
                    try:
                        conn, _ = server.accept()
                    except socket.timeout:
                        continue
                    with conn:
                        conn.settimeout(3)
                        try:
                            with conn.makefile('rb') as stream:
                                raw = stream.readline(LIMIT+1)
                            if len(raw)>LIMIT or not raw.endswith(b'\n'):
                                raise ValueError('Malformed or oversized request')
                            req = json.loads(raw)
                            if not isinstance(req, dict):
                                raise ValueError('Request must be an object')
                            method = req.get('method')
                            if method != 'status' and (req.get('revision') != loaded_revision or (method != 'stop' and revision() != loaded_revision)):
                                raise ValueError('Worker code changed; no edit executed, start a new request')
                            result = None
                            if method == 'status':
                                pass
                            elif method == 'clear':
                                engine.clear_raw_cache()
                            elif method == 'stop':
                                engine.clear_raw_cache()
                                stopping = True
                            elif method == 'develop':
                                if not isinstance(req.get('recipe'), dict):
                                    raise ValueError('recipe must be an object')
                                requests += 1
                                old_cwd = os.getcwd()
                                try:
                                    os.chdir(req['cwd'])
                                    result = engine.develop(req['input'], req['recipe'], req['out'], size=int(req['size'])).to_dict()
                                finally:
                                    os.chdir(old_cwd)
                            else:
                                raise ValueError('Unknown method')
                            reply = {'ok': True, 'result': result, 'worker': metrics()}
                        except Exception as exc:
                            reply = {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'worker': metrics()}
                        try:
                            packet = json.dumps(reply, allow_nan=False).encode()+b'\n'
                        except (ValueError, TypeError):
                            packet = json.dumps({'ok': False, 'error': 'Result cannot be serialized; image may already exist, inspect before retrying', 'worker': metrics()}).encode()+b'\n'
                        try:
                            conn.sendall(packet)
                        except (BrokenPipeError, ConnectionResetError):
                            # A lost reply does not justify replaying a file write.
                            pass
                        last = time.monotonic()
            finally:
                engine.clear_raw_cache()
                if address.exists() and address.stat().st_ino == identity:
                    address.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', default=str(ROOT/'.a6700-runtime'))
    parser.add_argument('--idle-seconds', type=float, default=600)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('command', nargs='?', choices=['develop','auto','status','clear','stop'])
    parser.add_argument('input', nargs='?')
    parser.add_argument('-o','--out', default='preview.jpg')
    parser.add_argument('--size', type=int, default=1400)
    parser.add_argument('--recipe')
    parser.add_argument('--set', action='append', default=[])
    args = parser.parse_args()
    if args.idle_seconds <= 0 or args.idle_seconds > 3600:
        parser.error('idle-seconds must be in (0, 3600]')
    runtime = Path(args.runtime_dir).expanduser().absolute()
    prepare_runtime(runtime)
    if args.worker:
        serve(runtime, args.idle_seconds)
        return
    if args.command is None:
        parser.error('command required')
    if args.command in ('status','clear','stop'):
        info = probe(runtime)
        if not info:
            print(json.dumps({'running': False}))
            return
        if args.command != 'status':
            info = rpc(runtime, {'method':args.command,'revision':info['worker']['revision']})
        if args.command == 'stop':
            deadline = time.monotonic()+5
            while (runtime/'worker.sock').exists() and time.monotonic()<deadline:
                time.sleep(0.02)
            if (runtime/'worker.sock').exists():
                raise RuntimeError('Stop acknowledged but worker shutdown not yet verified')
        print(json.dumps({'running':args.command!='stop',**info}))
        return
    if not args.input:
        parser.error('input required')
    source = str(Path(args.input).expanduser().resolve(strict=True))
    output = str(Path(args.out).expanduser().absolute())
    recipe = {'exposure':0.1,'shadows':15,'vibrance':10,'sharpness':45} if args.command=='auto' else {}
    if args.recipe:
        loaded = json.loads(Path(args.recipe).read_text())
        if not isinstance(loaded,dict):
            raise ValueError('Recipe JSON must be an object')
        recipe.update(loaded)
    for setting in args.set:
        k,v = setting.split('=',1)
        try:
            recipe[k] = json.loads(v)
        except json.JSONDecodeError:
            recipe[k] = v
    wanted = ensure_worker(runtime,args.idle_seconds)
    reply = rpc(runtime, {'method':'develop','revision':wanted,'input':source,'out':output,'recipe':recipe,'size':args.size,'cwd':str(Path.cwd())})
    if args.command == 'auto':
        # Match original CLI's intentional recipe sidecar behavior.
        Path(str(Path(args.input).absolute())+'.a6700_rawsmith.json').write_text(json.dumps(reply['result']['recipe'],ensure_ascii=False,indent=2))
    print(json.dumps(reply,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}. An uncertain edit must not be retried blindly.',file=sys.stderr)
        sys.exit(1)
