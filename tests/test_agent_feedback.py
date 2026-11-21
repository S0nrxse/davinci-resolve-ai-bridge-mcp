"""Regressions for the problems an agent hit on a real Resolve 21.2 Free project (fakes only, no Resolve).

Fusion: Merge inputs refused, animated inputs, clearing keys, Lua running in the wrong clip's comp, Loaders
converted on paste/import. Rebuilds: carrying per-clip properties, markers and links. resolve_api indexing.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fakes

from bridge.operations import OperationError, ResolveOperations, fusion_conversions, setting_tools
from fakes import Clip, Media, Project, Resolve
from fusion_fakes import Comp, Output


def make(user_nodes=(), transport="console", render_start=0):
    clip = Clip(0, 100)
    clip.fusion = Comp(render_start, user_nodes)
    op = ResolveOperations(lambda: Resolve(Project([clip])), transport=transport)
    return op, clip.fusion, clip.GetUniqueId()


FADE = dict(
    name="Fade",
    nodes=[{"name": "Black", "type": "Background", "inputs": {"TopLeftAlpha": 1}},
           {"name": "Over", "type": "Merge", "inputs": {
               "Background": {"from": "Black"},
               "Blend": {"keyframes": [{"frame": 0, "value": 0}, {"frame": 12, "value": 1}]}}}],
    chain=[{"node": "Over", "input": "Foreground"}],
)


class MergeInputTests(unittest.TestCase):
    def test_unconnected_image_inputs_are_found_through_the_input_list(self):
        op, comp, item = make(user_nodes=("Glow",))
        result = op._op_fusion_build(dict(FADE, item_id=item))
        over, black = comp.FindTool("Fade_Over"), comp.FindTool("Fade_Black")
        # Fusion's GetInput is nil for image inputs; the old check refused "no input 'Background'".
        self.assertIsNone(over.GetInput("Background"))
        self.assertIs(over.inputs["Background"].source.tool, black)
        self.assertIs(over.inputs["Foreground"].source.tool, comp.FindTool("Glow1"))
        self.assertEqual(comp.chain()[0], "Fade_Over")
        self.assertTrue(result["verified"])

    def test_missing_input_is_still_refused(self):
        op, comp, item = make()
        with self.assertRaisesRegex(OperationError, "no input 'Backdrop'"):
            op._op_fusion_build({"item_id": item, "name": "X", "nodes": [
                {"name": "Over", "type": "Merge", "inputs": {"Backdrop": 1}}]})


class AnimatedInputTests(unittest.TestCase):
    def key(self, op, item, keys, **extra):
        return op._op_fusion_keyframe_input(dict({"item_id": item, "tool": "Glow1", "input": "Glow",
                                                  "keyframes": [{"frame": f, "value": v} for f, v in keys]}, **extra))

    def test_replace_and_merge_on_an_animated_number(self):
        op, comp, item = make(user_nodes=("Glow",))
        self.assertEqual(self.key(op, item, [(0, 0), (10, 1)])["mode"], "created")
        merged = self.key(op, item, [(20, 0.5)], replace=False)
        self.assertEqual(merged["mode"], "merged into the existing BezierSpline")
        self.assertEqual(sorted(comp.FindTool("Glow1").inputs["Glow"].keys), [0, 10, 20])
        replaced = self.key(op, item, [(5, 0.2), (6, 0.3)])
        self.assertEqual(replaced["mode"], "replaced the existing BezierSpline")
        self.assertEqual(sorted(comp.FindTool("Glow1").inputs["Glow"].keys), [5, 6])
        self.assertTrue(replaced["verified"])

    def test_merge_keys_into_an_animated_point_path(self):
        op, comp, item = make(user_nodes=("Transform",))
        first = op._op_fusion_keyframe_input({"item_id": item, "tool": "Transform1", "input": "Center", "keyframes": [
            {"frame": 0, "value": [0.1, 0.2]}, {"frame": 10, "value": [0.3, 0.4]}]})
        self.assertTrue(first["verified"])
        merged = op._op_fusion_keyframe_input({"item_id": item, "tool": "Transform1", "input": "Center",
                                               "replace": False, "keyframes": [{"frame": 20, "value": [0.9, 0.8]}]})
        self.assertEqual(merged["mode"], "merged into the existing XYPath")
        path = comp.FindTool("Transform1").inputs["Center"].path
        self.assertEqual(sorted(path.inputs["X"].keys), [0, 10, 20])
        self.assertAlmostEqual(comp.FindTool("Transform1").GetInput("Center", 0)[0], 0.1)
        self.assertEqual(comp.FindTool("Transform1").GetInput("Center", 20), (0.9, 0.8))

    def test_merge_into_an_unsupported_modifier_is_refused_without_changes(self):
        op, comp, item = make(user_nodes=("Transform",))
        node = comp.FindTool("Transform1")
        node.inputs["Center"].source = comp.AddTool("PolyPath").main_output  # Fusion page's default point path
        with self.assertRaisesRegex(OperationError, "PolyPath"):
            op._op_fusion_keyframe_input({"item_id": item, "tool": "Transform1", "input": "Center", "replace": False,
                                          "keyframes": [{"frame": 2, "value": [0.5, 0.5]}]})
        self.assertEqual(comp.FindTool("Transform1").inputs["Center"].source.tool.reg_id, "PolyPath")

    def test_lua_replaces_an_existing_modifier_instead_of_stacking(self):
        op, comp, item = make(user_nodes=("Glow",), transport="lua")
        self.key(op, item, [(0, 0), (10, 1)])
        script = comp.scripts[-1]
        self.assertLess(script.index('aib_tool:ConnectInput("Glow", nil)'), script.index("aib_tool.Glow = BezierSpline()"))


class ClearKeyframeTests(unittest.TestCase):
    def test_clear_leaves_the_value_at_the_first_frame_static(self):
        op, comp, item = make(user_nodes=("Glow",), render_start=50)
        op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow",
                                      "keyframes": [{"frame": 0, "value": 0.25}, {"frame": 10, "value": 1}]})
        result = op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow", "clear": True})
        glow = comp.FindTool("Glow1").inputs["Glow"]
        self.assertTrue(result["cleared"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["previous_modifier"], "BezierSpline")
        self.assertIsNone(glow.keys)
        self.assertEqual(glow.value, 0.25)

    def test_clear_with_a_value_and_on_a_point(self):
        op, comp, item = make(user_nodes=("Transform",))
        op._op_fusion_keyframe_input({"item_id": item, "tool": "Transform1", "input": "Center", "keyframes": [
            {"frame": 0, "value": [0.1, 0.2]}, {"frame": 10, "value": [0.3, 0.4]}]})
        result = op._op_fusion_keyframe_input({"item_id": item, "tool": "Transform1", "input": "Center",
                                               "clear": True, "value": [0.5, 0.6]})
        center = comp.FindTool("Transform1").inputs["Center"]
        self.assertTrue(result["cleared"])
        self.assertIsNone(center.path)
        self.assertEqual(comp.FindTool("Transform1").GetInput("Center"), {1: 0.5, 2: 0.6})

    def test_clear_on_a_static_input_or_an_image_connection(self):
        op, comp, item = make(user_nodes=("Glow",))
        result = op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow", "clear": True})
        self.assertFalse(result["cleared"])
        self.assertIn("not animated", result["note"])
        with self.assertRaisesRegex(OperationError, "image connection"):
            op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Input", "clear": True})
        self.assertEqual(comp.chain(), ["Glow1", "MediaIn1"])


class WrongCompTests(unittest.TestCase):
    def test_run_lua_binds_comp_for_the_chunk_and_refuses_get_current_comp(self):
        op, comp, item = make(transport="lua")
        op._op_fusion_run_lua({"item_id": item, "code": "return 1"})
        script = comp.scripts[-1]
        self.assertIn('aib_load("local comp = ...; " ..', script)
        self.assertIn("aib_chunk(comp)", script)
        # The guard runs first: it returns unless this comp carries the one-time target tag.
        self.assertTrue(script.startswith('if comp == nil or comp:GetData("ResolveAIBridgeTarget") ~= "'))
        before = len(comp.scripts)
        with self.assertRaisesRegex(OperationError, "GetCurrentComp"):
            op._op_fusion_run_lua({"item_id": item, "code": "local c = fusion:GetCurrentComp()\nreturn 1"})
        self.assertEqual(len(comp.scripts), before)  # nothing was sent

    def test_script_that_runs_elsewhere_fails_instead_of_reporting(self):
        op, comp, item = make(transport="lua")
        comp.fail_lua = True  # Resolve ran it in another comp: the guard returned before doing anything.
        with self.assertRaisesRegex(OperationError, "did not run in this clip's Fusion composition"):
            op._op_fusion_run_lua({"item_id": item, "code": "return 1"})
        with self.assertRaisesRegex(OperationError, "Nothing was changed"):
            op._op_fusion_apply_setting({"item_id": item, "setting": "{ Tools = ordered() { G = Glow {} } }"})

    def test_build_and_keyframes_fall_back_to_node_handles(self):
        op, comp, item = make(user_nodes=("Glow",), transport="lua")
        comp.fail_lua = True
        result = op._op_fusion_build(dict(FADE, item_id=item))
        self.assertIn("direct node calls", result["route"])
        self.assertTrue(result["verified"])
        self.assertIs(comp.FindTool("Fade_Over").inputs["Background"].source.tool, comp.FindTool("Fade_Black"))
        keyed = op._op_fusion_keyframe_input({"item_id": item, "tool": "Glow1", "input": "Glow",
                                              "keyframes": [{"frame": 0, "value": 0}, {"frame": 4, "value": 1}]})
        self.assertIn("direct node calls", keyed["route"])
        self.assertAlmostEqual(comp.FindTool("Glow1").GetInput("Glow", 4), 1)


PHOTO = '''{ Tools = ordered() {
    Photo = Loader { Clips = { Clip { ID = "Clip1", Filename = "/Users/me/Photo \\"1\\".jpg", GlobalStart = 0 } } },
    Over = Merge { Inputs = { Foreground = Input { SourceOp = "Photo", Source = "Output" } } },
} }'''


class LoaderConversionTests(unittest.TestCase):
    def paste_as(self, comp, tools):
        def execute(script):
            comp.scripts.append(script)
            for name, reg_id in tools:
                comp.AddTool(reg_id).Name = name
            key, _, value = script.rsplit("comp:SetData(", 1)[-1].partition(", ")
            comp.SetData(key.strip('"'), value.rstrip(")").strip('"'))
        comp.Execute = execute

    def test_parser_tells_file_loaders_from_exported_media_ins(self):
        tools = setting_tools(PHOTO)
        self.assertEqual([(t["name"], t["reg_id"]) for t in tools], [("Photo", "Loader"), ("Over", "Merge")])
        self.assertEqual(tools[0]["file"], '/Users/me/Photo \\"1\\".jpg')
        exported = setting_tools('Composition { Tools = { MediaIn1 = Loader { CustomData = { MediaProps = '
                                 '{ MEDIA_PATH = "/x.mp4" } }, Clips = { Clip { Filename = "/x.mp4" } } } } }')
        self.assertTrue(exported[0]["media_in"])
        self.assertEqual(setting_tools("not a setting"), [])

    def test_paste_reports_a_converted_loader(self):
        op, comp, item = make(transport="lua")
        self.paste_as(comp, [("Photo", "MediaIn"), ("Over", "Merge")])
        result = op._op_fusion_apply_setting({"item_id": item, "setting": PHOTO})
        self.assertEqual(result["converted_tools"][0]["actual"], "MediaIn")
        self.assertIn("Photo", result["warning"])

    def test_kept_loader_is_not_reported(self):
        op, comp, item = make(transport="lua")
        self.paste_as(comp, [("Photo", "Loader"), ("Over", "Merge")])
        result = op._op_fusion_apply_setting({"item_id": item, "setting": PHOTO})
        self.assertNotIn("warning", result)
        self.assertEqual(fusion_conversions(setting_tools(PHOTO), comp), [])


class ApiReferenceTests(unittest.TestCase):
    def test_lua_arrays_and_frame_keyed_dicts_index_correctly(self):
        ref = ResolveOperations._api_ref
        lua_list = {1: "first", 2: "second", 3: "third"}
        self.assertEqual([ref("$0[%d]" % i, [lua_list]) for i in range(3)], ["first", "second", "third"])
        self.assertEqual(ref("$0[1]", [{"1": "a", "2": "b"}]), "b")
        markers = {0: "at 0", 24: "at 24"}  # GetMarkers: keyed by frame, not 1..n
        self.assertEqual(ref("$0[24]", [markers]), "at 24")
        self.assertEqual(ref("$0[0]", [markers]), "at 0")
        with self.assertRaises(OperationError):
            ref("$0[3]", [lua_list])


class MarkerClip(Clip):
    """A clip with item markers and linked items, which the plain fake lacks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.item_markers, self.linked = {}, []

    def GetMarkers(self):
        return dict(self.item_markers)

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.item_markers[frame] = dict(color=color, name=name, note=note, duration=duration, customData=custom)
        return True

    def GetLinkedItems(self):
        return list(self.linked)


class CarryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "talk.mov"
        path.write_bytes(b"x")
        self.media = Media(path)
        self.media.props["Frames"] = "1000"
        self.video = MarkerClip(0, 200, self.media, 100, name="talk", kind="video", track=1)
        self.audio = MarkerClip(0, 200, self.media, 100, name="talk", kind="audio", track=1)
        self.video.linked, self.audio.linked = [self.audio], [self.video]
        self.audio.props["AudioVolume"] = -6.0
        self.video.item_markers[10] = dict(color="Green", name="keep", note="", duration=1, customData="")
        self.project = Project([self.video, self.audio])
        self.op = ResolveOperations(lambda: Resolve(self.project))
        self.links = []
        patcher = mock.patch.object(fakes.Timeline, "SetClipsLinked",
                                    lambda timeline, items, on: self.links.append(len(items)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        pool = self.project.pool

        def append(requests):
            out = []
            for r in requests:
                piece = MarkerClip(r["recordFrame"], r["recordFrame"] + r["endFrame"] - r["startFrame"],
                                   r["mediaPoolItem"], r["startFrame"],
                                   kind="video" if r["mediaType"] == 1 else "audio", track=r["trackIndex"])
                self.project.current.clips.append(piece)
                out.append(piece)
            return out
        pool.AppendToTimeline = append

    def test_remove_ranges_carries_properties_markers_and_links(self):
        out = self.op._op_remove_ranges({"ranges": [{"start_frame": 50, "end_frame": 60}]})
        copy = self.project.current
        audio = sorted((c for c in copy.clips if c.kind == "audio"), key=lambda c: c.start)
        video = sorted((c for c in copy.clips if c.kind == "video"), key=lambda c: c.start)
        self.assertEqual([c.props.get("AudioVolume") for c in audio], [-6.0, -6.0])
        self.assertEqual(video[0].item_markers.keys(), {10})  # same source in: marker frame is unambiguous
        self.assertEqual(video[1].item_markers, {})  # moved head: convention unverified, so not guessed
        self.assertTrue(any("AudioVolume" in line for line in out["carried"]))
        self.assertTrue(any("link group" in line for line in out["carried"]))
        self.assertEqual(self.links, [2, 2])
        self.assertTrue(any("transitions" in line for line in out["not_carried"]))
        self.assertFalse(any("marker" in line for line in out["not_carried"]))  # the only marker sat in piece 1

    def test_property_resolve_refuses_is_reported(self):
        class Stubborn(MarkerClip):
            def SetProperty(self, key, value):
                return key != "AudioVolume" and super().SetProperty(key, value)
        pool_append = self.project.pool.AppendToTimeline

        def append(requests):
            pieces = pool_append(requests)
            for piece in pieces:
                piece.__class__ = Stubborn
            return pieces
        self.project.pool.AppendToTimeline = append
        out = self.op._op_remove_ranges({"ranges": [{"start_frame": 50, "end_frame": 60}]})
        self.assertTrue(any("AudioVolume" in line for line in out["not_carried"]))
        self.assertTrue(any("AudioVolume" in line for line in out["warnings"]))

    def test_trim_edit_reports_carried_and_not_carried(self):
        out = self.op._op_trim_edit({"item_id": self.video.uid, "edge": "tail", "frames": -20, "ripple": True})
        self.assertTrue(out["verified"], out.get("verification"))
        pieces = [c for c in self.project.current.clips if c.kind == "audio"]
        self.assertEqual([c.props.get("AudioVolume") for c in pieces], [-6.0])
        video = next(c for c in self.project.current.clips if c.kind == "video")
        self.assertEqual(video.item_markers.keys(), {10})
        self.assertIn("not_carried", out)


class LuaReliabilityTests(unittest.TestCase):
    """Stale rejections, reconnects and parallel calls on the Lua route (worker RPCs mocked)."""

    def setUp(self):
        from bridge import client, lifecycle, lua_transport
        self.client, self.lua = client, lua_transport
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / "token.txt").write_text("rab_" + "test-only-token-" * 4)
        lifecycle.atomic_json(self.home / "lua-outbox.json", {})
        self.context = {"project_id": "p", "timeline_id": "t"}
        self.ops = mock.Mock()
        self.ops.queue_context.side_effect = lambda: dict(self.context)
        self.ops.dispatch.return_value = {"done": True}
        self.session = "s1"
        self.requests = []
        test = self

        def health(connection):
            connection.session = test.session
            return {"connected": True, "session": test.session}

        def request(connection, operation, arguments=None):
            test.requests.append(operation)
            return test.reply(operation)
        self.reply = lambda operation: None
        for patcher in (mock.patch.object(client, "HOME", self.home), mock.patch.object(lua_transport, "_observed", None),
                        mock.patch.object(lua_transport, "_last_cleanup", (self.home, 1e12)),
                        mock.patch.object(lua_transport.LuaClient, "health", health),
                        mock.patch.object(lua_transport.LuaClient, "request", request),
                        mock.patch.object(lua_transport, "ResolveOperations", return_value=self.ops)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_worker_restart_with_the_same_timeline_reconnects(self):
        self.lua.call("status")
        self.session = "s2"  # Stop/Start AI Bridge (Lua): a new session, same project and timeline open.
        result = self.lua.call("set_clip_color", {"item_id": "x", "color": "Blue"})
        self.assertTrue(result["done"])
        self.assertIn("Reconnected", result["transport_note"])
        self.ops.dispatch.assert_called_with("set_clip_color", {"item_id": "x", "color": "Blue"})

    def test_switched_timeline_is_refused_with_both_identities(self):
        self.lua.call("status")
        self.context["timeline_id"] = "other"
        with self.assertRaisesRegex(self.client.BridgeCallError, "was project p, timeline t, now project p, timeline other"):
            self.lua.call("set_clip_color", {"item_id": "x"})
        self.assertEqual(self.ops.dispatch.call_count, 1)  # only the status call ran

    def test_no_observation_is_still_refused(self):
        with self.assertRaisesRegex(self.client.BridgeCallError, "before editing"):
            self.lua.call("set_clip_color", {"item_id": "x"})
        self.ops.dispatch.assert_not_called()

    def test_refusal_before_dispatch_is_retried_once(self):
        attempts = []

        def reply(operation):
            if operation == "begin_operation":
                attempts.append(1)
                if len(attempts) == 1:
                    raise self.lua._Refused("Lua RPC refused: {'code': 'stale_request'}.", "stale_request")
        self.reply = reply
        self.lua.call("status")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.ops.dispatch.call_count, 1)
        self.assertEqual(self.requests.count("finish_operation"), 2)  # the refused lease was released too

    def test_repeated_refusal_reports_nothing_dispatched_and_keeps_the_observation(self):
        self.lua.call("status")
        observed = self.lua._observed

        def reply(operation):
            if operation == "begin_operation":
                raise self.lua._Refused("Lua RPC refused: {'code': 'expired'}.", "expired")
        self.reply = reply
        with self.assertRaisesRegex(self.client.BridgeCallError, "no new operation started"):
            self.lua.call("set_clip_color", {"item_id": "x"})
        self.assertEqual(self.lua._observed, observed)

    def test_failed_read_keeps_the_observation_but_an_uncertain_edit_clears_it(self):
        self.lua.call("status")
        observed = self.lua._observed
        self.ops.dispatch.side_effect = self.lua._ReplyFailure("reply not received")
        with self.assertRaises(self.client.BridgeCallError):
            self.lua.call("inspect_fusion", {})
        self.assertEqual(self.lua._observed, observed)
        with self.assertRaisesRegex(self.client.BridgeCallError, "No automatic replay"):
            self.lua.call("set_clip_color", {"item_id": "x"})
        self.assertIsNone(self.lua._observed)

    def test_lock_wait_does_not_consume_the_operation_deadline(self):
        from bridge import lifecycle
        deadlines = []
        real_acquire = lifecycle.WorkerLock.acquire
        tries = []

        def acquire(lock):
            tries.append(1)
            return len(tries) > 3 and real_acquire(lock)

        def reply(operation):
            if operation == "begin_operation":
                deadlines.append(1)
        self.reply = reply
        with mock.patch.object(lifecycle.WorkerLock, "acquire", acquire):
            self.lua.call("status", timeout=0.1)  # 3 x 50 ms of waiting > the 0.1 s operation timeout
        self.assertEqual(deadlines, [1])
        with mock.patch.object(lifecycle.WorkerLock, "acquire", lambda lock: False), \
                mock.patch.dict("os.environ", {"RESOLVE_AI_BRIDGE_LOCK_WAIT_SECONDS": "0"}):
            with self.assertRaisesRegex(self.client.BridgeTimeout, "no new operation started"):
                self.lua.call("status", timeout=0.1)


class TransportQueueTests(unittest.TestCase):
    def test_parallel_calls_in_one_process_run_one_at_a_time(self):
        import threading
        from bridge import transport
        active, peak, lock = [0], [0], threading.Lock()

        def slow(operation, params, timeout=30):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            import time
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"op": operation}
        with mock.patch.object(transport, "mode", return_value="lua"), \
                mock.patch.object(transport.lua_transport, "probe", return_value={"connected": True}), \
                mock.patch.object(transport.lua_transport, "call", side_effect=slow):
            threads = [threading.Thread(target=transport.call, args=("status", {})) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(peak[0], 1)

    def test_briefly_silent_worker_is_waited_for(self):
        from bridge import transport
        beats = iter([{"connected": False, "reason": "stale"}, {"connected": False}, {"connected": True}])
        with mock.patch.object(transport, "mode", return_value="lua"), \
                mock.patch.dict(transport._state, {"lua_seen": __import__("time").monotonic()}), \
                mock.patch.object(transport.time, "sleep"), \
                mock.patch.object(transport.lua_transport, "probe", side_effect=lambda: next(beats)), \
                mock.patch.object(transport.lua_transport, "call", return_value={"ok": 1}):
            self.assertEqual(transport.call("status", {}), ({"ok": 1}, "lua"))

    def test_never_seen_worker_is_not_waited_for(self):
        from bridge import transport
        with mock.patch.object(transport, "mode", return_value="lua"), \
                mock.patch.dict(transport._state, {"lua_seen": 0.0}), \
                mock.patch.object(transport.lua_transport, "probe", return_value={"connected": False}) as probe:
            with self.assertRaises(transport.client.BridgeOffline):
                transport.call("set_clip_color", {})
        self.assertEqual(probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
