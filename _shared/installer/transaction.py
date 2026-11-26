"""Place the selected targets through publish requests, and write the earlier records after placement.

Every placement builds one publish request and calls `install_transaction.publish` in this process with the
participants the install record names (the gateway participant when a gateway is
registered); the publish places the targets and writes their install record
entries in the same step. This module decides what each request names. The
copy markers belong to the staged payload; the snapshot, the sidecars and the install-state are written after placement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import shlex
import sys
from typing import TYPE_CHECKING

import install_transaction
import installed_payload
from installer_assets import is_link_or_reparse_point
from installer import gateway_participant, locations, report, runner, source
from installer.runner import ToolResult

if TYPE_CHECKING:
    from installer.cli import Context
    from installer.selection import Collected, Target

# The runtime companion scripts the context gateway runs beside the runtime `_shared`, relative to the
# checkout and to the runtime root; the runtime publish carries them with `_shared`.
RUNTIME_COMPANIONS = ("session-intent-analyzer/scripts/session_intent_ledger.py",)


@dataclass(frozen=True)
class LedgerLocations:
    roots: dict[Path, dict]
    unavailable_hosts: dict[str, str] = field(default_factory=dict)
    source_roots: dict[str, dict] = field(default_factory=dict)
    retired_roots: tuple[Path, ...] = ()
    configured_roots: dict[str, Path] = field(default_factory=dict)
    observation_errors: dict[str, str] = field(default_factory=dict)
    hook_roots: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    host_config_errors: dict[str, str] = field(default_factory=dict)
    installed_hosts: tuple[str, ...] = ()
    source_root: Path | None = None

    def publication_source(self, checkout: dict) -> dict:
        """Keep previously observed host identities even while this checkout is still the source."""
        roots = {ledger: relation if relation['hosts'] else relation['source_root']
                 for ledger, relation in self.source_roots.items()}
        return dict(checkout, ledger_source_roots=roots)

    @property
    def active_config_errors(self) -> dict[str, str]:
        """Config failures that can conceal an installed host's shared record dependency."""
        return {host: cause for host, cause in self.host_config_errors.items()
                if host in self.installed_hosts or self.hook_roots.get(host) != ()}

    @property
    def dependency_errors(self) -> tuple[tuple[str, str], ...]:
        """Ordered causes preventing shared record movement or source cleanup."""
        return (*self.observation_errors.items(), *self.active_config_errors.items(),
                *self.unavailable_hosts.items())


def installed_ledger_locations(*, ghost_root: Path | None = None) -> LedgerLocations:
    """Group actual installed bindings and their source relationships, without changing them."""
    import install_hooks
    import runtime_config

    ghost_root = locations.record_root() if ghost_root is None else ghost_root
    errors = {}
    config_path = ghost_root / 'config.json'
    try:
        config = runtime_config.load_config(env={'GHOST_ALICE_GHOST_ROOT': str(ghost_root)}, strict=True)
    except (OSError, ValueError) as exc:
        config = {}
        errors[str(config_path)] = str(exc)
    record_path = str(installed_payload.read_path(ghost_root))
    try:
        record = installed_payload.read(ghost_root) or {}
        source_record = record.get('source', {})
    except (OSError, installed_payload.RecordError) as exc:
        record = {}
        source_record = {}
        errors[record_path] = str(exc)
    retained = source_record.get('ledger_source_roots', {})
    origin = source_record.get('root')
    fixed = (ghost_root / 'session-intent').resolve()
    retired = {Path(value).resolve() for value in config.get('retired_ledger_roots', [])}
    grouped = {}
    unavailable = {}
    configured = {}
    hook_roots = {}
    host_errors = {}
    relationships = {}

    def add_binding(host, value, raw_root):
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ValueError(f'Installed ledger root for {host} must be an absolute path string')
        root = Path(raw_root).resolve()
        state = 'fixed' if root == fixed else 'unconfirmed' if str(config_path) in errors else 'retired' if root in retired else 'unknown'
        entry = grouped.setdefault(root, dict(hosts={}, source_root=None, state=state))
        entry['hosts'][host] = dict(value, ledger_root=raw_root)
        saved = retained.get(str(root))
        previous = saved.get('source_root') if isinstance(saved, dict) and host in saved['hosts'] else None
        previous = previous or origin
        if (root != fixed and root not in retired and previous and Path(previous).is_absolute()
                and root.is_relative_to(Path(previous).resolve())):
            entry['source_root'] = Path(previous).resolve()
            relation = relationships.setdefault(str(root), dict(source_root=str(entry['source_root']), hosts=[]))
            if host not in relation['hosts']:
                relation['hosts'].append(host)
            if str(config_path) not in errors:
                entry['state'] = 'source-bound'
        return root

    for host in dict.fromkeys([*install_hooks.PLATFORMS, *config.get('install_locations', {})]):
        value = {}
        try:
            value = install_hooks.observed_installation(host, hook_fallback=False, config=config)
            if 'ledger_root' in value:
                configured[host] = add_binding(host, value, value['ledger_root'])
        except (OSError, ValueError) as exc:
            host_errors[host] = f'{config_path}: install_locations.{host}: {exc}'
        if host in install_hooks.PLATFORMS:
            try:
                hook_roots[host] = tuple(sorted(add_binding(host, value, raw_root)
                    for raw_root in install_hooks.installed_hook_ledger_roots(host)))
            except (OSError, ValueError) as exc:
                unavailable[host] = str(exc)
    leftovers = tuple(Path(value) for value in config.get('retired_ledger_roots', []) if os.path.lexists(value))
    installed_hosts = set()
    for destination, target in record.get('targets', {}).items():
        try:
            installed_hosts.update(installed_payload._check_entry(destination, target)['used_by'])
        except installed_payload.RecordError as exc:
            errors[record_path] = '; '.join(filter(None, (errors.get(record_path), str(exc))))
    observed = LedgerLocations(grouped, unavailable, retired_roots=leftovers, configured_roots=configured,
        observation_errors=errors, hook_roots=hook_roots, host_config_errors=host_errors,
        installed_hosts=tuple(sorted(installed_hosts)), source_root=Path(origin).resolve() if origin else None)
    # Retain a previously observed host's relation while its binding cannot be read.
    # Unattributed legacy facts survive uncertainty but cannot identify a later binding.
    for root, saved in retained.items():
        if Path(root).resolve() in retired or Path(root).resolve() == fixed:
            continue
        if isinstance(saved, str):
            saved = dict(source_root=saved, hosts=[])
        hidden = {host for host in saved['hosts']
                  if str(config_path) in errors or host in host_errors or host in unavailable}
        if hidden:
            relation = relationships.setdefault(root, dict(source_root=saved['source_root'], hosts=[]))
            relation['hosts'].extend(hidden - set(relation['hosts']))
        elif not saved['hosts'] and (errors or host_errors or unavailable or Path(root) in grouped):
            relationships.setdefault(root, saved)
    observed.source_roots.update({root: dict(value, hosts=sorted(value['hosts']))
                                 for root, value in relationships.items()})
    return observed


def _record_roots_overlap(original: Path, root: Path) -> bool:
    return original == root or original in root.parents or root in original.parents


def _record_source_hosts(original: Path, observed: LedgerLocations) -> set[str]:
    return {host for root, value in observed.roots.items()
            if _record_roots_overlap(original, root)
            for host in value['hosts']}


def _saved_record_source_writers(removal: dict) -> tuple[set[str], dict[str, str]]:
    writers = removal.get('writer_hosts', [])
    unavailable = removal.get('unavailable_writer_hosts', {})
    if (not isinstance(writers, list) or any(not isinstance(host, str) or not host for host in writers)
            or not isinstance(unavailable, dict)
            or any(not isinstance(host, str) or not host or not isinstance(cause, str)
                   for host, cause in unavailable.items())):
        raise ValueError('Invalid record source writer references in the completed journal')
    return set(writers), dict(unavailable)


@dataclass
class RecordSourceRetention:
    blocked_hosts: dict[str, dict[str, str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    cleanup_sources: dict[Path, Path] = field(default_factory=dict)


def retain_record_source_writers(ghost: Path, prior_locations: LedgerLocations | None,
                                 observation_error: Exception | None = None) -> RecordSourceRetention:
    """Preserve cleanup observations; return only the detach scopes that would lose required facts."""
    import install_hooks

    install_transaction._require_lock(ghost)
    result = RecordSourceRetention()
    if (prior_locations is not None and not prior_locations.retired_roots
            and not prior_locations.dependency_errors
            and observation_error is None):
        return result
    observed = prior_locations or LedgerLocations({})
    result.errors.update(observed.observation_errors)
    unavailable = dict(observed.unavailable_hosts)
    if observation_error is not None:
        unavailable.update({host: str(observation_error) for host in install_hooks.PLATFORMS})
        result.errors[str(ghost / 'config.json')] = str(observation_error)
    # Unreadable installation facts plus unreadable hooks can conceal an unjournaled source.
    for host, cause in unavailable.items():
        dependencies = dict(result.errors)
        if host in observed.host_config_errors:
            dependencies[str(ghost / 'config.json')] = observed.host_config_errors[host]
        for path, detail in dependencies.items():
            result.blocked_hosts.setdefault(host, {})[path] = (
                f'Cannot detach {host}: installation observation is unavailable: {detail}; '
                f'its actual hook roots cannot be confirmed: {cause}')
    remaining = {path.resolve(): _record_source_hosts(path.resolve(), observed) | set(unavailable)
                 for path in observed.retired_roots}
    fixed = (ghost / 'session-intent').resolve()
    for host, roots in observed.hook_roots.items():
        if result.errors or host in observed.host_config_errors:
            for path in roots:
                if path != fixed:
                    remaining.setdefault(path, set()).add(host)
    root = install_transaction.rollbacks_root(ghost)
    try:
        if is_link_or_reparse_point(root):
            raise ValueError(f'Cleanup journal root is a linked path: {root}')
        folders = sorted(root.iterdir()) if root.exists() else []
    except (OSError, ValueError) as exc:
        result.errors[str(root)] = str(exc)
        folders = []
    for folder in folders:
        path = folder / install_transaction.JOURNAL_FILENAME
        try:
            if not install_transaction.is_journal_candidate(folder):
                continue
            journal = install_transaction.Journal.load(ghost, folder.name)
            if journal.phase != 'done' or 'source_cleanup' not in journal.data:
                continue
            removal = journal.data['source_cleanup']
            if (not isinstance(removal, dict) or not isinstance(removal.get('path'), str)
                    or not Path(removal['path']).is_absolute()):
                raise ValueError(f'Invalid record source cleanup in {path}')
            original = Path(removal['path']).resolve()
            if not os.path.lexists(original):
                continue
            result.cleanup_sources[folder] = original
            writers, saved_unavailable = _saved_record_source_writers(removal)
            confirmed = _record_source_hosts(original, observed)
            remaining.setdefault(original, set()).update(confirmed | set(unavailable))
            writers.update(confirmed)
            saved_unavailable.update(unavailable)
            revised = dict(removal, writer_hosts=sorted(writers), unavailable_writer_hosts=saved_unavailable)
            if revised != removal:
                journal.data['source_cleanup'] = revised
                journal.save()
            remaining[original].difference_update(writers | set(saved_unavailable))
        except (OSError, ValueError, install_transaction.InstallTransactionError) as exc:
            result.errors[str(path)] = str(exc)
    for original, hosts in remaining.items():
        # Config survives full uninstall and remains an input of the next cleanup.
        preserved = {host for host, path in observed.configured_roots.items()
                     if _record_roots_overlap(original, path)}
        for host in hosts - preserved:
            cause = (f'Cannot detach {host}: No completed cleanup journal preserves its reference to {original}; '
                     'cleanup would lose its host reference')
            if host in unavailable:
                cause += f'; actual hook observation failed: {unavailable[host]}'
            result.blocked_hosts.setdefault(host, {})[str(original)] = cause
    return result


def cleanup_record_source(ghost: Path, removal: dict, *, prior_locations: LedgerLocations | None = None) -> str:
    """Delete only the verified remainder named by a completed migration journal."""
    import content_digest
    import runtime_config

    config = runtime_config.load_config(env={'GHOST_ALICE_GHOST_ROOT': str(ghost)}, strict=True)
    if (not isinstance(removal, dict) or not isinstance(removal.get('path'), str)
            or not isinstance(removal.get('destination'), str)
            or not isinstance(removal.get('baseline'), dict)):
        raise ValueError('Invalid record source cleanup in the completed journal')
    original, destination = Path(removal['path']), Path(removal['destination'])
    baseline = removal['baseline'].get('folder')
    if (not original.is_absolute() or not destination.is_absolute() or original.resolve() != original
            or original == destination or original in destination.parents or destination in original.parents
            or str(original) not in config.get('retired_ledger_roots', [])
            or destination.is_symlink() or not (destination / 'ghost-state.sqlite3').is_file()
            or not isinstance(baseline, dict) or 'other' in baseline.values()):
        raise ValueError(f'Record source cleanup cannot confirm the retired location: {original}')
    if not os.path.lexists(original):
        return 'absent'
    observed = installed_ledger_locations(ghost_root=ghost)
    if observed.dependency_errors:
        raise ValueError('Cannot confirm record source cleanup: ' + '; '.join(
            f'{path}: {cause}' for path, cause in observed.dependency_errors))
    if any(_record_roots_overlap(original, root) for root in observed.roots):
        raise ValueError(f'An installed host still uses the retired record source: {original}')
    writer_hosts, unavailable_hosts = _saved_record_source_writers(removal)
    if prior_locations is not None:
        writer_hosts.update(_record_source_hosts(original, prior_locations))
        unavailable_hosts.update(prior_locations.unavailable_hosts)
    # A fresh readable binding lookup above and absent processes resolve the earlier host uncertainty.
    # Empty post-detach bindings alone cannot erase either saved host set.
    writer_hosts.update(unavailable_hosts)
    from installer import hosts
    for host, writer in hosts.observe_writers(sorted(writer_hosts)).items():
        if writer.state != 'absent':
            raise ValueError(f'Cannot remove {original}: {host} writer {writer.state}: {writer.detail}')
    registered = gateway_participant.participants(ghost)
    participant = registered[0] if registered else None
    if isinstance(participant, gateway_participant.UnreadableRecordParticipant):
        raise ValueError(participant.reason)
    effects = participant.effects if participant is not None else None
    platform = participant.platform if participant is not None else None
    live = gateway_participant.listener_binding(
        gateway_participant.loaded_service(effects=effects, platform=platform), (original,), effects=effects)
    if live.state not in ('absent', 'unrelated'):
        raise ValueError(f'Cannot remove {original}: gateway {live.state}: {live.detail}')
    remaining = content_digest.state(original, include_generated=True)
    if remaining is not None:
        changed = ([str(original)] if 'folder' not in remaining else
                   [str(original / name) for name, value in remaining['folder'].items() if baseline.get(name) != value])
        if changed:
            raise ValueError('Retired record source changed before cleanup: ' + '; '.join(changed))
        # Missing entries were deleted by a previous attempt after their data had been verified in B.
        install_transaction._remove_path(original)
        return 'removed'
    return 'absent'


def cleanup_retired_ledgers() -> tuple[Path, ...]:
    """Finish already accepted moves using the existing retirement facts and publish journals."""
    import ledger_access

    ghost = locations.record_root()
    install_transaction._require_lock(ghost)
    observed = installed_ledger_locations()
    old_roots = observed.retired_roots
    if observed.dependency_errors:
        raise ValueError('Cannot confirm record source cleanup: ' + '; '.join(
            f'{path}: {cause}' for path, cause in observed.dependency_errors))
    # A previous partial deletion already has a verified baseline; do not reopen a half-deleted DB.
    install_transaction.cleanup_completed(ghost, record_source_cleanup=cleanup_record_source)
    if install_transaction.unfinished(ghost):
        raise ValueError('Record source cleanup requires the unfinished installation to recover first')
    destination = locations.ledger_root().resolve()
    ledger = ledger_access.load_ledger()
    for root in old_roots:
        if not os.path.lexists(root):
            continue
        if (root.is_symlink() or not root.is_dir() or root.resolve() != root
                or root == destination or root in destination.parents or destination in root.parents):
            raise ValueError(f'Invalid retired record source: {root}')
        if any(_record_roots_overlap(root, current) for current in observed.roots):
            raise ValueError(f'An installed host still uses the retired record source: {root}')
        try:
            baseline = ledger.verify_moved_source(root, destination)
        except (ValueError, OSError) as exc:
            raise ValueError(f'Cannot remove old record folder {root}; retained-history check against {destination}: {exc}') from exc
        ledger.relocate_moved_links(root, destination)
        journal = install_transaction.Journal.begin(dict(ghost_root=str(ghost), host='installer'), [])
        journal.data['source_cleanup'] = dict(path=str(root), destination=str(destination),
                                              baseline=baseline)
        journal.mark('done')
        retained = install_transaction.cleanup_completed(ghost, record_source_cleanup=cleanup_record_source)
        if os.path.lexists(root):
            raise ValueError(f'Record source cleanup did not remove {root}: {"; ".join(retained)}')
    return old_roots


@dataclass(frozen=True)
class LedgerMoveResult:
    """The migration boundary reached; callers display it without parsing exception prose."""

    status: str
    detail: str = ''
    recovery_paths: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()
    phase: str = ''
    affected_paths: tuple[str, ...] = ()


def migrate_ledger(old_root: Path, *, writers_stopped: bool) -> LedgerMoveResult:
    """Move a stopped installation's ledger to its fixed root with existing journal/service owners.

    The caller confirms host and CLI writers have stopped. The gateway participant confirms
    service absence. Commit data and every binding before allowing that service to resume.
    """
    from dataclasses import asdict
    import shutil
    import install_destination
    import install_hooks
    import ledger_access
    import runtime_config
    from installer.participant import PublishContext

    if not writers_stopped:
        return LedgerMoveResult('deferred', 'Host and CLI ledger writers must be stopped before migration')
    ghost = locations.record_root().resolve()
    install_transaction._require_lock(ghost)
    old_root, new_root = Path(old_root).expanduser().resolve(), locations.ledger_root()
    if old_root == new_root or old_root.is_relative_to(new_root) or new_root.is_relative_to(old_root):
        raise ValueError('Ledger migration requires separate source and fixed destination roots')
    if not old_root.is_dir() or new_root.is_symlink():
        raise ValueError('Ledger migration requires an existing source directory and an unlinked destination')
    ledger_access.require_writable_root(old_root, env={'GHOST_ALICE_GHOST_ROOT': str(ghost)})
    config_path = ghost / 'config.json'
    config = runtime_config.load_config(env={'GHOST_ALICE_GHOST_ROOT': str(ghost)}, strict=True)
    observed = installed_ledger_locations()
    if observed.dependency_errors:
        return LedgerMoveResult('deferred', '; '.join(f'{path}: {cause}' for path, cause in
            observed.dependency_errors))
    prepared, bindings = {}, {}
    for host, value in observed.roots.get(old_root, {}).get('hosts', {}).items():
        destination = install_destination.InstallDestination.parse(value)
        if Path(destination.ghost_root).resolve() != ghost:
            raise ValueError(f'Ledger binding for {host} belongs to another installation')
        if host not in install_hooks.PLATFORMS:
            raise ValueError(f'The installed {host} adapter is required to retarget its ledger hooks')
        prepared.update(install_hooks.prepare_ledger_retarget(host, destination=destination, ledger_root=new_root))
        bindings[host] = asdict(replace(destination, ledger_root=str(new_root)))
    participants, blocked = gateway_participant.ledger_migration_participants(ghost, old_root, new_root)
    if blocked is not None:
        return LedgerMoveResult('deferred', blocked)
    service_after = None
    for participant in participants:
        changes, section = participant.prepare_ledger_retarget(old_root, new_root)
        if changes:
            prepared.update(changes)
            service_after = section
    if not bindings and service_after is None:
        raise ValueError('No installed host or registered gateway uses the selected ledger root')
    retired = list(dict.fromkeys([*config.get('retired_ledger_roots', []), str(old_root)]))
    revised = runtime_config.prepare_config(dict(install_locations=bindings, retired_ledger_roots=retired), config_path)
    prepared[config_path] = (json.dumps(revised, ensure_ascii=False, indent=2) + '\n').encode()
    # Resolve only configuration files, so publication and restoration preserve user-owned symlinks.
    physical = {path.resolve(): raw for path, raw in prepared.items()}
    before = {path: install_transaction.ReplacementBatch.identity(path) for path in physical}
    journal = install_transaction.Journal.begin(dict(ghost_root=str(ghost), host='installer'), [])
    temporary = journal.folder / 'input'
    journal.data['preparation_dir'] = temporary.name
    journal.save()
    temporary.mkdir()
    snapshot = temporary / 'ledger'
    targets = [dict(name='ledger', kind='data', source=str(snapshot), dest=str(new_root), mode='data')]
    for index, (logical, raw) in enumerate(prepared.items()):
        path = logical.resolve()
        staged = temporary / f'config-{index}'
        staged.write_bytes(raw)
        if path.exists():
            shutil.copymode(path, staged)
        else:
            staged.chmod(0o600)
        targets.append(dict(name=path.name, kind='configuration', source=str(staged), dest=str(path),
                            logical_dest=str(logical), mode='copy'))
    journal.data['targets'] = targets
    journal.save()
    context = PublishContext(ghost, 'installer', journal.txid,
                             tuple(Path(target['dest']) for target in targets) + tuple(prepared), journal.checkpoint)
    batch = install_transaction.ReplacementBatch(
        [(target['source'], target['dest'], target['mode']) for target in targets], rollback_root=journal.folder)
    ledger = ledger_access.load_ledger()
    committed = False
    finishing = 'resume_incomplete'
    outcome = LedgerMoveResult('complete')
    try:
        for participant in participants:
            participant.before_publish(context)
        import content_digest
        original_source, original_destination = ledger.snapshot_roots(old_root, new_root, snapshot, writers_stopped=True)
        if any(install_transaction.ReplacementBatch.identity(path) != original for path, original in before.items()):
            raise ValueError('Installed configuration changed during ledger migration')
        batch.stage()
        if (any(Path(target.get('logical_dest', target['dest'])).resolve() != Path(target['dest']) for target in targets)
                or any(item['before'] != before[item['dest']] for item in batch.prepared[1:])):
            raise ValueError('Installed configuration path or content changed during ledger migration')
        journal.record_staged(batch)
        if journal.data['targets'][0]['before'] != original_destination:
            raise ValueError('Record destination changed after snapshot validation')
        batch.publish()
        if content_digest.state(new_root, include_generated=True) != journal.data['targets'][0]['candidate']:
            raise ValueError('Published ledger differs from its verified snapshot')
        if any(path.read_bytes() != raw for path, raw in physical.items()):
            raise ValueError('Published ledger bindings differ from the prepared configuration')
        if content_digest.state(old_root, include_generated=True) != original_source:
            raise ValueError('Record source changed before the move committed')
        journal.data['source_cleanup'] = dict(path=str(old_root), destination=str(new_root),
                                              baseline=original_source)
        if service_after is not None:
            installed_payload.update(ghost, {'service': service_after}, journal=journal)
            if 'gateway' in journal.data['participants']:
                journal.data['participants']['gateway']['service'] = dict(service_after)
        # Persist the new service identity with the data-commit boundary before any writer resumes.
        journal.mark('committed')
        committed = True
        for participant in participants:
            if service_after is not None:
                participant.service = service_after
            participant.after_publish(context)
        finishing = 'mark_incomplete'
        journal.mark('done')
    except Exception as exc:
        if committed:
            outcome = LedgerMoveResult(finishing, str(exc))
        else:
            try:
                for participant in participants:
                    participant.before_restore(context)
                batch.restore()
                journal.restore_record(ghost)
                for participant in participants:
                    participant.after_restore(context)
                journal.mark('restored')
                outcome = LedgerMoveResult('deferred' if isinstance(exc, ledger.LedgerSnapshotConflict) else 'restored', str(exc))
            except Exception as recovery:
                outcome = LedgerMoveResult('restore_incomplete', f'{exc}; {recovery}', (str(journal.folder),),
                    phase=journal.phase, affected_paths=tuple(target['dest'] for target in targets))
    finally:
        batch.cleanup()
    try:
        cleanup = install_transaction.cleanup_completed(ghost, record_source_cleanup=cleanup_record_source)
    except install_transaction.InstallTransactionError as exc:
        if outcome.status == 'complete':
            return LedgerMoveResult('cleanup_failed', str(exc), phase=journal.phase,
                                    affected_paths=(str(old_root), str(new_root)), retained=(str(old_root),))
        return replace(outcome, detail=f'{outcome.detail}; {exc}')
    if outcome.status == 'complete' and os.path.lexists(old_root):
        return LedgerMoveResult('cleanup_failed', f'The old record folder remains: {old_root}',
                                retained=(str(old_root), *cleanup), phase=journal.phase,
                                affected_paths=(str(old_root), str(new_root)))
    if outcome.status == 'complete' and cleanup:
        return LedgerMoveResult('complete', retained=tuple(cleanup))
    return outcome


@dataclass
class CopyBatch:
    """Targets a copy-only install publishes in one request."""

    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)

    def add(self, src: Path, dest: Path, label: str, kind: str, status: str, provider: str = "core") -> None:
        self.pairs.append((Path(src), Path(dest)))
        self.labels.append(label)
        self.kinds.append(kind)
        self.statuses.append(status)
        self.providers.append(provider)


@dataclass
class SyncCounts:
    installed: int = 0
    updated: int = 0
    skipped: int = 0


def record_source(ctx: "Context") -> dict:
    """The origin of the Core files this command copies. Repair binds it to the verified active package."""
    if ctx.record_source is None:
        ctx.record_source = source.input_record()
    return ctx.record_source


def _request_target(ctx: "Context", name: str, source_path: Path, dest: Path, *, kind: str, mode: str,
                    provider: str = "core", status: str | None = None) -> dict:
    """One target of a publish request; an addon target records the commit of its own checkout."""
    commit = record_source(ctx)["commit"] if provider == "core" else source.commit_of(Path(source_path))
    target = {"name": name, "kind": kind, "provider": provider, "source": str(source_path),
              "source_commit": commit, "dest": str(dest), "mode": mode}
    if status:
        target["status"] = status
    return target


def _relation_errors(ctx: "Context", observed: LedgerLocations) -> dict[str, str]:
    """The causes that stop every publish of this checkout."""
    errors = dict(observed.observation_errors)
    if observed.source_root is not None and observed.source_root != Path(record_source(ctx)['root']).resolve():
        # A malformed active host entry may still depend on the old common source
        # even when no individual ledger relationship was recorded before it broke.
        errors.update(observed.active_config_errors)
    return errors


def publish(ctx: "Context", targets: list[dict], progress: dict | None = None, *,
            repair_from: Path | None = None) -> ToolResult:
    """Build one publish request for `targets` and call `install_transaction.publish` in this process
    with the participants the install record names.

    The request names the host, the record root, the failure event log, the installer
    and source sections and the targets. A refused publish prints its cause on stderr
    (the structured gateway cause when it has one) and returns its exit code.
    """
    checkout = record_source(ctx)
    observed = installed_ledger_locations()
    errors = _relation_errors(ctx, observed)
    if ctx.platform in observed.host_config_errors:
        errors[ctx.platform] = observed.host_config_errors[ctx.platform]
    if errors:
        raise ValueError('Cannot confirm installed record source relationships: ' + '; '.join(
            f'{path}: {cause}' for path, cause in errors.items()))
    # Repair replaces missing targets. Only an installation assigns the installer and its release origin.
    installer, origin = {}, {}
    if repair_from is None:
        origin = checkout
        installer = {"commit": checkout["commit"], "schema_version": installed_payload.SCHEMA_VERSION}
        if source.install_request():
            installer["active_root"] = str(runner.REPO_ROOT)
    request = {"schema_version": install_transaction.REQUEST_SCHEMA_VERSION, "host": ctx.platform,
               "ghost_root": str(locations.record_root()), "event_log": str(locations.events_log(ctx.platform)),
               "installer": installer,
               "source": observed.publication_source(origin), "asset_root": str(repair_from or runner.REPO_ROOT),
               "targets": targets}
    if repair_from is None and any(target['kind'] == 'support' and target['name'] == '_shared' for target in targets):
        request['install_state'] = str(locations.install_state_path(ctx.platform))
    if progress:
        request["progress"] = progress
    participants = gateway_participant.participants(locations.record_root())
    try:
        install_transaction.publish(request, participants)
    except install_transaction.InstallTransactionError as exc:
        if hasattr(exc, "code"):
            print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))),
                  file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return ToolResult(getattr(exc, "exit_code", 1))
    return ToolResult(0)


def copy_target(ctx: "Context", target: "Target", dest: Path, kind: str = "skill", *,
                repair_from: Path | None = None) -> bool:
    """One copy publish of `target` at `dest`; `Staged copy replace failed: <dest>` on failure."""
    request = _request_target(ctx, target.name, target.path, dest, kind=kind, mode="copy", provider=target.provider)
    if not publish(ctx, [request], repair_from=repair_from).ok:
        report.error(f"Staged copy replace failed: {dest}")
        return False
    return True


def copy_targets_batch(ctx: "Context", batch: CopyBatch, *, progress_label: str | None = None) -> bool:
    """Every target of the batch in one publish request, with the progress label and the
    event file when they are set."""
    if not batch.pairs:
        return True
    targets = [_request_target(ctx, label, src, dest, kind=kind, mode="copy", provider=provider, status=status)
               for (src, dest), label, kind, status, provider
               in zip(batch.pairs, batch.labels, batch.kinds, batch.statuses, batch.providers)]
    progress = {}
    if progress_label:
        progress["label"] = progress_label
    event_file = report.state().event_file
    if event_file is not None:
        progress["event_file"] = str(event_file)
    if not publish(ctx, targets, progress).ok:
        report.error("Staged copy batch replace failed")
        return False
    return True


def link_or_copy(ctx: "Context", target: "Target", dest: Path, kind: str = "skill") -> int:
    """A link publish; only exit code 1 falls back to a copy publish, other codes are
    returned as they are."""
    request = _request_target(ctx, target.name, target.path, dest, kind=kind, mode="link", provider=target.provider)
    result = publish(ctx, [request])
    if result.ok:
        return 0
    if result.returncode != 1:
        return result.returncode
    return 0 if copy_target(ctx, target, dest, kind) else 1


def record_current_links(ctx: "Context", links: list[tuple["Target", Path, str]]) -> None:
    """One publish request for the `(target, install location, kind)` links that placement found
    already pointing at their source: the publish records them and replaces nothing. A failure is
    reported and the install goes on."""
    if not links:
        return
    targets = [_request_target(ctx, target.name, target.path, dest, kind=kind, mode="link", provider=target.provider,
                               status="current") for target, dest, kind in links]
    if not publish(ctx, targets).ok:
        report.warn("The install record could not record the targets that were already installed")


def _shared_target(source_root: Path | None = None) -> "Target":
    from installer.selection import Target

    return Target("_shared", (source_root or runner.REPO_ROOT) / "_shared")


def install_shared(ctx: "Context", skills_dir: Path, copy_only: bool, *, repair_from: Path | None = None) -> int:
    """`_shared` placement: copy in copy mode, skip when already linked to the
    source, else link-or-copy."""
    shared = _shared_target(repair_from)
    shared_dest = Path(skills_dir) / "_shared"
    if not shared.path.is_dir():
        return 0
    if copy_only or repair_from is not None:
        return 0 if copy_target(ctx, shared, shared_dest, "support", repair_from=repair_from) else 1
    if shared_dest.is_symlink() and os.readlink(shared_dest) == str(shared.path):
        report.warn("_shared. Already installed (skipped)")
        return 0
    return link_or_copy(ctx, shared, shared_dest, "support")


def _command_targets(ctx: "Context") -> list[dict]:
    """The commands in `bin/` that start this package."""
    targets = []
    for name, original in locations.MANAGEMENT_COMMANDS.items():
        entry = runner.REPO_ROOT / original
        if entry.is_file():
            target = _request_target(ctx, name, entry, locations.record_root() / 'bin' / name,
                                     kind='command', mode='copy')
            if original == 'install.sh':
                target['command_text'] = '#!/usr/bin/env bash\nexec bash ' + shlex.quote(str(entry)) + ' --management-entry "$@"\n'
            elif original == 'install.ps1':
                target['command_text'] = "& '" + str(entry).replace("'", "''") + "' --management-entry @args\nexit $LASTEXITCODE\n"
            targets.append(target)
    return targets


def sync_runtime_shared(ctx: "Context") -> bool:
    """Publish `<checkout>/_shared` and the runtime companions under the runtime root, and the commands in `bin/`,
    in one request; one publish, so a registered gateway stops and starts once."""
    shared = _shared_target()
    if not shared.path.is_dir():
        return True
    runtime_root = locations.runtime_root()
    targets = [_request_target(ctx, shared.name, shared.path, runtime_root / "_shared", kind="runtime", mode="copy")]
    for relative in RUNTIME_COMPANIONS:
        companion = runner.REPO_ROOT / relative
        if companion.is_file():
            targets.append(_request_target(ctx, relative, companion, runtime_root / relative, kind="runtime", mode="copy"))
    targets.extend(_command_targets(ctx))
    if not publish(ctx, targets).ok:
        report.error(f"Staged copy replace failed: {runtime_root / '_shared'}")
        return False
    report.detail_ok("runtime _shared -> copied")
    return True


def report_gateway(ctx: "Context") -> tuple[str, bool] | None:
    """One line about the context gateway after the runtime publish of an install and at the end of a repair,
    read-only, and the same line returned as `(summary, running)` for the report tail.

    With a `service` section: INFO with the service's runtime root while the service runs, WARN when it does
    not; the gateway participant already stopped and started it around the runtime publish. Without one:
    WARN when launchd has loaded a gateway service whose program lies under the runtime root
    (`gateway_participant.loaded_service`, the removal's own observation). The installer has no record of
    that service's files, so it neither stops nor starts it; the line names the registration command, which
    records the service and restarts it. An observation that is not clear counts as none and leaves one
    detail line. WARN when the record cannot be read.
    """
    root = locations.record_root()
    try:
        record = installed_payload.read(root)
    except installed_payload.RecordError:
        summary = f"Context gateway: the install record cannot be read: {installed_payload.path(root)}"
        report.warn(summary)
        return summary, False
    service = (record or {}).get("service")
    if not isinstance(service, dict):
        return _report_unrecorded_service()
    summary = f"Context gateway: registered, runtime {service.get('runtime_root')}"
    observed = gateway_participant.loaded_service()
    if observed.state == 'loaded' and observed.pid is not None:
        summary += ", service running"
        report.info(summary)
        return summary, True
    summary += (", service not running" if observed.state == 'absent'
                else ", service loaded; running process unconfirmed" if observed.state == 'loaded'
                else f", service state unknown ({observed.detail})")
    report.warn(summary)
    return summary, False


def _report_unrecorded_service() -> tuple[str, bool] | None:
    """The `report_gateway` line for a gateway service that launchd runs from the runtime root while the
    install record has no `service` section; None without one."""
    seen = gateway_participant.loaded_service()
    if seen.state in ("inconclusive", "unreadable"):
        report.info(f"Context gateway: launchd observation was inconclusive ({seen.detail}); "
                    "no unrecorded service reported")
        return None
    if seen.state != "loaded" or not seen.runs_under(locations.runtime_root()):
        return None
    summary = (f"Context gateway: a loaded service runs from the runtime without an install record entry; "
               f"register it with {report.install_command()} gateway --codex-dir <dir> --ledger-root <dir> "
               f"--state-dir <dir> --launch-agent <plist>, which restarts it")
    report.warn(summary)
    return summary, True


def activate(ctx: "Context", selections: dict[str, list[str]]) -> None:
    """Make this package the active installer, under the install lock and before any host file changes.

    Every stop condition and the Git hook registration come first, so a stop leaves the install record unchanged.
    The package then keeps `selections` and the source of this request as an unfinished installation, and one
    replacement of the record names this package. From that point on the management command, also one an earlier
    package published, starts this package, and this package recovers an interrupted placement. Each recorded command
    is then replaced for one of its users: from then on no earlier package starts before this one. A host that does not
    use a command yet receives it with its runtime (`sync_runtime_shared`)."""
    from installer import git_hooks

    root, folder = locations.record_root(), runner.REPO_ROOT.parent
    source.check_saved_package(folder)
    sections = {'installer': {'active_root': str(runner.REPO_ROOT)}}
    pending = installed_payload.pending_source(root)
    if pending is not None and installed_payload.matches_pending(installed_payload.read(root) or {}, pending):
        sections['source'] = pending['source']
    observed = installed_ledger_locations()
    errors = _relation_errors(ctx, observed)
    if selections.keys() <= observed.host_config_errors.keys():
        errors.update(observed.host_config_errors)
    if errors:
        raise ValueError('; '.join(f'{path}: {cause}' for path, cause in errors.items()))
    inventories, errors = migration_inventories(root)
    for error in errors:
        if error.host not in selections:
            raise error
    git_hooks.register(Path(source.input_record()['root']))
    for error in errors:
        backup_unreadable_inventory(error.file)
    report.run_logged(migrate_record, ctx, inventories=inventories)
    install_transaction._write_json(folder / 'version.json', dict(
        source._version_metadata(folder), status='candidate', selections=selections, source=source.input_record()))
    record = installed_payload.read(root)
    if record is not None:
        installed_payload.update(root, sections)
    if pending is not None:
        (root / installed_payload.PENDING_SOURCE_FILENAME).unlink()
    recorded = (record or {}).get('targets', {})
    commands: dict[str, list[dict]] = {}
    for target in _command_targets(ctx):
        key = installed_payload.target_key(target['dest'], recorded)
        users = set(recorded.get(key, {}).get('used_by', ())) - observed.host_config_errors.keys()
        if users:
            commands.setdefault(min(users), []).append(target)
    for host, targets in commands.items():
        if not publish(replace(ctx, platform=host), targets).ok:
            raise ValueError(f"Staged copy replace failed: {root / 'bin'}")


def migration_inventories(root: Path) -> tuple[dict[str, dict], list[installed_payload.InventoryError]]:
    if not installed_payload.awaits_migration(installed_payload.read(root)):
        return {}, []
    hosts = [path.stem for path in locations.install_state_manifests(root / 'install-state')]
    return installed_payload.earlier_inventories(root, hosts)


def migrate_record(ctx: "Context", *, inventories=None) -> None:
    """Create the install record once from an earlier installer's records (`installed_payload.migrate`)
    for every host, then the gateway's `service` section once from its installation receipt
    (`installed_payload.migrate_service`). An unreadable inventory stops migration before any write."""
    root = locations.record_root()
    try:
        if inventories is None:
            inventories, errors = migration_inventories(root)
            if errors:
                raise errors[0]
        runtime_targets = {}
        if inventories:
            import runtime_config
            config = runtime_config.load_config(env={'GHOST_ALICE_GHOST_ROOT': str(root)}, strict=True)
            for host in inventories:
                binding = config.get('install_locations', {}).get(host, {})
                shared = Path(binding.get('runtime_shared_dir') or locations.runtime_shared_dir())
                for relative in ('_shared', *RUNTIME_COMPANIONS):
                    origin = binding.get('core_source_root')
                    runtime_targets.setdefault(host, []).append(dict(target_name=relative,
                        dest_path=str(shared if relative == '_shared' else shared.parent / relative),
                        source_path=str(Path(origin) / relative) if origin else '', install_mode='copy'))
        summary = installed_payload.migrate(root, list(inventories), installer_commit=record_source(ctx)["commit"],
                                            runtime_targets=runtime_targets, inventories=inventories)
    except (installed_payload.RecordError, installed_payload.NotLockHolder, OSError) as exc:
        report.warn(f"Install record migration skipped: {exc}")
    else:
        if summary is not None:
            report.info(f"Install record created from earlier install records: {summary['targets']} targets "
                        f"(migrated {summary['migrated']}, unknown {summary['unknown']})")
    try:
        service = installed_payload.migrate_service(root)
    except (installed_payload.RecordError, installed_payload.NotLockHolder, OSError) as exc:
        report.warn(f"Gateway record migration skipped: {exc}")
        return
    if service is not None:
        report.info(f"Gateway record created from its installation receipt: {service['label']} "
                    f"(companion targets added {service['companions']})")


def backup_unreadable_inventory(inventory: Path) -> None:
    import install_state_writer
    moved = install_state_writer.preserve_unreadable(inventory)
    if moved:
        report.warn(report.message('inventory_preserved', file=inventory, backup=moved[0], cause=moved[1]))


def cleanup_obsolete_copies(ctx: "Context", collected: "Collected") -> bool:
    """Hand verified placements to the original-copy cleanup owner before final success."""
    from uninstall_cleanup import cleanup_obsolete_copies as cleanup
    root = locations.record_root()
    targets = [(target.name, target.path, Path(ctx.skills_dir)/target.name, target.provider)
               for target in collected.all_targets]
    if collected.support_target_total:
        targets.append(('_shared', runner.REPO_ROOT/'_shared', Path(ctx.skills_dir)/'_shared', 'core'))
    record = installed_payload.read(root)
    targets.extend((entry['name'], Path(entry['source']), Path(dest), entry['provider'])
                   for dest, entry in (record or {}).get('targets', {}).items()
                   if entry.get('kind') == 'runtime' and ctx.platform in entry.get('used_by', []))
    completed = [dict(target_name=name, source_path=str(src), dest_path=str(dest), provider=provider,
                      source_root=str(runner.REPO_ROOT), action='replacement-verified')
                 for name, src, dest, provider in targets]
    results = cleanup(root=root, platform=ctx.platform, completed=completed, after_verified_install=True)
    for item in results:
        message = f"Previous original {item['path']}: {item['action']} ({item['reason']})"
        if item['action'] == 'manual-review':
            report.warn(message)
        elif item['action'] != 'preserved':
            report.info(message)
    return not any(item['action'] == 'manual-review' for item in results)


def detect_install_mode(dest: Path, copy_only: bool) -> str:
    """`symlink`, `copy`, `copy-fallback` or `missing`."""
    dest = Path(dest)
    if dest.is_symlink():
        return "symlink"
    if dest.exists():
        return "copy" if copy_only else "copy-fallback"
    return "missing"


def remove_installed_target(ctx: "Context", label: str, target: Path, *,
                            source_path: Path | None = None, apply_changes: bool = True,
                            only_if_absent: bool = False) -> dict:
    """Use full removal's ownership and preservation owner for one selected slot.

    A preview returns its proposed action. An applied removal also retires only that
    destination from the current host inventory; historical publish records stay.
    """
    from uninstall_cleanup import _target_item, _apply_target_item, release_target_records

    target = Path(target)
    state_path = locations.install_state_path(ctx.platform)
    try:
        manifest = installed_payload.read_inventory(state_path, platform=ctx.platform)
    except (OSError, ValueError) as exc:
        return dict(target_name=label, path=str(target), action="manual-review",
                    reason="install-state-unreadable", detail=str(exc), removal_incomplete=True)
    manifest = manifest or dict(platform=ctx.platform, source_root=str(runner.REPO_ROOT), targets=[])
    matches = [row for row in manifest['targets']
               if installed_payload.inventory_destination(row['dest_path']) == target]
    row = matches[0] if matches else None
    if any(row['target_name'] != label for row in matches):
        return dict(target_name=label, path=str(target), action="manual-review",
                    reason="target-name-mismatch", removal_incomplete=True)
    if row is None:
        row = dict(target_name=label, dest_path=str(target), install_mode=detect_install_mode(target, False))
        if source_path is not None:
            row["source_path"] = str(source_path)
    result = _target_item(row, manifest=manifest, manifest_path=state_path,
                          platform=ctx.platform)
    if apply_changes and not only_if_absent:
        try:
            _apply_target_item(result, row, manifest=manifest, manifest_path=state_path, platform=ctx.platform)
        except (OSError, ValueError) as exc:
            result.update(action='manual-review', reason='target-removal-failed', detail=str(exc),
                          file_state='present' if os.path.lexists(target) else 'absent')
    if only_if_absent and result["action"] != "missing":
        result.update(action="manual-review", reason="target-appeared-after-preview")
    if result["action"] == "manual-review":
        result["removal_incomplete"] = True
    if apply_changes and result["action"] in {"removed", "quarantined-removed", "missing", "reference-released"}:
        try:
            release_target_records(root=locations.record_root(), platform=ctx.platform, destination=target,
                                   kind='support' if label == '_shared' else 'skill', state_path=state_path)
        except (OSError, ValueError, installed_payload.NotLockHolder) as exc:
            result.update(action='manual-review', reason='target-record-cleanup-failed', detail=str(exc), removal_incomplete=True,
                          file_state='present' if os.path.lexists(target) else 'absent')
            return result
        if result["action"] != "missing":
            report.ok(f"{label}: current host reference released; shared files retained" if
                      result['action'] == 'reference-released' else f"{label} removed")
            if result.get("backup_path"):
                report.info(f"Preserved {label}: {result['backup_path']}")
    return result


def guard_gateway_removal(ctx: "Context", targets: list[Path]) -> int:
    """The gateway participant's judgment of removing `targets`, in this process: 0 to go on (the
    service was independent or is detached), else the cause's exit code; 0 with no targets."""
    if not targets:
        return 0
    return gateway_participant.guard(locations.record_root(), [Path(target) for target in targets])


def verify_after_copy(ctx: "Context", skills_dir: Path, copy_only: bool, collected: "Collected") -> bool:
    """Verify every initially selected target, including a source or destination that disappeared after selection."""
    skills_dir = Path(skills_dir)
    shared_src = runner.REPO_ROOT / "_shared"
    args = ["--platform", ctx.platform, "--state-root", str(locations.install_state_path(ctx.platform).parent)]
    targets: list[str] = []
    if collected.support_target_total:
        targets.extend(["--target", "_shared", str(shared_src), str(skills_dir / "_shared"),
                        detect_install_mode(skills_dir / "_shared", copy_only)])
    for target in collected.all_targets:
        dest = skills_dir / target.name
        targets.extend(["--target", target.name, str(target.path), str(dest), detect_install_mode(dest, copy_only)])
    if not targets:
        return True
    if not runner.run_tool("merge-companion/scripts/install_verifier.py", *args, *targets).ok:
        report.error("Post-install source/destination verification failed; aborting before snapshot")
        return False
    return True


def snapshot_after_install(ctx: "Context") -> bool:
    """After selected targets pass verification, snapshot the currently installed managed copies, including
    earlier installs, then create the pending manifest when absent. This inventory has its own observation time."""
    pending_root = locations.pending_root(ctx.platform)
    snapshot = pending_root / "snapshot.json"
    try:
        pending_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.error(f"merge-companion snapshot directory cannot be created; aborting install: {pending_root}")
        return False
    result = runner.run_tool("merge-companion/scripts/snapshot_cli.py", "--output", str(snapshot),
                             "--platform", ctx.platform, "--skills-dir", str(ctx.skills_dir),
                             "--install-record-root", str(locations.record_root()))
    if not result.ok:
        report.error("merge-companion snapshot capture failed; aborting install")
        return False
    return write_empty_pending_manifest_if_missing(ctx, ctx.platform, pending_root / "manifest.json")


def write_empty_pending_manifest_if_missing(ctx: "Context", platform_dir: str, manifest: Path) -> bool:
    """`pending_manifest_writer.py <manifest> <platform>` when the manifest is missing."""
    if Path(manifest).is_file():
        return True
    return runner.run_tool("_shared/pending_manifest_writer.py", str(manifest), platform_dir).ok


def write_install_state_manifest(ctx: "Context", skills_dir: Path, copy_only: bool, collected: "Collected") -> bool:
    """`install_state_writer.py <platform> <root> <branch> <head> <dirty> <state> NAME SRC DEST MODE ...`."""
    skills_dir = Path(skills_dir)
    state_path = locations.install_state_path(ctx.platform)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.error(f"Could not create install-state directory; aborting install: {state_path.parent}")
        return False
    info = source.resolve_source()
    targets: list[str] = []
    shared_src = runner.REPO_ROOT / "_shared"
    if collected.support_target_total:
        targets.extend(["_shared", str(shared_src), str(skills_dir / "_shared"),
                        detect_install_mode(skills_dir / "_shared", copy_only)])
    for target in collected.all_targets:
        dest = skills_dir / target.name
        targets.extend([target.name, str(target.path), str(dest), detect_install_mode(dest, copy_only)])
    result = runner.run_tool("_shared/install_state_writer.py", ctx.platform, str(runner.REPO_ROOT), info.branch, info.head,
                             info.dirty, str(state_path), *targets)
    if not result.ok:
        report.error(f"Install-state manifest write failed; aborting install: {state_path}")
        return False
    report.info(f"Install-state manifest: {state_path}")
    return True
