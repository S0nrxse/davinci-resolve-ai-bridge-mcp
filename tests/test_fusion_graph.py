"""fusion_graph (explicit links) and fusion_copy_comp (export/import), against tests/fusion_fakes.py.

These do not contact Resolve. The comp-file calls (ExportFusionComp, ImportFusionComp,
DeleteFusionCompByName, LoadFusionCompByName, RenameFusionCompByName) are modelled on the
documented API and on what Resolve 21.2 Free did live; the fake's behaviour is an assumption.
"""

import os
import tempfile
import unittest
from pathlib import Path

from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve
from fusion_fakes import Comp, comp_from_text, comp_text, lupa

LUA = unittest.skipUnless(lupa, "install lupa to run generated Fusion Lua")


class CompClip(Clip):
    """A clip holding a list of named comps, with Resolve's comp-file calls."""

    def __init__(self, *args, comps=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.comps = [["Composition %d" % (i + 1), comp] for i, comp in enumerate(comps)]
        self.loaded = self.comps[0][0] if self.comps else None
        self.convert_loaders = False
        self.refuse_loaded_delete = True  # Live: DeleteFusionCompByName fails while that comp is loaded.
        self.undeletable = set()
        self.calls = []

    def _find(self, name):
        return next((entry for entry in self.comps if entry[0] == name), None)

    def _next_name(self):
        n = 1
        while self._find("Composition %d" % n):
            n += 1
        return "Composition %d" % n

    def GetFusionCompCount(self):
        return len(self.comps)

    def GetFusionCompByIndex(self, index):
        return self.comps[index - 1][1] if 1 <= index <= len(self.comps) else None

    def GetFusionCompNameList(self):
        return [name for name, _ in self.comps]

    def AddFusionComp(self):
        self.calls.append("AddFusionComp")
        comp = Comp()
        self.comps.append([self._next_name(), comp])
        self.loaded = self.comps[-1][0]
        return comp

    def LoadFusionCompByName(self, name):
        entry = self._find(name)
        if entry:
            self.loaded = name
        return entry[1] if entry else None

    def DeleteFusionCompByName(self, name):
        self.calls.append("Delete:" + name)
        entry = self._find(name)
        if entry is None or name in self.undeletable or (self.refuse_loaded_delete and name == self.loaded):
            return False
        self.comps.remove(entry)
        return True

    def RenameFusionCompByName(self, old, new):
        entry = self._find(old)
        if entry is None or self._find(new):
            return False
        entry[0] = new
        if self.loaded == old:
            self.loaded = new
        return True

    def ExportFusionComp(self, path, index):
        comp = self.GetFusionCompByIndex(index)
        if comp is None:
            return False
        Path(path).write_text(comp_text(comp), encoding="utf-8")
        return True

    def ImportFusionComp(self, path):
        self.calls.append("ImportFusionComp")
        comp = comp_from_text(Path(path).read_text(encoding="utf-8"), self.convert_loaders)
        self.comps.append([self._next_name(), comp])  # The loaded comp stays as it was.
        return comp


def make(*clips, transport="console"):
    op = ResolveOperations(lambda: Resolve(Project(list(clips))), transport=transport)
    return op


def single(user_nodes=(), comp=True, transport="console"):
    clip = CompClip(0, 100, comps=[Comp(0, user_nodes)] if comp else [])
    return make(clip, transport=transport), clip


MASKED_TITLE = dict(
    name="Look",
    nodes=[{"name": "Soft", "type": "Blur", "inputs": {"XBlurSize": 2}},
           {"name": "Words", "type": "TextPlus", "inputs": {"StyledText": "Hello"}},
           {"name": "Vig", "type": "EllipseMask", "inputs": {"SoftEdge": 0.3}},
           {"name": "Over", "type": "Merge", "inputs": {
               "Blend": {"keyframes": [{"frame": 0, "value": 0}, {"frame": 10, "value": 1}]}}}],
    links=[["chain", "Soft.Input"], ["Soft.Output", "Over.Background"],
           ["Words.Output", "Over.Foreground"], ["Vig.Mask", "Over.EffectMask"]],
    output="Over",
)


def source_tool(tool, input_id):
    output = tool.inputs[input_id].GetConnectedOutput()
    return output.tool.Name if output is not None else None


class GraphTests(unittest.TestCase):
    def test_links_wire_merge_inputs_and_mask_and_output_feeds_media_out(self):
        op, clip = single(user_nodes=("Glow",))
        comp = clip.comps[0][1]
        result = op._op_fusion_graph(dict(MASKED_TITLE, item_id=clip.GetUniqueId()))
        over = comp.FindTool("Look_Over")
        self.assertEqual(source_tool(over, "Background"), "Look_Soft")
        self.assertEqual(source_tool(over, "Foreground"), "Look_Words")
        self.assertEqual(source_tool(over, "EffectMask"), "Look_Vig")
        self.assertEqual(source_tool(comp.FindTool("Look_Soft"), "Input"), "Glow1")  # the chain, not MediaIn
        self.assertEqual(source_tool(comp.FindTool("MediaOut1"), "Input"), "Look_Over")
        self.assertEqual(result["stage"], "custom:Look")
        self.assertEqual(result["nodes"], {"Soft": "Look_Soft", "Words": "Look_Words", "Vig": "Look_Vig",
                                           "Over": "Look_Over"})
        self.assertTrue(result["feeds_media_out"])
        self.assertTrue(result["verified"])
        self.assertEqual(len(result["links_made"]), 4)
        self.assertTrue(all(link["verified"] for link in result["links_made"]))
        self.assertEqual(result["links_made"][0]["from"], "chain (Glow1)")
        self.assertIn("direct node calls", result["route"])
        self.assertFalse(result["comp_created"])
        self.assertAlmostEqual(over.GetInput("Blend", 10), 1)
        self.assertEqual(comp.locks, 0)
        # Live, Resolve only re-read (viewer, Deliver) a comp edited inside a kept undo group.
        self.assertEqual((comp.undo_depth, comp.undo_groups), (0, 1))
        chain = op._op_inspect_fusion({"item_id": clip.GetUniqueId()})["bridge_chain"]
        self.assertEqual(chain, {"source": "Glow1", "stages_before_media_out": ["custom:Look"]})

    def test_media_in_and_existing_tool_sources_and_chain_feeds(self):
        op, clip = single(user_nodes=("Glow",))
        comp = clip.comps[0][1]
        op._op_fusion_graph({"item_id": clip.GetUniqueId(), "name": "Mix", "nodes": [
            {"name": "Key", "type": "BrightnessContrast"},
            {"name": "Over", "type": "Merge"},
            {"name": "Back", "type": "Merge"}],
            "links": [["MediaIn.Output", "Key"], ["chain", "Over.Background"], ["Key", "Over.Foreground"],
                      ["Glow1.Output", "Back.Foreground"], ["Over.Output", "Back.Background"],
                      ["chain", "Back.EffectMask"]],
            "output": "Back"})
        self.assertEqual(source_tool(comp.FindTool("Mix_Key"), "Input"), "MediaIn1")
        self.assertEqual(source_tool(comp.FindTool("Mix_Over"), "Background"), "Glow1")
        self.assertEqual(source_tool(comp.FindTool("Mix_Back"), "Foreground"), "Glow1")
        self.assertEqual(source_tool(comp.FindTool("Mix_Back"), "EffectMask"), "Glow1")  # second chain link: a feed
        self.assertEqual(source_tool(comp.FindTool("MediaOut1"), "Input"), "Mix_Back")

    def test_same_name_replaces_the_previous_graph_and_a_fusion_build_stage(self):
        op, clip = single(user_nodes=("Glow",))
        comp, item = clip.comps[0][1], clip.GetUniqueId()
        op._op_fusion_build({"item_id": item, "name": "Look", "nodes": [{"name": "Old", "type": "Blur"}]})
        op._op_fusion_graph(dict(MASKED_TITLE, item_id=item))
        self.assertIsNone(comp.FindTool("Look_Old"))
        result = op._op_fusion_graph({"item_id": item, "name": "Look",
                                      "nodes": [{"name": "Haze", "type": "Glow", "inputs": {"Glow": 0.5}}],
                                      "links": [["chain", "Haze.Input"]]})
        self.assertTrue(result["verified"])
        for gone in ("Look_Over", "Look_Soft", "Look_Words", "Look_Vig"):
            self.assertIsNone(comp.FindTool(gone))
        self.assertFalse([t for t in comp.tools if t.Name.startswith("AIBridgeRetired")])
        self.assertEqual(comp.chain(), ["Look_Haze", "Glow1", "MediaIn1"])
        reset = op._op_fusion_graph({"item_id": item, "name": "Look", "reset": True})
        self.assertEqual(reset["removed"], ["custom:Look"])
        self.assertEqual(comp.chain(), ["Glow1", "MediaIn1"])

    def test_validation_errors_change_nothing(self):
        op, clip = single(comp=False)
        item = clip.GetUniqueId()
        node = {"name": "A", "type": "Blur"}
        merge = {"name": "M", "type": "Merge"}
        bad = [
            {"nodes": [node], "links": []},                                           # no links
            {"nodes": [node], "links": [["MediaIn", "A.Input"]]},                     # no chain link
            {"nodes": [node, merge], "links": [["chain", "A.Input"], ["A", "M.Background"]]},  # no output
            {"nodes": [node], "links": [["chain", "A.Input"]], "output": "Nope"},
            {"nodes": [node], "links": [["chain", "Elsewhere.Input"]]},               # target outside the graph
            {"nodes": [node], "links": [["chain", "A.Input"], ["Glow1", "A.EffectMask"]]},  # no comp to read from
            {"nodes": [node, merge], "links": [["chain", "A.Input"], ["A.Foo", "M.Background"]], "output": "M"},
            {"nodes": [node, merge], "links": [["chain", "M.Background"], ["M", "A.Input"], ["A", "M.Foreground"]],
             "output": "M"},                                                          # loop
            {"nodes": [node], "links": [["chain", "A.Input"], ["MediaIn", "A.Input"]]},  # one input, two sources
            {"nodes": [{"name": "A", "type": "Blur", "inputs": {"Input": 1}}], "links": [["chain", "A.Input"]]},
            {"nodes": [{"name": "A", "type": "Blur", "inputs": {"EffectMask": {"from": "MediaIn"}}}],
             "links": [["chain", "A.Input"]]},
            {"nodes": [{"name": "A", "type": "Blur", "inputs": {"XBlurSize": {"keyframes": [{"frame": 500, "value": 1}]}}}],
             "links": [["chain", "A.Input"]]},
            {"nodes": [node, dict(node)], "links": [["chain", "A"]]},               # duplicate names
            {"nodes": [{"name": "A", "type": "Blur", "inputs": {"XBlurSize": [1, 2, 3]}}],
             "links": [["chain", "A.Input"]]},                                    # bad static value
            {"nodes": [node], "links": [["chain"]]},
        ]
        for params in bad:
            with self.subTest(params=params):
                with self.assertRaises(OperationError) as caught:
                    op._op_fusion_graph(dict(params, item_id=item))
                self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(clip.comps, [])
        self.assertNotIn("AddFusionComp", clip.calls)

    def test_validation_with_an_existing_comp_leaves_it_untouched(self):
        op, clip = single(user_nodes=("Glow",))
        comp, item = clip.comps[0][1], clip.GetUniqueId()
        op._op_fusion_graph(dict(MASKED_TITLE, item_id=item))
        before = [t.Name for t in comp.tools]
        for params in ({"links": [["chain", "Soft.Input"], ["Missing1", "Over.Foreground"]]},
                       {"links": [["chain", "Soft.Input"], ["Look_Over", "Over.Foreground"]]}):  # part of the stage
            with self.assertRaises(OperationError):
                op._op_fusion_graph(dict(MASKED_TITLE, item_id=item, **params))
            self.assertEqual([t.Name for t in comp.tools], before)

    def test_failure_rolls_back_created_nodes_and_restores_the_old_stage(self):
        op, clip = single(user_nodes=("Glow",))
        comp, item = clip.comps[0][1], clip.GetUniqueId()
        op._op_fusion_graph(dict(MASKED_TITLE, item_id=item))
        before = [t.Name for t in comp.tools]
        for params in ({"nodes": [{"name": "A", "type": "Blur", "inputs": {"Nope": 1}}],
                        "links": [["chain", "A.Input"]]},
                       {"nodes": [{"name": "A", "type": "Blur"}], "links": [["chain", "A.Nope"]]},
                       {"nodes": [{"name": "A", "type": "NotARealTool"}], "links": [["chain", "A"]]},
                       {"nodes": [{"name": "A", "type": "Background"}], "links": [["chain", "A"]]}):  # no main input
            with self.assertRaises(OperationError):
                op._op_fusion_graph(dict(params, item_id=item, name="Look"))
            self.assertEqual([t.Name for t in comp.tools], before)
            self.assertEqual(comp.chain()[:2], ["Look_Over", "Look_Soft"])
            self.assertEqual(comp.locks, 0)
            self.assertEqual(comp.undo_depth, 0)  # rolled-back edits still close their undo group
        self.assertEqual(len(clip.comps), 1)

    def test_failure_removes_the_comp_it_created(self):
        op, clip = single(comp=False)
        clip.refuse_loaded_delete = False
        with self.assertRaises(OperationError) as caught:
            op._op_fusion_graph({"item_id": clip.GetUniqueId(), "nodes": [
                {"name": "A", "type": "Blur", "inputs": {"Nope": 1}}], "links": [["chain", "A.Input"]]})
        self.assertIn("Nope", str(caught.exception))
        self.assertIn("was removed again", str(caught.exception))
        self.assertEqual(clip.comps, [])
        self.assertIn("Delete:Composition 1", clip.calls)

    def test_failure_reports_a_created_comp_resolve_would_not_remove(self):
        op, clip = single(comp=False)  # refuse_loaded_delete: the new comp is the loaded one
        with self.assertRaises(OperationError) as caught:
            op._op_fusion_graph({"item_id": clip.GetUniqueId(), "nodes": [
                {"name": "A", "type": "Blur", "inputs": {"Nope": 1}}], "links": [["chain", "A.Input"]]})
        self.assertIn("did not remove", str(caught.exception))
        self.assertEqual(len(clip.comps), 1)
        self.assertEqual(sorted(t.reg_id for t in clip.comps[0][1].tools), ["MediaIn", "MediaOut"])
        # A later graph reuses that comp instead of stacking "Composition 2".
        op._op_fusion_graph({"item_id": clip.GetUniqueId(), "nodes": [{"name": "A", "type": "Blur"}],
                             "links": [["chain", "A.Input"]]})
        self.assertEqual(clip.GetFusionCompNameList(), ["Composition 1"])

    def test_success_on_a_clip_without_a_comp_creates_one(self):
        op, clip = single(comp=False)
        result = op._op_fusion_graph({"item_id": clip.GetUniqueId(), "nodes": [{"name": "A", "type": "Blur"}],
                                      "links": [["chain", "A.Input"]]})
        self.assertTrue(result["comp_created"])
        self.assertEqual(clip.comps[0][1].chain(), ["Graph_A", "MediaIn1"])

    def test_unused_nodes_are_reported(self):
        op, clip = single()
        result = op._op_fusion_graph({"item_id": clip.GetUniqueId(), "nodes": [
            {"name": "A", "type": "Blur"}, {"name": "Stray", "type": "Glow"}],
            "links": [["chain", "A.Input"]], "output": "A"})
        self.assertIn("Stray", " ".join(result["warnings"]))

    @LUA
    def test_lua_transport_runs_the_generated_script(self):
        op, clip = single(user_nodes=("Glow",), transport="lua")
        comp = clip.comps[0][1]
        result = op._op_fusion_graph(dict(MASKED_TITLE, item_id=clip.GetUniqueId()))
        self.assertEqual(result["route"], "comp:Execute Lua")
        self.assertEqual(source_tool(comp.FindTool("Look_Over"), "EffectMask"), "Look_Vig")
        self.assertTrue(result["verified"])


class CopyCompTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.env = os.environ.get("RESOLVE_AI_BRIDGE_HOME")
        os.environ["RESOLVE_AI_BRIDGE_HOME"] = self.home.name
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.env is None:
            os.environ.pop("RESOLVE_AI_BRIDGE_HOME", None)
        else:
            os.environ["RESOLVE_AI_BRIDGE_HOME"] = self.env

    def photo_source(self):
        comp = Comp()
        photo = comp.AddTool("Loader")
        photo.Name, photo.inputs["Clip"].value = "Photo", "/Users/me/photo.jpg"
        comp.AddTool("Merge").Name = "Over"
        return CompClip(0, 100, comps=[comp], name="source")

    def test_copy_to_two_targets_reports_loader_conversion(self):
        source = self.photo_source()
        empty = CompClip(100, 200, comps=[Comp()], name="empty")  # e.g. left behind by a failed build
        bare = CompClip(200, 300, name="bare")
        for target in (empty, bare):
            target.convert_loaders = True
        op = make(source, empty, bare)
        result = op._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(),
                                          "to_item_ids": [empty.GetUniqueId(), bare.GetUniqueId()]})
        first, second = result["targets"]
        self.assertTrue(first["imported"] and second["imported"])
        self.assertEqual((first["comp_count_before"], first["comp_count_after"]), (1, 1))
        self.assertEqual((second["comp_count_before"], second["comp_count_after"]), (0, 1))
        self.assertTrue(first["replaced"])
        self.assertEqual(empty.GetFusionCompNameList(), ["Composition 1"])  # renamed back, no "Composition 2"
        self.assertTrue(Path(first["backup_file"]).exists())
        for target in (first, second):
            self.assertEqual([(p["tool"], p["actual"]) for p in target["converted_tools"]], [("Photo", "MediaIn")])
            self.assertIn("Photo", target["warning"])
            self.assertFalse(target["verified"])
        self.assertIsNotNone(empty.comps[0][1].FindTool("Over"))
        self.assertTrue(result["comp_file"].startswith(self.home.name))
        self.assertIn("warnings", result["note"])
        self.assertIn("MediaIn", result["unverified"])

    def test_kept_loader_is_verified_without_warning(self):
        source, bare = self.photo_source(), CompClip(100, 200, name="bare")
        result = make(source, bare)._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(),
                                                         "to_item_ids": [bare.GetUniqueId()]})
        target = result["targets"][0]
        self.assertTrue(target["verified"])
        self.assertIsNone(target["warning"])
        self.assertEqual(bare.comps[0][1].FindTool("Photo").inputs["Clip"].value, "/Users/me/photo.jpg")

    def test_refuses_to_clobber_user_nodes_without_approval(self):
        source = self.photo_source()
        busy = CompClip(100, 200, comps=[Comp(0, ("Glow",))], name="busy")
        op = make(source, busy)
        params = {"from_item_id": source.GetUniqueId(), "to_item_ids": [busy.GetUniqueId()]}
        with self.assertRaises(OperationError) as caught:
            op._op_fusion_copy_comp(params)
        self.assertIn("Glow1", str(caught.exception))
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertNotIn("ImportFusionComp", busy.calls)
        self.assertIsNotNone(busy.comps[0][1].FindTool("Glow1"))
        result = op._op_fusion_copy_comp(dict(params, user_approved=True))
        target = result["targets"][0]
        self.assertTrue(target["replaced"])
        self.assertEqual(target["replaced_nodes"], ["Glow1"])
        self.assertIn("Glow1", Path(target["backup_file"]).read_text())
        self.assertEqual(len(busy.comps), 1)
        self.assertIsNone(busy.comps[0][1].FindTool("Glow1"))

    def test_bridge_effects_also_need_approval(self):
        source = self.photo_source()
        zoomed = CompClip(100, 200, comps=[Comp()], name="zoomed")
        op = make(source, zoomed)
        op._op_animate_zoom({"item_id": zoomed.GetUniqueId()})
        with self.assertRaises(OperationError) as caught:
            op._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(), "to_item_ids": [zoomed.GetUniqueId()]})
        self.assertIn("zoom", str(caught.exception))

    def test_several_comps_refuse_replace_and_add_stacks_with_a_warning(self):
        source = self.photo_source()
        double = CompClip(100, 200, comps=[Comp(), Comp()], name="double")
        op = make(source, double)
        with self.assertRaises(OperationError):
            op._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(), "to_item_ids": [double.GetUniqueId()],
                                     "user_approved": True})
        self.assertEqual(len(double.comps), 2)
        result = op._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(), "to_item_ids": [double.GetUniqueId()],
                                          "mode": "add"})
        target = result["targets"][0]
        self.assertEqual((target["comp_count_before"], target["comp_count_after"]), (2, 3))
        self.assertIn("3 Fusion comps", target["warning"])

    def test_old_comp_that_cannot_be_deleted_keeps_the_original(self):
        source = self.photo_source()
        stuck = CompClip(100, 200, comps=[Comp()], name="stuck")
        stuck.undeletable.add("Composition 1")
        op = make(source, stuck)
        target = op._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(),
                                          "to_item_ids": [stuck.GetUniqueId()]})["targets"][0]
        self.assertFalse(target["imported"])
        self.assertFalse(target["replaced"])
        self.assertEqual(target["comp_count_after"], 1)
        self.assertEqual(stuck.GetFusionCompNameList(), ["Composition 1"])
        self.assertIn("removed the imported copy again", target["warning"])

    def test_both_comps_kept_is_reported(self):
        source = self.photo_source()
        stuck = CompClip(100, 200, comps=[Comp()], name="stuck")
        stuck.undeletable.update({"Composition 1", "Composition 2"})
        target = make(source, stuck)._op_fusion_copy_comp({"from_item_id": source.GetUniqueId(),
                                                           "to_item_ids": [stuck.GetUniqueId()]})["targets"][0]
        self.assertEqual(target["comp_count_after"], 2)
        self.assertIn("kept both comps", target["warning"])

    def test_source_and_argument_errors(self):
        source, bare = self.photo_source(), CompClip(100, 200, name="bare")
        op = make(source, bare)
        for params in ({"from_item_id": bare.GetUniqueId(), "to_item_ids": [source.GetUniqueId()]},  # no comp
                       {"from_item_id": source.GetUniqueId(), "to_item_ids": [source.GetUniqueId()]},
                       {"from_item_id": source.GetUniqueId(), "to_item_ids": []},
                       {"from_item_id": source.GetUniqueId(), "to_item_ids": [bare.GetUniqueId()], "mode": "merge"},
                       {"from_item_id": source.GetUniqueId(), "to_item_ids": [bare.GetUniqueId()], "comp_index": 2}):
            with self.assertRaises(OperationError):
                op._op_fusion_copy_comp(params)
        self.assertEqual(bare.comps, [])

    def test_fake_round_trip(self):
        comp = Comp(0, ("Glow",))
        text = comp_text(comp)
        self.assertEqual(sorted(t.Name for t in comp_from_text(text).tools), ["Glow1", "MediaIn1", "MediaOut1"])


if __name__ == "__main__":
    unittest.main()
