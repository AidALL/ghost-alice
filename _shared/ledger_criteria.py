"""Store current criteria and the definitions frozen by work-unit registration."""
from __future__ import annotations

import json

import ledger_registrations
import response_route as routes
from source_reference import json_digest


_FLAGS = {"admitted", "selectable"}
_COORDINATES = {"work_unit_id", "contract_revision"}
_CURRENT_FIELDS = _COORDINATES | _FLAGS | {"id", "summary", "source"}
_UNIT_FIELDS = _COORDINATES | _FLAGS | {"owner", "kind", "criteria"}


def create_schema(connection):
    """Create the tables within the caller's transaction, including all scoped references."""
    connection.execute("""CREATE TABLE IF NOT EXISTS criterion_values (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, work_unit_id TEXT NOT NULL,
        contract_revision INTEGER NOT NULL CHECK(contract_revision > 0), criterion_id TEXT NOT NULL,
        value_no INTEGER NOT NULL CHECK(value_no > 0), criterion_text TEXT NOT NULL, origin_kind TEXT NOT NULL,
        PRIMARY KEY(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no),
        FOREIGN KEY(platform,session_id) REFERENCES sessions(platform,session_id)) WITHOUT ROWID""")
    connection.execute("""CREATE TABLE IF NOT EXISTS current_criteria (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
        criterion_id TEXT NOT NULL, work_unit_id TEXT, contract_revision INTEGER, value_no INTEGER,
        criterion_text TEXT, origin_kind TEXT, selectable INTEGER NOT NULL CHECK(selectable IN (0,1)),
        flag_keys INTEGER NOT NULL CHECK(flag_keys BETWEEN 0 AND 3), raw_flag_json TEXT, legacy_fields_json TEXT,
        PRIMARY KEY(platform,session_id,position),
        CHECK((work_unit_id IS NULL AND contract_revision IS NULL AND value_no IS NULL
               AND criterion_text IS NOT NULL AND origin_kind IS NOT NULL) OR
              (work_unit_id IS NOT NULL AND contract_revision IS NOT NULL AND value_no IS NOT NULL
               AND criterion_text IS NULL AND origin_kind IS NULL)),
        FOREIGN KEY(platform,session_id) REFERENCES sessions(platform,session_id),
        FOREIGN KEY(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
          REFERENCES criterion_values(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
        ) WITHOUT ROWID""")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS current_criteria_owned
        ON current_criteria(platform,session_id,work_unit_id,contract_revision,criterion_id)
        WHERE work_unit_id IS NOT NULL""")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS current_criteria_unowned
        ON current_criteria(platform,session_id,criterion_id) WHERE work_unit_id IS NULL""")
    connection.execute("""CREATE INDEX IF NOT EXISTS current_criteria_selectable_owner
        ON current_criteria(platform,session_id,work_unit_id,contract_revision)
        WHERE work_unit_id IS NOT NULL AND selectable=1""")
    connection.execute("""CREATE TABLE IF NOT EXISTS work_unit_revisions (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, work_unit_id TEXT NOT NULL,
        contract_revision INTEGER NOT NULL CHECK(contract_revision > 0), position INTEGER NOT NULL CHECK(position >= 0),
        kind TEXT, has_owner INTEGER NOT NULL CHECK(has_owner IN (0,1)),
        has_definitions INTEGER NOT NULL CHECK(has_definitions IN (0,1)),
        selectable INTEGER NOT NULL CHECK(selectable IN (0,1)),
        flag_keys INTEGER NOT NULL CHECK(flag_keys BETWEEN 0 AND 3), raw_flag_json TEXT, legacy_fields_json TEXT,
        PRIMARY KEY(platform,session_id,work_unit_id,contract_revision), UNIQUE(platform,session_id,position),
        FOREIGN KEY(platform,session_id) REFERENCES sessions(platform,session_id)) WITHOUT ROWID""")
    connection.execute("""CREATE TABLE IF NOT EXISTS revision_criteria (
        platform TEXT NOT NULL, session_id TEXT NOT NULL, work_unit_id TEXT NOT NULL,
        contract_revision INTEGER NOT NULL, criterion_id TEXT NOT NULL, value_no INTEGER NOT NULL,
        position INTEGER NOT NULL CHECK(position >= 0),
        PRIMARY KEY(platform,session_id,work_unit_id,contract_revision,criterion_id),
        UNIQUE(platform,session_id,work_unit_id,contract_revision,position),
        FOREIGN KEY(platform,session_id,work_unit_id,contract_revision)
          REFERENCES work_unit_revisions(platform,session_id,work_unit_id,contract_revision),
        FOREIGN KEY(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
          REFERENCES criterion_values(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
        ) WITHOUT ROWID""")


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _extras(row, columns):
    extra = {key: value for key, value in row.items() if key not in columns}
    return _json(extra) if extra else None


def _flag_columns(row):
    raw = routes.ledger.selectable_value(row)
    keys = int("selectable" in row) + 2 * int("admitted" in row)
    raw_json = _json(raw) if keys and type(raw) is not bool else None
    return int(raw is True), keys, raw_json


def _public_fields(eligible, keys, raw, extras):
    result = json.loads(extras) if extras is not None else {}
    flag = json.loads(raw) if raw is not None else bool(eligible)
    if keys & 1:
        result["selectable"] = flag
    if keys & 2:
        result["admitted"] = flag
    return result


def _value_number(connection, key, text, origin):
    where = "platform=? AND session_id=? AND work_unit_id=? AND contract_revision=? AND criterion_id=?"
    found = connection.execute(f"SELECT value_no FROM criterion_values WHERE {where} "
                               "AND criterion_text=? AND origin_kind=?", (*key, text, origin)).fetchone()
    if found:
        return found[0]
    number = connection.execute(f"SELECT COALESCE(MAX(value_no),0)+1 FROM criterion_values WHERE {where}",
                                key).fetchone()[0]
    connection.execute("""INSERT INTO criterion_values
        (platform,session_id,work_unit_id,contract_revision,criterion_id,value_no,criterion_text,origin_kind)
        VALUES (?,?,?,?,?,?,?,?)""", (*key, number, text, origin))
    return number


_CURRENT_QUERY = """SELECT c.position,c.work_unit_id,c.contract_revision,c.criterion_id,
         coalesce(v.criterion_text,c.criterion_text), coalesce(v.origin_kind,c.origin_kind),
         c.selectable,c.flag_keys,c.raw_flag_json,c.legacy_fields_json
    FROM current_criteria c {index} LEFT JOIN criterion_values v
    USING(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
    WHERE c.platform=? AND c.session_id=?"""


def _public_criterion(row):
    _, unit, revision, criterion_id, summary, source, eligible, keys, raw, extras = row
    if summary is None or source is None:
        raise routes.RouteSchemaError("current criterion value reference is missing")
    result = _public_fields(eligible, keys, raw, extras)
    result.update(id=criterion_id, summary=summary, source=source)
    if unit is not None:
        result.update(work_unit_id=unit, contract_revision=revision)
    return result


def read_current(connection, platform, session_id, criterion_keys=None):
    """Read the complete ordered list or the entries with the specified owned keys."""
    owner = platform, session_id
    if criterion_keys is None:
        rows = connection.execute(_CURRENT_QUERY.format(index="") + " ORDER BY c.position", owner)
    else:
        rows = []
        by_key = (_CURRENT_QUERY.format(index="INDEXED BY current_criteria_owned")
                  + " AND c.work_unit_id=? AND c.contract_revision=? AND c.criterion_id=?")
        for key in set(criterion_keys):
            rows.extend(connection.execute(by_key, (*owner, *key)))
        rows.sort(key=lambda row: row[0])
    return [_public_criterion(row) for row in rows]


def selectable_execution_units(connection, platform, session_id, unit_keys):
    """Find one selectable current criterion per requested execution unit using its partial index."""
    eligible = set()
    for key in set(unit_keys):
        if connection.execute("""SELECT 1 FROM current_criteria WHERE platform=? AND session_id=?
                AND work_unit_id=? AND contract_revision=? AND selectable=1 LIMIT 1""",
                (platform, session_id, *key)).fetchone():
            eligible.add(key)
    return eligible


def read_registry(connection, platform, session_id):
    """Rebuild the work-unit registry in original order, preserving absent or empty definitions."""
    units = []
    for position, unit, revision, kind, has_owner, has_definitions, eligible, keys, raw, extras, cid, text, origin in (
        connection.execute("""SELECT u.position,u.work_unit_id,u.contract_revision,u.kind,u.has_owner,
                u.has_definitions,u.selectable,u.flag_keys,u.raw_flag_json,u.legacy_fields_json,
                d.criterion_id,v.criterion_text,v.origin_kind
            FROM work_unit_revisions u LEFT JOIN revision_criteria d
              USING(platform,session_id,work_unit_id,contract_revision)
            LEFT JOIN criterion_values v
              USING(platform,session_id,work_unit_id,contract_revision,criterion_id,value_no)
            WHERE u.platform=? AND u.session_id=? ORDER BY u.position,d.position""", (platform, session_id))
    ):
        if position > len(units):
            raise routes.RouteSchemaError("stored work unit revision position is missing")
        if len(units) == position:
            row = _public_fields(eligible, keys, raw, extras)
            row.update(work_unit_id=unit, contract_revision=revision)
            if kind is not None:
                row["kind"] = kind
            if has_owner:
                row["owner"] = dict(platform=platform, session_id=session_id)
            if has_definitions:
                row["criteria"] = []
            units.append(row)
        if cid is not None:
            if text is None or origin is None or not has_definitions:
                raise routes.RouteSchemaError("frozen criterion definition is missing")
            units[position]["criteria"].append(dict(id=cid, summary=text, source=origin))
    return units


def write_current(connection, platform, session_id, criteria, *, imported=False, event=None):
    """Replace the ordered current list, keeping legacy field spelling and unchanged value references."""
    owner = platform, session_id
    if not isinstance(criteria, list):
        raise routes.RouteSchemaError("stored acceptance criteria must be a list")
    prior = {row[0]: _public_criterion(row) for row in connection.execute(_CURRENT_QUERY.format(index=""), owner)}
    # Compare canonical JSON to distinguish e.g. a legacy flag of 1 from true.
    changed = [i for i, row in enumerate(criteria) if i not in prior or _json(row) != _json(prior[i])]
    removed = [i for i in prior if i >= len(criteria)]
    if not changed and not removed:
        return
    if event is not None:
        # Most updates already authenticate these exact rows in semantic_changes.
        # An ownership binding or a legacy field outside that projection needs its own digest.
        change = dict(before_digest=json_digest([prior[i] for i in sorted(prior)]), after_digest=json_digest(criteria))
        if change != event.get("semantic_changes", {}).get("acceptance_criteria"):
            event["criterion_rows_change"] = change
    preserved = {_json(row) for row in prior.values() if "work_unit_id" in row
                 and type(routes.ledger.selectable_value(row)) is not bool}
    seen = set()
    for row in criteria:
        if not isinstance(row, dict) or not {"id", "summary", "source"} <= row.keys():
            raise routes.RouteSchemaError("stored criterion fields are incomplete")
        if _COORDINATES & row.keys():
            validation = row
            if ((imported or preserved) and type(routes.ledger.selectable_value(row)) is not bool
                    and (imported or _json(row) in preserved)):
                # Legacy import retains single raw or absent flags. Validate every other
                # owned field here; route validation still checks the original flag.
                validation = dict(routes.selection_view(row), selectable=False)
            key = routes._criterion(validation)
        else:
            if not all(isinstance(row[key], str) for key in ("id", "summary", "source")):
                raise routes.RouteSchemaError("unbound criterion ID, text and source must be strings")
            key = None, None, row["id"]
        if key in seen:
            raise routes.RouteSchemaError("stored criterion is ambiguous")
        seen.add(key)
    # Free old keys before writing the changed positions. Rebinding and reordering
    # can both reuse a key that belonged to a different position.
    connection.executemany("DELETE FROM current_criteria WHERE platform=? AND session_id=? AND position=?",
                           [(*owner, i) for i in changed if i in prior] + [(*owner, i) for i in removed])
    for position in changed:
        row = criteria[position]
        unit, revision, cid = row.get("work_unit_id"), row.get("contract_revision"), row["id"]
        text, origin = row["summary"], row["source"]
        number = None
        if unit is not None:
            number = _value_number(connection, (*owner, unit, revision, cid), text, origin)
            text = origin = None
        connection.execute("""INSERT INTO current_criteria
            (platform,session_id,position,criterion_id,work_unit_id,contract_revision,value_no,criterion_text,
             origin_kind,selectable,flag_keys,raw_flag_json,legacy_fields_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*owner, position, cid, unit, revision, number, text, origin, *_flag_columns(row), _extras(row, _CURRENT_FIELDS)))


def _insert_unit(connection, owner, position, unit):
    work_unit, revision = routes._unit_key(unit)
    key = *owner, work_unit, revision
    if "owner" in unit and unit["owner"] != dict(platform=owner[0], session_id=owner[1]):
        raise routes.RouteSchemaError("unit owner must match the authoritative session")
    if "kind" in unit and unit["kind"] not in ("execution", "nonexecution"):
        raise routes.RouteSchemaError("stored unit kind is invalid")
    definitions = unit.get("criteria", [])
    if not isinstance(definitions, list):
        raise routes.RouteSchemaError("frozen criterion definitions must be a list")
    connection.execute("""INSERT INTO work_unit_revisions
        (platform,session_id,work_unit_id,contract_revision,position,kind,has_owner,has_definitions,
         selectable,flag_keys,raw_flag_json,legacy_fields_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (*key, position, unit.get("kind"), int("owner" in unit), int("criteria" in unit),
         *_flag_columns(unit), _extras(unit, _UNIT_FIELDS)))
    seen = set()
    for index, definition in enumerate(definitions):
        routes._object(definition, {"id", "summary", "source"}, "frozen criterion definition")
        _, _, cid = routes._criterion(dict(definition, work_unit_id=work_unit, contract_revision=revision, selectable=True))
        if cid in seen:
            raise routes.RouteSchemaError("frozen criterion definitions are ambiguous")
        seen.add(cid)
        number = _value_number(connection, (*key, cid), definition["summary"], definition["source"])
        connection.execute("""INSERT INTO revision_criteria
            (platform,session_id,work_unit_id,contract_revision,criterion_id,value_no,position)
            VALUES (?,?,?,?,?,?,?)""", (*key, cid, number, index))


def write_registry(connection, platform, session_id, units):
    """Append frozen revisions and update their mutable work-selection flags."""
    owner = platform, session_id
    if not isinstance(units, list) or any(not isinstance(unit, dict) for unit in units):
        raise routes.RouteSchemaError("stored work unit registry must be a list of objects")
    prior = read_registry(connection, *owner)
    if len(units) < len(prior):
        raise routes.RouteSchemaError("frozen work unit revision cannot be removed")
    seen = set()
    for position, unit in enumerate(units):
        key = routes._unit_key(unit)
        if key in seen:
            raise routes.RouteSchemaError("stored work unit is ambiguous")
        seen.add(key)
        if position >= len(prior):
            _insert_unit(connection, owner, position, unit)
            continue
        old = prior[position]
        if _json(old) == _json(unit):
            continue
        if _extras(old, _FLAGS) != _extras(unit, _FLAGS):
            raise routes.RouteSchemaError("same work unit revision cannot change its frozen definition or owner")
        connection.execute("""UPDATE work_unit_revisions SET selectable=?,flag_keys=?,raw_flag_json=?
            WHERE platform=? AND session_id=? AND work_unit_id=? AND contract_revision=?""",
            (*_flag_columns(unit), *owner, *key))


def _moved_criteria(db, owner):
    """Read ordered public values and check the stored positions and effective selection flags."""
    result = []
    for row in db.execute(_CURRENT_QUERY.format(index="") + " ORDER BY c.position", owner):
        criterion = _public_criterion(row)
        if (row[0] != len(result) or _flag_columns(criterion) != row[6:9]
                or _extras(criterion, _CURRENT_FIELDS) != row[9]):
            raise ValueError("invalid moved current_criteria position or selection flag")
        result.append(criterion)
    return result


def _moved_conditions_match(old, new, owner, events):
    original = _moved_criteria(old, owner)
    digest = json_digest(original)
    witnessed = False
    for _, event in events:
        if event.get("event") == "acceptance-criteria-ownership-bound" and "criterion_rows_change" not in event:
            # Older bindings name the original ownerless rows but have no exact row hashes.
            # Replay only against a known full list, never guess intermediate values from a hash.
            if original is None:
                return False
            ids = event.get("criterion_ids")
            if (not isinstance(ids, list) or not ids or not all(isinstance(cid, str) for cid in ids)
                    or "work_unit_id" not in event or "contract_revision" not in event):
                return False
            matches = [row for row in original if "work_unit_id" not in row and row["id"] in ids]
            if len(matches) != len(ids):
                return False
            for row in matches:
                row.update(work_unit_id=event["work_unit_id"], contract_revision=event["contract_revision"])
            digest = json_digest(original)
            witnessed = True
        elif event.get("event") in ("intent-updated", "input-observed", "acceptance-criteria-ownership-bound"):
            changes = event.get("semantic_changes", {})
            if not isinstance(changes, dict):
                return False
            change = event.get("criterion_rows_change", changes.get("acceptance_criteria"))
            if change is None:
                continue
            if not isinstance(change, dict) or change.get("before_digest") != digest:
                return False
            digest = change.get("after_digest")
            original = None
            witnessed = True
    return witnessed and digest == json_digest(_moved_criteria(new, owner))


def _moved_registry_matches(old, new, owner, events):
    # Registrations already authenticate the flag of every submitted unit, including unselected ones.
    flags = {}
    registered = False
    for sequence, event in events:
        if event.get("event") == ledger_registrations.EVENT:
            route = ledger_registrations.ready_projection(new, *owner, sequence)
            flags.update({routes._unit_key(unit): _flag_columns(unit) for unit in route["work_units"]})
            registered = True
    if not registered:
        return False
    query = "SELECT * FROM work_unit_revisions WHERE platform=? AND session_id=? ORDER BY position"
    retained = iter(new.execute(query, owner))
    for row in old.execute(query, owner):
        # Each registration canonicalizes previous flag aliases even on units omitted from this submission.
        flag = flags.get(row[2:4], (row[8], int(bool(row[9])), row[10]))
        if next(retained, None) != (*row[:8], *flag, *row[11:]):
            return False
    return True


def moved_rows_preserved(table, columns, old, new, owner, events):
    """Only later commits in the same session can justify changing its original projection."""
    if table == "current_criteria" and columns == (
            "platform", "session_id", "position", "criterion_id", "work_unit_id", "contract_revision",
            "value_no", "criterion_text", "origin_kind", "selectable", "flag_keys", "raw_flag_json", "legacy_fields_json"):
        return _moved_conditions_match(old, new, owner, events)
    if table == "work_unit_revisions" and columns == (
            "platform", "session_id", "work_unit_id", "contract_revision", "position", "kind", "has_owner",
            "has_definitions", "selectable", "flag_keys", "raw_flag_json", "legacy_fields_json"):
        return _moved_registry_matches(old, new, owner, events)
    return False
