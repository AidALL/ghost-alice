"""Structural checks for SKILL.md Contents and heading parity.

When Contents exists, every h2 outside fenced code must be listed and every
Contents anchor must resolve. Short skills without Contents are exempt.
Reference-file TOC presence is checked separately.

Run: python3 -m pytest scripts/tests/test_validate_toc_parity.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_skills as vs  # noqa: E402


def _run(text: str):
    """Call the parity check on raw SKILL.md text; return its Issue list."""
    issues: list = []
    vs.validate_phase_toc_parity(Path("alpha/SKILL.md"), text, issues)
    return issues


CLEAN = """\
---
name: alpha
description: x
---

# Alpha
## Contents

- [Overview](#overview)
- [Details](#details)

## Overview
body
## Details
body
"""

MISSING_FROM_TOC = """\
---
name: alpha
description: x
---

# Alpha
## Contents

- [Overview](#overview)

## Overview
body
## Details
body
"""

DANGLING_ANCHOR = """\
---
name: alpha
description: x
---

# Alpha
## Contents

- [Overview](#overview)
- [Old Name](#old-name)

## Overview
body
## New Name
body
"""

HEADING_INSIDE_FENCE = """\
---
name: alpha
description: x
---

# Alpha
## Contents

- [Overview](#overview)

## Overview
Example template below:

````markdown
## Details
this is example content, not a real section
````
"""

NO_CONTENTS = """\
---
name: alpha
description: x
---

# Alpha
## Overview
body
## Details
body
"""


class TocParityTest(unittest.TestCase):
    def _rules(self, issues):
        return [i.rule for i in issues]

    def test_clean_skill_has_no_parity_issue(self):
        self.assertEqual(_run(CLEAN), [])

    def test_heading_missing_from_contents_is_flagged(self):
        issues = _run(MISSING_FROM_TOC)
        self.assertTrue(issues, "a heading absent from Contents must be flagged")
        self.assertTrue(any("Details" in i.message for i in issues), self._rules(issues))
        # TOC parity is build-failing: it must be ERROR so CI blocks the drift.
        self.assertTrue(all(i.severity == "ERROR" for i in issues),
                        [i.severity for i in issues])

    def test_dangling_contents_anchor_is_flagged(self):
        issues = _run(DANGLING_ANCHOR)
        self.assertTrue(issues, "a Contents anchor with no matching heading must be flagged")
        self.assertTrue(any("old-name" in i.message.lower() for i in issues), self._rules(issues))
        self.assertTrue(all(i.severity == "ERROR" for i in issues),
                        [i.severity for i in issues])

    def test_heading_inside_code_fence_is_not_a_section(self):
        # `## Details` lives inside a ````markdown fence -> not a real section -> no flag.
        self.assertEqual(_run(HEADING_INSIDE_FENCE), [])

    def test_skill_without_contents_is_exempt(self):
        # Short skills without a Contents section are not forced to have one.
        self.assertEqual(_run(NO_CONTENTS), [])


if __name__ == "__main__":
    unittest.main()
