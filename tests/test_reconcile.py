import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location(
    "reconcile", Path(__file__).resolve().parents[1] / "bridge/reconcile.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class NudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in {"BRIDGE": self.root, "DELIV": self.root / "delivered",
                            "SEEN": self.root / "seen_task_ids.txt",
                            "LOG": self.root / "logs/reconcile.log",
                            "BUS": self.root / "bus/events.jsonl"}.items():
            p = patch.object(r, name, value)
            p.start()
            self.addCleanup(p.stop)
        (self.root / "outbox/new").mkdir(parents=True)
        self.post = Mock()
        p = patch.object(r.urllib.request, "urlopen", self.post)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(r, "resolve_webhook", return_value=("https://example.test", ""))
        p.start()
        self.addCleanup(p.stop)

    def task(self, task_id):
        path = self.root / "outbox/new" / (task_id + ".json")
        path.write_text(json.dumps({"task_id": task_id}))
        os.utime(path, (0, 0))
        return path

    def tick(self, now):
        with patch.object(r.time, "time", return_value=now):
            r.main()

    def test_permanent_no_return_is_bounded_even_after_restart_and_post_failure(self):
        self.task("lost")
        self.post.side_effect = OSError("offline")
        self.tick(1000)
        # Re-read disk every tick; touching the task must not reset the budget.
        os.utime(self.root / "outbox/new/lost.json", (1000, 1000))
        for now in range(1001, 1000 + 8 * 86400, 60):
            self.tick(now)
        self.assertEqual(self.post.call_count, 3)
        self.assertTrue((self.root / "outbox/failed/lost.json").exists())
        self.assertTrue((self.root / "outbox/failed/lost.json.reason").exists())
        events = [json.loads(x) for x in r.BUS.read_text().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["level"], "error")
        state = json.loads((self.root / "state/nudge_attempts.json").read_text())["lost"]
        self.assertEqual(state["first_stale_ts"], 1000)
        self.assertEqual(state["nudge_count"], 3)
        self.assertTrue(state["exhausted"])

    def test_backoff_and_final_grace_period(self):
        self.task("lost")
        for now, calls in [(1000, 1), (1059, 1), (1060, 2), (1179, 2),
                           (1180, 3), (1419, 3), (1420, 3)]:
            self.tick(now)
            self.assertEqual(self.post.call_count, calls)
        self.assertTrue((self.root / "outbox/failed/lost.json").exists())

    def test_ttl_expires_without_webhook(self):
        self.task("lost")
        with patch.object(r, "resolve_webhook", return_value=("", "")):
            self.tick(1000)
            self.tick(1900)
            self.tick(2000)
        self.assertEqual(self.post.call_count, 0)
        self.assertTrue((self.root / "outbox/failed/lost.json").exists())
        self.assertEqual(len(r.BUS.read_text().splitlines()), 1)

    def test_delivered_and_seen_archive_before_exhaustion(self):
        for task_id in ("delivered", "seen"):
            self.task(task_id)
        self.tick(1000)
        r.DELIV.mkdir()
        (r.DELIV / "delivered.md").write_text("ok")
        r.SEEN.write_text("seen\n")
        self.tick(10000)
        self.assertEqual(self.post.call_count, 1)  # existing batch POST preserved
        for task_id in ("delivered", "seen"):
            self.assertTrue((self.root / "outbox/done" / (task_id + ".json")).exists())
        self.assertEqual(json.loads((self.root / "state/nudge_attempts.json").read_text()), {})
        self.assertFalse(r.BUS.exists())

    def test_corrupt_state_cannot_reset_budget(self):
        self.task("lost")
        (self.root / "state").mkdir()
        (self.root / "state/nudge_attempts.json").write_text("broken")
        with self.assertRaises(json.JSONDecodeError):
            self.tick(1000)
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
