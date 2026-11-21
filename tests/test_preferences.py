"""Per-project editor preferences and the Fusion frame-format report (fakes only)."""

import tempfile
import unittest
from pathlib import Path

from bridge import preferences
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve, Timeline
from fusion_fakes import Comp


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.project = Project([Clip(0, 100)])
        self.op = ResolveOperations(lambda: Resolve(self.project), runtime_home=self.home.name)

    def test_set_get_validate_and_delete(self):
        out = self.op.dispatch("set_preferences", {"updates": {"agent_bin": "Scratch", "tighten_lead_frames": 3}})
        self.assertEqual(out["preferences"], {"agent_bin": "Scratch", "tighten_lead_frames": 3})
        self.assertEqual(self.op.dispatch("get_preferences", {})["preferences"]["agent_bin"], "Scratch")
        with self.assertRaisesRegex(OperationError, "Unknown preference"):
            self.op.dispatch("set_preferences", {"updates": {"agent_bn": "x"}})
        with self.assertRaisesRegex(OperationError, "must be a whole number"):
            self.op.dispatch("set_preferences", {"updates": {"tighten_lead_frames": "3"}})
        self.op.dispatch("set_preferences", {"updates": {"agent_bin": None}})
        self.assertNotIn("agent_bin", preferences.get(self.home.name, "Test project"))
        self.assertTrue((Path(self.home.name) / "preferences.json").is_file())

    def test_agent_bin_preference_is_the_default_bin(self):
        self.op.dispatch("set_preferences", {"updates": {"agent_bin": "Scratch"}})
        filed = []
        self.op._file_into_bin = lambda timeline, name=None: filed.append(name) or name
        self.op.dispatch("preview_timeline", {})
        self.op.dispatch("preview_timeline", {"bin": "Other"})
        self.assertEqual(filed, ["Scratch", "Other"])

    def test_no_home_means_defaults(self):
        op = ResolveOperations(lambda: Resolve(self.project))
        self.assertEqual(op._preference("agent_bin", "Agent Work"), "Agent Work")
        with self.assertRaisesRegex(OperationError, "no bridge home"):
            op.dispatch("set_preferences", {"updates": {"notes": "x"}})


class FrameFormatTests(unittest.TestCase):
    def test_inspect_reports_comp_size_and_source(self):
        clip = Clip(0, 100)
        clip.media.props.update({"File Path": "/media/face.mov", "Resolution": "1280x720"})
        clip.fusion = Comp(-12)
        clip.fusion.GetPrefs = lambda key: {"Comp.FrameFormat.Width": 1280, "Comp.FrameFormat.Height": 720}.get(key)
        op = ResolveOperations(lambda: Resolve(Project([clip])))
        out = op._op_inspect_fusion({"item_id": clip.uid})
        fmt = out["frame_format"]
        self.assertEqual((fmt["width"], fmt["height"], fmt["render_start"]), (1280.0, 720.0, -12.0))
        self.assertEqual(fmt["media_in_source"], "/media/face.mov")


if __name__ == "__main__":
    unittest.main()
