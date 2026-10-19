"""Concrete installer I/O. Policy and transaction recovery live elsewhere."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import urllib.request
import uuid

from context_gateway_model import Artifact, GatewayBlocked, LABEL, ServiceObservation


def service_domain(host_platform):
    return f'gui/{os.getuid()}' if host_platform == 'darwin' else ''


def snapshot(path, *, content=True):
    path = Path(path)
    resolved = path.resolve()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return Artifact(path, resolved, 'missing')
    identity = (info.st_dev, info.st_ino)
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return Artifact(path, resolved, 'symlink', mode=mode, identity=identity)
    if not stat.S_ISREG(info.st_mode):
        return Artifact(path, resolved, 'other', mode=mode, identity=identity)
    raw = path.read_bytes() if content else None
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
        raise GatewayBlocked('Concurrent artifact change during inspection: ' + str(path))
    return Artifact(path, resolved, 'file', raw, mode, identity)


def require_regular(paths):
    for path in paths:
        if Path(path).is_symlink():
            raise GatewayBlocked('Symlinked gateway artifact must be preserved: ' + str(path))


def write(path, raw, mode=0o600):
    require_regular([path])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def observe(domain):
    try:
        result = subprocess.run(['launchctl', 'print', domain + '/' + LABEL], capture_output=True)
    except OSError as exc:
        return ServiceObservation('unknown', detail=type(exc).__name__)
    if result.returncode == 113:
        return ServiceObservation('absent', returncode=result.returncode)
    if result.returncode != 0:
        return ServiceObservation('unknown', returncode=result.returncode, detail='Native discovery failed')
    output = result.stdout.decode(errors='replace') if isinstance(result.stdout, bytes) else result.stdout
    matches = re.findall(r'^\s*path = (.+\.plist)\s*$', output or '', re.M)
    agent = Path(matches[0]) if len(matches) == 1 and Path(matches[0]).is_absolute() else None
    return ServiceObservation('present', agent, result.returncode,
                              '' if agent else 'Native service definition path is unavailable')


def service_state(domain):
    value = observe(domain)
    if value.presence == 'unknown':
        raise RuntimeError('Service state is unavailable')
    return value


def stop(domain, *, state=service_state):
    result = subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    if result.returncode:
        raise RuntimeError('Service stop failed')
    deadline = time.monotonic() + 10
    while True:
        observed = state(domain)
        value = observed.presence if isinstance(observed, ServiceObservation) else observed
        if value == 'absent':
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Service stop is unconfirmed')
        time.sleep(0.05)


def start(domain, agent):
    result = subprocess.run(['launchctl', 'bootstrap', domain, str(agent)], capture_output=True)
    if result.returncode:
        raise RuntimeError('Service bootstrap failed')


def ready(state):
    deadline = time.monotonic() + 10
    while True:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/health",
                headers={'X-Ghost-Context-Scope': state['scope']})
            with urllib.request.urlopen(request, timeout=1) as reply:
                if json.load(reply) == {'status': 'ready'}:
                    return True
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


class Platform:
    snapshot = staticmethod(snapshot)
    observe = staticmethod(observe)

    def __init__(self, *, write=write, state=service_state, stop=stop, start=start, ready=ready):
        self.write, self.state, self.stop, self.start, self.ready = write, state, stop, start, ready

    @staticmethod
    def children(path):
        return tuple(path.iterdir()) if path.is_dir() else ()

    @staticmethod
    def mkdir(path, mode=0o777):
        path.mkdir(mode=mode)

    @staticmethod
    def unlink(path):
        path.unlink(missing_ok=True)

    @staticmethod
    def rmdir(path):
        path.rmdir()


@contextmanager
def operation_lock(home):
    """Use the installer's existing lock; never adopt an unrelated holder."""
    import install_lock
    from install_lock import InstallLockError, acquire_lock, release_lock
    path = Path(home) / '.ghost-alice/install.lock'
    # Reuse only this process's lock or its calling installer's lock; install_lock owns that judgment.
    try:
        held = install_lock.held_by_process_tree(path)
    except InstallLockError as exc:
        raise GatewayBlocked(str(exc)) from exc
    if held:
        yield
        return
    before = path.parent.exists()
    try:
        acquire_lock(path, owner='context-gateway')
    except InstallLockError as exc:
        raise GatewayBlocked('Another installer holds the operation lock') from exc
    owned = snapshot(path)
    try:
        yield
    finally:
        current = snapshot(path)
        if current.identity == owned.identity and current.raw == owned.raw:
            release_lock(path)
        if not before:
            try:
                path.parent.rmdir()
            except OSError:
                pass
