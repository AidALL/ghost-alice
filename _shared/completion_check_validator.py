"""Completion-check validator for the Claude Stop hook.

Validates the `[completion-check]` block of a final response. The claim-evidence-map
honesty core (claim + evidence + verdict, unverified=none) is always required; the
separate acceptance-criteria enumeration is optional.

Public API:
    validate_completion_text(text, require_completion_check=False) -> str | None
        Returns None when `text` is valid or is not a completion claim,
        otherwise the deny-reason string.

Helpers are exposed as module-level snake_case functions for unit testing:
    strip_control_blocks, extract_control_block, looks_like_completion_claim,
    requires_completion_check,
    extract_top_level_field_section, section_is_none,
    extract_acceptance_criteria_ids, extract_claim_evidence_entries,
    validate_completion_evidence_map

Standard library only (Python 3.11+): the `re` module.
"""

import re


_CONTROL_BLOCKS = frozenset(
    {"tool-checkpoint", "gate-state", "task-router", "boundary-contract", "io-trace"}
)

# Block header `^\[([a-z0-9-]+)\]`, case-insensitive. The position variant
# tolerates leading whitespace so an indented header is still located, keeping it
# consistent with extract_control_block (which matches on the stripped line). A
# mismatch here previously yielded zero positions for an indented header and
# crashed the position lookup with IndexError.
_BLOCK_HEADER_RE = re.compile(r"^\[([a-z0-9-]+)\]", re.I)
_BLOCK_HEADER_POSITION_RE = re.compile(r"^[ \t]*\[([a-z0-9-]+)\]", re.I | re.M)

# Split on \r?\n only; splitlines() would also split on other Unicode line
# boundaries, which we do not want here.
_NEWLINE_SPLIT_RE = re.compile(r"\r?\n")

# Explicit claim detection is marker-only ([completion-check]). Mandatory final
# block mode also uses a narrow executed-work closure detector so routine
# explanations do not become completion-check retry loops.

_VERIFICATION_DONE_RE = re.compile(r"-\s*verification-before-completion:\s*done\b")
_SKILL_CALL_RE = re.compile(r"-\s*skill-call:\s*([^\n]+)")
# skills-loaded accepts three equivalent serializations (inline CSV, flow-list
# `[...]`, or a nested bullet list). The header line is matched here; token
# extraction and nested-list collection happen in extract_skills_loaded().
_SKILLS_LOADED_HEADER_RE = re.compile(r"^\s*-?\s*skills-loaded\s*:\s*(.*)$", re.I)
_ZERO_WIDTH_RE = re.compile("[​‌‍‎‏﻿]")

_TOP_LEVEL_FIELD_RE = re.compile(r"^-\s*[A-Za-z0-9_-]+\s*:")
# Acceptance-criteria id: a leading `- <id>:` on each line.
_ACCEPTANCE_ID_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.=-]+)\s*:", re.M)
# Claim-evidence entry field patterns, case-insensitive.
_CLAIM_RE = re.compile(r"^\s*-\s*claim\s*:\s*(.+?)\s*$", re.I)
_ENTRY_FIELD_RE = re.compile(r"^\s*(criterion|evidence|verdict)\s*:\s*(.+?)\s*$", re.I)
# A `- none` line, case-insensitive.
_NONE_LINE_RE = re.compile(r"^-\s*none\b", re.I)

_VALID_VERDICTS = ("pass", "fail", "unverified")
_EXECUTED_WORK_CLAIM_PATTERNS = (
    re.compile(
        r"\b(?:the\s+)?(?:requested\s+)?(?:work|task|change|fix|implementation|"
        r"request|issue|bug)\s+(?:is\s+)?(?:complete|completed|done|fixed|"
        r"resolved|implemented)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:i\s+)?(?:completed|finished|fixed|resolved|implemented)\s+"
        r"(?:the\s+)?(?:requested\s+)?(?:work|task|change|fix|request|issue|bug)\b",
        re.I,
    ),
    re.compile(r"\b(?:all\s+)?tests?\s+(?:pass|passed|green)\b", re.I),
    re.compile(r"\b(?:build|lint|typecheck|test\s+suite)\s+(?:succeeded|passed|is\s+clean)\b", re.I),
    re.compile(r"(?:작업|변경|수정|구현|요청|이슈|버그).{0,12}(?:완료|끝냈|끝남|고쳤|해결|반영)"),
    re.compile(r"(?:테스트|빌드|린트|타입체크).{0,12}(?:통과|성공|깨끗)"),
)

# Spans that are quotation or code, not the model's own assertion. Stripped
# before executed-work detection so illustrative output, quoted user text, and
# fenced examples do not trigger a spurious completion-check demand.
_FENCED_CODE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Single-quoted spans only count when the quotes sit on word boundaries, so an
# ordinary contraction apostrophe (won't, don't, y'all) is not mistaken for a
# quotation delimiter and does not swallow a real claim between two contractions.
_QUOTED_SPAN_RE = re.compile(
    "(?<![A-Za-z0-9])'[^'\n]*'(?![A-Za-z0-9])"
    "|\"[^\"\n]*\""
    "|(?<![A-Za-z0-9])‘[^’\n]*’(?![A-Za-z0-9])"
    "|“[^”\n]*”"
)

# Executed-work detection runs per clause. Two lead families are exempt, with
# different governance scope:
#   - GOAL / enumeration leads (goal, acceptance criteria, todo, next steps,
#     plan, ...) introduce a list and govern the WHOLE sentence, so a
#     comma-separated goal list ("Goal: tests pass, lint is clean") is exempt.
#   - CONDITIONAL leads (if, once, when, ...) govern only their own protasis, so
#     the asserted main clause after the comma ("If you're curious, I fixed it")
#     is still checked.
# Sentences split on . ! ? and newlines; within a sentence, clauses split on
# commas. ";" and ":" are deliberately NOT split points so a goal lead governs an
# enumeration across them ("Acceptance criteria: all tests pass; not there yet").
# A clause that negates the closure verb is also exempt. The ambiguous temporal
# leads after/before/until are intentionally absent: they usually introduce an
# asserted main clause ("After much effort the task is complete"), not a
# hypothetical.
_SENTENCE_SPLIT_RE = re.compile(r"[\n.!?]+")
_COMMA_SPLIT_RE = re.compile(r",+")
_GOAL_LEAD_RE = re.compile(
    r"^\s*(?:[>*\-]|\d+[.)])?\s*"
    r"(?:goal|goals|aim|aims|objective|objectives|target|targets|"
    r"acceptance\s+criteria|criteria|todo|to-?do|next\s+steps?|"
    r"plan|plans|remaining|pending)\b"
    r"|^\s*(?:목표|예정)",
    re.I,
)
_CONDITIONAL_LEAD_RE = re.compile(
    r"^\s*(?:[>*\-]|\d+[.)])?\s*"
    r"(?:if|once|when|whenever|unless|as\s+soon\s+as)\b"
    r"|^\s*만약",
    re.I,
)
_NEGATED_CLOSURE_RE = re.compile(
    r"\b(?:not|never|isn['’]?t|aren['’]?t|wasn['’]?t|weren['’]?t|won['’]?t|"
    r"cannot|can['’]?t|don['’]?t|doesn['’]?t|didn['’]?t|no\s+longer)\s+"
    r"(?:yet\s+|fully\s+|completely\s+|quite\s+)*"
    r"(?:complete|completed|done|fixed|resolved|implemented|passing|pass|"
    r"passed|green|clean|succeed|succeeded)\b"
    r"|\bnot\s+there\s+yet\b|\bnot\s+yet\b"
    r"|아직|않았|않은|않고",
    re.I,
)


def _split_lines(text):
    return _NEWLINE_SPLIT_RE.split("" if text is None else str(text))


def _strip_noncommittal_spans(text):
    """Drop fenced/inline code, blockquotes, and quoted spans.

    These carry illustrative or reported content, not the model's own
    executed-work assertion, so they must not trigger completion detection.
    """
    text = _FENCED_CODE_RE.sub(" ", "" if text is None else str(text))
    text = _INLINE_CODE_RE.sub(" ", text)
    kept = [line for line in _split_lines(text) if not line.lstrip().startswith(">")]
    text = "\n".join(kept)
    return _QUOTED_SPAN_RE.sub(" ", text)


def strip_control_blocks(text):
    """Remove control blocks so claim detection ignores their contents.

    A control block starts at a `[name]` header whose name is in
    `_CONTROL_BLOCKS` and ends at the first blank line or a line that is
    exactly `---`.
    """
    lines = _split_lines(text)
    kept = []
    in_control_block = False
    for line in lines:
        trimmed = line.strip()
        block = _BLOCK_HEADER_RE.match(trimmed)
        if block and block.group(1).lower() in _CONTROL_BLOCKS:
            in_control_block = True
            continue
        if in_control_block:
            if not trimmed or trimmed == "---":
                in_control_block = False
            continue
        kept.append(line)
    return "\n".join(kept)


def extract_control_block(text, name):
    """Return the body of the named `[name]` block.

    Keeps lines after the matching header until the next `[header]` line,
    then strips surrounding whitespace.
    """
    lines = _split_lines(text)
    wanted = ("" if name is None else str(name)).lower()
    kept = []
    in_block = False
    for line in lines:
        block = _BLOCK_HEADER_RE.match(line.strip())
        if block:
            if in_block:
                break
            if block.group(1).lower() == wanted:
                in_block = True
            continue
        if in_block:
            kept.append(line)
    return "\n".join(kept).strip()


def find_control_block_positions(text, name):
    """Return start offsets for `[name]` headers."""
    wanted = ("" if name is None else str(name)).lower()
    return [
        match.start()
        for match in _BLOCK_HEADER_POSITION_RE.finditer("" if text is None else str(text))
        if match.group(1).lower() == wanted
    ]


def looks_like_completion_claim(text):
    """True only when a real `[completion-check]` block header is present.

    A header is a line whose stripped form begins with the `[completion-check]`
    token. A mere inline prose mention of the token (for example, explaining what
    the block contains) does not count, so meta-discussion is not mistaken for a
    completion claim. Substring keyword matching of intent is intentionally NOT
    used: it false-positives and string-matching intent is unreliable.
    """
    for line in _split_lines(text):
        match = _BLOCK_HEADER_RE.match(line.strip())
        if match and match.group(1).lower() == "completion-check":
            return True
    return False


def requires_completion_check(text):
    """True when text makes an executed-work closure claim without the marker.

    This is intentionally narrower than ordinary recommendation or explanation
    detection. It protects claims that work finished or verification passed, but
    avoids blocking routine meta-discussion, options, or status explanations.

    Detection runs per clause on text with control blocks and non-committal
    spans (code, quotes, blockquotes) removed, and skips clauses that are
    hypothetical / goal-framing or that negate the closure verb, so future,
    conditional, quoted, and illustrative phrasing does not trigger the gate.
    """
    cleaned = _strip_noncommittal_spans(strip_control_blocks(text))
    for sentence in _SENTENCE_SPLIT_RE.split(cleaned):
        if not sentence.strip():
            continue
        clauses = _COMMA_SPLIT_RE.split(sentence)
        # A goal / enumeration lead at the sentence start governs the whole list.
        if clauses and _GOAL_LEAD_RE.search(clauses[0]):
            continue
        for clause in clauses:
            if not clause.strip():
                continue
            # A conditional lead governs only its own protasis clause.
            if _CONDITIONAL_LEAD_RE.search(clause):
                continue
            if _NEGATED_CLOSURE_RE.search(clause):
                continue
            if any(pattern.search(clause) for pattern in _EXECUTED_WORK_CLAIM_PATTERNS):
                return True
    return False


def extract_top_level_field_section(block, field_name):
    """Return the indented body under a top-level `- field_name:` line.

    Collects lines after the field header until the next top-level
    `- something:` line, then strips surrounding whitespace.
    """
    lines = _split_lines(block)
    field_pattern = re.compile(r"^-\s*" + re.escape(field_name) + r"\s*:", re.I)
    start = -1
    for index, line in enumerate(lines):
        if field_pattern.search(line):
            start = index
            break
    if start < 0:
        return ""
    kept = []
    for index in range(start + 1, len(lines)):
        if _TOP_LEVEL_FIELD_RE.search(lines[index]):
            break
        if lines[index].strip() and lines[index] == lines[index].lstrip():
            break
        kept.append(lines[index])
    return "\n".join(kept).strip()


def section_is_none(section):
    """True only when every meaningful line is a `- none` line.

    An empty section returns False.
    """
    meaningful = [line.strip() for line in _split_lines(section)]
    meaningful = [line for line in meaningful if line]
    if len(meaningful) == 0:
        return False
    return all(_NONE_LINE_RE.search(line) for line in meaningful)


def extract_acceptance_criteria_ids(completion_check):
    """List acceptance-criteria ids, skipping placeholder `<...>` ids."""
    section = extract_top_level_field_section(completion_check, "acceptance-criteria")
    ids = []
    for match in _ACCEPTANCE_ID_RE.finditer(section):
        if "<" not in match.group(1):
            ids.append(match.group(1))
    return ids


def extract_claim_evidence_entries(completion_check):
    """Parse claim-evidence-map into a list of dict entries.

    Each entry begins on a `- claim:` line; subsequent `criterion:`,
    `evidence:`, and `verdict:` lines attach to the current entry.
    """
    section = extract_top_level_field_section(completion_check, "claim-evidence-map")
    entries = []
    current = None
    for line in _split_lines(section):
        claim = _CLAIM_RE.match(line)
        if claim:
            current = {"claim": claim.group(1).strip()}
            entries.append(current)
            continue
        if current is None:
            continue
        field = _ENTRY_FIELD_RE.match(line)
        if field:
            current[field.group(1).lower()] = field.group(2).strip()
    return entries


def validate_completion_evidence_map(completion_check):
    """Validate the claim-evidence-map honesty core, with optional acceptance-criteria.

    The claim-evidence-map (claim + evidence + verdict, unverified=none) is always
    required. The separate acceptance-criteria enumeration is optional: when it is
    present (full form) every entry must bind to a known criterion id; when it is
    absent (compact form) the criterion reference is optional. This lets a turn
    with no semantic intent delta drop the duplicated acceptance-criteria block
    while keeping each claim bound to fresh evidence and a verdict.

    Returns a deny-reason string on the first failure, else None.
    """
    entries = extract_claim_evidence_entries(completion_check)
    if len(entries) == 0:
        return "[completion-check] must include non-empty claim-evidence-map."

    acceptance_ids = extract_acceptance_criteria_ids(completion_check)
    acceptance_id_set = set(acceptance_ids)
    full_form = len(acceptance_ids) > 0
    for entry in entries:
        if full_form:
            criterion = entry.get("criterion") or ""
            # An entry may reference one or more acceptance ids (comma/space separated);
            # every referenced id must be a known acceptance-criteria id.
            criterion_ids = [token for token in re.split(r"[,\s]+", criterion.strip()) if token]
            if not criterion_ids or any(token not in acceptance_id_set for token in criterion_ids):
                return "Every claim-evidence-map entry must reference an acceptance-criteria criterion id."
        if not entry.get("evidence"):
            return "Every claim-evidence-map entry must include evidence."
        verdict = entry.get("verdict", "").lower()
        if verdict not in _VALID_VERDICTS:
            return "Every claim-evidence-map entry must include verdict: pass | fail | unverified."
        if verdict == "unverified":
            return (
                "A finalized [completion-check] cannot contain an 'unverified' verdict. "
                "Verify the claim to pass/fail, or report partial state in prose "
                "without a [completion-check] block."
            )

    unverified_section = extract_top_level_field_section(completion_check, "unverified")
    if not unverified_section:
        return "[completion-check] must include an unverified section."
    if not section_is_none(unverified_section):
        return (
            "A finalized [completion-check] requires the unverified section to be 'none'. "
            "If items remain unverified, report partial state in prose instead of finalizing."
        )
    return None


def _split_skill_tokens(raw):
    """Split a skills-loaded value into normalized skill tokens.

    Accepts comma- or newline-separated values; strips surrounding brackets,
    quotes, whitespace, and zero-width characters so that visually-identical
    values do not produce false negatives.
    """
    tokens = []
    for part in re.split(r"[,\n]+", raw):
        token = _ZERO_WIDTH_RE.sub("", part).strip().strip("[]").strip().strip("\"'").strip()
        if token:
            tokens.append(token)
    return tokens


def _skill_name(token):
    """Bare skill name: the leading component before any space or annotation.

    ``verification-before-completion (this turn)`` -> ``verification-before-completion``
    while a different skill such as ``verification-before-completion-v2`` keeps its
    suffix and therefore does not match the canonical name on exact comparison.
    """
    return re.split(r"[\s(]", token, maxsplit=1)[0].strip().rstrip(":").lower()


def extract_all_control_blocks(text, name):
    """Return every `[name]` block body in document order.

    Unlike ``extract_control_block`` (first block only), this collects all
    occurrences so that a malformed later block cannot hide behind a valid
    earlier one.
    """
    lines = _split_lines(text)
    wanted = ("" if name is None else str(name)).lower()
    blocks = []
    current = None
    for line in lines:
        block = _BLOCK_HEADER_RE.match(line.strip())
        if block:
            if current is not None:
                blocks.append("\n".join(current).strip())
                current = None
            if block.group(1).lower() == wanted:
                current = []
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current).strip())
    return blocks


def extract_skills_loaded(io_trace):
    """Return skill tokens from an io-trace `skills-loaded` field.

    Accepts three equivalent serializations so the gate is format-agnostic:
      - inline CSV:   ``- skills-loaded: a, b``
      - flow list:    ``- skills-loaded: [a, b]``
      - nested list:  ``- skills-loaded:`` then indented ``  - a`` / ``  - b`` lines
    """
    lines = _split_lines(io_trace)
    for index, line in enumerate(lines):
        header = _SKILLS_LOADED_HEADER_RE.match(line)
        if not header:
            continue
        inline = header.group(1).strip()
        if inline.startswith("[") and inline.endswith("]"):
            inline = inline[1:-1]
        if inline.strip():
            return _split_skill_tokens(inline)
        # Nested form: collect indented bullet lines. Blank-line gaps and
        # non-bullet prose lines under the header are tolerated (skipped), so the
        # list is not truncated by a stray blank line or an introductory line.
        # Collection stops at the next top-level `- field:` entry or a
        # non-indented line that is neither blank nor a bullet.
        tokens = []
        for nxt in lines[index + 1:]:
            if not nxt.strip():
                continue
            if _TOP_LEVEL_FIELD_RE.search(nxt):
                break
            bullet = re.match(r"^\s*-\s*(.+?)\s*$", nxt)
            if bullet:
                tokens.extend(_split_skill_tokens(bullet.group(1)))
                continue
            if nxt[:1] in (" ", "\t"):
                # Indented non-bullet prose under the header: ignore and keep scanning.
                continue
            break
        return tokens
    return []


def validate_completion_text(text, *, require_completion_check=False):
    """Validate a completion claim.

    Returns None when `text` is empty, is not a completion claim, or passes
    every gate; otherwise the deny-reason string. When
    `require_completion_check` is true, executed-work closure claims must carry
    the explicit marker, while routine explanations remain allowed.
    """
    text = "" if text is None else str(text)
    if not text:
        return None
    if not looks_like_completion_claim(text):
        if require_completion_check and requires_completion_check(text):
            return (
                "Completion or success claims about executed work require a "
                "[completion-check] block with fresh verification evidence."
            )
        return None

    completion_check = extract_control_block(text, "completion-check")
    if not completion_check:
        return (
            "Completion or success claims about executed work require a "
            "[completion-check] block with fresh evidence."
        )

    completion_positions = find_control_block_positions(text, "completion-check")
    if not completion_positions:
        # Header body was extracted but no position was located (e.g. an unusual
        # header form). Treat as a missing marker rather than crashing.
        return (
            "Completion or success claims about executed work require a "
            "[completion-check] block with fresh evidence."
        )
    first_completion = completion_positions[0]
    gate_state_positions = find_control_block_positions(text, "gate-state")
    if any(position > first_completion for position in gate_state_positions):
        return "[gate-state] belongs to the opening surface and must not appear after [completion-check]."

    io_trace_positions = find_control_block_positions(text, "io-trace")
    if io_trace_positions and io_trace_positions[0] < first_completion:
        return "[io-trace] must appear after [completion-check] on finalized completion surfaces."

    if not _VERIFICATION_DONE_RE.search(completion_check):
        return "[completion-check] must include '- verification-before-completion: done'."

    skill_call = _SKILL_CALL_RE.search(completion_check)
    if not skill_call or "verification-before-completion" not in skill_call.group(1):
        return " ".join(
            [
                "verification-reminder: emitted.",
                "Run verification-before-completion before claiming completion.",
                "Do not claim a verification skill-call unless the verification skill "
                "was actually loaded this turn.",
            ]
        )

    if "[io-trace]" not in text:
        return "A verification skill-call should be backed by [io-trace] with skills-loaded evidence."

    io_trace = extract_control_block(text, "io-trace")
    skills_loaded = extract_skills_loaded(io_trace)
    if not any(_skill_name(skill) == "verification-before-completion" for skill in skills_loaded):
        return (
            "The [io-trace] skills-loaded list should include verification-before-completion "
            "when completion-check claims that skill-call."
        )

    # Validate every completion-check block, not just the first, so a malformed
    # later block cannot pass by hiding behind a valid earlier one.
    for block in extract_all_control_blocks(text, "completion-check"):
        reason = validate_completion_evidence_map(block)
        if reason:
            return reason
    return None
