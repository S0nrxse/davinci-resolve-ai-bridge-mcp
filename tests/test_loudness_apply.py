"""measure_loudness(apply=True) reports what Resolve really did (fakes only, needs the MCP SDK)."""

import json
import unittest
from unittest import mock

try:
    from bridge import server
except ImportError:  # The MCP SDK is not installed here.
    server = None

MEASURED = {"clips": [{"item_id": "a", "gain_db": 6.0, "peak_limited": False},
                      {"item_id": "b", "gain_db": 30.0, "peak_limited": False}],
            "program": {}}


@unittest.skipIf(server is None, "MCP SDK not installed")
class LoudnessApplyTests(unittest.TestCase):
    def run_apply(self, set_reply, read_reply):
        calls = []

        def fake_result(operation, params, timeout=None):
            calls.append((operation, params))
            return json.dumps(set_reply if operation == "set_clip_volume" else read_reply)

        with mock.patch.object(server.transport, "call", return_value=({"sources": [], "skipped": []}, "lua")), \
                mock.patch.object(server, "_ffmpeg", return_value="/fake/ffmpeg"), \
                mock.patch("bridge.audio_regions.measure_loudness", return_value=json.loads(json.dumps(MEASURED))), \
                mock.patch.object(server, "_result", side_effect=fake_result):
            return json.loads(server.measure_loudness(apply=True)), calls

    def test_verified_write_reports_success(self):
        out, calls = self.run_apply({"ok": True, "result": {"verified": True, "clips": []}}, None)
        self.assertTrue(out["ok"])
        self.assertIn("were set", out["result"]["note"])
        self.assertEqual([c[0] for c in calls], ["set_clip_volume"])
        self.assertEqual(out["result"]["capped"], [{"item_id": "b", "wanted_db": 30.0, "set_db": 24.0}])

    def test_transport_failure_reads_back_and_says_so(self):
        read = {"ok": True, "result": {"clips": [{"requested_for": "a", "item": "a1", "db": 6.0},
                                                 {"requested_for": "b", "item": "b1", "db": 0.0}]}}
        out, calls = self.run_apply({"ok": False, "error": "Lua RPC failed (Incomplete Lua reply line)"}, read)
        self.assertFalse(out["ok"])
        self.assertEqual([c[0] for c in calls], ["set_clip_volume", "read_clip_volumes"])
        self.assertEqual(calls[1][1], {"item_ids": ["a", "b"]})
        applied = out["result"]["applied"]
        self.assertFalse(applied["verified"])
        self.assertEqual(applied["at_target"], 1)
        self.assertIn("FAILED", out["result"]["note"])
        self.assertIn("1 of 2", out["result"]["note"])
        self.assertNotIn("were set to the gains", out["result"]["note"])

    def test_unreadable_outcome_is_unknown(self):
        out, _ = self.run_apply({"ok": False, "error": "timeout"}, {"ok": False, "error": "offline"})
        self.assertFalse(out["ok"])
        self.assertIn("outcome is unknown", out["result"]["note"])

    def test_unverified_write_is_not_success(self):
        out, _ = self.run_apply({"ok": True, "result": {"verified": False, "clips": [{"verified": False}]}},
                                {"ok": True, "result": {"clips": []}})
        self.assertFalse(out["ok"])
        self.assertIn("did not verify", out["result"]["note"])


if __name__ == "__main__":
    unittest.main()
