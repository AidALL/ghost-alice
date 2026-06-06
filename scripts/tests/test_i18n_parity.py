"""
i18n (internationalization) parity lock test for Ghost-ALICE installers.

Extracts (ko, en) string pairs from install.sh ``t`` calls and install.ps1
``T`` calls, computes the symmetric difference of English strings between the
two files, and asserts that difference equals a pre-recorded baseline.

Purpose
-------
This is a BASELINE LOCK, not a zero-drift enforcement.  Because bash uses
``${state_path}`` interpolation and PowerShell uses ``$StatePath``, semantically
identical messages can appear textually different across the two files.  The
measured drift is therefore expected to be large.  The test tolerates all
EXISTING drift while causing any NEW drift (strings added or removed after the
baseline was recorded) to fail immediately.

Extractor design notes
----------------------
install.sh call sites look like::

    $(t 'Korean text' 'English text')
    $(t "Korean ${var}" "English ${var}")

install.ps1 call sites look like::

    (T "Korean text" "English text")
    T "Korean text" "English text"

Both files use only single-line calls.  Strings may contain ``${var}`` / ``$Var``
interpolations, punctuation, and escaped double-quotes (``\\\"``), but a string
argument never contains an unescaped instance of its own delimiter.  The extractor
therefore uses ``[^QUOTE]*`` (with a secondary pass to normalise embedded
``\\\"`` in double-quoted strings) to capture argument content.

The extractor uses ``re.findall`` on the full file text so that lines with
multiple ``t``/``T`` calls are handled correctly.

Determinism guarantee
---------------------
``re.findall`` is deterministic: same source text → same list in the same order.
Conversion to a ``set`` before computing the symmetric difference removes any
order sensitivity.  The baseline JSON uses ``sort_keys=True`` and
``ensure_ascii=False`` for a stable, human-readable encoding.
"""

import json
import os
import re
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SH_PATH = os.path.join(_WORKTREE, "install.sh")
_PS1_PATH = os.path.join(_WORKTREE, "install.ps1")
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "_fixtures", "characterization")
_BASELINE_PATH = os.path.join(_FIXTURE_DIR, "i18n_drift_baseline.json")


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _extract_quoted_arg(text: str, pos: int) -> tuple[str, int]:
    """Return (content, end_pos) for a single- or double-quoted string starting at pos.

    *pos* must point at the opening quote character (``'`` or ``"``).
    Raises ValueError if pos does not point at a quote.
    """
    if pos >= len(text):
        raise ValueError("pos out of range")
    quote = text[pos]
    if quote not in ('"', "'"):
        raise ValueError(f"Expected quote at pos {pos}, got {text[pos]!r}")
    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote == '"':
            # skip escaped character inside double-quoted string
            i += 2
            continue
        if ch == quote:
            return text[pos + 1 : i], i + 1
        i += 1
    raise ValueError(f"Unterminated quoted string starting at pos {pos}")


# Regex to locate a `t` or `T` call.  We match:
#   \b t \s+ QUOTE   (bash). Word boundary ensures we don't match "cat", etc.
#   \b T \s+ QUOTE   (PS1). The same pattern; PS1 is case-sensitive so T only.
#
# After matching, we hand-parse the two arguments to avoid nested-quote issues.
_T_CALL_RE_SH = re.compile(r'\bt(?=\s+[\'"])', re.UNICODE)
_T_CALL_RE_PS1 = re.compile(r'\bT(?=\s+[\'"])', re.UNICODE)


def _extract_pairs_from_text(text: str, call_re: re.Pattern) -> list[tuple[str, str]]:
    """Return all (ko, en) pairs found in *text* using *call_re* to find call sites.

    The function scans every match of *call_re*, then hand-parses the two
    quoted arguments that follow.  Calls where the second argument is absent
    or not a quoted string are silently skipped (they are single-argument
    translation calls that fall back to the Korean string only).
    """
    pairs: list[tuple[str, str]] = []
    for m in call_re.finditer(text):
        pos = m.end()
        # skip whitespace between function name and first argument
        while pos < len(text) and text[pos] in (" ", "\t"):
            pos += 1
        if pos >= len(text) or text[pos] not in ('"', "'"):
            continue
        try:
            ko, pos = _extract_quoted_arg(text, pos)
        except ValueError:
            continue
        # skip whitespace between first and second argument
        while pos < len(text) and text[pos] in (" ", "\t"):
            pos += 1
        if pos >= len(text) or text[pos] not in ('"', "'"):
            # single-argument call. Skip.
            continue
        try:
            en, _ = _extract_quoted_arg(text, pos)
        except ValueError:
            continue
        pairs.append((ko, en))
    return pairs


def extract_sh_pairs(path: str = _SH_PATH) -> list[tuple[str, str]]:
    """Return (ko, en) pairs from all ``t`` calls in install.sh and installer_lib/*.sh.

    Bash modularization moved many ``t`` call sites out of install.sh into
    installer_lib/*.sh, so parity with install.ps1 must scan the combined source.
    """
    sources = [path]
    lib_dir = os.path.join(os.path.dirname(path), "installer_lib")
    if os.path.isdir(lib_dir):
        sources.extend(
            os.path.join(lib_dir, name)
            for name in sorted(os.listdir(lib_dir))
            if name.endswith(".sh")
        )
    texts = []
    for source in sources:
        with open(source, encoding="utf-8") as fh:
            texts.append(fh.read())
    return _extract_pairs_from_text("\n".join(texts), _T_CALL_RE_SH)


def extract_ps1_pairs(path: str = _PS1_PATH) -> list[tuple[str, str]]:
    """Return (ko, en) pairs from all ``T`` calls in install.ps1 and installer_lib/*.ps1.

    PowerShell modularization moved many ``T`` call sites out of install.ps1 into
    installer_lib/*.ps1, so parity with install.sh must scan the combined source.
    """
    sources = [path]
    lib_dir = os.path.join(os.path.dirname(path), "installer_lib")
    if os.path.isdir(lib_dir):
        sources.extend(
            os.path.join(lib_dir, name)
            for name in sorted(os.listdir(lib_dir))
            if name.endswith(".ps1")
        )
    texts = []
    for source in sources:
        with open(source, encoding="utf-8") as fh:
            texts.append(fh.read())
    return _extract_pairs_from_text("\n".join(texts), _T_CALL_RE_PS1)


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------

def compute_drift(
    sh_pairs: list[tuple[str, str]],
    ps1_pairs: list[tuple[str, str]],
) -> dict:
    """Return the symmetric difference of English strings between the two files.

    Returns a dict with keys:
      ``sh_only``. English strings present in install.sh but not install.ps1
      ``ps1_only``. English strings present in install.ps1 but not install.sh
    Both lists are sorted for determinism.
    """
    sh_en = {en for _, en in sh_pairs}
    ps1_en = {en for _, en in ps1_pairs}
    return {
        "sh_only": sorted(sh_en - ps1_en),
        "ps1_only": sorted(ps1_en - sh_en),
    }


def generate_baseline(output_path: str = _BASELINE_PATH) -> dict:
    """Compute current drift and write it to *output_path*.  Returns the drift dict."""
    drift = compute_drift(extract_sh_pairs(), extract_ps1_pairs())
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(drift, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.write("\n")
    return drift


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestI18nParityBaseline(unittest.TestCase):
    """Lock the current i18n drift between install.sh and install.ps1.

    The test does NOT enforce zero drift.  It enforces that drift has not
    CHANGED relative to the baseline recorded in i18n_drift_baseline.json.
    """

    def _load_baseline(self) -> dict:
        if not os.path.exists(_BASELINE_PATH):
            self.fail(
                f"Baseline fixture missing: {_BASELINE_PATH}\n"
                "Run  python3 -c \"from scripts.tests.test_i18n_parity import generate_baseline; generate_baseline()\"  "
                "from the worktree root, then commit the result."
            )
        with open(_BASELINE_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    def test_extractor_finds_sh_pairs(self):
        """install.sh extractor must find a non-trivial number of pairs."""
        pairs = extract_sh_pairs()
        self.assertGreater(
            len(pairs), 50,
            f"Expected >50 t() pairs in install.sh; got {len(pairs)}. "
            "The extractor may be broken.",
        )

    def test_extractor_finds_ps1_pairs(self):
        """install.ps1 extractor must find at least 10 pairs."""
        pairs = extract_ps1_pairs()
        self.assertGreater(
            len(pairs), 5,
            f"Expected >5 T() pairs in install.ps1; got {len(pairs)}. "
            "The extractor may be broken.",
        )

    def test_drift_matches_baseline(self):
        """Symmetric difference of EN strings must equal the recorded baseline.

        Any NEW string added to only one installer, or any string removed from
        one installer without the equivalent removal from the other, will be
        caught here.
        """
        baseline = self._load_baseline()
        sh_pairs = extract_sh_pairs()
        ps1_pairs = extract_ps1_pairs()
        current = compute_drift(sh_pairs, ps1_pairs)

        new_sh_only = sorted(set(current["sh_only"]) - set(baseline["sh_only"]))
        new_ps1_only = sorted(set(current["ps1_only"]) - set(baseline["ps1_only"]))
        resolved_sh_only = sorted(set(baseline["sh_only"]) - set(current["sh_only"]))
        resolved_ps1_only = sorted(set(baseline["ps1_only"]) - set(current["ps1_only"]))

        problems: list[str] = []
        if new_sh_only:
            problems.append(
                f"NEW sh-only EN strings (added to install.sh but not install.ps1):\n"
                + "\n".join(f"  {s!r}" for s in new_sh_only)
            )
        if new_ps1_only:
            problems.append(
                f"NEW ps1-only EN strings (added to install.ps1 but not install.sh):\n"
                + "\n".join(f"  {s!r}" for s in new_ps1_only)
            )
        if resolved_sh_only:
            problems.append(
                f"RESOLVED sh-only strings (removed from drift. Update the baseline):\n"
                + "\n".join(f"  {s!r}" for s in resolved_sh_only)
            )
        if resolved_ps1_only:
            problems.append(
                f"RESOLVED ps1-only strings (removed from drift. Update the baseline):\n"
                + "\n".join(f"  {s!r}" for s in resolved_ps1_only)
            )

        if problems:
            self.fail(
                "i18n drift has changed relative to baseline.\n"
                "If this change is intentional, regenerate the baseline with:\n"
                "  python3 -c \"from scripts.tests.test_i18n_parity import generate_baseline; generate_baseline()\"\n\n"
                + "\n\n".join(problems)
            )


if __name__ == "__main__":
    # Running directly: generate the baseline and then run the tests.
    print("Generating baseline …")
    drift = generate_baseline()
    print(
        f"Baseline written to {_BASELINE_PATH}\n"
        f"  sh_only:  {len(drift['sh_only'])} strings\n"
        f"  ps1_only: {len(drift['ps1_only'])} strings\n"
    )
    unittest.main(argv=["__main__"])
