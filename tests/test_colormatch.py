"""Shot matching: CDL solver, LUT writer, ffmpeg measurement and the match_shot flow against fakes."""
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from bridge import colormatch as cm
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").is_file() else None)


def _pixels(count=4000, low=0.06, high=0.9, seed=7):
    rng = random.Random(seed)
    return [(rng.uniform(low, high), rng.uniform(low, high), rng.uniform(low, high)) for _ in range(count)]


class GradedClip(Clip):
    """A fake TimelineItem with a node graph and Resolve 21.2's GetCDL/SetCDL string format."""

    def __init__(self, *args, readback=True, sticky=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.cdl = {"NodeIndex": 1, "Slope": "1.000000 1.000000 1.000000", "Offset": "0.000000 0.000000 0.000000",
                    "Power": "1.000000 1.000000 1.000000", "Saturation": 1}
        self.set_calls = []
        self.sticky = sticky
        if not readback:
            self.GetCDL = None

    def GetNodeGraph(self):
        return type("Graph", (), {"GetNumNodes": lambda _self: 1})()

    def GetCDL(self, node_index):
        return dict(self.cdl) if int(node_index) == 1 else None

    def SetCDL(self, cdl_map):
        self.set_calls.append(dict(cdl_map))
        if self.sticky:
            self.cdl = {"NodeIndex": int(cdl_map["NodeIndex"]), "Slope": cdl_map["Slope"], "Offset": cdl_map["Offset"],
                        "Power": cdl_map["Power"], "Saturation": float(cdl_map["Saturation"])}
        return True


class SolverTests(unittest.TestCase):
    def test_identity_when_clips_are_equal(self):
        pixels = _pixels()
        result = cm.solve_cdl(pixels, pixels)
        self.assertTrue(cm.is_identity(result["cdl"], 1e-6), result["cdl"])
        self.assertEqual(result["clamped"], [])
        self.assertAlmostEqual(result["residual_after_predicted"]["percentile_rms"], 0.0, places=6)

    def test_recovers_known_slope_and_offset(self):
        reference = _pixels()
        gains, lifts = (0.8, 1.0, 1.15), (0.05, 0.0, -0.03)
        target = [tuple(p[c] * gains[c] + lifts[c] for c in range(3)) for p in reference]
        cdl = cm.solve_cdl(target, reference)["cdl"]
        for channel in range(3):
            self.assertAlmostEqual(cdl["slope"][channel], 1 / gains[channel], delta=0.01)
            self.assertAlmostEqual(cdl["offset"][channel], -lifts[channel] / gains[channel], delta=0.01)
            self.assertAlmostEqual(cdl["power"][channel], 1.0, delta=0.01)

    def test_recovers_known_power_from_the_median(self):
        reference = _pixels()
        target = [tuple(v ** 1.25 for v in p) for p in reference]  # reference = target ** 0.8
        result = cm.solve_cdl(target, reference)
        for channel in range(3):
            self.assertAlmostEqual(result["cdl"]["power"][channel], 0.8, delta=0.01)
            self.assertAlmostEqual(result["cdl"]["slope"][channel], 1.0, delta=0.02)
        self.assertLess(result["residual_after_predicted"]["percentile_rms"], 0.002)
        self.assertGreater(result["residual_before"]["percentile_rms"], 0.02)

    def test_saturation_is_solved_after_the_channel_curves(self):
        reference = _pixels()
        washed = []
        for p in reference:
            luma = sum(w * v for w, v in zip(cm.LUMA, p))
            washed.append(tuple(luma + 0.6 * (v - luma) for v in p))
        cdl = cm.solve_cdl(washed, reference)["cdl"]
        self.assertGreater(cdl["saturation"], 1.2)

    def test_strength_blends_toward_identity(self):
        cdl = {"slope": [2.0, 1.0, 0.5], "offset": [0.1, 0.0, -0.1], "power": [1.5, 1.0, 0.8], "saturation": 1.4}
        self.assertTrue(cm.is_identity(cm.blend_cdl(cdl, 0.0), 1e-12))
        half = cm.blend_cdl(cdl, 0.5)
        self.assertEqual(half["slope"], [1.5, 1.0, 0.75])
        self.assertEqual(half["offset"], [0.05, 0.0, -0.05])
        self.assertAlmostEqual(half["saturation"], 1.2)
        self.assertEqual(cm.blend_cdl(cdl, 1.0)["power"], [1.5, 1.0, 0.8])
        reference = _pixels()
        target = [tuple(v * 0.7 for v in p) for p in reference]
        none = cm.solve_cdl(target, reference, strength=0.0)
        self.assertTrue(cm.is_identity(none["cdl"], 1e-12))
        self.assertEqual(none["residual_after_predicted"]["percentile_rms"], none["residual_before"]["percentile_rms"])
        partial = cm.solve_cdl(target, reference, strength=0.5)
        self.assertLess(partial["residual_after_predicted"]["percentile_rms"], none["residual_before"]["percentile_rms"])

    def test_clamps_to_sane_ranges(self):
        cdl, clamped = cm.clamp_cdl({"slope": [9.0, 1.0, 0.1], "offset": [0.5, 0.0, -0.9],
                                     "power": [3.0, 1.0, 0.1], "saturation": 4.0})
        self.assertEqual(cdl["slope"], [2.5, 1.0, 0.4])
        self.assertEqual(cdl["offset"], [0.3, 0.0, -0.3])
        self.assertEqual(cdl["power"], [2.0, 1.0, 0.5])
        self.assertEqual(cdl["saturation"], 1.6)
        self.assertEqual(clamped, ["slope.r", "slope.b", "offset.r", "offset.b", "power.r", "power.b", "saturation"])
        reference = _pixels(low=0.1, high=0.95)
        dark = [tuple(v * 0.15 for v in p) for p in reference]  # Needs a slope of ~6.7: far past a shot match.
        result = cm.solve_cdl(dark, reference)
        self.assertIn("slope.g", result["clamped"])
        self.assertLessEqual(max(result["cdl"]["slope"]), cm.LIMITS["slope"][1])

    def test_apply_cdl_saturation_zero_is_rec709_luma(self):
        out = cm.apply_cdl((0.8, 0.2, 0.4), dict(cm.IDENTITY, saturation=0.0))
        luma = 0.2126 * 0.8 + 0.7152 * 0.2 + 0.0722 * 0.4
        for value in out:
            self.assertAlmostEqual(value, luma)

    def test_clipped_and_black_pixels_are_excluded(self):
        pixels = _pixels(1000) + [(0.0, 0.0, 0.0)] * 300 + [(1.0, 1.0, 1.0)] * 200
        kept, excluded = cm.usable_pixels(pixels)
        self.assertEqual(excluded, 500)
        self.assertEqual(len(kept), 1000)
        all_black = [(0.0, 0.0, 0.0)] * 100
        self.assertEqual(cm.usable_pixels(all_black), (all_black, 0))


class CubeTests(unittest.TestCase):
    def test_cube_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.cube"
            cm.write_cube(cm.IDENTITY, path, size=5)
            lines = path.read_text(encoding="ascii").splitlines()
        self.assertTrue(lines[0].startswith("TITLE "))
        self.assertEqual(lines[1:4], ["LUT_3D_SIZE 5", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"])
        rows = [tuple(float(v) for v in line.split()) for line in lines[4:]]
        self.assertEqual(len(rows), 125)
        self.assertEqual(rows[0], (0.0, 0.0, 0.0))
        self.assertEqual(rows[1], (0.25, 0.0, 0.0))   # Red changes fastest.
        self.assertEqual(rows[5], (0.0, 0.25, 0.0))
        self.assertEqual(rows[25], (0.0, 0.0, 0.25))
        self.assertEqual(rows[-1], (1.0, 1.0, 1.0))

    def test_cube_applies_the_cdl(self):
        cdl = {"slope": [0.5, 1.0, 1.0], "offset": [0.1, 0.0, 0.0], "power": [1.0, 1.0, 1.0], "saturation": 1.0}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.cube"
            cm.write_cube(cdl, path, size=3)
            rows = path.read_text(encoding="ascii").splitlines()[4:]
        self.assertEqual(rows[2], "0.600000 0.000000 0.000000")  # r=1 -> 1*0.5+0.1
        with self.assertRaises(cm.MatchError):
            cm.write_cube(cdl, "unused.cube", size=1)


@unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
class FfmpegMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        cls.reference = root / "reference.mkv"
        cls.tinted = root / "tinted.mkv"
        lossless = ["-pix_fmt", "bgr0", "-c:v", "ffv1"]
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=2"]
                       + lossless + [str(cls.reference)], check=True, timeout=60)
        # red: 0.05 + 0.85 * x, blue: 0.80 * x, green untouched.
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(cls.reference), "-vf",
                        "colorlevels=romin=0.05:romax=0.90:bomin=0.0:bomax=0.80"] + lossless + [str(cls.tinted)],
                       check=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def source(self, path):
        return {"item_id": path.stem, "name": path.name, "file_path": str(path), "source_start_sec": 0.0,
                "duration_sec": 2.0, "track": "V1"}

    def test_samples_small_rgb_frames_across_the_range(self):
        sample = cm.sample_source(FFMPEG, self.source(self.reference), frames=8)
        self.assertEqual(len(sample["sampled_source_sec"]), 8)
        self.assertEqual(len(sample["pixels"]), 8 * 64 * 36)
        self.assertGreater(sample["sampled_source_sec"][0], 0.0)
        self.assertLess(sample["sampled_source_sec"][-1], 2.0)
        self.assertTrue(all(0.0 <= v <= 1.0 for p in sample["pixels"][:500] for v in p))

    def test_solver_recovers_colorlevels_tint(self):
        reference = cm.sample_source(FFMPEG, self.source(self.reference))
        tinted = cm.sample_source(FFMPEG, self.source(self.tinted))
        result = cm.solve_cdl(tinted["pixels"], reference["pixels"])
        cdl = result["cdl"]
        self.assertAlmostEqual(cdl["slope"][0], 1 / 0.85, delta=0.03)
        self.assertAlmostEqual(cdl["offset"][0], -0.05 / 0.85, delta=0.02)
        self.assertAlmostEqual(cdl["slope"][1], 1.0, delta=0.02)
        self.assertAlmostEqual(cdl["slope"][2], 1 / 0.80, delta=0.03)
        self.assertAlmostEqual(cdl["offset"][2], 0.0, delta=0.02)
        self.assertLess(result["residual_after_predicted"]["percentile_rms"], 0.01)
        self.assertGreater(result["residual_before"]["percentile_rms"], 0.04)

    def test_equal_sources_give_identity(self):
        one = cm.sample_source(FFMPEG, self.source(self.reference))
        result = cm.solve_cdl(one["pixels"], one["pixels"])
        self.assertTrue(cm.is_identity(result["cdl"], 1e-6))

    def test_preview_is_three_panels_side_by_side(self):
        reference = cm.sample_source(FFMPEG, self.source(self.reference), frames=3)
        tinted = cm.sample_source(FFMPEG, self.source(self.tinted), frames=3)
        cdl = cm.solve_cdl(tinted["pixels"], reference["pixels"])["cdl"]
        with tempfile.TemporaryDirectory() as folder:
            cube = cm.write_cube(cdl, Path(folder) / "m.cube")
            path, error = cm.render_preview(FFMPEG, dict(reference, file_path=str(self.reference)),
                                            dict(tinted, file_path=str(self.tinted)), cube, Path(folder) / "p.png",
                                            height=90)
            self.assertIsNone(error)
            width, height = ResolveOperations._image_dimensions(path)
        self.assertEqual(height, 90)
        self.assertEqual(width, 3 * 160 + 16)


@unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
class MatchShotFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FfmpegMeasurementTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def build(self, **target_kwargs):
        self.reference_clip = GradedClip(0, 48, Media(self.reference), name="reference.mkv")
        self.target_clip = GradedClip(48, 96, Media(self.tinted), name="tinted.mkv", **target_kwargs)
        self.ops = ResolveOperations(lambda: Resolve(Project([self.reference_clip, self.target_clip])))
        self.calls = []

        def bridge(op, params):
            self.calls.append(op)
            return self.ops.dispatch(op, params)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        return bridge

    def run_match(self, bridge, **kwargs):
        return cm.match_shot(bridge, FFMPEG, self.reference_clip.uid, self.target_clip.uid,
                             frames=4, work_dir=self.work.name, **kwargs)

    def test_read_only_by_default(self):
        bridge = self.build()
        report, preview = self.run_match(bridge)
        self.assertFalse(report["applied"])
        self.assertEqual(self.target_clip.set_calls, [])
        self.assertNotIn("apply_shot_match", self.calls)
        self.assertEqual(self.calls, ["shot_match_sources"])  # One read-only timeline scan.
        self.assertEqual(report["bridge_reads"], "shot_match_sources")
        self.assertTrue(Path(preview).is_file())
        self.assertIn("NOT Resolve's render", report["preview"]["note"])
        self.assertAlmostEqual(report["proposed_cdl"]["slope"][0], 1 / 0.85, delta=0.03)
        self.assertEqual(report["set_clip_grade_args"]["item_id"], self.target_clip.uid)
        self.assertTrue(Path(report["lut_path"]).is_file())
        self.assertIn("ask before", report["next_step"].lower())

    def test_apply_sets_and_verifies_readback(self):
        bridge = self.build()
        report, _ = self.run_match(bridge, apply=True, strength=0.5)
        self.assertTrue(report["applied"])
        self.assertTrue(report["verified"])
        self.assertEqual(len(self.target_clip.set_calls), 1)
        sent = self.target_clip.set_calls[0]
        self.assertEqual(sent["NodeIndex"], "1")
        slope = float(sent["Slope"].split()[0])
        self.assertAlmostEqual(slope, 1 + (1 / 0.85 - 1) * 0.5, delta=0.02)
        self.assertTrue(cm.is_identity(report["apply_result"]["previous"]))
        self.assertEqual(self.reference_clip.set_calls, [])

    def test_apply_reports_a_readback_mismatch(self):
        bridge = self.build(sticky=False)
        report, _ = self.run_match(bridge, apply=True)
        self.assertFalse(report["applied"])
        self.assertIs(report["verified"], False)
        self.assertIn("NOT applied", report["apply_result"]["note"])

    def test_apply_without_getcdl_is_unverified(self):
        bridge = self.build(readback=False)
        report, _ = self.run_match(bridge, apply=True)
        self.assertIsNone(report["verified"])
        self.assertIn("unverified", report["apply_result"]["note"])
        self.assertTrue(any("GetCDL" in w for w in report["warnings"]))

    def test_warns_when_reference_is_graded(self):
        bridge = self.build()
        self.reference_clip.cdl["Slope"] = "1.200000 1.000000 1.000000"
        report, _ = self.run_match(bridge)
        self.assertTrue(any("UNGRADED" in w for w in report["warnings"]), report["warnings"])
        self.assertEqual(report["reference_grade"]["node_1_cdl"]["slope"], [1.2, 1.0, 1.0])

    def test_refuses_missing_target(self):
        bridge = self.build()
        with self.assertRaises(cm.MatchError) as caught:
            cm.match_shot(bridge, FFMPEG, self.reference_clip.uid, "no-such-clip", work_dir=self.work.name)
        self.assertIn("Timeline item not found: no-such-clip", str(caught.exception))
        self.assertEqual(self.target_clip.set_calls, [])
        with self.assertRaises(cm.MatchError):
            cm.match_shot(bridge, FFMPEG, self.reference_clip.uid, "playhead", work_dir=self.work.name)
        with self.assertRaises(cm.MatchError):
            cm.match_shot(bridge, FFMPEG, self.reference_clip.uid, self.reference_clip.uid, work_dir=self.work.name)
        with self.assertRaises(cm.MatchError):
            cm.match_shot(bridge, FFMPEG, self.reference_clip.uid, self.target_clip.uid, strength=1.5)

    def test_falls_back_to_read_only_ops_when_the_scan_op_is_refused(self):
        bridge = self.build()

        def strict(op, params):  # Like a queue that has not observed this client's context yet.
            if op == "shot_match_sources":
                raise RuntimeError("Inspect resolve_status or timeline_overview in this client before editing.")
            return bridge(op, params)
        report, _ = self.run_match(strict)
        self.assertEqual(report["bridge_reads"], "media_sources+get_clip_grade")
        self.assertEqual(self.calls, ["media_sources", "media_sources", "get_clip_grade", "get_clip_grade"])
        self.assertAlmostEqual(report["proposed_cdl"]["slope"][2], 1 / 0.80, delta=0.03)
        with self.assertRaisesRegex(cm.MatchError, "target clip 'gone' was not found"):
            cm.match_shot(strict, FFMPEG, self.reference_clip.uid, "gone", work_dir=self.work.name)

    def test_power_hitting_its_bound_is_reported_as_clamped(self):
        reference = _pixels(low=0.1, high=0.9)
        target = [tuple(v ** 3.0 for v in p) for p in reference]  # Needs power 1/3, below the 0.5 floor.
        result = cm.solve_cdl(target, reference)
        self.assertIn("power.r", result["clamped"])
        self.assertAlmostEqual(result["cdl"]["power"][0], 0.5, delta=1e-3)

    def test_apply_op_refuses_changed_source_and_bad_values(self):
        self.build()
        base = {"target_item_id": self.target_clip.uid, "slope": [1, 1, 1], "offset": [0, 0, 0],
                "power": [1, 1, 1], "saturation": 1.0}
        with self.assertRaisesRegex(OperationError, "changed since it was measured"):
            self.ops.dispatch("apply_shot_match", dict(base, expected_file_path="/elsewhere.mov"))
        with self.assertRaisesRegex(OperationError, "slope must be"):
            self.ops.dispatch("apply_shot_match", dict(base, slope=[9, 1, 1]))
        with self.assertRaisesRegex(OperationError, "unique id"):
            self.ops.dispatch("apply_shot_match", dict(base, target_item_id="playhead"))
        self.assertEqual(self.target_clip.set_calls, [])


if __name__ == "__main__":
    unittest.main()
