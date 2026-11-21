"""General Fusion tools: custom stages, keyframes, pasted settings, Lua and context.

These run against tests/fusion_fakes.py, not a real Resolve.
"""

import unittest

from bridge.operations import FUSION_TEXT_LIMIT, OperationError, ResolveOperations, _lua_long_string, _lua_string
from fakes import Clip, Project, Resolve
from fusion_fakes import Comp, lupa

LUA = unittest.skipUnless(lupa, "install lupa to run generated Fusion Lua")

FADE = dict(
    name="Fade",
    nodes=[{"name": "Black", "type": "Background", "inputs": {"TopLeftAlpha": 1}},
           {"name": "Over", "type": "Merge", "inputs": {
               "Background": {"from": "Black"},
               "Blend": {"keyframes": [{"frame": 0, "value": 0}, {"frame": 12, "value": 1}]}}}],
    chain=[{"node": "Over", "input": "Foreground"}],
)


def make(user_nodes=(), render_start=0, start=0, end=100, transport="console"):
    clip = Clip(start, end)
    clip.fusion = Comp(render_start, user_nodes)
    op = ResolveOperations(lambda: Resolve(Project([clip])), transport=transport)
    return op, clip.fusion, clip.GetUniqueId()


def fake_paste(comp, reg_ids, wire=False):
    """Stand in for comp:Paste: add tools, then report the wrapper finished."""
    def execute(script):
        comp.scripts.append(script)
        added = [comp.AddTool(reg_id) for reg_id in reg_ids]
        if wire:
            for upstream, downstream in zip(added, added[1:]):
                downstream.FindMainInput(1).ConnectTo(upstream.main_output)
        key, _, value = script.rsplit("comp:SetData(", 1)[-1].partition(", ")
        comp.SetData(key.strip('"'), value.rstrip(")").strip('"'))
    comp.Execute = execute


class CustomStageTests(unittest.TestCase):
    def test_single_node_stage_sits_after_builtins_and_before_speed(self):
        op, comp, item = make(user_nodes=("Glow",))
        op._op_animate_zoom({"item_id": item, "end_zoom": 1.2})
        op._op_change_clip_speed({"item_id": item, "speed": 0.5})
        result = op._op_fusion_build({"item_id": item, "name": "Soft",
                                      "nodes": [{"name": "Haze", "type": "Blur", "inputs": {"XBlurSize": 4}}]})
        self.assertEqual(result["stage"], "custom:Soft")
        self.assertTrue(result["verified"])
        self.assertEqual(comp.chain(), ["ResolveAIBridgeTimeSpeed", "Soft_Haze", "AIBridgeZoom", "Glow1", "MediaIn1"])
        self.assertEqual(comp.FindTool("Soft_Haze").GetInput("XBlurSize"), 4)
        self.assertEqual(comp.locks, 0)

    def test_chain_is_inferred_from_from_links(self):
        # The agent's call that used to fail: a Merge wired only with {"from": ...}, no chain.
        op, comp, item = make(user_nodes=("Glow",))
        result = op._op_fusion_build({"item_id": item, "name": "Tint", "nodes": [
            {"name": "Col", "type": "Background"},
            {"name": "Over", "type": "Merge", "inputs": {"Background": {"from": "chain"}, "Foreground": {"from": "Col"},
                                                        "Blend": 0.5}}]})
        over = comp.FindTool("Tint_Over")
        self.assertIn("Over is the only node", result["chain_inferred"])
        self.assertIs(over.inputs["Background"].source.tool, comp.FindTool("Glow1"))
        self.assertIs(over.inputs["Foreground"].source.tool, comp.FindTool("Tint_Col"))
        self.assertIs(comp.FindTool("MediaOut1").FindMainInput(1).GetConnectedOutput().tool, over)
        self.assertTrue(result["verified"])
        self.assertEqual((comp.locks, comp.undo_depth, comp.undo_groups), (0, 0, 1))

    def test_ambiguous_graph_still_needs_a_chain(self):
        op, comp, item = make()
        before = [t.Name for t in comp.tools]
        with self.assertRaisesRegex(OperationError, "A, B all are"):
            op._op_fusion_build({"item_id": item, "name": "X", "nodes": [
                {"name": "A", "type": "Blur"}, {"name": "B", "type": "Glow"}]})
        self.assertEqual([t.Name for t in comp.tools], before)

    def test_custom_stages_run_in_name_order(self):
        op, comp, item = make()
        for name in ("B", "A"):
            op._op_fusion_build({"item_id": item, "name": name, "nodes": [{"name": "G", "type": "Glow"}]})
        self.assertEqual(comp.chain(), ["B_G", "A_G", "MediaIn1"])

    def test_fade_feeds_a_named_entry_input_and_keys_blend(self):
        op, comp, item = make(user_nodes=("Glow",), render_start=1000)
        result = op._op_fusion_build(dict(FADE, item_id=item))
        over, black = comp.FindTool("Fade_Over"), comp.FindTool("Fade_Black")
        self.assertIs(over.inputs["Foreground"].source.tool, comp.FindTool("Glow1"))
        self.assertIs(over.inputs["Background"].source.tool, black)
        self.assertIs(comp.FindTool("MediaOut1").FindMainInput(1).GetConnectedOutput().tool, over)
        self.assertAlmostEqual(over.GetInput("Blend", 1000), 0)
        self.assertAlmostEqual(over.GetInput("Blend", 1012), 1)
        self.assertEqual(sorted(over.inputs["Blend"].keys), [1000, 1012])  # keys only at the listed frames
        self.assertTrue(result["verified"])
        chain = op._op_inspect_fusion({"item_id": item})["bridge_chain"]
        self.assertEqual(chain, {"source": "Glow1", "stages_before_media_out": ["custom:Fade"]})

    def test_rebuilding_a_stage_replaces_it_and_keeps_the_source(self):
        op, comp, item = make(user_nodes=("Glow",))
        op._op_fusion_build(dict(FADE, item_id=item))
        op._op_animate_zoom({"item_id": item})
        result = op._op_fusion_build({"item_id": item, "name": "Fade",
                                      "nodes": [{"name": "Soft", "type": "Blur", "inputs": {"XBlurSize": 2}}]})
        self.assertEqual(result["fusion_chain"]["stages"], ["zoom", "custom:Fade"])
        self.assertIsNone(comp.FindTool("Fade_Over"))
        self.assertIsNone(comp.FindTool("Fade_Black"))
        self.assertEqual(comp.chain(), ["Fade_Soft", "AIBridgeZoom", "Glow1", "MediaIn1"])
        self.assertFalse([t for t in comp.tools if t.Name.startswith("AIBridgeRetired")])

    def test_baked_easing_writes_a_key_per_frame(self):
        op, comp, item = make()
        op._op_fusion_build({"item_id": item, "name": "Pop", "nodes": [{"name": "T", "type": "Transform", "inputs": {
            "Size": {"keyframes": [{"frame": 0, "value": 1}, {"frame": 10, "value": 1.5, "easing": "ease_out"}]},
            "Center": {"keyframes": [{"frame": 0, "value": [0.3, 0.5]}, {"frame": 10, "value": [0.5, 0.5]}]}}}]})
        node = comp.FindTool("Pop_T")
        self.assertEqual(len(node.inputs["Size"].keys), 11)
        self.assertAlmostEqual(node.GetInput("Size", 5), 1 + 0.5 * 0.75)
        self.assertAlmostEqual(node.GetInput("Center", 10)[0], 0.5)

    def test_text_value_and_media_in_connection(self):
        op, comp, item = make()
        result = op._op_fusion_build({"item_id": item, "name": "Title", "nodes": [
            {"name": "Words", "type": "TextPlus", "inputs": {"StyledText": 'Say "hi"\nthere'}},
            {"name": "Comp", "type": "Merge", "inputs": {"Foreground": {"from": "Words"}, "EffectMask": {"from": "MediaIn"}}}],
            "chain": ["Comp"]})
        self.assertEqual(comp.FindTool("Title_Words").GetInput("StyledText"), 'Say "hi"\nthere')
        self.assertIs(comp.FindTool("Title_Comp").inputs["EffectMask"].source.tool, comp.FindTool("MediaIn1"))
        self.assertTrue(result["verified"])

    def test_chain_feed_input(self):
        op, comp, item = make(user_nodes=("Glow",))
        op._op_fusion_build({"item_id": item, "name": "Dup", "nodes": [
            {"name": "Soft", "type": "Blur"},
            {"name": "Mix", "type": "Merge", "inputs": {"Foreground": {"from": "chain"}}}],
            "chain": ["Soft", {"node": "Mix", "input": "Background"}]})
        glow = comp.FindTool("Glow1")
        self.assertIs(comp.FindTool("Dup_Soft").inputs["Input"].source.tool, glow)
        self.assertIs(comp.FindTool("Dup_Mix").inputs["Foreground"].source.tool, glow)
        self.assertIs(comp.FindTool("Dup_Mix").inputs["Background"].source.tool, comp.FindTool("Dup_Soft"))
        self.assertEqual(op._op_inspect_fusion({"item_id": item})["bridge_chain"]["source"], "Glow1")

    def test_failures_roll_back(self):
        op, comp, item = make(user_nodes=("Glow",))
        op._op_fusion_build(dict(FADE, item_id=item))
        before = [t.Name for t in comp.tools]
        for bad in ({"nodes": [{"name": "A", "type": "Glow"}, {"name": "B", "type": "Blur", "inputs": {"Nope": 1}}],
                     "chain": ["A", "B"]},
                    {"nodes": [{"name": "A", "type": "NotARealTool"}]},
                    {"nodes": [{"name": "A", "type": "Glow"}, {"name": "B", "type": "Blur"}]},  # no chain
                    {"nodes": [{"name": "A", "type": "Glow", "inputs": {"Glow": {"from": "Missing"}}}]}):
            with self.assertRaises(OperationError):
                op._op_fusion_build(dict(bad, item_id=item, name="Fade"))
            self.assertEqual([t.Name for t in comp.tools], before)
            self.assertEqual(op._op_inspect_fusion({"item_id": item})["bridge_chain"]["stages_before_media_out"],
                             ["custom:Fade"])

    def test_keyframe_validation_happens_before_any_change(self):
        op, comp, item = make(start=200, end=300)
        for keys in ([{"frame": 150, "value": 1}],
                     [{"frame": 0, "value": 1}, {"frame": 0, "value": 2}],
                     [{"frame": 0, "value": 1}, {"frame": 5, "value": [0.5, 0.5]}],
                     [{"frame": 0}],
                     [{"frame": 0, "value": 1}, {"frame": 5, "value": 2, "easing": "linear"}][:1] + [{"timeline_frame": 100, "value": 2}]):
            with self.assertRaises(OperationError):
                op._op_fusion_build({"item_id": item, "name": "X", "nodes": [
                    {"name": "T", "type": "Transform", "inputs": {"Size": {"keyframes": keys}}}]})
        self.assertEqual(len(comp.tools), 2)

    def test_reset_removes_only_that_stage(self):
        op, comp, item = make(user_nodes=("Glow",))
        op._op_fusion_build(dict(FADE, item_id=item))
        op._op_apply_blur_effect({"item_id": item})
        result = op._op_fusion_build({"item_id": item, "name": "Fade", "reset": True})
        self.assertEqual(result["removed"], ["custom:Fade"])
        self.assertIsNone(comp.FindTool("Fade_Black"))
        self.assertEqual(comp.chain(), ["AIBridgeBlur_1", "Glow1", "MediaIn1"])
        self.assertEqual(op._op_fusion_build({"item_id": item, "name": "Fade", "reset": True})["removed"], [])


class KeyframeInputTests(unittest.TestCase):
    def test_user_node_input_is_animated_and_verified(self):
        op, comp, item = make(user_nodes=("Glow",), render_start=50)
        result = op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow",
                                               "keyframes": [{"frame": 0, "value": 0}, {"frame": 20, "value": 0.8}]})
        self.assertTrue(result["verified"])
        self.assertAlmostEqual(comp.FindTool("Glow1").GetInput("Glow", 70), 0.8)
        self.assertEqual(comp.chain(), ["Glow1", "MediaIn1"])

    def test_static_value_and_errors(self):
        op, comp, item = make(user_nodes=("Glow",))
        self.assertTrue(op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow", "value": 0.3})["verified"])
        self.assertEqual(comp.FindTool("Glow1").GetInput("Glow"), 0.3)
        for params in ({"tool": "Missing", "input": "Glow", "value": 1},
                       {"tool": "Glow1", "input": "Nope", "value": 1},
                       {"tool": "Glow1", "input": "Glow"},
                       {"tool": "Glow1", "input": "Glow", "keyframes": [{"frame": 500, "value": 1}]}):
            with self.assertRaises(OperationError):
                op._op_fusion_keyframe_input(dict(params, item_id=item))

    def test_no_comp_is_not_created(self):
        op, comp, item = make()
        clip = op._find_timeline_items([item])[0][0]
        clip.fusion = None
        with self.assertRaises(OperationError):
            op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow", "value": 1})
        self.assertIsNone(clip.fusion)

    def test_inspect_tool_lists_inputs(self):
        op, comp, item = make(user_nodes=("Glow",))
        result = op._op_fusion_inspect_tool({"item_id": item, "tool": "Glow1"})
        self.assertEqual(result["reg_id"], "Glow")
        ids = {entry["id"]: entry for entry in result["inputs"]}
        self.assertEqual(ids["Input"]["connected_to"], "MediaIn1")
        self.assertIn("value", ids["Glow"])
        self.assertEqual([e["id"] for e in op._op_fusion_inspect_tool({"item_id": item, "tool": "Glow1", "search": "glow"})["inputs"]], ["Glow"])
        with self.assertRaisesRegex(OperationError, "Glow1"):
            op._op_fusion_inspect_tool({"item_id": item, "tool": "Missing"})


class PasteAndLuaTests(unittest.TestCase):
    def test_paste_reports_new_tools_and_can_join_the_chain(self):
        op, comp, item = make(user_nodes=("Glow",), transport="lua")
        fake_paste(comp, ["Blur", "BrightnessContrast"], wire=True)
        text = "{ Tools = ordered() { Blur1 = Blur {} } } -- ]] and ]=] inside"
        result = op._op_fusion_apply_setting({"item_id": item, "setting": text, "name": "Look",
                                              "insert_into_chain": {"entry": "Blur1", "exit": "BrightnessContrast1"}})
        self.assertEqual(result["pasted_tools"], ["Blur1", "BrightnessContrast1"])
        self.assertIn(_lua_long_string(text), comp.scripts[-1])
        self.assertIn("bmd.readstring", comp.scripts[-1])
        self.assertEqual(comp.chain(), ["BrightnessContrast1", "Blur1", "Glow1", "MediaIn1"])
        self.assertEqual(result["fusion_chain"]["stages"], ["custom:Look"])

    def test_paste_without_insert_leaves_tools_unconnected(self):
        op, comp, item = make(transport="lua")
        fake_paste(comp, ["Glow"])
        result = op._op_fusion_apply_setting({"item_id": item, "setting": "{ Tools = {} }"})
        self.assertEqual(result["pasted_tools"], ["Glow1"])
        self.assertEqual(comp.chain(), ["MediaIn1"])
        self.assertIn("not connected", result["note"])

    def test_paste_limits_and_failures(self):
        op, comp, item = make(transport="lua")
        with self.assertRaisesRegex(OperationError, "limit"):
            op._op_fusion_apply_setting({"item_id": item, "setting": "x" * (FUSION_TEXT_LIMIT + 1)})
        fake_paste(comp, [])
        with self.assertRaisesRegex(OperationError, "pasted no tools"):
            op._op_fusion_apply_setting({"item_id": item, "setting": "{}"})
        fake_paste(comp, ["Glow"])
        with self.assertRaisesRegex(OperationError, "pasted tools are"):
            op._op_fusion_apply_setting({"item_id": item, "setting": "{}", "insert_into_chain": {"entry": "X", "exit": "Glow1"}})
        self.assertEqual(len(comp.tools), 2)  # the pasted Glow was removed again
        op.transport = "console"
        with self.assertRaisesRegex(OperationError, "Console worker"):
            op._op_fusion_apply_setting({"item_id": item, "setting": "{}"})
        with self.assertRaisesRegex(OperationError, "Console worker"):
            op._op_fusion_run_lua({"item_id": item, "code": "return 1"})

    @LUA
    def test_run_lua_returns_json_and_reports_errors(self):
        op, comp, item = make(user_nodes=("Glow",), transport="lua")
        result = op._op_fusion_run_lua({"item_id": item, "code": 'return {name = comp:FindTool("Glow1") and "found", n = 2, list = {1, 2}}'})
        self.assertEqual(result["result"], {"name": "found", "n": 2, "list": [1, 2]})
        self.assertTrue(result["finished"])
        with self.assertRaisesRegex(OperationError, "boom"):
            op._op_fusion_run_lua({"item_id": item, "code": "error('boom')"})
        with self.assertRaisesRegex(OperationError, "syntax"):
            op._op_fusion_run_lua({"item_id": item, "code": "return ]] ("})

    @LUA
    def test_lua_literals_round_trip(self):
        runtime = lupa.LuaRuntime()
        for text in ('plain', 'quote " and \\ slash', 'line\nbreak\ttab\x01', 'ends with ]', 'has ]] and ]=]', 'é ünïcode'):
            self.assertEqual(runtime.eval(_lua_string(text)), text)
            self.assertEqual(runtime.eval(_lua_long_string(text)), text)

    @LUA
    def test_lua_transport_build_runs_generated_script(self):
        op, comp, item = make(user_nodes=("Glow",), render_start=10, transport="lua")
        result = op._op_fusion_build(dict(FADE, item_id=item))
        self.assertTrue(result["verified"], getattr(comp, "last_error", ""))
        over = comp.FindTool("Fade_Over")
        self.assertIs(over.inputs["Background"].source.tool, comp.FindTool("Fade_Black"))
        self.assertAlmostEqual(over.GetInput("Blend", 22), 1)
        with self.assertRaisesRegex(OperationError, "no image input"):
            op._op_fusion_build({"item_id": item, "name": "Title", "nodes": [{"name": "Words", "type": "TextPlus"}]})
        op._op_fusion_build({"item_id": item, "name": "Title", "nodes": [
            {"name": "Words", "type": "TextPlus", "inputs": {"StyledText": 'Quote " ]] done'}},
            {"name": "Over", "type": "Merge", "inputs": {"Foreground": {"from": "Words"}}}], "chain": ["Over"]})
        self.assertEqual(comp.FindTool("Title_Words").GetInput("StyledText"), 'Quote " ]] done')
        result = op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow",
                                               "keyframes": [{"frame": 0, "value": 0}, {"frame": 4, "value": 1, "easing": "linear"}]})
        self.assertTrue(result["verified"])
        self.assertAlmostEqual(comp.FindTool("Glow1").GetInput("Glow", 12), 0.5)


class CurrentContextTests(unittest.TestCase):
    def test_offline_and_empty_states(self):
        self.assertFalse(ResolveOperations(lambda: None)._op_current_context({})["online"])
        context = ResolveOperations(lambda: Resolve(None))._op_current_context({})
        self.assertTrue(context["online"])
        self.assertIsNone(context["project"])
        self.assertEqual(context["clips_at_playhead"], [])
        project = Project([])
        project.current = None
        context = ResolveOperations(lambda: Resolve(project))._op_current_context({})
        self.assertEqual((context["project"], context["timeline"]), ("Test project", None))

    def test_clips_under_playhead_top_track_first(self):
        clips = [Clip(0, 40), Clip(40, 100, name="Interview"), Clip(10, 20, track=2), Clip(45, 60, track=2, name="Logo")]
        clips[1].fusion = Comp()
        project = Project(clips)
        project.current.current = "00:00:02:00"  # frame 48 at 24 fps
        context = ResolveOperations(lambda: Resolve(project))._op_current_context({})
        self.assertEqual(context["playhead_frame"], 48)
        self.assertEqual(context["fps"], 24.0)
        self.assertEqual([(c["name"], c["track"], c["label"], c["offset"]) for c in context["clips_at_playhead"]],
                         [("Logo", 2, "V2.2", 3), ("Interview", 1, "V1.2", 8)])
        self.assertTrue(context["clips_at_playhead"][1]["has_fusion"])
        self.assertFalse(context["clips_at_playhead"][0]["has_fusion"])


if __name__ == "__main__":
    unittest.main()
