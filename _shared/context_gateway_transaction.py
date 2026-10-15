"""One phase machine owns gateway application and reverse recovery."""
from __future__ import annotations

from context_gateway_model import GatewayBlocked, GatewayFailure, GatewayServiceError, LABEL, ServiceObservation, digest


def matches(current, expected, *, identity=True):
    return (current.kind == expected.kind and current.resolved == expected.resolved
            and current.raw == expected.raw and current.mode == expected.mode
            and (not identity or current.identity == expected.identity))


def assert_current(io, expected, *, identity=True):
    current = io.snapshot(expected.path, content=expected.raw is not None)
    if not matches(current, expected, identity=identity):
        raise RuntimeError('Concurrent artifact change prevents execution or recovery: ' + str(expected.path))


class _Journal:
    def __init__(self, io, plan):
        self.io, self.plan = io, plan
        self.written, self.after, self.directories, self.auxiliary = [], {}, {}, {}

    def mkdir(self, path, mode=0o777):
        missing = []
        cursor = path
        while not self.io.snapshot(cursor, content=False).exists:
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            self.io.mkdir(directory, mode if directory == path else 0o777)
            self.directories[directory] = self.io.snapshot(directory, content=False)

    def prepare(self, item):
        self.mkdir(item.path.parent)
        if self.io.snapshot(item.path, content=False).exists:
            raise RuntimeError('A recovery/log artifact already exists; preserve it')
        self.auxiliary[item.path] = (item, None)
        try:
            self.io.write(item.path, item.raw, 0o600)
        finally:
            self.auxiliary[item.path] = (item, self.io.snapshot(item.path, content=not item.mutable))

    def apply(self, mutation):
        assert_current(self.io, mutation.before)
        self.mkdir(mutation.before.path.parent)
        self.written.append(mutation)
        try:
            if mutation.after is None:
                self.io.unlink(mutation.before.path)
            else:
                self.io.write(mutation.before.path, mutation.after, mutation.mode)
        finally:
            self.after[mutation.before.path] = self.io.snapshot(mutation.before.path)

    def restore(self, *, renewed_paths=()):
        # Validate every attempted change before restoring any artifact.
        for mutation in self.written:
            path = mutation.before.path
            current = self.io.snapshot(path)
            if matches(current, mutation.before):
                continue
            owned = self.after[path]
            if (not matches(current, owned) or current.raw != mutation.after
                    or (mutation.after is not None and current.kind != 'file')):
                raise RuntimeError('Concurrent artifact change prevents recovery: ' + str(path))
        for mutation in reversed(self.written):
            before = mutation.before
            if matches(self.io.snapshot(before.path), before):
                continue
            assert_current(self.io, self.after[before.path])
            if before.kind == 'missing':
                self.io.unlink(before.path)
            else:
                self.io.write(before.path, before.raw, before.mode)
        # An unmodified guard is as important as a file restored by this run.
        # In particular, never restart a concurrently edited prior runtime.
        touched = {m.before.path for m in self.written}
        for guard in self.plan.guards:
            identity = guard.path not in touched and guard.path not in renewed_paths and guard.resolved not in renewed_paths
            assert_current(self.io, guard, identity=identity)

    def clean_first_install(self):
        owned = set(self.directories) | {
            p for p in self.auxiliary if self.io.snapshot(p, content=False).exists}
        directories = [self.plan.state_dir, *self.directories]
        for path in dict.fromkeys(directories):
            current = self.io.snapshot(path, content=False)
            if not current.exists:
                continue
            if (current.kind != 'other' or (path in self.directories
                    and current.identity != self.directories[path].identity)
                    or set(self.io.children(path)) != {p for p in owned if p.parent == path}):
                raise RuntimeError('Concurrent directory change prevents first-install cleanup')
        for path, (item, expected) in self.auxiliary.items():
            current = self.io.snapshot(path, content=not item.mutable)
            if current.kind == 'missing':
                continue
            if expected is None or current.kind != 'file' or not matches(current, expected):
                raise RuntimeError('Concurrent recovery/log change prevents first-install cleanup')
        for path in reversed(self.auxiliary):
            self.io.unlink(path)
        for path in reversed(self.directories):
            self.io.rmdir(path)


def _state(io, domain, plan=None):
    value = io.state(domain)
    if isinstance(value, ServiceObservation):
        if value.presence == 'present':
            if plan is not None and value.agent != plan.agent:
                raise RuntimeError('Loaded gateway service does not match the owned service definition')
            value = 'running'
        else:
            value = value.presence
    if value not in ('running', 'absent'):
        raise RuntimeError('Native service observation is inconclusive')
    return value


def preview(plan, *, was_running):
    """Describe the validated effects without exposing configuration contents."""
    def mutations(items):
        return [dict(path=str(m.before.path), action='delete' if m.after is None else 'write',
                     mode=m.mode if m.after is not None else None) for m in items]
    steps = [dict(phase='prepare', directories=[str(plan.state_dir)] if plan.operation == 'install' else [],
        files=[dict(path=str(p.path), action='create', mutable=p.mutable) for p in plan.preparation])]
    if was_running:
        steps.append(dict(phase='stop', domain=plan.domain))
    steps.extend([dict(phase='verify-absence', domain=plan.domain),
                  dict(phase='stage', files=mutations(plan.staged))])
    if plan.candidate is not None:
        steps.extend([dict(phase='bootstrap', domain=plan.domain, agent=str(plan.agent)),
                      dict(phase='readiness', listener='loopback')])
    steps.extend([dict(phase='commit', files=mutations(plan.committed)),
                  dict(phase='retire', files=mutations(plan.retired))])
    touched = {m.before.path for m in (*plan.staged, *plan.committed, *plan.retired)}
    preserved = {str(a.path) for a in plan.guards if a.path not in touched}
    if plan.operation == 'detach':
        preserved.add(str(plan.state_dir))
    recovery = ['validate-attempt-owned-artifacts', 'restore-written-files-in-reverse-order',
                'validate-unchanged-dependencies']
    if plan.candidate is not None:
        recovery[:0] = ['stop-attempted-candidate', 'confirm-candidate-absence']
    if was_running:
        recovery.append('restart-prior-service-and-confirm-readiness')
    if plan.operation == 'install' and plan.prior_state is None:
        recovery.append('remove-only-attempt-created-recovery-files-and-directories')
    return dict(status='dry-run', service=LABEL, action=plan.result.get('action', 'would-install'),
        changes=mutations((*plan.staged, *plan.committed, *plan.retired)), plan=dict(
            operation=plan.operation, service_before='running' if was_running else 'absent',
            preconditions=[dict(path=str(a.path), kind=a.kind, mode=a.mode,
                digest=digest(a.raw) if a.raw is not None else None) for a in plan.guards],
            steps=steps, preserved=sorted(preserved), recovery=recovery,
            recovery_unconfirmed='preserve-current-artifacts-and-block-dependent-work'))


def execute(plan, io, *, confirm=True, participant=None):
    for guard in plan.guards:
        assert_current(io, guard)
    try:
        was_running = _state(io, plan.domain, plan) == 'running'
    except Exception as exc:
        raise GatewayServiceError('Service state is unavailable; no installed artifacts changed') from exc
    if was_running and plan.prior_state is None:
        raise GatewayBlocked('An unowned running gateway service must be preserved')
    if not confirm:
        return preview(plan, was_running=was_running)
    journal = _Journal(io, plan)
    stopped, attempted_start, external_attempted, phase = False, False, False, 'prepare'
    try:
        if plan.operation == 'install':
            journal.mkdir(plan.state_dir, 0o700)
        for item in plan.preparation:
            journal.prepare(item)
        if was_running:
            phase = 'stop'
            io.stop(plan.domain)
            stopped = True
        phase = 'verify-absence'
        if _state(io, plan.domain, plan) != 'absent':
            raise RuntimeError('Gateway service absence is unconfirmed')
        phase = 'stage'
        for guard in plan.guards:
            assert_current(io, guard)
        if participant is not None:
            external_attempted = True
            participant.publish()
        for mutation in plan.staged:
            journal.apply(mutation)
        if plan.candidate is not None:
            phase, attempted_start = 'bootstrap', True
            io.start(plan.domain, plan.agent)
            phase = 'readiness'
            if not io.ready(plan.candidate):
                raise RuntimeError('Candidate service is not ready')
        phase = 'commit'
        for mutation in plan.committed:
            journal.apply(mutation)
        phase = 'retire'
        for mutation in plan.retired:
            journal.apply(mutation)
    except Exception as failure:
        if phase == 'stop' or (phase == 'verify-absence' and not stopped):
            raise GatewayServiceError('Gateway service stop/absence is unconfirmed; no active files changed; retain Core for review',
                                      phase=phase) from failure
        try:
            if attempted_start:
                if _state(io, plan.domain, plan) == 'running':
                    io.stop(plan.domain)
                if _state(io, plan.domain, plan) != 'absent':
                    raise RuntimeError('Candidate stop is unconfirmed')
            if external_attempted:
                participant.restore()
            journal.restore(renewed_paths=getattr(participant, 'renewed_paths', ()) if external_attempted else ())
            if was_running and stopped:
                io.start(plan.domain, plan.agent)
                if not io.ready(plan.prior_state):
                    raise RuntimeError('Restored service is not ready')
            if plan.operation == 'install' and plan.prior_state is None:
                journal.clean_first_install()
        except Exception as recovery:
            reference = plan.archive or getattr(participant, 'recovery_reference', 'current artifacts')
            raise GatewayFailure(f'{failure}; recovery failed ({recovery}); preserved recovery: {reference}',
                                 phase=phase) from failure
        detail = ('Gateway detachment failed; prior configuration/service retained' if plan.operation == 'detach'
                  else f'Installation failed in {phase}; prior artifacts and service state restored')
        raise GatewayFailure(detail, phase=phase, restored=True) from failure
    result = dict(plan.result)
    if plan.operation == 'detach':
        result['action'] = 'detached'
    return result
