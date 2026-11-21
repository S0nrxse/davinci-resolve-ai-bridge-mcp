"""Offline tests for tools/probes/live_probes.py: an injected fake call, never a live bridge."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from fractions import Fraction
from unittest.mock import patch
import xml.etree.ElementTree as ET

from bridge.operations import OperationError, RESOLVE_API_DESTRUCTIVE
from tools.probes import live_probes as probes


class BridgeTimeout(Exception):
    """Same class name as bridge.client.BridgeTimeout, without importing a transport."""


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _run(name, simulated, project=None, **options):
    with tempfile.TemporaryDirectory() as folder:
        return probes.run_probe(name, project or simulated.project, simulated, "/fake/clip.mov", None, Path(folder), **options)


class RefusalTests(unittest.TestCase):
    def test_refuses_a_project_name_that_does_not_match(self):
        simulated = probes.SimulatedResolve(project="Client scratch")
        report = _run("audio_volume", simulated, project="Other scratch")
        self.assertIn("not 'Other scratch'", report["refused"])
        self.assertEqual([call["op"] for call in simulated.sent], ["status"])

    def test_refuses_a_matching_name_without_scratch_or_probe(self):
        simulated = probes.SimulatedResolve(project="Wedding edit")
        report = _run("still_duration", simulated)
        self.assertIn("scratch' or 'probe'", report["refused"])
        self.assertEqual([call["op"] for call in simulated.sent], ["status"])

    def test_refuses_while_rendering(self):
        simulated = probes.SimulatedResolve(rendering=True)
        report = _run("set_position", simulated)
        self.assertIn("render", report["refused"])
        self.assertEqual([call["op"] for call in simulated.sent], ["status", "resolve_api"])

    def test_cli_rejects_a_scratch_name_without_scratch_or_probe_before_any_call(self):
        with patch.object(probes, "bridge_call", side_effect=AssertionError("contacted the bridge")) as live, \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            probes.main(["--run", "still_duration", "--scratch-project", "My edit"], out=io.StringIO())
        live.assert_not_called()


class OfflineModeTests(unittest.TestCase):
    def test_list_and_plan_never_call_the_bridge(self):
        with patch.object(probes, "bridge_call", side_effect=AssertionError("contacted the bridge")) as live:
            listing = io.StringIO()
            self.assertEqual(probes.main(["--list"], out=listing), 0)
            for name in probes.PROBES:
                self.assertIn(name, listing.getvalue())
                plan = io.StringIO()
                self.assertEqual(probes.main(["--plan", name], out=plan), 0)
                data = json.loads(plan.getvalue())
                self.assertTrue(data["offline"])
                self.assertEqual(data["would_send"][0]["op"], "status")
                self.assertEqual(data["simulated_outcome"], "completed")
        live.assert_not_called()


class ProbeRunTests(unittest.TestCase):
    def test_every_probe_checks_status_first_never_deletes_and_restores_the_timeline(self):
        for name in probes.PROBES:
            with self.subTest(name):
                simulated = probes.SimulatedResolve()
                report = _run(name, simulated)
                ops = [call["op"] for call in simulated.sent]
                self.assertEqual(ops[0], "status")
                self.assertFalse(report.get("stopped") or report.get("error") or report.get("halted"), report)
                for value in _walk([call["params"] for call in simulated.sent]):
                    if isinstance(value, dict):
                        self.assertNotIn("allow_destructive", value)
                        self.assertNotIn(value.get("call"), RESOLVE_API_DESTRUCTIVE)
                self.assertNotIn("delete_clips", ops)
                self.assertNotIn("cleanup_ai_timelines", ops)
                self.assertEqual(simulated.sent[-2], {"op": "open_timeline", "params": {"timeline_id": "sim-tl-1"}})
                self.assertEqual(ops[-1], "status")
                self.assertTrue(report["restored"]["timeline"]["restored"])
                self.assertTrue(report["restored"]["media_pool_folder"]["restored"])
                for created in report["created_timelines"]:
                    self.assertRegex(created, r"^AI probe · .+ — [0-9a-f]{8}$")
                # Only the original timeline is ever re-opened.
                opened = [call["params"] for call in simulated.sent if call["op"] == "open_timeline"]
                self.assertEqual(opened, [{"timeline_id": "sim-tl-1"}])

    def test_missing_methods_are_reported_unavailable(self):
        report = _run("item_offsets", probes.SimulatedResolve(absent={"SetLeftOffset", "SetRightOffset"}))
        verdicts = [entry["verdict"] for entry in report["questions"]]
        self.assertEqual(verdicts, ["unavailable", "unavailable"])

    def test_set_position_stops_after_unavailable(self):
        simulated = probes.SimulatedResolve(absent={"SetPosition"})
        report = _run("set_position", simulated)
        self.assertEqual([entry["verdict"] for entry in report["questions"]], ["unavailable"])

    def test_uncertain_outcome_halts_without_restoring(self):
        simulated = probes.SimulatedResolve()

        def call(operation, params, timeout):
            if operation == "create_timeline":
                raise BridgeTimeout("Client timed out waiting for create_timeline")
            return simulated(operation, params, timeout)
        with tempfile.TemporaryDirectory() as folder:
            report = probes.run_probe("audio_volume", simulated.project, call, "/fake/clip.mov", None, Path(folder))
        self.assertIn("Uncertain outcome", report["halted"])
        self.assertNotIn("open_timeline", [entry["op"] for entry in simulated.sent])
        self.assertIn("Not attempted", report["restored"]["timeline"])

    def test_guard_mismatch_halts(self):
        class NoSwitch(probes.SimulatedResolve):
            def _op_create_timeline(self, params):
                self.timelines.append({"id": "sim-tl-9", "name": params["name"], "tracks": {}})
                return {"created": params["name"], "active": True}  # Claims active but leaves the user's open.
        simulated = NoSwitch()
        report = _run("audio_volume", simulated)
        self.assertIn("Guard", report["halted"])
        self.assertNotIn("open_timeline", [entry["op"] for entry in simulated.sent])

    def test_ordinary_operation_errors_do_not_halt(self):
        simulated = probes.SimulatedResolve()

        def call(operation, params, timeout):
            if operation == "resolve_api" and any(step["call"] == "AppendToTimeline" for step in params["steps"]):
                raise OperationError("failed_step 4 (media_pool.AppendToTimeline): boom. Nothing ran.")
            return simulated(operation, params, timeout)
        with tempfile.TemporaryDirectory() as folder:
            report = probes.run_probe("audio_volume", simulated.project, call, "/fake/clip.mov", None, Path(folder))
        self.assertIsNone(report["halted"])
        self.assertIn("placed no audio", report["stopped"])
        self.assertEqual(simulated.sent[-2]["op"], "open_timeline")


SAMPLE_2997 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" frameDuration="1001/30000s"/>
    <asset id="r2" name="a.mov" start="0s" duration="10s" hasVideo="1" hasAudio="1" format="r1"/>
    <effect id="r7" name="Other" uid="x"/>
  </resources>
  <library><event name="e"><project name="p">
    <sequence format="r1" duration="4004/1000s" tcStart="0s">
      <spine>
        <asset-clip name="a" ref="r2" offset="0s" duration="2002/1000s" start="0s"><adjust-transform scale="1 1"/></asset-clip>
        <asset-clip name="b" ref="r2" offset="2002/1000s" duration="2002/1000s" start="1001/1000s"/>
      </spine>
    </sequence>
  </project></event></library>
</fcpxml>
"""


def seconds(element, attribute):
    return probes._time(element.get(attribute))


class FcpxmlEditTests(unittest.TestCase):
    def parse(self, text):
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'))
        return ET.fromstring(text[text.index("<fcpxml"):])

    def test_dissolve_and_fades(self):
        text, changes = probes.edit_fcpxml(SAMPLE_2997, 29.97, dissolve_frames=30, fade_frames=15)
        root = self.parse(text)
        spine = root.find(".//spine")
        self.assertEqual([child.tag for child in spine], ["asset-clip", "transition", "asset-clip"])
        transition = spine[1]
        # 30 frames at 1001/30000 s = 1001/1000 s, centred on the cut at 2002/1000 s.
        self.assertEqual(seconds(transition, "duration"), Fraction(1001, 1000))
        self.assertEqual(seconds(transition, "offset"), Fraction(2002, 1000) - Fraction(1001, 2000))
        effect = root.find("./resources/effect[@name='Cross Dissolve']")
        self.assertEqual(effect.get("id"), "r8")
        self.assertEqual(transition.find("filter-video").get("ref"), "r8")
        second = spine[2]
        self.assertEqual(second.get("offset"), "2002/1000s")  # Untouched.
        fades = second.find("adjust-volume/param[@name='amount']")
        self.assertEqual(seconds(fades.find("fadeIn"), "duration"), Fraction(1001, 2000))
        self.assertEqual(seconds(fades.find("fadeOut"), "duration"), Fraction(1001, 2000))
        self.assertIsNone(spine[0].find("adjust-volume"))
        self.assertEqual(len(changes), 2)

    def test_move_inserts_a_gap_and_shifts_clip_two(self):
        text, changes = probes.edit_fcpxml(SAMPLE_2997, 29.97, move_frames=30, fade_frames=15)
        root = self.parse(text)
        spine = root.find(".//spine")
        self.assertEqual([child.tag for child in spine], ["asset-clip", "gap", "asset-clip"])
        self.assertEqual(seconds(spine[1], "offset"), Fraction(2002, 1000))
        self.assertEqual(seconds(spine[1], "duration"), Fraction(1001, 1000))
        self.assertEqual(seconds(spine[2], "offset"), Fraction(3003, 1000))
        self.assertEqual(spine[0].get("offset"), "0s")
        self.assertEqual(seconds(root.find(".//sequence"), "duration"), Fraction(5005, 1000))
        self.assertIsNotNone(spine[2].find("adjust-volume/param/fadeIn"))
        self.assertIn("moved later by 30 frames", changes[-1])

    def test_fades_follow_existing_adjustments(self):
        text, _ = probes.edit_fcpxml(SAMPLE_2997.replace('start="1001/1000s"/>', 'start="1001/1000s"><adjust-transform/><marker start="0s"/></asset-clip>'),
                                     29.97, fade_frames=10)
        second = self.parse(text).find(".//spine")[1]
        self.assertEqual([child.tag for child in second], ["adjust-transform", "adjust-volume", "marker"])

    def test_rejects_impossible_edits(self):
        with self.assertRaises(ValueError):
            probes.edit_fcpxml(SAMPLE_2997, 29.97, dissolve_frames=30, move_frames=30)
        with self.assertRaises(ValueError):
            probes.edit_fcpxml(SAMPLE_2997, 29.97, dissolve_frames=31)
        with self.assertRaises(ValueError):
            probes.edit_fcpxml("<fcpxml><library/></fcpxml>", 24, fade_frames=10)

    def test_generated_png_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            data = probes.write_png(Path(folder) / "still.png", 8, 4).read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data[16:24], (8).to_bytes(4, "big") + (4).to_bytes(4, "big"))


if __name__ == "__main__":
    unittest.main()
