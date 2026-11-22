"""Store response-route choices at their original event sequence in the caller's transaction."""
from __future__ import annotations

import json

from source_reference import json_digest


FORMAT = "route-registration.v1"
EVENT = "response-route-recorded"
ROUTE_FIELDS = ("platform", "session_id", "input_event_id", "schema_version", "boundary_ref", "routing_surface")
CHOICE_FIELDS = ("work_units", "selected_work_unit_ids", "selected_criteria")
REFERENCE_FIELDS = ("work_unit_id", "contract_revision", "criterion_id")


class RegistrationError(ValueError):
    code = "route-schema"


class RegistrationNotFound(RegistrationError):
    code = "registration-not-found"


class NotRegistration(RegistrationError):
    code = "not-registration"


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS route_units (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, registration_sequence INTEGER NOT NULL,
        work_unit_id TEXT NOT NULL, contract_revision INTEGER NOT NULL,
        selectable INTEGER NOT NULL CHECK (selectable IN (0, 1)),
        submission_position INTEGER NOT NULL CHECK (submission_position >= 0),
        selection_position INTEGER CHECK (selection_position IS NULL OR (selection_position >= 0 AND selectable = 1)),
        PRIMARY KEY (platform, session_id, registration_sequence, work_unit_id, contract_revision),
        UNIQUE (platform, session_id, registration_sequence, work_unit_id),
        UNIQUE (platform, session_id, registration_sequence, submission_position),
        UNIQUE (platform, session_id, registration_sequence, selection_position),
        FOREIGN KEY (platform, session_id, registration_sequence)
            REFERENCES intent_events (platform, session_id, sequence),
        FOREIGN KEY (platform, session_id, work_unit_id, contract_revision)
            REFERENCES work_unit_revisions (platform, session_id, work_unit_id, contract_revision)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS route_criteria (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, registration_sequence INTEGER NOT NULL,
        work_unit_id TEXT NOT NULL, contract_revision INTEGER NOT NULL, criterion_id TEXT NOT NULL,
        selection_position INTEGER NOT NULL CHECK (selection_position >= 0),
        PRIMARY KEY (platform, session_id, registration_sequence, work_unit_id, contract_revision, criterion_id),
        UNIQUE (platform, session_id, registration_sequence, selection_position),
        FOREIGN KEY (platform, session_id, registration_sequence, work_unit_id, contract_revision)
            REFERENCES route_units (platform, session_id, registration_sequence, work_unit_id, contract_revision),
        FOREIGN KEY (platform, session_id, work_unit_id, contract_revision, criterion_id)
            REFERENCES revision_criteria (platform, session_id, work_unit_id, contract_revision, criterion_id)
    )""")


def _metadata(projection):
    metadata = {k: projection[k] for k in ROUTE_FIELDS}
    if "declared_remaining_work" in projection:
        metadata["declared_remaining_work"] = projection["declared_remaining_work"]
    return metadata


def _digest(projection):
    # Only the immutable registration participates. Reader diagnostics and current input do not.
    return json_digest({**_metadata(projection), **{k: projection[k] for k in CHOICE_FIELDS}})


def registration_event(projection, timestamp):
    return dict(event=EVENT, ts=timestamp, registration_format=FORMAT, **_metadata(projection),
                response_route_digest=_digest(projection))


def write_selection(connection, sequence, projection):
    """The caller has validated the projection and allocated the event in this write transaction."""
    scope = (projection["platform"], projection["session_id"], sequence)
    selected = {name: pos for pos, name in enumerate(projection["selected_work_unit_ids"])}
    connection.executemany("""INSERT INTO route_units
        (platform, session_id, registration_sequence, work_unit_id, contract_revision,
         selectable, submission_position, selection_position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(*scope, unit["work_unit_id"], unit["contract_revision"], unit["selectable"],
          position, selected.get(unit["work_unit_id"])) for position, unit in enumerate(projection["work_units"])])
    connection.executemany("""INSERT INTO route_criteria
        (platform, session_id, registration_sequence, work_unit_id, contract_revision,
         criterion_id, selection_position) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(*scope, row["work_unit_id"], row["contract_revision"], row["criterion_id"], position)
         for position, row in enumerate(projection["selected_criteria"])])


def _event_at(connection, platform, session_id, sequence):
    if type(sequence) is not int or sequence <= 0:
        raise RegistrationError("registration sequence must be a positive integer")
    row = None
    if connection is not None and sequence <= (1 << 63) - 1:
        row = connection.execute("""SELECT event_json FROM intent_events
            WHERE platform=? AND session_id=? AND sequence=?""", (platform, session_id, sequence)).fetchone()
    if row is None:
        raise RegistrationNotFound(f"registration {sequence} does not exist")
    try:
        event = json.loads(row[0])
    except (TypeError, ValueError) as error:
        raise RegistrationError("stored event JSON is invalid") from error
    if not isinstance(event, dict):
        raise RegistrationError("stored event must be an object")
    if event.get("event") != EVENT:
        raise NotRegistration(f"event {sequence} is not a response route registration")
    if event.get("platform") != platform or event.get("session_id") != session_id:
        raise RegistrationError("registration event owner is invalid")
    if "registration_format" in event and event["registration_format"] != FORMAT:
        raise RegistrationError("unsupported registration format")
    return event


def _native_projection(connection, platform, session_id, sequence, event):
    scope = (platform, session_id, sequence)
    try:
        route = _metadata(event)
    except KeyError as error:
        raise RegistrationError(f"registration event is missing {error.args[0]}") from error
    if any(key in event for key in CHOICE_FIELDS):
        raise RegistrationError("registered selections have conflicting representations")
    owner = dict(platform=platform, session_id=session_id)
    units, selected_positions = [], []
    for name, revision, selectable, position, selection, kind, has_owner in connection.execute("""
        SELECT u.work_unit_id, u.contract_revision, u.selectable, u.submission_position,
               u.selection_position, r.kind, r.has_owner
          FROM route_units AS u LEFT JOIN work_unit_revisions AS r
            USING (platform, session_id, work_unit_id, contract_revision)
         WHERE u.platform=? AND u.session_id=? AND u.registration_sequence=?
         ORDER BY u.submission_position""", scope):
        if kind is None or has_owner != 1:
            raise RegistrationError(f"broken frozen work unit reference for {name} revision {revision}")
        if position != len(units):
            raise RegistrationError("stored work unit submission order is invalid")
        units.append(dict(work_unit_id=name, contract_revision=revision, kind=kind,
                          selectable=bool(selectable), owner=owner.copy()))
        if selection is not None:
            selected_positions.append((selection, name))
    selected_positions.sort()
    if any(position != expected for expected, (position, _) in enumerate(selected_positions)):
        raise RegistrationError("stored work unit selection order is invalid")
    selected_ids = [name for _, name in selected_positions]
    criteria = []
    for name, revision, criterion_id, position, value_no, summary, source in connection.execute("""
        SELECT s.work_unit_id, s.contract_revision, s.criterion_id, s.selection_position,
               d.value_no, v.criterion_text, v.origin_kind
          FROM route_criteria AS s LEFT JOIN revision_criteria AS d
            USING (platform, session_id, work_unit_id, contract_revision, criterion_id)
          LEFT JOIN criterion_values AS v
            USING (platform, session_id, work_unit_id, contract_revision, criterion_id, value_no)
         WHERE s.platform=? AND s.session_id=? AND s.registration_sequence=?
         ORDER BY s.selection_position""", scope):
        if value_no is None or summary is None or source is None:
            raise RegistrationError(f"broken frozen criterion reference for {name} revision {revision} {criterion_id}")
        if position != len(criteria):
            raise RegistrationError("stored criterion selection order is invalid")
        criteria.append(dict(work_unit_id=name, contract_revision=revision, criterion_id=criterion_id,
                             id=criterion_id, summary=summary, source=source, selectable=True))
    route.update(work_units=units, selected_work_unit_ids=selected_ids, selected_criteria=criteria)
    if _digest(route) != event.get("response_route_digest"):
        raise RegistrationError("stored response route registration digest mismatch")
    return route


def ready_projection(connection, platform, session_id, sequence):
    """Reconstruct the originally accepted projection, before current-input and eligibility checks."""
    try:
        event = _event_at(connection, platform, session_id, sequence)
    except (RegistrationNotFound, NotRegistration) as error:
        raise RegistrationError(f"invalid latest registration reference: {error}") from error
    if event.get("registration_format") != FORMAT:
        raise RegistrationError("latest registration pointer does not reference a native registration")
    return dict(_native_projection(connection, platform, session_id, sequence, event), status="ready")


def _legacy_field(connection, platform, session_id, field):
    row = connection.execute("""SELECT json_extract(state_json, ?) FROM sessions
        WHERE platform=? AND session_id=?""", ("$." + field, platform, session_id)).fetchone()
    return json.loads(row[0]) if row and row[0] is not None else None


def _snapshot_digests(stored):
    if not isinstance(stored, dict) or not all(key in stored for key in ROUTE_FIELDS + CHOICE_FIELDS):
        return {}
    # A subsequent input or portable import may have marked the snapshot stale.
    original = dict(stored, status="ready")
    legacy_digest = json_digest(original)
    original.pop("status")
    # A two-file export can also contain a native digest and public event, without relationship rows.
    return {legacy_digest: original, _digest(stored): {**_metadata(stored), **{k: stored[k] for k in CHOICE_FIELDS}}}


def legacy_registration_sequence(connection, platform, session_id, stored):
    digests = _snapshot_digests(stored)
    if not digests:
        return None
    placeholders = ",".join("?" for _ in digests)
    candidates = connection.execute(f"""
        SELECT sequence FROM intent_events WHERE platform=? AND session_id=?
        AND json_extract(event_json,'$.event')=? AND json_extract(event_json,'$.input_event_id')=?
        AND json_extract(event_json,'$.response_route_digest') IN ({placeholders})
        LIMIT 2""", (platform, session_id, EVENT, stored["input_event_id"], *digests)).fetchall()
    if len(candidates) != 1:
        return None
    sequence = candidates[0][0]
    event = _event_at(connection, platform, session_id, sequence)
    _check_legacy_selection(digests[event["response_route_digest"]], event)
    return sequence


def _check_legacy_selection(original, event):
    keys = [{k: row.get(k) for k in REFERENCE_FIELDS} for row in original["selected_criteria"]]
    if (any(original.get(k) != event.get(k) for k in ("platform", "session_id", "input_event_id"))
            or original["selected_work_unit_ids"] != event.get("selected_work_unit_ids")
            or keys != event.get("selected_criteria")):
        raise RegistrationError("legacy registration selections disagree with the authenticated route")


def _legacy_registration(connection, platform, session_id, sequence, event, legacy_units):
    """Old events recorded selected keys only. Recover other fields only when the original proves them."""
    selected = event.get("selected_work_unit_ids")
    references = event.get("selected_criteria")
    if not isinstance(selected, list) or not isinstance(references, list):
        raise RegistrationError("stored legacy registration has invalid selection lists")
    if not all(isinstance(name, str) and name for name in selected) or len(set(selected)) != len(selected):
        raise RegistrationError("stored legacy selected work units are invalid")
    expected_digest = event.get("response_route_digest")
    stored = _legacy_field(connection, platform, session_id, "response_route")
    original = _snapshot_digests(stored).get(expected_digest)
    if original is not None:
        _check_legacy_selection(original, event)
        return dict(original, registration_sequence=sequence, response_route_digest=expected_digest,
                    digest_verification="verified")

    registry = legacy_units() if legacy_units is not None else _legacy_field(connection, platform, session_id, "work_units")
    owner = dict(platform=platform, session_id=session_id)
    definitions = {}
    for unit in registry or []:
        if isinstance(unit, dict) and unit.get("owner") == owner:
            definitions.setdefault((unit.get("work_unit_id"), unit.get("contract_revision")), []).append(unit)
    revisions, criteria = {}, []
    for ref in references:
        if not isinstance(ref, dict) or any(k not in ref for k in REFERENCE_FIELDS):
            raise RegistrationError("stored legacy criterion reference is invalid")
        name, revision, criterion_id = (ref[k] for k in REFERENCE_FIELDS)
        if (not isinstance(name, str) or name not in selected or type(revision) is not int or revision < 1
                or not isinstance(criterion_id, str) or not criterion_id
                or revisions.setdefault(name, revision) != revision):
            raise RegistrationError("stored legacy criterion owner or revision is invalid")
        matches = definitions.get((name, revision), [])
        values = [c for c in matches[0].get("criteria", []) if c.get("id") == criterion_id] if len(matches) == 1 else []
        resolved = {k: ref[k] for k in REFERENCE_FIELDS}
        if len(values) == 1:
            resolved.update(id=criterion_id, summary=values[0]["summary"], source=values[0]["source"], selectable=True)
        else:
            resolved["definition_unavailable"] = True
        criteria.append(resolved)
    units = []
    for name in selected:
        revision = revisions.get(name)
        unit = dict(work_unit_id=name, contract_revision=revision, selectable=True)
        matches = definitions.get((name, revision), [])
        if revision is not None and len(matches) == 1 and "kind" in matches[0]:
            unit.update(kind=matches[0]["kind"], owner=owner.copy())
        units.append(unit)
    return dict(**owner, input_event_id=event.get("input_event_id"), registration_sequence=sequence,
                work_units=units, selected_work_unit_ids=selected, selected_criteria=criteria,
                response_route_digest=expected_digest, digest_verification="unavailable",
                unavailable_fields=["work_units.submission_order", "work_units.unselected",
                                    "routing_surface", "boundary_ref", "declared_remaining_work"])


def read_registration(connection, platform, session_id, sequence, *, legacy_units=None):
    """Read a registration in the caller's read snapshot; current input and selectable flags are irrelevant."""
    event = _event_at(connection, platform, session_id, sequence)
    if "registration_format" not in event:
        return _legacy_registration(connection, platform, session_id, sequence, event, legacy_units)
    route = _native_projection(connection, platform, session_id, sequence, event)
    return dict(route, registration_sequence=sequence, response_route_digest=event["response_route_digest"],
                digest_verification="verified")


def public_event(connection, platform, session_id, sequence, event):
    """Preserve the existing event-reader and two-file JSON export shapes."""
    if event.get("registration_format") != FORMAT:
        return event
    route = _native_projection(connection, platform, session_id, sequence, event)
    fields = ("ts", "event", "platform", "session_id", "input_event_id",
              "response_route_digest", "ledger_revision", "mutation_id")
    public = {k: event[k] for k in fields if k in event}
    public["selected_work_unit_ids"] = route["selected_work_unit_ids"]
    public["selected_criteria"] = [{k: row[k] for k in REFERENCE_FIELDS} for row in route["selected_criteria"]]
    return public
