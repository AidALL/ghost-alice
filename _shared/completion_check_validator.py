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

# Match block headers case-insensitively and allow leading whitespace so position lookup agrees with control-block extraction.
_BLOCK_HEADER_RE = re.compile(r"^\[([a-z0-9-]+)\]", re.I)
_BLOCK_HEADER_POSITION_RE = re.compile(r"^[ \t]*\[([a-z0-9-]+)\]", re.I | re.M)

# Split on \r?\n only; splitlines() would also split on other Unicode line boundaries, which we do not want here.
_NEWLINE_SPLIT_RE = re.compile(r"\r?\n")

# Explicit claim detection is marker-only ([completion-check]). Mandatory final block mode also uses a narrow executed-work closure detector so routine explanations do not become completion-check retry loops.

_VERIFICATION_DONE_RE = re.compile(r"-\s*verification-before-completion:\s*done\b")
_SKILL_CALL_RE = re.compile(r"-\s*skill-call:\s*([^\n]+)")
_ZERO_WIDTH_RE = re.compile("[​‌‍‎‏﻿]")

_TOP_LEVEL_FIELD_RE = re.compile(r"^-\s*[A-Za-z0-9_-]+\s*:")
# Acceptance-criteria id: a leading `- <id>:` on each line.
_ACCEPTANCE_ID_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.=-]+)\s*:", re.M)
# Claim-evidence entry field patterns, case-insensitive.
_CLAIM_RE = re.compile(r"^\s*-\s*claim\s*:\s*(.*?)\s*$", re.I)
_ENTRY_FIELD_RE = re.compile(r"^\s*(criterion|evidence|verdict)\s*:\s*(.*?)\s*$", re.I)
# Exactly `none` or `- none`, case-insensitive; never a prefix of pending work.
_NONE_LINE_RE = re.compile(r"^(?:-\s*)?none\s*$", re.I)

# A completion noun is not an executed-work assertion (e.g. "작업 완료 시간",
# "작업 간 완료 의존관계"). Require an assertive predicate, or a bare status at
# clause end. The Korean boundary prevents a past stem inside a hypothetical
# modifier ("완료했을 때") from becoming an assertion.
_KOREAN_ASSERTED_END = (
    r"(?:했(?:습니다|어요|다|음|고|으며)?|하였(?:습니다|다|음)|"
    r"됐(?:습니다|어요|다|음|고)?|되었(?:습니다|다|음|고)|되었습니다|"
    r"함|됨|입니다|임)(?![가-힣])"
)
_KOREAN_STATUS_END = (
    r"(?:" + _KOREAN_ASSERTED_END
    + r"|\s*(?:된\s*상태|상태)(?:입니다|다|임)(?![가-힣])|(?=\s*(?:$|;)))"
)

_VALID_VERDICTS = ("pass", "fail", "unverified")
# Executed work includes read-only deliverables. Match their asserted closure,
# not the word "complete" alone; the same clause/example filters apply below.
_READ_ONLY_WORK_NOUNS = (
    r"(?:reviews?|reports?|analys(?:is|es)|audits?|research|investigations?)"
)
_CLOSURE_ADVERBS = r"(?:now|already|fully|finally|successfully|completely|actually)"
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
    re.compile(
        r"\b" + _READ_ONLY_WORK_NOUNS
        + r"\s+(?:(?P<read_only_finite>(?:is|are|was|were)\s+|"
        r"(?:has|have|had)\s+(?:" + _CLOSURE_ADVERBS + r"\s+)*been\s+))?"
        r"(?:" + _CLOSURE_ADVERBS + r"\s+)*(?:complete|completed|done|finished)\b",
        re.I,
    ),
    re.compile(
        r"\b(?P<read_only_action>completed|finished)\s+"
        r"(?:(?:a|an|the|my|our|all)\s+)?"
        r"(?:requested\s+)?(?:read[- ]only\s+)?" + _READ_ONLY_WORK_NOUNS + r"\b",
        re.I,
    ),
    re.compile(r"\b(?:all\s+)?tests?\s+(?:pass|passed|green)\b", re.I),
    re.compile(r"\b(?:build|lint|typecheck|test\s+suite)\s+(?:succeeded|passed|is\s+clean)\b", re.I),
    re.compile(r"(?:작업|변경|수정|구현|요청|이슈|버그|검토|보고서|평가|분석|조사|연구).{0,12}(?:완료|끝남|해결|반영)" + _KOREAN_STATUS_END),
    re.compile(r"(?:테스트|빌드|린트|타입체크).{0,12}(?:통과|성공|깨끗)" + _KOREAN_STATUS_END),
    re.compile(r"(?:완료|수정|구현|해결|반영)" + _KOREAN_ASSERTED_END + r"|(?:끝냈|고쳤)(?:습니다|다)"),
)

# Spans that are quotation or code, not the model's own assertion. Stripped before executed-work detection so illustrative output, quoted user text, and fenced examples do not trigger a spurious completion-check demand.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)$")
_QUOTE_LINE_RE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_LIST_ITEM_RE = re.compile(r"^ *(?:[*+-]|[0-9]{1,9}[.)])[ \t]+")
_PARAGRAPH_BREAK_RE = re.compile(
    r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|(?:[*+-]|1[.)])[ \t]+|"
    r"(?:[-*_][ \t]*){3,}$|(?:=+|-+)[ \t]*$|<[!/A-Za-z])"
)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Single-quoted spans only count when the quotes sit on word boundaries, so an ordinary contraction apostrophe (won't, don't, y'all) is not mistaken for a quotation delimiter and does not swallow a real claim between two contractions.
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

# Executed-work detection runs per clause. Two lead families are exempt, with different governance scope:
#   - GOAL / enumeration leads (goal, acceptance criteria, todo, next steps,
# plan, ...) introduce a list and govern the WHOLE sentence, so a comma-separated goal list ("Goal: tests pass, lint is clean") is exempt.
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
# A modal governs a bare status or participle only within its predicate scope.
# Coordinated subjects and contrast/semicolon boundaries can start a separate
# assertion; an explicit finite status predicate is evaluated independently.
_PREDICATE_SCOPE_BOUNDARY_RE = re.compile(
    r";|\b(?:but|however|yet)\b|\band\s+(?=(?:i|we|they|he|she|it)\b)",
    re.I,
)
_FUTURE_PREDICATE_RE = re.compile(r"\b(?:will|would|shall|\w+['’]ll)\b", re.I)
_INQUIRY_SUBORDINATOR_RE = re.compile(r"\b(?:whether|if)\b", re.I)
_TEMPORAL_SUBORDINATOR_RE = re.compile(
    r"\b(?:when|whenever|once|until|before|after|unless|as\s+soon\s+as)\b",
    re.I,
)
# Finite action and factual reporting predicates open their own assertion
# scope. A shared future auxiliary still governs coordinated participles.
_COORDINATED_ASSERTION_PREDICATE_RE = re.compile(
    r"\band\s+(?:(?:have|has|had)\s+)?(?:" + _CLOSURE_ADVERBS + r"\s+)*"
    r"(?P<verb>completed|finished|confirmed|verified|established|determined|"
    r"concluded|reported|stated|asserted|observed|proved|found|showed)\b",
    re.I,
)
# Pronoun subjects may carry contracted have/has. Noun-phrase possessives
# remain distinct from those perfect auxiliaries.
_ACTION_ACTOR = (
    r"(?:(?:i|we|you|they)(?:['’]ve)?|(?:he|she|it)(?:['’]s)?|"
    r"(?:the|our|my)\s+(?:team|agent|assistant|reviewer))"
)
_INQUIRY_ACTION_SUBJECT_RE = re.compile(
    r"\b(?:whether|if)\s+(?:or\s+not\s+)?" + _ACTION_ACTOR + r"\s+",
    re.I,
)
_ACTION_PREDICATE_PREFIX_RE = re.compile(
    r"\b(?:" + _ACTION_ACTOR + r"|have|has|had)\s+"
    r"(?:" + _CLOSURE_ADVERBS + r"\s+)*$",
    re.I,
)
_ACTION_OBJECT_DETERMINER_RE = re.compile(r"\s+(?:a|an|the|my|our|all)\b", re.I)
_ACTION_CLAUSE_HEAD_RE = re.compile(
    r"^\s*(?:[>*\-]\s+)?" + _ACTION_ACTOR + r"\s+"
    r"(?P<shared_negative>(?:(?:have|has|had|do|does|did)\s+)?"
    r"(?:not|never)\b|(?:haven['’]?t|hasn['’]?t|hadn['’]?t|"
    r"don['’]?t|doesn['’]?t|didn['’]?t)\b)?",
    re.I,
)
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
    r"haven['’]?t|hasn['’]?t|hadn['’]?t|cannot|can['’]?t|don['’]?t|"
    r"doesn['’]?t|didn['’]?t|no\s+longer)\s+"
    r"(?:(?:yet|fully|completely|quite|now|already|finally|successfully|"
    r"actually|been|be|have)\s+)*"
    r"(?:complete|completed|done|finished|fixed|resolved|implemented|passing|pass|"
    r"passed|green|clean|succeed|succeeded)\b"
    r"|\bnot\s+there\s+yet\b|\bnot\s+yet\b"
    # "미완료" means incomplete. Remove the whole negative token so the
    # detector cannot match its "완료" suffix, without hiding another claim.
    r"|미완료|아직|않았|않은|않고"
    # Korean negation is post-verbal ("완료되지 않았다" = is NOT complete), so the closure word survives if only the negation particle is removed. Match the closure word together with its trailing negation so the whole span is stripped and the executed-work pattern no longer sees a bare "작업...완료".
    r"|(?:완료|끝냈|끝남|고쳤|해결|반영|통과|성공|깨끗)"
    r"[^\n.!?;,]{0,6}(?:않|안\s|아니|아닙|하지\s*못|되지\s*못|못했|못했다|못했습니다|볼\s*수[^\n.!?;,]{0,8}없)",
    re.I,
)
_HONEST_NEGATIVE_STATUS_RE = re.compile(
    r"\b(?:have|has|had)\s+not\s+(?:verified?|confirmed?|checked|proven)\b"
    r"[^\n.!?;,]{0,80}\b(?:tests?\s+)?(?:pass(?:ed|ing)?|complete|completed|done|fixed|resolved|clean|green)\b"
    r"|\b(?:haven['’]?t|hasn['’]?t|hadn['’]?t)\s+(?:verified?|confirmed?|checked|proven)\b"
    r"[^\n.!?;,]{0,80}\b(?:tests?\s+)?(?:pass(?:ed|ing)?|complete|completed|done|fixed|resolved|clean|green)\b"
    r"|\b(?:do|does|did)\s+not\s+(?:think|believe|claim|say)\b"
    r"[^\n.!?;,]{0,80}\b(?:tests?\s+)?(?:pass(?:ed|ing)?|complete|completed|done|fixed|resolved|clean|green)\b"
    r"|\b(?:don['’]?t|doesn['’]?t|didn['’]?t)\s+(?:think|believe|claim|say)\b"
    r"[^\n.!?;,]{0,80}\b(?:tests?\s+)?(?:pass(?:ed|ing)?|complete|completed|done|fixed|resolved|clean|green)\b"
    r"|(?:완료|통과|성공|해결|반영)[^\n.!?;,]{0,24}(?:여부|라고|했다고|한\s*것|된\s*것)"
    r"[^\n.!?;,]{0,24}(?:말할\s*수[^\n.!?;,]{0,12}없|확인하지\s*못|검증하지\s*못|보기\s*어렵|볼\s*수[^\n.!?;,]{0,8}없|아니|아닙)"
    r"|(?:말할\s*수[^\n.!?;,]{0,12}없|확인하지\s*못|검증하지\s*못|보기\s*어렵)[^\n.!?;,]{0,40}"
    r"(?:완료|통과|성공|해결|반영)",
    re.I,
)


def _split_lines(text):
    return _NEWLINE_SPLIT_RE.split("" if text is None else str(text))


def _strip_noncommittal_spans(text):
    """Drop fenced/inline code, blockquotes, and quoted spans.

    These carry illustrative or reported content, not the model's own
    executed-work assertion, so they must not trigger completion detection.
    """
    text = _strip_block_examples(text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return _QUOTED_SPAN_RE.sub(" ", text)


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
        control_fields = bool(header and (header.group(1).lower() in _CONTROL_BLOCKS
                                          or header.group(1).lower() == "completion-check"))
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
    for line in _split_lines(_strip_block_examples(text)):
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
    cleaned = strip_control_blocks(_strip_noncommittal_spans(text))
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
            claim_clause = _HONEST_NEGATIVE_STATUS_RE.sub(" ", clause)
            claim_clause = _NEGATED_CLOSURE_RE.sub(" ", claim_clause)
            for pattern in _EXECUTED_WORK_CLAIM_PATTERNS:
                for match in pattern.finditer(claim_clause):
                    if _asserted_work_predicate(match, claim_clause):
                        return True
    return False


def _asserted_work_predicate(match, clause):
    """Keep current read-only assertions distinct from a future predicate.

    Only the read-only patterns need this distinction: their bare status and
    past-participle forms can also occur under "will mark" or "will have".
    An inquiry does not assert its outcome, and a temporal/conditional status
    governed by a future promise is not a current outcome either. A separate
    finite status still states its own outcome; do not discard the whole clause
    for a modal or dependent predicate.
    """
    groups = match.groupdict()
    if not ("read_only_finite" in groups or "read_only_action" in groups):
        return True
    if groups.get("read_only_action") and not _read_only_action_has_predicate(match, clause):
        return False
    prefix = clause[:match.start()]
    scope_start = 0
    for boundary in _PREDICATE_SCOPE_BOUNDARY_RE.finditer(prefix):
        scope_start = boundary.end()
    inherited_future = False
    for predicate in _COORDINATED_ASSERTION_PREDICATE_RE.finditer(clause, scope_start):
        if predicate.start("verb") > match.start():
            break
        inherited_future = inherited_future or bool(
            _FUTURE_PREDICATE_RE.search(clause[scope_start:predicate.start()])
        )
        # Coordination keeps the actor introduced inside an inquiry in that
        # dependent subject scope. Explicit clause/subject boundaries above
        # still open a separate assertion after the inquiry.
        if _INQUIRY_ACTION_SUBJECT_RE.search(clause[scope_start:predicate.start()]):
            continue
        scope_start = predicate.start("verb")
    scoped_prefix = prefix[scope_start:]
    if inherited_future:
        return False
    if _INQUIRY_SUBORDINATOR_RE.search(scoped_prefix):
        return False
    for subordinator in _TEMPORAL_SUBORDINATOR_RE.finditer(scoped_prefix):
        if _FUTURE_PREDICATE_RE.search(scoped_prefix[:subordinator.start()]):
            return False
    if groups.get("read_only_finite"):
        return True
    return _FUTURE_PREDICATE_RE.search(scoped_prefix) is None


def _read_only_action_has_predicate(match, clause):
    """Require an action context for a completed/finished noun phrase.

    An actor or perfect auxiliary establishes a finite action. An explicit
    object determiner distinguishes a bare initial/coordinated action from an
    attributive noun phrase. A declarative actor supplies the shared subject
    for a coordinated action regardless of the preceding verb's inflection;
    a shared negative auxiliary still governs that action.
    """
    prefix = clause[:match.start()]
    scope_start = 0
    for boundary in _PREDICATE_SCOPE_BOUNDARY_RE.finditer(prefix):
        scope_start = boundary.end()
    scoped_prefix = prefix[scope_start:]
    if _ACTION_PREDICATE_PREFIX_RE.search(scoped_prefix):
        return True
    if not _ACTION_OBJECT_DETERMINER_RE.match(clause, match.end("read_only_action")):
        return False
    if re.fullmatch(r"\s*(?:[>*\-]\s+)?", scoped_prefix):
        return True
    head = _ACTION_CLAUSE_HEAD_RE.match(scoped_prefix)
    if head is None or head.group("shared_negative"):
        return False
    return any(predicate.start("verb") == match.start()
               for predicate in _COORDINATED_ASSERTION_PREDICATE_RE.finditer(clause, scope_start))


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
    for name in ("acceptance-criteria", "claim-evidence-map", "unverified"):
        headers = re.findall(r"^-\s*" + re.escape(name) + r"\s*:", completion_check, re.I | re.M)
        if len(headers) > 1:
            return f"[completion-check] must not contain duplicate {name} sections."
    entries = extract_claim_evidence_entries(completion_check)
    if len(entries) == 0:
        return "[completion-check] must include non-empty claim-evidence-map."

    acceptance_ids = extract_acceptance_criteria_ids(completion_check)
    acceptance_id_set = set(acceptance_ids)
    full_form = len(acceptance_ids) > 0
    for entry in entries:
        if entry.get("_invalid"):
            return entry["_invalid"]
        if not entry.get("claim"):
            return "Every claim-evidence-map entry must include a non-empty claim."
        if full_form:
            criterion = entry.get("criterion") or ""
            # Every explicitly referenced ID must be declared, regardless of the list delimiter.
            criterion_ids = [token for token in re.split(r"[,;\s]+", criterion.strip()) if token]
            if not criterion_ids or any(token not in acceptance_id_set for token in criterion_ids):
                return "Every claim-evidence-map entry must reference an acceptance-criteria criterion id."
        if not entry.get("evidence"):
            return "Every claim-evidence-map entry must include evidence."
        verdict = entry.get("verdict", "").lower()
        if not verdict:
            return "Every claim-evidence-map entry must include verdict: pass | fail | unverified."
        if verdict not in _VALID_VERDICTS:
            return (
                "Invalid verdict value in claim-evidence-map. Write verdict: pass or verdict: fail "
                "on its own line, without punctuation, quotes, or commentary. If the claim is "
                "unverified, report partial state without a finalized [completion-check]."
            )
        if verdict == "unverified":
            return (
                "A finalized [completion-check] cannot contain an 'unverified' verdict. " "Verify the claim to pass/fail, or report partial state in prose " "without a [completion-check] block."
            )

    unverified_section = extract_top_level_field_section(completion_check, "unverified")
    if not unverified_section:
        return "[completion-check] must include an unverified section."
    if not section_is_none(unverified_section):
        return (
            "A finalized [completion-check] requires the unverified section to be 'none'. " "If items remain unverified, report partial state in prose instead of finalizing."
        )
    return None


def _split_skill_tokens(raw):
    """Split a skills-loaded value into normalized skill tokens.

    Accepts comma-, semicolon- or newline-separated values; strips surrounding brackets,
    quotes, whitespace, and zero-width characters so that visually-identical
    values do not produce false negatives.
    """
    tokens = []
    for part in re.split(r"[,;\n]+", raw):
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
    return re.split(r"[\s(]", token, maxsplit=1)[0].strip().rstrip(":.").lower()


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


def validate_completion_text(text, *, require_completion_check=False, require_skill_self_report=True):
    """Validate a completion claim.

    Returns None when `text` is empty, is not a completion claim, or passes
    every gate; otherwise the deny-reason string. When
    `require_completion_check` is true, executed-work closure claims must carry
    the explicit marker, while routine explanations remain allowed.

    `require_skill_self_report` (default True) keeps the self-reported
    verification-before-completion lines. A host that can verify the skill
    call from its own transcript (Claude Code) passes False. Printed io-trace
    is optional display, not verification evidence; every evidence check
    still applies independently of its presence, order or skill spelling.
    """
    text = _strip_block_examples(text)
    if not text:
        return None
    if not looks_like_completion_claim(text):
        if require_completion_check and requires_completion_check(text):
            return (
                "Completion or success claims about executed work require a " "[completion-check] block with valid verification evidence."
            )
        return None

    completion_check = extract_control_block(text, "completion-check")
    if not completion_check:
        return (
            "Completion or success claims about executed work require a " "[completion-check] block with fresh evidence."
        )

    completion_positions = find_control_block_positions(text, "completion-check")
    if not completion_positions:
        # Header body was extracted but no position was located (e.g. an unusual header form). Treat as a missing marker rather than crashing.
        return (
            "Completion or success claims about executed work require a " "[completion-check] block with fresh evidence."
        )
    first_completion = completion_positions[0]
    gate_state_positions = find_control_block_positions(text, "gate-state")
    if any(position > first_completion for position in gate_state_positions):
        return "[gate-state] belongs to the opening surface and must not appear after [completion-check]."

    if require_skill_self_report:
        if not _VERIFICATION_DONE_RE.search(completion_check):
            return "[completion-check] must include '- verification-before-completion: done'."

        skill_call = _SKILL_CALL_RE.search(completion_check)
        if not skill_call or not any(
            _skill_name(token) == "verification-before-completion"
            for token in _split_skill_tokens(skill_call.group(1))
        ):
            return " ".join(
                [
                    "verification-reminder: emitted.",
                    "Run verification-before-completion before claiming completion.",
                    "Do not claim a verification skill-call unless a fresh or valid retained instruction body was used and its workflow executed this turn.",
                ]
            )

    # Validate every completion-check block, not just the first, so a malformed later block cannot pass by hiding behind a valid earlier one.
    for block in extract_all_control_blocks(text, "completion-check"):
        reason = validate_completion_evidence_map(block)
        if reason:
            return reason
    return None
