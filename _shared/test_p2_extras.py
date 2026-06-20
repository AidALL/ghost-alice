"""TDD for remaining addon logic: marker v2 (T2.8/T2.9), collision detection
(T2.1/T2.2), and core-dependency block (T2.11).

Run: python3 -m pytest _shared/test_p2_extras.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402
import addon_registry as reg  # noqa: E402
import addon_uninstall as un  # noqa: E402
import installer_assets as iassets  # noqa: E402


def _sidecar(addon_id, *, provided=None, depends_on_core=None):
    return {
        "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
        "source": f"/s/{addon_id}", "platform": "claude", "owner": "addon",
        "origin": f"addon:{addon_id}", "depends_on_core": depends_on_core or [],
        "min_core_version": "0.0.0", "installed_at": "t", "provided": provided or [],
    }


class MarkerV2Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _managed_dir(self, name, *, addon_id):
        d = self.root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("body", encoding="utf-8")
        iassets.write_ownership_marker(
            d, platform="claude", asset_id=name, source_repo="repo", source_commit="abc",
            install_mode="copy", owner="addon", addon_id=addon_id, provided_kind="skill")
        return d

    def test_marker_carries_addon_attribution(self):
        d = self._managed_dir("foo", addon_id="x")
        marker = json.loads((d / ".ghost-alice-install.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["owner"], "addon")
        self.assertEqual(marker["addon_id"], "x")
        self.assertEqual(marker["provided_kind"], "skill")

    def test_classify_addon_id_mismatch_is_conflict(self):
        d = self._managed_dir("foo", addon_id="x")
        wrong = iassets.classify_skill_root(d, expected_addon_id="y")
        self.assertEqual(wrong.ownership, iassets.OWNERSHIP_CONFLICT)
        self.assertEqual(wrong.reason, "marker-addon-mismatch")
        right = iassets.classify_skill_root(d, expected_addon_id="x")
        self.assertEqual(right.ownership, iassets.OWNERSHIP_GHOST_ALICE_MANAGED)

    def test_classify_without_expected_addon_id_unaffected(self):
        d = self._managed_dir("foo", addon_id="x")
        self.assertEqual(iassets.classify_skill_root(d).ownership, iassets.OWNERSHIP_GHOST_ALICE_MANAGED)

    def test_classify_symlink_ignores_expected_addon_id(self):
        # Contract: symlink-mode targets carry no marker, so classify proves
        # ownership from the link target (repo-relative), NOT from expected_addon_id.
        repo = self.root / "repo"
        src = repo / "noop"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("body", encoding="utf-8")
        link = self.root / "skills" / "noop"
        link.parent.mkdir(parents=True)
        link.symlink_to(src, target_is_directory=True)
        # expected_addon_id is irrelevant for a symlink; the link-to-repo wins.
        result = iassets.classify_skill_root(link, expected_addon_id="anything", repo_root=repo)
        self.assertEqual(result.ownership, iassets.OWNERSHIP_GHOST_ALICE_MANAGED)
        self.assertEqual(result.reason, "symlink-to-repo")

    def test_classify_marker_without_addon_id_fails_closed(self):
        # a v1-style marker (no addon_id) cannot prove addon ownership -> fail closed
        d = self.root / "foo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("body", encoding="utf-8")
        iassets.write_ownership_marker(d, platform="claude", asset_id="foo",
                                       source_repo="r", source_commit="c", install_mode="copy")
        result = iassets.classify_skill_root(d, expected_addon_id="x")
        self.assertEqual(result.ownership, iassets.OWNERSHIP_CONFLICT)
        self.assertEqual(result.reason, "marker-addon-unattributed")
        # without expected_addon_id (the core/legacy path) it is still managed
        self.assertEqual(iassets.classify_skill_root(d).ownership, iassets.OWNERSHIP_GHOST_ALICE_MANAGED)


class CollisionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.skills = self.root / ".claude" / "skills"
        self.addons = self.root / ".ghost-alice" / "addons"
        self.skills.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_collision_when_dest_absent(self):
        targets = ai.load_addon_targets([FIXTURE_ROOT])  # noop
        self.assertEqual(ai.detect_collisions(
            targets, skills_dir=self.skills, addons_dir=self.addons,
            core_skill_names={"task-router"}), [])

    def test_collision_with_core_skill(self):
        (self.skills / "noop").mkdir()  # pretend a core skill 'noop' already lives here
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        cols = ai.detect_collisions(targets, skills_dir=self.skills, addons_dir=self.addons,
                                    core_skill_names={"noop"})
        self.assertEqual([(c["name"], c["owner"]) for c in cols], [("noop", "core")])

    def test_collision_with_other_addon(self):
        dest = self.skills / "noop"
        dest.mkdir()
        reg.write_record(_sidecar("other", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": "h", "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        targets = ai.load_addon_targets([FIXTURE_ROOT])  # noop addon = 'noop'
        cols = ai.detect_collisions(targets, skills_dir=self.skills, addons_dir=self.addons)
        self.assertEqual([(c["name"], c["owner"], c["owner_addon_id"]) for c in cols],
                         [("noop", "addon", "other")])

    def test_same_addon_reinstall_clean_is_not_a_collision(self):
        # Same addon owns dest AND the live target still hash-matches the sidecar
        # -> a clean update, not a collision.
        import hash_utils  # noqa: PLC0415
        dest = self.skills / "noop"
        dest.mkdir()
        (dest / "SKILL.md").write_text("orig", encoding="utf-8")
        live = hash_utils.hash_target(str(dest), "copy")
        reg.write_record(_sidecar("noop", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": live, "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        targets = ai.load_addon_targets([FIXTURE_ROOT])  # addon_id 'noop' owns dest already
        self.assertEqual(ai.detect_collisions(targets, skills_dir=self.skills, addons_dir=self.addons), [])

    def test_same_addon_drift_is_a_collision(self):
        # H1: same addon owns dest but the LIVE target drifted from the recorded
        # content_hash (user edited the installed copy) -> must be flagged so the
        # install aborts and the user's change is preserved, never clobbered.
        import hash_utils  # noqa: PLC0415
        dest = self.skills / "noop"
        dest.mkdir()
        (dest / "SKILL.md").write_text("orig", encoding="utf-8")
        recorded = hash_utils.hash_target(str(dest), "copy")
        reg.write_record(_sidecar("noop", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": recorded, "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        (dest / "SKILL.md").write_text("USER EDITED", encoding="utf-8")  # drift
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        cols = ai.detect_collisions(targets, skills_dir=self.skills, addons_dir=self.addons)
        self.assertEqual([(c["name"], c["owner"], c["owner_addon_id"]) for c in cols],
                         [("noop", "addon-drift", "noop")])

    def test_same_addon_skill_symlink_to_install_source_is_clean(self):
        # wedge fix: a skill symlink already pointing at THIS install's source is the
        # installer's own re-point, NOT user drift -- even if the recorded hash is stale.
        import hash_utils  # noqa: PLC0415
        src_b = self.root / "srcB" / "noop"
        src_b.mkdir(parents=True)
        (src_b / "SKILL.md").write_text("x", encoding="utf-8")
        dest = self.skills / "noop"
        dest.symlink_to(src_b)  # live symlink -> srcB (the source we are installing from)
        stale = hash_utils.hash_target(str(self.root / "srcA" / "noop"), "symlink")  # recorded = link->srcA
        reg.write_record(_sidecar("noop", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "symlink", "content_hash": stale, "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        target = ai.AddonTarget(name="noop", source=src_b, addon_id="noop", addon_version="1.0.0",
                                origin="addon:noop", platforms=("claude",), depends_on_core=(),
                                secrets=(), tags=())
        self.assertEqual(ai.detect_collisions([target], skills_dir=self.skills, addons_dir=self.addons), [])

    def test_same_addon_skill_symlink_to_foreign_dir_is_drift(self):
        # user repointed the symlink elsewhere -> still drift (must not be cleared).
        import hash_utils  # noqa: PLC0415
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("x", encoding="utf-8")
        src_b = self.root / "srcB" / "noop"
        src_b.mkdir(parents=True)
        (src_b / "SKILL.md").write_text("x", encoding="utf-8")
        dest = self.skills / "noop"
        dest.symlink_to(foreign)
        stale = hash_utils.hash_target(str(src_b), "symlink")
        reg.write_record(_sidecar("noop", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "symlink", "content_hash": stale, "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        target = ai.AddonTarget(name="noop", source=src_b, addon_id="noop", addon_version="1.0.0",
                                origin="addon:noop", platforms=("claude",), depends_on_core=(),
                                secrets=(), tags=())
        cols = ai.detect_collisions([target], skills_dir=self.skills, addons_dir=self.addons)
        self.assertEqual([c["owner"] for c in cols], ["addon-drift"])

    def test_safe_provision_rejects_lexical_dotdot(self):
        # _safe_provision must enforce lexical ".." containment itself (not rely on caller).
        # Escape to a NON-existent path outside base so the clobber-guard cannot mask it.
        base = self.root / "base"
        base.mkdir()
        outside = self.root / "new_evil.md"  # does not exist yet
        src = self.root / "src.md"
        src.write_text("ADDON", encoding="utf-8")
        with self.assertRaises(ai.AddonManifestError):
            ai._safe_provision(str(src), base, base / ".." / "new_evil.md", {})
        self.assertFalse(outside.exists())  # never created outside base

    def test_same_addon_missing_hash_fails_closed_as_drift(self):
        # ownership unprovable (empty recorded hash) -> fail closed as drift.
        dest = self.skills / "noop"
        dest.mkdir()
        (dest / "SKILL.md").write_text("x", encoding="utf-8")
        reg.write_record(_sidecar("noop", provided=[{
            "kind": "skill", "name": "noop", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": "", "marker": "", "metadata": {}}]),
            addons_dir=self.addons)
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        cols = ai.detect_collisions(targets, skills_dir=self.skills, addons_dir=self.addons)
        self.assertEqual([c["owner"] for c in cols], ["addon-drift"])

    def test_cli_detect_collisions_exit_2(self):
        (self.skills / "noop").mkdir()
        rc = ai.main(["detect-collisions", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                      "--skills-dir", str(self.skills), "--addons-dir", str(self.addons),
                      "--core-skill", "noop"])
        self.assertEqual(rc, 2)

    def test_cli_detect_collisions_clean_exit_0(self):
        rc = ai.main(["detect-collisions", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                      "--skills-dir", str(self.skills), "--addons-dir", str(self.addons)])
        self.assertEqual(rc, 0)


class DependentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self._tmp.name) / "addons"

    def tearDown(self):
        self._tmp.cleanup()

    def test_core_skill_dependents(self):
        reg.write_record(_sidecar("a", depends_on_core=["task-router", "x"]), addons_dir=self.addons)
        reg.write_record(_sidecar("b", depends_on_core=["x"]), addons_dir=self.addons)
        reg.write_record(_sidecar("c", depends_on_core=[]), addons_dir=self.addons)
        self.assertEqual(un.core_skill_dependents("task-router", addons_dir=self.addons), ["a"])
        self.assertEqual(un.core_skill_dependents("x", addons_dir=self.addons), ["a", "b"])
        self.assertEqual(un.core_skill_dependents("none", addons_dir=self.addons), [])

    def test_cli_dependents_exit_2_when_blocked(self):
        reg.write_record(_sidecar("a", depends_on_core=["task-router"]), addons_dir=self.addons)
        rc = un.main(["--dependents", "task-router", "--addons-dir", str(self.addons)])
        self.assertEqual(rc, 2)
        rc2 = un.main(["--dependents", "nobody", "--addons-dir", str(self.addons)])
        self.assertEqual(rc2, 0)

    def test_cli_dependents_fails_closed_on_skipped_sidecar(self):
        # M6: an UNREADABLE / future-major sidecar might itself depend_on_core the
        # core skill. scan_records skips it -> we must block (rc=2), not fail open.
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "corruptdep.json").write_text("{ not valid json", encoding="utf-8")
        rc = un.main(["--dependents", "nobody", "--addons-dir", str(self.addons)])
        self.assertEqual(rc, 2)  # would be 0 (fail-open) before the fix

    def test_cli_dependents_fails_closed_on_future_major_sidecar(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        future = _sidecar("future", depends_on_core=["task-router"])
        future["schema_version"] = "2.0"  # unsupported major -> scan_records skips it
        (self.addons / "future.json").write_text(json.dumps(future), encoding="utf-8")
        rc = un.main(["--dependents", "task-router", "--addons-dir", str(self.addons)])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
