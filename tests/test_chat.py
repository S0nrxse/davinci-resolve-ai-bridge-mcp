"""Chat panel server: security guards, thread flow and the Claude Code approval round trip.

A fake ``claude`` executable speaks the stream-json control protocol, so these
tests need neither a signed-in Claude Code nor DaVinci Resolve.
"""

import importlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = textwrap.dedent('''\
    #!%(python)s
    import itertools, json, os, sys
    with open(os.path.join(os.path.dirname(__file__), "argv.log"), "a") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
    SESSION = "sess-fork" if "--fork-session" in sys.argv else "sess-1"
    COUNTER = itertools.count(1)
    TURNS = itertools.count(1)
    def out(o):
        if o.get("type") in ("assistant", "user"):
            o["uuid"] = "m%%d" %% next(COUNTER)
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("type") == "control_request" and msg["request"]["subtype"] == "initialize":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}}})
        elif msg.get("type") == "user":
            prompt = msg["message"]["content"]
            if isinstance(prompt, str) and prompt.startswith("/compact"):
                with open(os.path.join(os.path.dirname(__file__), "compact.log"), "a") as log:
                    log.write(prompt + "\\n")
                out({"type": "system", "subtype": "status", "status": "compacting"})
                out({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual", "pre_tokens": 37033, "post_tokens": 9399}})
                out({"type": "user", "message": {"role": "user", "content": "This session is being continued..."}, "isReplay": True})
                out({"type": "result", "subtype": "success", "session_id": SESSION, "result": ""})
                continue
            tid = "tu%%d" %% next(TURNS)
            out({"type": "system", "subtype": "init", "session_id": SESSION, "model": "fake",
                 "mcp_servers": [{"name": "resolve-ai-bridge", "status": "connected"}]})
            out({"type": "stream_event", "event": {"type": "message_start"}})
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Plan the "}}})
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "glitch."}}})
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Building "}}})
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "it."}}})
            tool_input = {"item_id": "playhead", "name": "Glitch", "nodes": [{"name": "Shake", "type": "Transform", "inputs": {}}], "chain": ["Shake"]}
            out({"type": "assistant", "message": {"model": "fake", "usage": {"input_tokens": 2, "cache_read_input_tokens": 40000, "cache_creation_input_tokens": 500, "output_tokens": 100}, "content": [
                {"type": "tool_use", "id": tid, "name": "mcp__resolve-ai-bridge__fusion_build", "input": tool_input}]}})
            out({"type": "control_request", "request_id": "req-1", "request": {
                "subtype": "can_use_tool", "tool_name": "mcp__resolve-ai-bridge__fusion_build", "input": tool_input, "tool_use_id": tid}})
            steered = []
            while True:  # Steering messages may arrive before the approval.
                incoming = json.loads(sys.stdin.readline())
                if incoming.get("type") == "control_response":
                    break
                if incoming.get("type") == "user":
                    steered.append(incoming["message"]["content"])
            behavior = incoming["response"]["response"]["behavior"]
            ok = behavior == "allow"
            out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": not ok,
                "content": [{"type": "text", "text": json.dumps({"ok": ok, "context_seen": "<resolve_context>" in json.dumps(prompt),
                    "image_seen": isinstance(prompt, list) and any(b.get("type") == "image" for b in prompt),
                    "steered": steered})}]}]}})
            out({"type": "result", "subtype": "success", "session_id": SESSION, "total_cost_usd": 0.01, "duration_ms": 5,
                 "modelUsage": {"fake": {"outputTokens": 100, "contextWindow": 200000}}})
''')


class ChatServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(FAKE_CLAUDE % {"python": sys.executable})
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cls.env_backup = dict(os.environ)
        os.environ["RESOLVE_AI_BRIDGE_HOME"] = str(base / "home")
        os.environ["RESOLVE_AI_BRIDGE_MODE"] = "lua"
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        # Always test this checkout's chat package, never an installed runtime another test put on sys.path.
        sys.path.insert(0, str(ROOT))
        for name in [m for m in sys.modules if m == "chat" or m.startswith("chat.")]:
            del sys.modules[name]
        cls.server_mod = importlib.import_module("chat.server")
        from http.server import ThreadingHTTPServer
        cls.app = cls.server_mod.App()
        cls.app.settings["checkpoints"] = False  # Enabled per test, with a stand-in bridge.
        cls.argv_log = bin_dir / "argv.log"
        cls.server_mod.Handler.app = cls.app
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server_mod.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for thread in cls.app.threads.values():
            if thread.agent:
                thread.agent.close()
        os.environ.clear()
        os.environ.update(cls.env_backup)
        cls.tmp.cleanup()

    def call(self, method, path, body=None, token=True, host=None, origin=None):
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
        if token:
            headers["X-Chat-Token"] = self.app.token
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request("http://127.0.0.1:%d/api/%s" % (self.port, path), data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def wait_for(self, thread_id, predicate, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, data = self.call("GET", "threads/" + thread_id)
            if predicate(data):
                return data
            time.sleep(0.05)
        self.fail("Timed out; last events: %s" % [e["type"] for e in data.get("events", [])])

    def test_requires_token(self):
        status, _ = self.call("GET", "state", token=False)
        self.assertEqual(status, 401)

    def test_rejects_foreign_host_and_origin(self):
        self.assertEqual(self.call("GET", "state", host="evil.example:%d" % self.port)[0], 403)
        self.assertEqual(self.call("GET", "state", origin="http://evil.example")[0], 403)
        self.assertEqual(self.call("GET", "state", origin="http://127.0.0.1:%d" % self.port)[0], 200)

    def test_state_lists_agents(self):
        status, state = self.call("GET", "state")
        self.assertEqual(status, 200)
        claude = next(a for a in state["agents"] if a["id"] == "claude")
        self.assertTrue(claude["available"])
        self.assertTrue(claude["approvals"])

    def run_turn(self, decision, attachments=(), steer=None):
        self.call("POST", "settings", {"mode": "ask", "attach_context": True})
        _, thread = self.call("POST", "threads", {"agent": "claude"})
        status, _ = self.call("POST", "threads/%s/messages" % thread["id"],
                              {"text": "Glitch into this clip", "attachments": list(attachments)})
        self.assertEqual(status, 200)
        data = self.wait_for(thread["id"], lambda d: any(e["type"] == "permission" for e in d["events"]))
        if steer:
            status, _ = self.call("POST", "threads/%s/messages" % thread["id"], {"text": steer})
            self.assertEqual(status, 200)
        request = next(e for e in data["events"] if e["type"] == "permission")["request"]
        status, result = self.call("POST", "threads/%s/permissions/%s" % (thread["id"], request), {"decision": decision})
        self.assertEqual((status, result["ok"]), (200, True))
        return self.wait_for(thread["id"], lambda d: any(e["type"] == "turn_end" for e in d["events"]))

    def next_turn(self, thread_id, text, decision="allow"):
        before = self.call("GET", "threads/" + thread_id)[1]
        turns = sum(1 for e in before["events"] if e["type"] == "turn_end")
        asks = sum(1 for e in before["events"] if e["type"] == "permission")
        self.assertEqual(self.call("POST", "threads/%s/messages" % thread_id, {"text": text})[0], 200)
        data = self.wait_for(thread_id, lambda d: sum(1 for e in d["events"] if e["type"] == "permission") > asks)
        request = [e for e in data["events"] if e["type"] == "permission"][-1]["request"]
        self.call("POST", "threads/%s/permissions/%s" % (thread_id, request), {"decision": decision})
        return self.wait_for(thread_id, lambda d: sum(1 for e in d["events"] if e["type"] == "turn_end") > turns)

    def test_rewind_cuts_the_chat_and_forks_the_session(self):
        data = self.run_turn("allow")
        data = self.next_turn(data["id"], "Make it longer")
        second = [e for e in data["events"] if e["type"] == "user"][1]
        self.assertEqual(second["anchor"], {"session": "sess-1", "uuid": "m2"})  # end of turn one
        status, preview = self.call("POST", "threads/%s/rewind" % data["id"], {"message": second["id"], "preview": True})
        self.assertEqual((status, preview["messages"], preview["project_edits"]), (200, 1, 1))
        status, out = self.call("POST", "threads/%s/rewind" % data["id"], {"message": second["id"]})
        self.assertEqual((status, out["text"], out["project_restored"]), (200, "Make it longer", False))
        after = self.call("GET", "threads/" + data["id"])[1]
        self.assertEqual([e["text"] for e in after["events"] if e["type"] == "user"], ["Glitch into this clip"])
        self.assertEqual((after["session"], after["resume_at"]), ("sess-1", "m2"))
        after = self.next_turn(data["id"], "Make it shorter")
        argv = json.loads(self.argv_log.read_text().splitlines()[-1])
        self.assertEqual(argv[argv.index("--resume"):][:5], ["--resume", "sess-1", "--resume-session-at", "m2", "--fork-session"])
        self.assertEqual((after["session"], after["resume_at"]), ("sess-fork", None))

    def test_rewind_to_the_first_message_starts_fresh(self):
        data = self.run_turn("allow")
        first = next(e for e in data["events"] if e["type"] == "user")
        self.assertEqual(self.call("POST", "threads/%s/rewind" % data["id"], {"message": first["id"]})[0], 200)
        after = self.call("GET", "threads/" + data["id"])[1]
        self.assertEqual((after["events"], after["session"], after["resume_at"]), ([], None, None))
        self.assertEqual(self.call("POST", "threads/%s/rewind" % data["id"], {"message": first["id"]})[0], 400)

    def bridge_stub(self, fail_checkpoint=False):
        calls = []

        def call(operation, params=None, timeout=None):
            calls.append((operation, params))
            if operation == "status":
                return {"online": True}, "lua"
            if operation == "checkpoint_timeline":
                if fail_checkpoint:
                    raise RuntimeError("bridge offline")
                return {"checkpoint": {"id": "cp1", "name": "Checkpoint · Main · x"}, "timeline": {"id": "t1", "name": "Main"},
                        "bin": "AI Checkpoints"}, "lua"
            if operation == "restore_checkpoint":
                return {"restored": {"id": "r1", "name": "Main (rewound)"}, "previous": {"id": "t1", "name": "Main"}}, "lua"
            raise RuntimeError("not stubbed: %s" % operation)
        return calls, call

    def test_checkpoint_before_first_edit_and_restore_on_rewind(self):
        from unittest import mock
        calls, stub = self.bridge_stub()
        self.app.settings["checkpoints"] = True
        try:
            with mock.patch("bridge.transport.call", side_effect=stub):
                data = self.run_turn("allow")
                user = next(e for e in data["events"] if e["type"] == "user")
                self.assertEqual(user["checkpoint"]["id"], "cp1")
                self.assertTrue(any(e["type"] == "checkpoint" for e in data["events"]))
                label = next(p for op, p in calls if op == "checkpoint_timeline")["label"]
                self.assertIn("Glitch into this clip", label)
                _, preview = self.call("POST", "threads/%s/rewind" % data["id"], {"message": user["id"], "preview": True})
                self.assertEqual(preview["checkpoint"]["id"], "cp1")
                status, out = self.call("POST", "threads/%s/rewind" % data["id"],
                                        {"message": user["id"], "restore_project": True})
                self.assertEqual((status, out["project_restored"]), (200, True))
                self.assertIn(("restore_checkpoint", {"checkpoint_id": "cp1"}), calls)
                calls.clear()
                self.run_turn("deny")  # Declined edits never take a checkpoint.
                self.assertNotIn("checkpoint_timeline", [op for op, _ in calls])
        finally:
            self.app.settings["checkpoints"] = False

    def test_failed_checkpoint_warns_but_does_not_block_the_edit(self):
        from unittest import mock
        _, stub = self.bridge_stub(fail_checkpoint=True)
        self.app.settings["checkpoints"] = True
        try:
            with mock.patch("bridge.transport.call", side_effect=stub):
                data = self.run_turn("allow")
        finally:
            self.app.settings["checkpoints"] = False
        self.assertTrue(next(e for e in data["events"] if e["type"] == "tool_result")["ok"])
        notice = next(e for e in data["events"] if e["type"] == "notice")
        self.assertIn("Rewind can't restore the project", notice["text"])

    def test_hand_edits_between_turns_are_reported(self):
        from unittest import mock
        layouts = [[("a", "cam.mov", "V1", 0, 48, 0)], [("a", "cam.mov", "V1", 0, 40, 0)]]
        state = {"n": 0}

        def call(operation, params=None, timeout=None):
            if operation != "timeline_fingerprint":
                raise RuntimeError("offline")
            items = [dict(zip(("id", "name", "track", "start", "end", "offset"), i)) for i in layouts[state["n"]]]
            return {"project": "P", "timeline": {"id": "t", "name": "Main"}, "fps": 24, "start_frame": 0,
                    "timelines": [{"id": "t", "name": "Main"}], "markers": {}, "items": items}, "lua"
        with mock.patch("bridge.transport.call", side_effect=call):
            data = self.run_turn("allow")
            thread = self.app.threads[data["id"]]
            thread.baseline_job.join(5)
            state["n"] = 1  # The user trims the clip by hand in Resolve.
            data = self.next_turn(data["id"], "Now add a fade")
        second = [e for e in data["events"] if e["type"] == "user"][1]
        self.assertEqual(second["changes"], ["Trimmed “cam.mov” on V1 (tail -8 frames)."])
        first = [e for e in data["events"] if e["type"] == "user"][0]
        self.assertIsNone(first.get("changes"))

    def test_seek_by_timecode_and_clip_name(self):
        from unittest import mock
        calls = []

        def call(operation, params=None, timeout=None):
            calls.append((operation, params))
            if operation == "timeline_fingerprint":
                return {"fps": 24, "drop_frame": False, "items": [
                    {"id": "x", "name": "Interview_A_cam2.mov", "track": "V2", "start": 86448, "end": 86500}]}, "lua"
            return {}, "lua"
        with mock.patch("bridge.transport.call", side_effect=call):
            status, out = self.call("POST", "seek", {"timecode": "01:00:12:04"})
            self.assertEqual((status, out["timecode"]), (200, "01:00:12:04"))
            status, out = self.call("POST", "seek", {"clip": "interview_a_cam2.mov"})
            self.assertEqual((status, out["timecode"], out["track"]), (200, "01:00:02:00", "V2"))
            self.assertEqual(self.call("POST", "seek", {"timecode": "12:04; rm -rf"})[0], 400)
            self.assertEqual(self.call("POST", "seek", {"clip": "missing.mov"})[0], 400)
        self.assertIn(("set_playhead", {"timecode": "01:00:02:00"}), calls)
        self.assertEqual(calls[0][0], "status")  # handshake before moving the playhead

    def test_reopened_chats_get_image_previews(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        _, att = self.call("POST", "attachments", {"name": "still.png", "type": "image/png", "data": png})
        _, thread = self.call("POST", "threads", {"agent": "claude"})
        legacy = {"type": "user", "id": "old1", "text": "look", "attachments": [
            {k: att[k] for k in ("id", "name", "type", "size")}, {"id": "gone", "name": "x.png", "type": "image/png"}]}
        self.app.threads[thread["id"]].data["events"].append(legacy)
        _, data = self.call("GET", "threads/" + thread["id"])
        atts = data["events"][-1]["attachments"]
        self.assertTrue(atts[0]["thumb_id"].startswith("f-"))
        self.assertNotIn("thumb_id", atts[1])  # deleted files stay as plain chips
        self.assertNotIn("thumb_id", self.app.threads[thread["id"]].data["events"][-1]["attachments"][0])  # not rewritten

    def test_approved_turn_round_trip(self):
        data = self.run_turn("allow")
        types = [e["type"] for e in data["events"]]
        for expected in ("user", "session", "assistant_start", "text", "tool_start", "permission",
                         "permission_resolved", "tool_result", "turn_end"):
            self.assertIn(expected, types)
        text = next(e for e in data["events"] if e["type"] == "text")
        self.assertEqual(text["text"], "Building it.")
        result = next(e for e in data["events"] if e["type"] == "tool_result")
        self.assertTrue(result["ok"])
        self.assertIn('"context_seen": true', result["summary"])
        self.assertEqual(data["session"], "sess-1")
        self.assertEqual(data["title"], "Glitch into this clip")
        saved = json.loads((self.server_mod.THREADS / ("%s.json" % data["id"])).read_text())
        self.assertEqual(len(saved["events"]), len(data["events"]))

    def test_thinking_is_streamed_and_coalesced(self):
        data = self.run_turn("allow")
        thoughts = [e for e in data["events"] if e["type"] == "thought"]
        self.assertEqual([t["text"] for t in thoughts], ["Plan the glitch."])
        order = [e["type"] for e in data["events"] if e["type"] in ("thought", "text", "tool_start")]
        self.assertEqual(order[:2], ["thought", "text"])

    def test_steering_mid_turn(self):
        data = self.run_turn("allow", steer="Make it subtler")
        users = [e for e in data["events"] if e["type"] == "user"]
        self.assertEqual([u.get("steer") for u in users], [False, True])
        result = next(e for e in data["events"] if e["type"] == "tool_result")
        self.assertIn("Make it subtler", result["summary"])

    def test_image_attachment_is_sent_inline(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        status, att = self.call("POST", "attachments", {"name": "../evil name.png", "type": "image/png", "data": png})
        self.assertEqual(status, 200)
        self.assertEqual(att["name"], "evil name.png")
        self.assertTrue(att["path"].startswith(str(self.server_mod.ATTACHMENTS)))
        data = self.run_turn("allow", attachments=[att["id"]])
        user = next(e for e in data["events"] if e["type"] == "user")
        self.assertEqual(user["attachments"][0]["id"], att["id"])
        result = next(e for e in data["events"] if e["type"] == "tool_result")
        self.assertIn('"image_seen": true', result["summary"])

    def test_skills_listing_and_dollar_expansion(self):
        skills_dir = Path(self.tmp.name) / "skills" / "my-cut"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: my-cut\ndescription: Cuts things.\n---\nSteps")
        original = self.server_mod.SKILL_DIRS
        self.server_mod.SKILL_DIRS = (Path(self.tmp.name) / "skills",)
        try:
            status, data = self.call("GET", "skills")
            names = {i["name"]: i for i in data["items"]}
            self.assertEqual(status, 200)
            self.assertEqual(names["my-cut"]["description"], "Cuts things.")
            self.assertIn("talking-head-cleanup", names)  # bundled DaVinci skill
            expanded = self.app._expand_skills("please $my-cut this and pay $5")
            self.assertTrue(expanded.startswith("Use the my-cut skill"))
            self.assertNotIn("Use the 5", expanded)
        finally:
            self.server_mod.SKILL_DIRS = original

    def test_path_attachment_references_without_copying(self):
        big = Path(self.tmp.name) / "clip.mov"
        big.write_bytes(b"0" * 2048)
        status, att = self.call("POST", "attachments/path", {"path": str(big)})
        self.assertEqual(status, 200)
        self.assertEqual(att["path"], str(big))
        found = self.app.find_attachment(att["id"])
        self.assertEqual((found["path"], found["size"]), (str(big), 2048))
        self.assertEqual(self.call("POST", "attachments/path", {"path": "/nope/x.mov"})[0], 400)
        self.assertTrue(att["thumb_id"].startswith("f-"))  # videos get a poster for the sent-message chip
        self.assertEqual(self.app.media_paths[att["thumb_id"]], str(big))

    def test_file_search_includes_recent_attachments(self):
        big = Path(self.tmp.name) / "interview_take2.mov"
        big.write_bytes(b"1")
        self.call("POST", "attachments/path", {"path": str(big)})
        status, data = self.call("GET", "files?q=take2")
        self.assertEqual(status, 200)
        self.assertEqual(data["items"][0]["kind"], "recent")

    def test_file_search_offers_previews_only_for_found_media(self):
        clip = Path(self.tmp.name) / "preview_me.mov"
        note = Path(self.tmp.name) / "preview_me.txt"
        clip.write_bytes(b"1")
        note.write_bytes(b"1")
        for path in (clip, note):
            self.call("POST", "attachments/path", {"path": str(path)})
        _, data = self.call("GET", "files?q=preview_me")
        by_name = {item["name"]: item for item in data["items"]}
        self.assertTrue(by_name["preview_me.mov"]["thumb_id"].startswith("f-"))
        self.assertNotIn("thumb_id", by_name["preview_me.txt"])
        self.assertEqual(self.app.media_paths[by_name["preview_me.mov"]["thumb_id"]], str(clip))
        self.assertEqual(self.call("GET", "thumb?id=f-0000000000000000")[0], 404)  # unlisted ids can't be read
        self.assertNotIn("Media pool", [i["detail"] for i in data["items"]])  # file previews aren't pool clips

    def test_shortcuts_are_saved_and_filtered(self):
        status, settings = self.call("POST", "settings", {"shortcuts": {"send": "Meta+Enter", "bogus": "X"}})
        self.assertEqual(settings["shortcuts"], {"send": "Meta+Enter"})

    def test_unknown_attachment_ids_are_ignored(self):
        self.assertIsNone(self.app.find_attachment("../../etc"))

    def test_denied_turn_reports_failure(self):
        data = self.run_turn("deny")
        result = next(e for e in data["events"] if e["type"] == "tool_result")
        self.assertFalse(result["ok"])
        resolved = next(e for e in data["events"] if e["type"] == "permission_resolved")
        self.assertEqual(resolved["decision"], "deny")

    def test_read_only_tools_skip_approval(self):
        from chat.agents import RESOLVE_PREFIX, ClaudeCodeAgent
        agent = ClaudeCodeAgent(lambda e: None, ".", {"command": "x"}, "", {"mode": "ask"})
        self.assertTrue(agent._auto_allow(RESOLVE_PREFIX + "timeline_overview"))
        self.assertTrue(agent._auto_allow(RESOLVE_PREFIX + "current_context"))
        self.assertFalse(agent._auto_allow(RESOLVE_PREFIX + "fusion_build"))
        self.assertFalse(agent._auto_allow("Bash"))
        agent.settings["mode"] = "auto"
        self.assertTrue(agent._auto_allow("Bash"))

    def test_context_usage_and_compact(self):
        data = self.run_turn("allow")
        thread_id = data["id"]
        usage = self.wait_for(thread_id, lambda d: d.get("context_usage"))["context_usage"]
        self.assertEqual((usage["used"], usage["window"]), (40602, 200000))
        self.assertNotIn("context_usage", [e["type"] for e in data["events"]])  # Not part of the transcript.
        status, _ = self.call("POST", "threads/%s/compact" % thread_id, {"instructions": "keep  the cut list"})
        self.assertEqual(status, 200)
        data = self.wait_for(thread_id, lambda d: sum(1 for e in d["events"] if e["type"] == "turn_end") == 2)
        compacted = next(e for e in data["events"] if e["type"] == "compacted")
        self.assertEqual((compacted["trigger"], compacted["pre_tokens"], compacted["post_tokens"]), ("manual", 37033, 9399))
        self.assertEqual(data["context_usage"]["used"], 9399)
        self.assertFalse([e for e in data["events"] if e["type"] == "user" and "continued" in str(e.get("text"))])
        # Typed as a command it compacts too, and is never sent with the Resolve context around it.
        self.assertEqual(self.call("POST", "threads/%s/messages" % thread_id, {"text": "/compact"})[0], 200)
        self.wait_for(thread_id, lambda d: sum(1 for e in d["events"] if e["type"] == "compacted") == 2)
        log = (self.argv_log.parent / "compact.log").read_text().splitlines()
        self.assertEqual(log[-2:], ["/compact keep the cut list", "/compact"])
        status, state = self.call("GET", "state")
        self.assertTrue(next(a for a in state["agents"] if a["id"] == "claude")["compact"])

    def test_compact_needs_a_session(self):
        _, thread = self.call("POST", "threads", {"agent": "claude"})
        status, body = self.call("POST", "threads/%s/compact" % thread["id"], {})
        self.assertEqual(status, 400)
        self.assertIn("Nothing to compact", body["error"])

    def test_rejects_bad_decision(self):
        _, thread = self.call("POST", "threads", {})
        status, _ = self.call("POST", "threads/%s/permissions/x" % thread["id"], {"decision": "maybe"})
        self.assertEqual(status, 400)



class AgentTaskListTest(unittest.TestCase):
    """The agent's own task list (TaskCreate/TaskUpdate/TaskList) becomes one whole-list ``tasks`` event."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from chat import agents
        self.agents = agents
        self.events = []
        self.agent = agents.ClaudeCodeAgent(self.events.append, ROOT, {}, "", {"mode": "ask"})

    def feed(self, tool_id, name, tool_input, result, ok=True):
        self.agent._handle({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}})
        self.agent._handle({"type": "user", "tool_use_result": result, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "is_error": not ok, "content": "x"}]}})

    def lists(self):
        return [e["tasks"] for e in self.events if e["type"] == "tasks"]

    def test_create_update_list_and_delete(self):
        self.feed("t1", "TaskCreate", {"subject": "Trim intro", "activeForm": "Trimming the intro"},
                  {"task": {"id": "1", "subject": "Trim intro"}})
        self.feed("t2", "TaskCreate", {"subject": "Add subtitles"}, {"task": {"id": "2", "subject": "Add subtitles"}})
        self.feed("t3", "TaskUpdate", {"taskId": "1", "status": "in_progress"}, {"success": True})
        self.assertEqual(self.lists()[-1], [
            {"id": "1", "subject": "Trim intro", "status": "in_progress", "active_form": "Trimming the intro"},
            {"id": "2", "subject": "Add subtitles", "status": "pending"}])
        self.feed("t4", "TaskUpdate", {"taskId": "1", "status": "completed"}, {"success": True})
        count = len(self.lists())
        self.feed("t5", "TaskList", {}, {"tasks": [{"id": "1", "subject": "Trim intro", "status": "completed"},
                                                    {"id": "2", "subject": "Add subtitles", "status": "pending"}]})
        self.assertEqual(len(self.lists()), count)  # Nothing changed, so no new event.
        self.feed("t6", "TaskUpdate", {"taskId": "2", "status": "deleted"}, {"success": True})
        self.assertEqual([t["id"] for t in self.lists()[-1]], ["1"])

    def test_failed_calls_and_subagent_tasks_are_ignored(self):
        self.feed("t1", "TaskCreate", {"subject": "Nope"}, {"task": {"id": "9"}}, ok=False)
        self.agent._handle({"type": "assistant", "parent_tool_use_id": "sub", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "TaskCreate", "input": {"subject": "Sub"}}]}})
        self.agent._handle({"type": "user", "parent_tool_use_id": "sub", "tool_use_result": {"task": {"id": "3"}},
                            "message": {"content": [{"type": "tool_result", "tool_use_id": "s1", "content": "x"}]}})
        self.assertEqual(self.lists(), [])

    def test_task_tools_never_ask_and_are_enabled(self):
        for tool in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
            self.assertTrue(self.agent._auto_allow(tool))
        self.assertFalse(self.agent.edits_project("TaskCreate"))

    def test_server_seeds_the_latest_list(self):
        from chat import server
        events = [{"type": "tasks", "tasks": [{"id": "1", "subject": "A", "status": "pending"}]},
                  {"type": "text"}, {"type": "tasks", "tasks": [{"id": "1", "subject": "A", "status": "completed"}]}]
        self.assertEqual(server.latest_tasks(events)[0]["status"], "completed")
        self.assertEqual(server.latest_tasks([]), [])


if __name__ == "__main__":
    unittest.main()
