"""Core structural validator for completion and partial evidence controls.

Validates final `[completion-check]` and scoped `[partial-check]` evidence.
Both require claim + evidence + pass/fail verdict. Finalized completion requires
unverified=none; partial reports require declared unresolved criterion IDs and
scope=partial. The caller and Core assessment own semantic claim scope.

Public API:
    validate_completion_text(text, require_completion_check=False) -> str | None
        Returns None when structural controls satisfy the supplied requirement,
        otherwise the diagnostic string.

Helpers are exposed as module-level snake_case functions for unit testing:
    extract_control_block, looks_like_completion_claim,
    extract_top_level_field_section, section_is_none,
    extract_acceptance_criteria_ids, extract_claim_evidence_entries,
    validate_completion_evidence_map

Python 3.11+ standard library, plus the sibling text_lines module, which owns line boundaries.
"""

import re
import sys
from pathlib import Path

# A file-path load, such as the installed-runtime check, still finds the sibling owner.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_lines import LINE_BREAK


# Printed control blocks whose nested fields are evidence data, not code examples.
_CONTROL_BLOCKS = frozenset({"boundary-contract", "completion-check", "partial-check"})

# Match block headers case-insensitively and allow leading whitespace so position lookup agrees with control-block extraction.
_BLOCK_HEADER_RE = re.compile(r"^\[([a-z0-9-]+)\]", re.I)


# Evidence detection recognizes structural control markers only. The caller supplies any mandatory evidence requirement.

_TOP_LEVEL_FIELD_RE = re.compile(r"^-\s*[A-Za-z0-9_-]+\s*:")
# Acceptance-criteria id: a leading `- <id>:` on each line.
_ACCEPTANCE_ID_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.=-]+)\s*:", re.M)
# Claim-evidence entry field patterns, case-insensitive.
_CLAIM_RE = re.compile(r"^\s*-\s*claim\s*:\s*(.*?)\s*$", re.I)
_ENTRY_FIELD_RE = re.compile(r"^\s*(criterion|evidence|verdict)\s*:\s*(.*?)\s*$", re.I)
# Exactly `none` or `- none`, case-insensitive; never a prefix of pending work.
_NONE_LINE_RE = re.compile(r"^(?:-\s*)?none\s*$", re.I)

_VALID_VERDICTS = ("pass", "fail", "unverified")

# Structural Markdown examples do not introduce actual control blocks.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)$")
_QUOTE_LINE_RE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_LIST_ITEM_RE = re.compile(r"^ *(?:[*+-]|[0-9]{1,9}[.)])[ \t]+")
_PARAGRAPH_BREAK_RE = re.compile(
    r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|(?:[*+-]|1[.)])[ \t]+|"
    r"(?:[-*_][ \t]*){3,}$|(?:=+|-+)[ \t]*$|<[!/A-Za-z])"
)
# Reserve balanced inline quotation delimiters at word boundaries before masking multiline examples.
_QUOTED_SPAN_RE = re.compile(
    "(?<![A-Za-z0-9])'[^'\n]*'(?![A-Za-z0-9])"
    "|\"[^\"\n]*\""
    "|(?<![A-Za-z0-9])‘[^’\n]*’(?![A-Za-z0-9])"
    "|“[^”\n]*”"
)
# Multiline examples use balanced matching delimiters. Same-delimiter nesting
# and apostrophes inside words are ambiguous, so they remain visible to checks.
_MULTILINE_QUOTED_SPAN_RE = re.compile(
    r"(?<![A-Za-z0-9\\])'[^']*\n[^']*'(?![A-Za-z0-9])"
    r'|(?<![A-Za-z0-9\\])"[^"]*\n[^"]*"(?![A-Za-z0-9])'
    "|‘[^‘’]*\n[^‘’]*’"
    "|“[^“”]*\n[^“”]*”"
)


def _split_lines(text):
    return LINE_BREAK.split("" if text is None else str(text))


def _consume_quote_container_prefixes(body, containers):
    """Consume the expected frames, leaving literal code payload untouched."""
    matched = []
    for kind, width in containers:
        if kind == "quote":
            quote = _QUOTE_LINE_RE.match(body)
            if quote is None:
                return body, tuple(matched), False
            body = quote.group(1)
        else:
            indentation = len(body) - len(body.lstrip(" "))
            if indentation < width:
                return body, tuple(matched), False
            body = body[width:]
        matched.append((kind, width))
    return body, tuple(matched), True


def _quoted_leaf_content(body, paragraph_open=False, containers=()):
    """Find the paragraph leaf inside demonstrated quote/list containers.

    CommonMark 0.31.2 sections 5.1/5.2 and Example 292 permit lazy paragraph
    continuation through those containers, but not through code or headings.
    """
    if not body.strip():
        return "", containers
    original = body
    body, matched, complete = _consume_quote_container_prefixes(body, containers)
    if (not complete and paragraph_open and not _FENCE_OPEN_RE.match(original)
            and not _PARAGRAPH_BREAK_RE.match(original) and not _QUOTE_LINE_RE.match(original)):
        # Existing paragraph leaves may omit container prefixes lazily.
        frames = list(containers)
    else:
        frames = list(matched)
        if not complete:
            paragraph_open = False
    while body.strip():
        indentation = len(body) - len(body.lstrip(" "))
        if indentation >= 4:
            # Indented code cannot interrupt an existing paragraph.
            return (body if paragraph_open else ""), tuple(frames)
        if re.fullmatch(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*", body):
            return "", tuple(frames)
        nested = _QUOTE_LINE_RE.match(body)
        if nested is not None:
            frames.append(("quote", 0))
            body = nested.group(1)
            continue
        item = _LIST_ITEM_RE.match(body)
        if item is not None:
            padding = len(item.group(0)) - len(item.group(0).rstrip(" \t"))
            end = item.end() if padding <= 4 else item.end() - padding + 1
            frames.append(("list", end))
            body = body[end:]
            paragraph_open = False
            continue
        return body, tuple(frames)
    return "", tuple(frames)


def _strip_block_examples(text):
    """Mask Markdown examples while retaining prose and real control fields.

    Fence delimiters must occupy a line, use the same character, and close with
    at least the opening length. Indented code starts at a paragraph boundary;
    indentation within real control records remains data. Blockquote paragraphs
    may continue lazily without a repeated marker, until a blank or new block.
    Keep line boundaries so examples cannot join otherwise separate assertions.
    """
    kept = []
    fence = None
    indented = False
    paragraph = False
    quote_paragraph = False
    quote_containers = ()
    quoted_fence = None
    control_fields = False
    list_indent = 0
    for line in _split_lines(text):
        expanded = line.expandtabs(4)
        blank = not line.strip()
        indentation = len(expanded) - len(expanded.lstrip(" "))
        if fence is not None:
            char, length, container_indent = fence
            if container_indent and not blank and indentation < container_indent:
                # A list container ending also ends its unclosed example fence.
                fence = None
                list_indent = 0
                paragraph = False
            else:
                content = expanded[container_indent:]
                if re.fullmatch(r" {0,3}" + re.escape(char) + "{" + str(length) + r",}[ \t]*", content):
                    fence = None
                kept.append("")
                continue
        quote = _QUOTE_LINE_RE.match(expanded)
        if quote:
            body = quote.group(1)
            if quoted_fence is not None:
                char, length, fence_containers = quoted_fence
                closing, _, complete = _consume_quote_container_prefixes(body, fence_containers)
                if not complete:
                    # The prior code leaf ended with its container, rather than
                    # being closed by a delimiter in a different container.
                    quoted_fence = None
                    quote_containers = fence_containers
                elif re.fullmatch(r" {0,3}" + re.escape(char) + "{" + str(length) + r",}[ \t]*", closing):
                    quoted_fence = None
                    quote_containers = fence_containers
                    quote_paragraph = False
                    paragraph = False
                    control_fields = False
                    list_indent = 0
                    kept.append("")
                    continue
                else:
                    # Normalize only the known container frames: any further
                    # quote/list markers are literal fence contents.
                    quote_paragraph = False
                    kept.append("")
                    continue
                quote_paragraph = False
            leaf, quote_containers = _quoted_leaf_content(body, quote_paragraph, quote_containers)
            opener = _FENCE_OPEN_RE.match(leaf)
            if opener and not (opener.group(1)[0] == "`" and "`" in opener.group(2)):
                quoted_fence = opener.group(1)[0], len(opener.group(1)), quote_containers
            quote_paragraph = bool(leaf.strip()) and not opener and not _PARAGRAPH_BREAK_RE.match(leaf)
            paragraph = False
            control_fields = False
            list_indent = 0
            kept.append("")
            continue
        # Missing quote markers cannot lazily continue a fenced code leaf.
        quoted_fence = None
        if quote_paragraph:
            if not blank and not _FENCE_OPEN_RE.match(expanded) and not _PARAGRAPH_BREAK_RE.match(expanded):
                kept.append("")
                continue
            quote_paragraph = False
        quote_containers = ()
        if blank:
            paragraph = False
            kept.append("")
            continue
        if indentation < list_indent:
            list_indent = 0
        code_indentation = indentation - list_indent
        if indented:
            if code_indentation >= 4:
                kept.append("")
                continue
            indented = False
        # A control record's nested fields are evidence data, not code examples.
        if control_fields and re.match(r"^\s*(?:-\s+|[A-Za-z0-9_-]+\s*:)", line):
            kept.append(line)
            paragraph = True
            continue
        control_fields = False
        if code_indentation >= 4 and not paragraph:
            indented = True
            kept.append("")
            continue
        list_item = _LIST_ITEM_RE.match(expanded)
        if list_item is not None:
            list_indent = list_item.end()
        content = expanded[list_indent:] if list_indent else expanded
        opener = _FENCE_OPEN_RE.match(content)
        if opener and not (opener.group(1)[0] == "`" and "`" in opener.group(2)):
            fence = opener.group(1)[0], len(opener.group(1)), list_indent
            paragraph = False
            kept.append("")
            continue
        header = _BLOCK_HEADER_RE.match(line.strip())
        control_fields = bool(header and header.group(1).lower() in _CONTROL_BLOCKS)
        kept.append(line)
        paragraph = bool(content.strip()) and not _PARAGRAPH_BREAK_RE.match(content)
    return _mask_multiline_quoted_examples("\n".join(kept))


def _mask_multiline_quoted_examples(text):
    """Mask balanced quoted examples, retaining outside text and line breaks.

    Reserve complete inline quotation boundaries before considering multiline
    examples. Neither endpoint of an existing inline span may be repurposed.
    Keep their original text, including evidence in actual control fields.
    Multiline exclusion requires an explicit quote opening at a line boundary;
    ambiguous prose spans remain visible to normal validation.
    """
    inline_boundaries = {position for inline in _QUOTED_SPAN_RE.finditer(text)
                         for position in (inline.start(), inline.end() - 1)}
    def mask(match):
        span = match.group(0)
        if match.start() in inline_boundaries or match.end() - 1 in inline_boundaries:
            return span
        opening_prefix = text[:match.start()].rsplit("\n", 1)[-1]
        if opening_prefix.strip():
            return span
        if span[0] in ("'", '"'):
            closing_prefix = span[:-1].rsplit("\n", 1)[-1]
            if closing_prefix and closing_prefix[-1].isspace() and closing_prefix.strip():
                return span
            if closing_prefix.endswith("\\"):
                return span
        return re.sub(r"[^\r\n]", " ", span)
    return _MULTILINE_QUOTED_SPAN_RE.sub(mask, text)


def _control_headers(text):
    """Locate real headers once; offsets always refer to the original text."""
    raw = "" if text is None else str(text)
    offsets = [0] + [match.end() for match in LINE_BREAK.finditer(raw)]
    for index, line in enumerate(_split_lines(_strip_block_examples(raw))):
        match = _BLOCK_HEADER_RE.match(line.strip())
        if match:
            yield match.group(1).lower(), index, offsets[index]


def extract_control_block(text, name):
    """Return the first real named block body; Markdown examples are excluded."""
    blocks = extract_all_control_blocks(text, name)
    return blocks[0] if blocks else ""


def find_control_block_positions(text, name):
    """Return original start offsets for real named headers."""
    wanted = ("" if name is None else str(name)).lower()
    return [offset for marker, _, offset in _control_headers(text) if marker == wanted]


def looks_like_completion_claim(text):
    """A real completion control declares completion; prose does not."""
    return bool(find_control_block_positions(text, "completion-check"))


def looks_like_evidence_claim(text):
    """A real completion or partial control declares an evidence submission."""
    return any(marker in {"completion-check", "partial-check"}
               for marker, _, _ in _control_headers(text))


def extract_top_level_field_section(block, field_name):
    """Return the inline value and indented body of `- field_name:`.

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
    # An inline value and an indented value express the same field. Keep both
    # if supplied so an inline "none" cannot hide an unresolved nested item.
    inline = field_pattern.sub("", lines[start], count=1).strip()
    kept = [inline] if inline else []
    for index in range(start + 1, len(lines)):
        if _TOP_LEVEL_FIELD_RE.search(lines[index]):
            break
        if lines[index].strip() and lines[index] == lines[index].lstrip():
            break
        kept.append(lines[index])
    return "\n".join(kept).strip()


def section_is_none(section):
    """True only when every meaningful line is exactly `none` or `- none`.

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


def _split_entry_fields(line):
    """Split explicit semicolon fields, preserving quoted command/evidence text.

    Only recognized field labels introduce a new field; other semicolons remain
    literal evidence. No missing claim, evidence, criterion or verdict is inferred.
    """
    parts = []
    start = 0
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'", "`"):
            # Apostrophes inside words are not quotation delimiters.
            if char != "'" or index == 0 or not line[index - 1].isalnum():
                quote = char
            continue
        if char == ";" and re.match(r"\s*(?:criterion|evidence|verdict)\s*:", line[index + 1:], re.I):
            parts.append(line[start:index])
            start = index + 1
    parts.append(line[start:])
    return parts


def extract_claim_evidence_entries(completion_check):
    """Parse claim-evidence-map into a list of dict entries.

    Each entry begins with `- claim:`; subsequent `criterion:`, `evidence:`,
    and `verdict:` fields attach to it on separate lines or after semicolons.
    """
    section = extract_top_level_field_section(completion_check, "claim-evidence-map")
    entries = []
    current = None
    for raw_line in _split_lines(section):
        for line in _split_entry_fields(raw_line):
            claim = _CLAIM_RE.match(line)
            if claim:
                current = {"claim": claim.group(1).strip()}
                entries.append(current)
                continue
            if re.match(r"^\s*-\s*\S", line):
                current = {"_invalid": "Every claim-evidence-map entry must begin with an explicit claim."}
                entries.append(current)
                continue
            field = _ENTRY_FIELD_RE.match(line)
            if field and current is not None:
                key = field.group(1).lower()
                if key in current:
                    current["_invalid"] = "Claim-evidence-map entries must not contain duplicate fields."
                else:
                    current[key] = field.group(2).strip()
    return entries


def validate_completion_evidence_map(completion_check, *, partial=False):
    """Validate declared claim/evidence rows and full or partial control shape.

    Full completion requires unverified=none. Partial evidence requires unique
    declared criteria, scope=partial and explicit unresolved criterion reasons.
    The display parser also accepts compact full controls without declarations;
    original-proof parsing requires criterion IDs, and native completion uses
    completion_assessment.py to authenticate the assistant's recorded judgment
    and exact response binding. The current-session assistant must still judge
    whether the evidence satisfies the user's criteria.

    Return all independent defects in one diagnostic, else None. Reporting a
    later defect never requires the model to submit another malformed block.
    """
    issues = []
    marker = "partial-check" if partial else "completion-check"
    for name in ("acceptance-criteria", "claim-evidence-map", "unverified", "scope"):
        headers = re.findall(r"^-\s*" + re.escape(name) + r"\s*:", completion_check, re.I | re.M)
        if len(headers) > 1:
            issues.append(f"[{marker}] must not contain duplicate {name} sections.")
    entries = extract_claim_evidence_entries(completion_check)
    if len(entries) == 0:
        issues.append(f"[{marker}] must include non-empty claim-evidence-map.")

    acceptance_ids = extract_acceptance_criteria_ids(completion_check)
    acceptance_id_set = set(acceptance_ids)
    full_form = len(acceptance_ids) > 0
    if partial:
        if not full_form or len(acceptance_ids) != len(acceptance_id_set):
            issues.append("[partial-check] must declare nonempty, unique acceptance-criteria IDs.")
        if extract_top_level_field_section(completion_check, "scope").strip() != "partial":
            issues.append("[partial-check] requires scope: partial.")
    for entry in entries:
        if entry.get("_invalid"):
            issues.append(entry["_invalid"])
        if not entry.get("claim"):
            issues.append("Every claim-evidence-map entry must include a non-empty claim.")
        if full_form:
            criterion = entry.get("criterion") or ""
            # Every explicitly referenced ID must be declared, regardless of the list delimiter.
            criterion_ids = [token for token in re.split(r"[,;\s]+", criterion.strip()) if token]
            if not criterion_ids or any(token not in acceptance_id_set for token in criterion_ids):
                issues.append("Every claim-evidence-map entry must reference an acceptance-criteria criterion id.")
        if not entry.get("evidence"):
            issues.append("Every claim-evidence-map entry must include evidence.")
        verdict = entry.get("verdict", "").lower()
        if not verdict:
            issues.append("Every claim-evidence-map entry must include verdict: pass | fail.")
        elif verdict not in _VALID_VERDICTS:
            issues.append(
                "Invalid verdict value in claim-evidence-map. Write verdict: pass or verdict: fail "
                "on its own line, without punctuation, quotes, or commentary. If the claim is "
                "unverified, keep it in [partial-check] unverified instead of claiming a verdict."
            )
        if verdict == "unverified":
            issues.append(
                "Evidence entries cannot contain an 'unverified' verdict. Verify the claim to pass/fail, "
                "or name the unresolved criterion in [partial-check] unverified."
            )

    unverified_section = extract_top_level_field_section(completion_check, "unverified")
    if partial:
        unresolved = extract_partial_unverified_ids(completion_check)
        lines = [line.strip() for line in _split_lines(unverified_section) if line.strip()]
        if (not lines or len(unresolved) != len(lines) or len(set(unresolved)) != len(unresolved)
                or any(key not in acceptance_id_set for key in unresolved)):
            issues.append("[partial-check] requires nonempty unverified entries as '- criterion-id: reason' using declared IDs.")
        covered = {token for entry in entries for token in re.split(r"[,;\s]+", entry.get("criterion", "")) if token}
        if acceptance_id_set - covered - set(unresolved):
            issues.append("Every [partial-check] criterion must have a verdict or an explicit unverified reason.")
    elif not unverified_section:
        issues.append("[completion-check] must include an unverified section.")
    elif not section_is_none(unverified_section):
        issues.append(
            "A finalized [completion-check] requires the unverified section to be 'none'. " "If items remain unverified, use [partial-check] for supported claims and unresolved criterion IDs instead of finalizing."
        )
    return " ".join(dict.fromkeys(issues)) if issues else None


def extract_partial_unverified_ids(block):
    """Return explicit unresolved criterion IDs; malformed entries remain invalid."""
    section = extract_top_level_field_section(block, "unverified")
    return [match.group(1) for line in _split_lines(section)
            if (match := re.fullmatch(r"\s*-\s*([A-Za-z0-9_.=-]+)\s*:\s*(\S.*?)\s*", line))]


def extract_original_proof(text):
    """Return the single real proof with its original bytes and line endings."""
    raw = "" if text is None else str(text)
    starts = [(index, offset) for marker, index, offset in _control_headers(raw)
              if marker in {"completion-check", "partial-check"}]
    if len(starts) != 1:
        raise ValueError('Exactly one original completion or partial proof is required')
    index, start = starts[0]
    offsets = [0] + [match.end() for match in LINE_BREAK.finditer(raw)]
    lines = _split_lines(raw)
    end = len(raw)
    for current in range(index + 1, len(lines)):
        line = lines[current]
        if line.strip() and not (line[:1].isspace() or line.startswith('- ')):
            end = offsets[current]
            break
    return raw[start:end].rstrip()


def review_comparison_text(text):
    """Compare review delivery while retaining the original evidence bytes.

    A real control marker is separate from its field list. Blank lines between
    those two elements are presentation spacing, not a change to either input.
    All fields, their order and indentation, and surrounding prose stay exact.
    This comparison value must never replace a stored proof or source digest.
    """
    raw = "" if text is None else str(text)
    lines = _split_lines(raw)
    offsets = [0] + [match.end() for match in LINE_BREAK.finditer(raw)]
    spans = []
    for marker, index, _ in _control_headers(raw):
        if marker not in {"completion-check", "partial-check"}:
            continue
        first = index + 1
        current = first
        while current < len(lines) and not lines[current].strip():
            current += 1
        if current > first and current < len(lines) and _TOP_LEVEL_FIELD_RE.match(lines[current]):
            spans.append((offsets[first], offsets[current]))
    for start, end in reversed(spans):
        raw = raw[:start] + raw[end:]
    return raw.strip()


def parse_original_completion_evidence(evidence_text, *, allow_partial=False, allow_failed_completion=False):
    """Interpret an original qualified proof for publication and retained claims.

    This parser checks structure. The current-session assistant judges claims
    against original sources; completion_assessment.py authenticates that record.
    Criterion IDs and pass rows alone do not establish semantic support.
    allow_failed_completion permits a full failure report through this parser.
    Failed criteria never enter criterion_ids; publication defaults to all-pass.
    """
    markers = [name for name, _, _ in _control_headers(evidence_text)
               if name in {"completion-check", "partial-check"}]
    if len(markers) != 1:
        raise ValueError("Original proof requires exactly one completion or partial control")
    partial = markers[0] == "partial-check"
    if partial and not allow_partial:
        raise ValueError("Original partial proof requires an allowed partial-check")
    problem = validate_completion_evidence_map(evidence_text, partial=partial)
    if problem:
        raise ValueError(problem)
    acceptance_ids = set(extract_acceptance_criteria_ids(evidence_text))
    if not acceptance_ids:
        raise ValueError("Original evidence requires qualified acceptance criterion IDs")
    if partial:
        declarations = [line for line in _split_lines(extract_top_level_field_section(
            evidence_text, "acceptance-criteria")) if line.strip()]
        if (len(declarations) != len(acceptance_ids)
                or any(not re.fullmatch(r"\s*-\s*[A-Za-z0-9_.=-]+\s*:\s*\S.*", line)
                       for line in declarations)):
            raise ValueError("Partial acceptance criteria require unique exact IDs and descriptions")
        # The ordinary display parser tolerates commentary. Original partial
        # evidence has a closed field vocabulary, so unknown rows fail closed.
        for raw in _split_lines(extract_top_level_field_section(evidence_text, "claim-evidence-map")):
            for line in _split_entry_fields(raw):
                if line.strip() and not (_CLAIM_RE.match(line) or _ENTRY_FIELD_RE.match(line)):
                    raise ValueError("Partial claim-evidence-map contains an unrecognized field")
    entries = extract_claim_evidence_entries(evidence_text)
    criterion_ids, failed_ids = [], set()
    for entry in entries:
        bound = [token for token in re.split(r"[,;\s]+", entry['criterion']) if token]
        verdict = entry['verdict']
        if partial or allow_failed_completion:
            if verdict not in {'pass', 'fail'}:
                raise ValueError("Evidence verdicts must be bare pass or fail")
            if verdict == 'fail':
                failed_ids.update(bound)
        elif verdict.lower() != 'pass':
            raise ValueError("Original completion evidence verdicts must all be pass")
        criterion_ids.extend(key for key in bound if key not in criterion_ids)
    if partial:
        unresolved = set(extract_partial_unverified_ids(evidence_text))
        return dict(claims=entries, criterion_ids=[key for key in criterion_ids
            if key not in failed_ids | unresolved], scope='partial',
            acceptance_ids=sorted(acceptance_ids), unverified_ids=sorted(unresolved))
    return dict(claims=entries, criterion_ids=[key for key in criterion_ids if key not in failed_ids])


def extract_all_control_blocks(text, name):
    """Return all real named block bodies using the same header boundaries."""
    visible = _split_lines(_strip_block_examples(text))
    headers = list(_control_headers(text))
    wanted = ("" if name is None else str(name)).lower()
    blocks = []
    for position, (marker, line, _) in enumerate(headers):
        if marker == wanted:
            end = headers[position + 1][1] if position + 1 < len(headers) else len(visible)
            blocks.append("\n".join(visible[line + 1:end]).strip())
    return blocks


def validate_completion_text(text, *, require_completion_check=False):
    """Validate finalized or scoped partial evidence.

    Returns None when all structural checks pass, otherwise a diagnostic.
    `require_completion_check` is the caller's explicit structured requirement:
    when true, a real completion or partial block must be present, including
    for empty text. This validator does not infer requirements from prose.

    Skill invocations and printed self-report/trace fields are workflow audit,
    not evidence eligibility. Core's assessment authenticates the recorded
    claim and whole-response judgments independently of those display fields.
    """
    if not looks_like_evidence_claim(text):
        if require_completion_check:
            return (
                "The caller requires a [completion-check] block with valid verification evidence, "
                "or [partial-check] with scope: partial, acceptance-criteria, supported claim-evidence-map verdicts, "
                "and unresolved criterion IDs in unverified when the overall task remains incomplete."
            )
        return None

    issues = []
    partial_blocks = extract_all_control_blocks(text, "partial-check")
    if partial_blocks and looks_like_completion_claim(text):
        issues.append("A response must use either [completion-check] or [partial-check], not both finalized and unresolved status.")
    for marker in ("completion-check", "partial-check"):
        positions = find_control_block_positions(text, marker)
        if not positions:
            continue
        for block in extract_all_control_blocks(text, marker):
            reason = validate_completion_evidence_map(block, partial=marker == "partial-check")
            if reason:
                issues.append(reason)
    return " ".join(dict.fromkeys(issues)) if issues else None
