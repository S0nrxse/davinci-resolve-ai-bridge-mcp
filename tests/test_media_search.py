"""list_media query/kind filters and speech_regions' compact word output (fakes only)."""

import json
import unittest
from unittest import mock

from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve

try:
    from bridge import server
except ImportError:  # The MCP SDK is not installed here.
    server = None


class Item:
    def __init__(self, name, path="", kind="Video + Audio"):
        self.name, self.uid = name, "id-" + name.replace(" ", "_") + str(id(self))[-4:]
        self.props = {"File Path": path, "Type": kind, "Frames": "10"}

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetClipProperty(self, key=None):
        return self.props.get(key) if key else dict(self.props)


class Folder:
    def __init__(self, name, clips=(), subs=()):
        self.name, self.clips, self.subs = name, list(clips), list(subs)

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class ListMediaTests(unittest.TestCase):
    def setUp(self):
        project = Project([Clip(0, 100)])
        self.root = Folder("Master", [Item("AI probe · roundtrip", kind="Timeline"),
                                      Item("talk.mp4", "/m/talk.mp4"),
                                      Item("my talk extra.mov", "/m/other.mov")],
                           [Folder("Bin", [Item("talk.mp4", "/m/talk.mp4"), Item("logo.png", "/m/logo.png", "Still")])])
        project.pool.GetRootFolder = lambda: self.root
        self.op = ResolveOperations(lambda: Resolve(project))

    def test_no_filter_lists_everything(self):
        self.assertEqual(self.op.dispatch("list_media", {})["count"], 5)

    def test_query_ranks_exact_names_first_and_groups_duplicates(self):
        out = self.op.dispatch("list_media", {"query": "TALK.mp4"})
        self.assertEqual([i["name"] for i in out["items"]], ["talk.mp4", "talk.mp4"])
        self.assertEqual(out["matched"], 2)
        self.assertEqual(len(out["same_file_entries"]["/m/talk.mp4"]), 2)
        out = self.op.dispatch("list_media", {"query": "talk"})
        self.assertEqual([i["name"] for i in out["items"]], ["talk.mp4", "talk.mp4", "my talk extra.mov"])

    def test_kind_filters_timelines(self):
        clips = self.op.dispatch("list_media", {"kind": "clips"})["items"]
        self.assertNotIn("Timeline", [i["type"] for i in clips])
        timelines = self.op.dispatch("list_media", {"kind": "timelines", "query": "probe"})["items"]
        self.assertEqual([i["name"] for i in timelines], ["AI probe · roundtrip"])
        with self.assertRaisesRegex(OperationError, "kind must be"):
            self.op.dispatch("list_media", {"kind": "bins"})


@unittest.skipIf(server is None, "MCP SDK not installed")
class SpeechRegionWordsTests(unittest.TestCase):
    CLIP = {"item_id": "a", "regions": [{"text": "hi there"}],
            "words": [{"word": "hi", "start_frame": 1, "end_frame": 4, "trusted": True},
                      {"word": "there", "start_frame": 5, "end_frame": 9, "trusted": True}]}

    def run_tool(self, **kwargs):
        payload = {"sources": [{"item_id": "a"}], "fps": 24}
        with mock.patch.object(server, "_transcripts", return_value=(payload, "fake", [{"words": []}])), \
                mock.patch.object(server, "_ffmpeg", return_value="/fake/ffmpeg"), \
                mock.patch("bridge.audio_regions.speech_regions", return_value=json.loads(json.dumps(self.CLIP))):
            return json.loads(server.speech_regions(item_id="a", **kwargs))

    def test_compact_full_and_none(self):
        clip = self.run_tool()["result"]["clips"][0]
        self.assertEqual(clip["words"], [["hi", 1, 4], ["there", 5, 9]])
        self.assertEqual(clip["word_count"], 2)
        self.assertEqual(self.run_tool(words="full")["result"]["clips"][0]["words"], self.CLIP["words"])
        none = self.run_tool(words="none")["result"]["clips"][0]
        self.assertNotIn("words", none)
        self.assertEqual((none["word_count"], none["regions"][0]["text"]), (2, "hi there"))
        self.assertFalse(self.run_tool(words="some")["ok"])


if __name__ == "__main__":
    unittest.main()
