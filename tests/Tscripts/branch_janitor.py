import unittest

import scripts.branch_janitor as bj


class classify(unittest.TestCase):
    def _classify(self, branches, merged=(), force=()):
        merged_set = set(merged)
        return bj.classify(
            branches,
            is_merged_fn=lambda b: b in merged_set,
            force_set=set(force),
        )

    def test_merged_branch_is_delete_candidate(self):
        plan = self._classify(["claude/a"], merged=["claude/a"])
        self.assertEqual(plan[bj.MERGED], ["claude/a"])
        self.assertEqual(plan[bj.LEFT_ALONE], [])

    def test_unmerged_branch_left_alone(self):
        plan = self._classify(["claude/a"])
        self.assertEqual(plan[bj.LEFT_ALONE], ["claude/a"])
        self.assertEqual(plan[bj.MERGED], [])
        self.assertEqual(plan[bj.FORCE], [])

    def test_force_overrides_unmerged(self):
        plan = self._classify(["claude/a"], force=["claude/a"])
        self.assertEqual(plan[bj.FORCE], ["claude/a"])
        self.assertEqual(plan[bj.LEFT_ALONE], [])

    def test_force_takes_precedence_over_merged(self):
        plan = self._classify(["claude/a"], merged=["claude/a"], force=["claude/a"])
        self.assertEqual(plan[bj.FORCE], ["claude/a"])
        self.assertEqual(plan[bj.MERGED], [])

    def test_mixed_set_partitions_cleanly(self):
        plan = self._classify(
            ["claude/merged", "claude/unmerged", "worker/forced"],
            merged=["claude/merged"],
            force=["worker/forced"],
        )
        self.assertEqual(plan[bj.MERGED], ["claude/merged"])
        self.assertEqual(plan[bj.LEFT_ALONE], ["claude/unmerged"])
        self.assertEqual(plan[bj.FORCE], ["worker/forced"])


class delete_branch(unittest.TestCase):
    def test_dry_run_is_noop_success(self):
        called = {"ran": False}

        def _boom(*a, **k):
            called["ran"] = True
            raise AssertionError("must not shell out in dry-run")

        real_run = bj.subprocess.run
        bj.subprocess.run = _boom
        try:
            self.assertTrue(bj.delete_branch("claude/whatever", dry_run=True))
        finally:
            bj.subprocess.run = real_run
        self.assertFalse(called["ran"])


class main(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "refresh_remotes": bj.refresh_remotes,
            "list_remote_branches": bj.list_remote_branches,
            "list_all_remote_branches": bj.list_all_remote_branches,
            "is_merged": bj.is_merged,
        }
        bj.refresh_remotes = lambda: None

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(bj, name, fn)

    def test_dry_run_json_reports_plan(self):
        bj.list_remote_branches = lambda prefixes: ["claude/gone", "claude/keep"]
        bj.is_merged = lambda b, base="origin/main": b == "claude/gone"

        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bj.main(["--dry-run", "--json"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('"deleted"', out)
        self.assertIn("claude/gone", out)
        self.assertIn("claude/keep", out)

    def test_force_delete_bypasses_prefix_filter(self):
        # A branch outside claude/*|worker/* (e.g. reviewer/*) named via
        # --force-delete must still be picked up, not silently dropped.
        bj.list_remote_branches = lambda prefixes: ["claude/keep"]
        bj.list_all_remote_branches = lambda: ["claude/keep", "reviewer/stray-branch"]
        bj.is_merged = lambda b, base="origin/main": False

        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bj.main(["--dry-run", "--json", "--force-delete", "reviewer/stray-branch"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("reviewer/stray-branch", out)
        self.assertIn('"deleted"', out)

    def test_force_delete_ignores_nonexistent_branch_name(self):
        # A --force-delete name that doesn't actually exist as a remote
        # branch must not be fabricated into the plan.
        bj.list_remote_branches = lambda prefixes: ["claude/keep"]
        bj.list_all_remote_branches = lambda: ["claude/keep"]
        bj.is_merged = lambda b, base="origin/main": False

        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bj.main(["--dry-run", "--json", "--force-delete", "reviewer/does-not-exist"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn("reviewer/does-not-exist", out)


if __name__ == "__main__":
    unittest.main()
