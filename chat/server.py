"""Local HTTP + Server-Sent Events backend for the Resolve AI Chat panel.

    python -m chat.server [--port 0] [--open]

Binds to 127.0.0.1 only. Every API call needs the per-launch token, and the Host
and Origin headers must name this server, so a web page in a browser cannot
drive the agent (DNS rebinding / cross-site requests are refused).
Standard library only.
"""

import argparse
import base64
import hashlib
import mimetypes
import re
import hmac
import json
import os
import queue
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.lifecycle import default_home  # noqa: E402
from chat.agents import AGENTS, AgentBase, find_executable  # noqa: E402
from chat.changes import describe as describe_changes, find_clip, timecode as clip_timecode  # noqa: E402

HOME = default_home().absolute()
CHAT_HOME = HOME / "chat-data"
THREADS = CHAT_HOME / "threads"
SNAPSHOTS = CHAT_HOME / "snapshots"  # Timeline fingerprint per thread, taken when its last turn ended.
WORKSPACE = CHAT_HOME / "workspace"
SETTINGS_FILE = CHAT_HOME / "settings.json"
SERVER_FILE = CHAT_HOME / "server.json"
DIST = Path(__file__).resolve().parent / "dist" / "index.html"
ATTACHMENTS = WORKSPACE / "attachments"
THUMBS = CHAT_HOME / "thumbs"
PREVIEWABLE = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".heic", ".tif",
               ".tiff", ".gif", ".webp", ".exr", ".dpx"}
MAX_BODY = 256 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_INLINE_IMAGE = 4 * 1024 * 1024  # Larger images are passed by path.
PERSISTED_SETTINGS = ("agent", "model", "effort", "mode", "safe_copy", "attach_context", "always_allow", "theme", "shortcuts",
                      "checkpoints", "track_changes")
CHANGES_BUDGET_SECONDS = 8.0  # How long a send may wait to learn what changed by hand since the last turn.
SHORTCUT_ACTIONS = ("send", "new_chat", "allow", "decline", "focus", "stop", "collapse", "attach", "skill")
SKILL_DIRS = (Path.home() / ".claude" / "skills",)  # Shared with Claude Code, so skills work in both.
BUNDLED_SKILLS = Path(__file__).resolve().parent / "skills"
THEMES = ("classic", "midnight", "glacier", "abyss", "iris")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

SYSTEM_PROMPT = """You are an editing assistant living in a chat panel docked beside DaVinci Resolve.
The user is editing right now and talks to you in editor language ("this clip", "the intro", "make it pop").

Tools: the resolve-ai-bridge MCP server controls the open Resolve project (Free or Studio).
- You work across the WHOLE project, not just the open timeline: every timeline, the media pool, Fusion
  comps, color, markers, titles, delivery. Call project_overview to see it all, open_timeline to switch,
  and resolve_api as the escape hatch for anything the scripting API allows that no structured tool covers.
- Each user message may start with a <resolve_context> block ("Project location": project, timeline, page, playhead and
  the clip under the playhead on each track). "This clip" means the topmost clip under the playhead unless the
  user says otherwise. Use the ids in that block; call current_context, project_overview or
  timeline_overview when you need more.
- Attachments: images the user attaches are included in the message; other files are given as paths.
  Media pool references name a clip by id and file path.
- Speech: transcribe (local whisper.cpp, word-level timeline frames, cached), generate_subtitles (.srt onto a
  subtitle track), find_filler_words and review_transcript_cuts (pink review markers), then apply_silence_cuts
  to cut approved markers on a duplicate timeline. review_silence finds pauses. Use transcripts to answer
  "where do I say X", plan cuts, and name chapter markers. search_transcript finds exact/fuzzy phrases.
  remove_pauses tightens speech across the whole timeline (report first; apply builds a new tightened timeline).
  remove_ranges cuts any ranges from every track on a duplicate timeline. animated_captions renders word-highlight
  captions as a transparent overlay on a new top track.
- Seeing: you can look at the footage. contact_sheet returns labelled frame grids (timeline, track, clip or media
  pool) so you can pick shots visually; timeline_frame shows one frame. detect_shots finds camera cuts;
  detect_beats finds tempo/beats/downbeats for cutting to music; add_markers places many markers at once.
  search_footage finds WHEN something is visible (on-screen text such as an app name or error message, scene
  labels, faces looking at the camera) via an on-device index; confirm hits with contact_sheet/timeline_frame.
- Skills: the user can invoke a skill with "$name". You can also CREATE reusable skills when asked ("make this a
  skill", "remember how to do this"): write ~/.claude/skills/<kebab-name>/SKILL.md with frontmatter
  (name, description that says when to use it) and concise steps that use these Resolve tools. Prefer
  DaVinci-specific, repeatable workflows (a caption style, a cleanup pass, a transition recipe). Skills saved
  there also work in Claude Code itself.
- Delegate independent, parallel research or analysis to subagents (the Task/Agent tool) when it helps.
- Fusion is your main instrument for effects, motion, transitions and keyframes:
  fusion_build (insert a node graph into a clip's chain, with keyframes), fusion_keyframe_input (animate any
  input of any node), fusion_inspect_tool / inspect_fusion (read before and after), fusion_apply_setting (paste
  a .setting macro), fusion_run_lua (last resort). Ready-made effects: animate_zoom, apply_blur_effect,
  apply_spotlight_mask, keyframe_clip_saturation, animate_color_fx, change_clip_speed, set_clip_transform.
- Resolve's API cannot add Edit-page transitions. Build transitions in Fusion on the clip's head or tail
  (e.g. keyframed Transform/Blur/Merge over the first or last frames) and say that is what you did.
- Frames: Fusion keyframe frames are clip offsets (0 = first frame of the clip) unless you pass timeline_frame.
Working style:
- For any request with three or more steps (a notes pass, a cleanup, a multi-shot fix), first lay the plan out with
  TaskCreate (one short imperative subject per step, plus activeForm), then TaskUpdate each to in_progress when you
  start it and completed when it's verified. The user sees this list live in the panel; keep exactly one task in
  progress and don't create tasks for one-step requests.
- Inspect, make one logical change per tool call, then verify (read back with inspect tools or timeline_frame).
- Report only what the tools confirm. If a result says verified=false or null, say so plainly.
- Ask before deleting clips, ripple edits or rendering. Keep replies short: what you changed, where (track,
  clip, timecode) and how to tweak it. Use bullet points sparingly; no headings for short answers.
- If the bridge is offline, tell the user to choose Workspace > Scripts > Resolve AI Bridge > Start AI Bridge (Lua).
- The user's current Resolve page (in <resolve_context>) hints at what they're focused on: grades on Color,
  comps on Fusion, levels on Fairlight. It's never a limit; work across the whole project whenever it helps.
- Replies render Markdown, including GitHub tables (good for shot lists, cut lists, before/after), plus simple
  inline HTML (<kbd>, <sub>, <sup>, <mark>, <details>/<summary>, <br>); no scripts, styles or images by URL.
- Timecodes you write as HH:MM:SS:FF and clip names in `code` become links in the panel that jump Resolve's
  playhead there, so cite exact timecodes and exact clip names.
- Timelines named "Checkpoint · …" (in the Agent Work bin) are the panel's rewind points, saved automatically
  before edits. Never open, edit, rename or delete them unless the user asks about them."""

SAFE_COPY_PROMPT = ("\nSafe mode is ON: before the first edit of a request, call preview_timeline to work on a duplicate "
                    "timeline, and tell the user the original is untouched.")


def latest_tasks(events):
    """The agent's task list as of the last ``tasks`` event (rewinds cut events, so this rewinds too)."""
    for event in reversed(events or []):
        if event.get("type") == "tasks":
            return [dict(t) for t in event.get("tasks") or []]
    return []


def _now():
    return time.time()


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex[:6]))
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def mcp_entry():
    """The installed MCP server entry, or the checkout's own server when running from source."""
    installed = HOME / "mcp-server-entry.json"
    try:
        entry = json.loads(installed.read_text(encoding="utf-8"))
        if entry.get("command") and Path(entry["command"]).exists():
            return entry
    except (OSError, ValueError):
        pass
    return {"command": sys.executable, "args": [str(ROOT / "bridge" / "server.py")],
            "env": {"RESOLVE_AI_BRIDGE_HOME": str(HOME)}}


class Bus:
    def __init__(self):
        self.subscribers = set()
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def publish(self, event):
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


class ContextPoller:
    """Polls the bridge for the editor's current context; pauses while an agent is editing."""

    def __init__(self, bus, app):
        self.bus = bus
        self.app = app
        self.value = {"connected": False, "state": "starting"}
        self.wake = threading.Event()
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def refresh(self):
        self.wake.set()

    def _fetch(self):
        try:
            from bridge import client, transport
        except Exception as exc:  # Missing optional runtime pieces.
            return {"connected": False, "state": "unavailable", "detail": str(exc)}
        try:
            payload, used = transport.call("current_context", {}, timeout=6.0)
            payload = dict(payload or {})
            payload.update({"connected": True, "state": "ready", "transport": used})
            return payload
        except client.BridgeOffline as exc:
            return {"connected": False, "state": "offline", "detail": str(exc)}
        except Exception as exc:
            message = str(exc)
            if "Unknown operation" in message:  # Older runtime: fall back to status.
                try:
                    payload, used = transport.call("status", {}, timeout=6.0)
                    payload = dict(payload or {})
                    payload.update({"connected": True, "state": "ready", "transport": used, "limited": True})
                    return payload
                except Exception as inner:
                    message = str(inner)
            return {"connected": False, "state": "error", "detail": message[:300]}

    # Failures this soon after a good read are treated as "busy", not "disconnected": a long Resolve
    # call (or another client's operation) pauses the worker's heartbeat and holds its lock.
    GRACE_SECONDS = 90.0

    def _loop(self):
        last_good, last_good_at, failures = None, 0.0, 0
        while True:
            busy = self.app.any_running()
            interval = 2.0 if self.app.viewers > 0 else 15.0
            if not busy and self.app.viewers > 0:
                value = self._fetch()
                now = _now()
                if value.get("connected"):
                    # While the playhead is moving (Resolve playing or scrubbing), check twice a second so the
                    # panel's timecode stays live; the panel extrapolates between checks.
                    if last_good is not None and value.get("playhead_frame") is not None \
                            and value.get("playhead_frame") != last_good.get("playhead_frame"):
                        interval = 0.5
                    last_good, last_good_at, failures = value, now, 0
                else:
                    failures += 1
                    if last_good is not None and now - last_good_at < self.GRACE_SECONDS and value.get("state") != "unavailable":
                        value = dict(last_good, busy=True, detail=value.get("detail"))
                    interval = min(10.0, 2.0 * (2 ** min(failures, 3)))  # Back off instead of competing.
                value["time"] = now
                with self.lock:
                    changed = {k: v for k, v in value.items() if k not in ("time", "detail")} != \
                        {k: v for k, v in self.value.items() if k not in ("time", "detail")}
                    self.value = value
                if changed:
                    self.bus.publish({"type": "context", "context": value})
            self.wake.wait(interval)
            self.wake.clear()


class Thread:
    def __init__(self, data):
        self.data = data
        self.agent = None
        self.lock = threading.Lock()
        self.checkpoint_for = None  # The user message whose first edit still needs a project checkpoint.
        self.baseline_job = None  # Background snapshot started at turn end; a send waits for it.

    @property
    def id(self):
        return self.data["id"]

    def summary(self):
        d = self.data
        running = bool(self.agent and self.agent.running)
        return {"id": d["id"], "title": d.get("title") or "New chat", "agent": d["agent"],
                "created": d["created"], "updated": d["updated"], "running": running,
                "messages": sum(1 for e in d["events"] if e["type"] == "user")}


class App:
    def __init__(self):
        for folder in (CHAT_HOME, THREADS, WORKSPACE):
            folder.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(folder, 0o700)
            except OSError:
                pass
        self.token = secrets.token_urlsafe(32)
        self.bus = Bus()
        self.viewers = 0
        self.threads = {}
        self.media_paths = {}  # media pool id -> file path, from the last listing
        self.settings = {"agent": "claude", "model": "default", "effort": "high", "mode": "ask", "safe_copy": False, "theme": "glacier", "shortcuts": {},
                         "attach_context": True, "always_allow": set(), "checkpoints": True, "track_changes": True}
        self._load()
        self.context = ContextPoller(self.bus, self)

    # ----------------------------------------------------------- persistence
    def _load(self):
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in PERSISTED_SETTINGS:
                if key in saved:
                    self.settings[key] = set(saved[key]) if key == "always_allow" else saved[key]
        except (OSError, ValueError):
            pass
        for path in sorted(THREADS.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("id") and isinstance(data.get("events"), list):
                    self.threads[data["id"]] = Thread(data)
            except (OSError, ValueError):
                continue

    def save_settings(self):
        data = {k: (sorted(v) if isinstance(v, set) else v) for k, v in self.settings.items() if k in PERSISTED_SETTINGS}
        _atomic_write(SETTINGS_FILE, data)

    def save_thread(self, thread):
        with thread.lock:
            _atomic_write(THREADS / ("%s.json" % thread.id), thread.data)

    def public_settings(self):
        return {k: (sorted(v) if isinstance(v, set) else v) for k, v in self.settings.items()}

    # ---------------------------------------------------------------- state
    def any_running(self):
        return any(t.agent and t.agent.running for t in self.threads.values())

    def agents(self):
        return [{"id": kind, "label": cls.label, "available": cls.available(),
                 "approvals": kind == "claude", "compact": bool(cls.compactable),
                 "path": find_executable("claude" if kind == "claude" else "codex")}
                for kind, cls in AGENTS.items()]

    def state(self):
        threads = sorted((t.summary() for t in self.threads.values()), key=lambda s: -s["updated"])
        return {"threads": threads, "settings": self.public_settings(), "agents": self.agents(),
                "context": self.context.value, "version": self._version()}

    @staticmethod
    def _version():
        try:
            from bridge.operations import AGENT_VERSION
            return AGENT_VERSION
        except Exception:
            return None

    # -------------------------------------------------------------- threads
    def create_thread(self, agent=None):
        agent = agent if agent in AGENTS else self.settings.get("agent", "claude")
        now = _now()
        data = {"id": uuid.uuid4().hex[:12], "title": "", "agent": agent, "created": now, "updated": now,
                "session": None, "events": []}
        thread = Thread(data)
        self.threads[thread.id] = thread
        self.save_thread(thread)
        self.bus.publish({"type": "threads", "threads": self.state()["threads"]})
        return thread

    def delete_thread(self, thread_id):
        thread = self.threads.pop(thread_id, None)
        if thread is None:
            return False
        if thread.agent:
            thread.agent.interrupt()
            thread.agent.close()
        try:
            (THREADS / ("%s.json" % thread_id)).unlink()
            (SNAPSHOTS / ("%s.json" % thread_id)).unlink(missing_ok=True)
        except OSError:
            pass
        self.bus.publish({"type": "threads", "threads": self.state()["threads"]})
        return True

    def _emitter(self, thread):
        def emit(event):
            event = dict(event, thread=thread.id, ts=_now())
            kind = event["type"]
            if kind in ("text_delta", "thought_delta"):
                whole = "text" if kind == "text_delta" else "thought"
                with thread.lock:
                    events = thread.data["events"]
                    last = events[-1] if events else None
                    if last and last["type"] == whole and last.get("msg") == event.get("msg"):
                        last["text"] += event["text"]
                        last["end"] = event["ts"]
                    else:
                        events.append({"type": whole, "msg": event.get("msg"), "text": event["text"],
                                       "thread": thread.id, "ts": event["ts"], "end": event["ts"],
                                       "parent": event.get("parent")})
            elif kind == "tool_input":
                with thread.lock:
                    for prior in reversed(thread.data["events"]):
                        if prior["type"] == "tool_start" and prior.get("tool_id") == event.get("tool_id"):
                            prior["input"] = event.get("input") or {}
                            break
            elif kind == "context_usage":
                with thread.lock:  # Shown in the model menu; not part of the transcript.
                    thread.data["context_usage"] = {k: event.get(k) for k in ("used", "window", "model")}
            elif kind not in ("status", "tool_pending", "compacting"):
                with thread.lock:
                    thread.data["events"].append(event)
                    thread.data["updated"] = event["ts"]
            if kind == "session":
                thread.data["session"] = event.get("session")
                thread.data["resume_at"] = None  # A rewind's fork has started; resume it normally from now on.
            if kind == "turn_end" and event.get("anchor"):
                thread.data["anchor"] = event["anchor"]
            self.bus.publish(event)
            if kind in ("turn_end", "permission"):
                self.save_thread(thread)
            if kind == "turn_end":
                self.refresh_baseline(thread)
                self.context.refresh()
                self.bus.publish({"type": "threads", "threads": self.state()["threads"]})
        return emit

    def _ensure_agent(self, thread):
        if thread.agent is None:
            cls = AGENTS[thread.data["agent"]]
            prompt = SYSTEM_PROMPT + (SAFE_COPY_PROMPT if self.settings.get("safe_copy") else "")
            thread.agent = cls(self._emitter(thread), WORKSPACE, mcp_entry(), prompt, self.settings)
            thread.agent.session = thread.data.get("session")
            thread.agent.resume_at = thread.data.get("resume_at")
            thread.agent.before_edit = lambda tool, thread=thread: self.checkpoint(thread)
            thread.agent.tasks = latest_tasks(thread.data["events"])
        return thread.agent

    def context_block(self):
        ctx = self.context.value or {}
        if not ctx.get("connected"):
            return "<resolve_context>Bridge offline (%s). Resolve may not be running the bridge.</resolve_context>" % (
                ctx.get("state") or "unknown")
        keep = {k: ctx.get(k) for k in ("project", "timeline", "timeline_id", "page", "playhead_timecode",
                                          "playhead_frame", "fps", "resolution", "clips_at_playhead",
                                          "timeline_count", "timelines", "media_pool_root_clip_count")
                if ctx.get(k) is not None}
        if not keep:
            keep = {k: v for k, v in ctx.items() if k not in ("time", "connected", "state", "transport")}
        return "<resolve_context>%s</resolve_context>" % json.dumps(keep, ensure_ascii=False, default=str)

    def save_attachment(self, name, data, mime=None):
        """Store an uploaded file in the agent's workspace; returns its public description."""
        if len(data) > MAX_UPLOAD:
            raise ValueError("Attachments are limited to 25 MB.")
        ATTACHMENTS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name or "file").name).strip() or "file"
        att_id = uuid.uuid4().hex[:10]
        path = ATTACHMENTS / ("%s-%s" % (att_id, safe))
        path.write_bytes(data)
        mime = mime or mimetypes.guess_type(safe)[0] or "application/octet-stream"
        info = {"id": att_id, "name": safe, "path": str(path), "type": mime, "size": len(data)}
        if mime in IMAGE_TYPES and len(data) <= 1536 * 1024:  # Thumbnail for the composer chip.
            info["preview"] = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
        return self._with_thumb(info)

    def attach_path(self, path):
        """Reference an existing file without copying it (big media stays where it is)."""
        target = Path(str(path)).expanduser()
        if not target.is_absolute() or not target.is_file():
            raise ValueError("That file doesn't exist.")
        size = target.stat().st_size
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime in IMAGE_TYPES and size <= MAX_INLINE_IMAGE:
            return self.save_attachment(target.name, target.read_bytes(), mime)  # Small images go inline.
        ATTACHMENTS.mkdir(parents=True, exist_ok=True)
        att_id = uuid.uuid4().hex[:10]
        link = ATTACHMENTS / ("%s-%s.ref" % (att_id, re.sub(r"[^A-Za-z0-9._ -]+", "_", target.name)))
        link.write_text(str(target), encoding="utf-8")
        return self._with_thumb({"id": att_id, "name": target.name, "path": str(target), "type": mime, "size": size})

    def search_files(self, query, limit=30):
        """Files for the composer's "/" picker: media pool clips, recent attachments, then common folders."""
        q = (query or "").strip().lower()
        items, seen = [], set()

        by_path = {path: media_id for media_id, path in self.media_paths.items()}

        def add(path, kind, detail=None):
            key = str(path)
            if key in seen or (q and q not in Path(key).name.lower()):
                return
            seen.add(key)
            try:
                size = Path(key).stat().st_size
            except OSError:
                return
            item = {"path": key, "name": Path(key).name, "kind": kind, "size": size, "detail": detail}
            thumb_id = by_path.get(key)
            if thumb_id is None and Path(key).suffix.lower() in PREVIEWABLE:
                # Only files this search returned can be previewed, like media pool clips.
                thumb_id = "f-" + hashlib.sha1(key.encode()).hexdigest()[:16]
                self.media_paths[thumb_id] = key
            if thumb_id is not None:
                item["thumb_id"] = str(thumb_id)
            items.append(item)

        if not any(not k.startswith("f-") for k in self.media_paths) and self.context.value.get("connected"):
            try:  # First "/" search: learn the media pool once (cached for later searches).
                from bridge import transport
                payload, _ = transport.call("list_media", {"limit": 2000}, timeout=20.0)
                for item in payload.get("items", []):
                    if item.get("id") and item.get("file_path"):
                        self.media_paths[str(item["id"])] = item["file_path"]
            except Exception:
                pass
        pool = [(media_id, path) for media_id, path in self.media_paths.items() if not media_id.startswith("f-")]
        for media_id, path in pool[:500]:
            add(path, "media", "Media pool")
        if ATTACHMENTS.is_dir():
            recent = sorted(ATTACHMENTS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
            for p in recent:
                real = Path(p.read_text(encoding="utf-8").strip()) if p.suffix == ".ref" else p
                add(real, "recent", "Recently attached")
        if len(items) < limit:
            for folder in ("Movies", "Desktop", "Downloads", "Documents"):
                base = Path.home() / folder
                try:
                    for root, dirs, files in os.walk(base):
                        dirs[:] = [d for d in dirs if not d.startswith(".")][:40]
                        if root.count(os.sep) - str(base).count(os.sep) >= 3:
                            dirs[:] = []
                        for name in files:
                            if not name.startswith("."):
                                add(Path(root) / name, "file", folder)
                            if len(items) >= limit * 3:
                                break
                except OSError:
                    continue
        order = {"media": 0, "recent": 1, "file": 2}
        items.sort(key=lambda i: (order[i["kind"]], not i["name"].lower().startswith(q), i["name"].lower()))
        return items[:limit]

    @staticmethod
    def skills():
        """Skills for the composer's "$" picker: the user's Claude Code skills plus the bridge's bundled ones."""
        found, seen = [], set()
        for scope, base in [("user", d) for d in SKILL_DIRS] + [("bundled", BUNDLED_SKILLS)]:
            if not base.is_dir():
                continue
            for skill_md in sorted(base.glob("*/SKILL.md")):
                try:
                    head = skill_md.read_text(encoding="utf-8", errors="replace")[:3000]
                except OSError:
                    continue
                name = re.search(r"^name:\s*(.+)$", head, re.M)
                desc = re.search(r"^description:\s*(.+)$", head, re.M)
                skill = (name.group(1).strip() if name else skill_md.parent.name).strip("\"'")
                if skill in seen:
                    continue
                seen.add(skill)
                found.append({"name": skill, "description": (desc.group(1).strip() if desc else "")[:300],
                              "scope": scope, "path": str(skill_md)})
        return found

    def find_attachment(self, att_id):
        if not re.fullmatch(r"[0-9a-f]{10}", str(att_id or "")):
            return None
        for path in ATTACHMENTS.glob("%s-*" % att_id):
            if path.suffix == ".ref":  # A referenced file that stays in place.
                real = Path(path.read_text(encoding="utf-8").strip())
                if not real.is_file():
                    return None
                mime = mimetypes.guess_type(real.name)[0] or "application/octet-stream"
                return self._with_thumb({"id": att_id, "name": real.name, "path": str(real), "type": mime,
                                         "size": real.stat().st_size})
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return self._with_thumb({"id": att_id, "name": path.name[11:], "path": str(path), "type": mime,
                                     "size": path.stat().st_size})
        return None

    def _with_thumb(self, info):
        """Video/image attachments get a poster id the panel can show (served by thumbnail())."""
        if Path(info["path"]).suffix.lower() in PREVIEWABLE:
            thumb_id = "f-" + hashlib.sha1(info["path"].encode()).hexdigest()[:16]
            self.media_paths[thumb_id] = info["path"]
            info["thumb_id"] = thumb_id
        return info

    def with_attachment_previews(self, data):
        """Older chats saved attachments without a poster id; add one when the file is still there, so
        reopened chats show their images and clips again."""
        events = []
        for event in data.get("events") or []:
            if event.get("type") == "user" and any(not a.get("thumb_id") for a in event.get("attachments") or []):
                fixed = []
                for att in event["attachments"]:
                    if not att.get("thumb_id"):
                        found = self.find_attachment(att.get("id", ""))
                        if found and found.get("thumb_id"):
                            att = dict(att, thumb_id=found["thumb_id"])
                    fixed.append(att)
                event = dict(event, attachments=fixed)
            events.append(event)
        return dict(data, events=events)

    def timeline_notes(self):
        from bridge import transport
        result, _ = transport.call("timeline_notes", {}, timeout=60.0)
        return result

    def thumbnail(self, media_id):
        """A poster frame for a media pool clip, made with Quick Look (or ffmpeg). Only clips the
        media listing returned are eligible, so this can't be used to read arbitrary files."""
        import hashlib
        import shutil
        import subprocess
        path = self.media_paths.get(str(media_id))
        if not path or not Path(path).is_file():
            return None
        THUMBS.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(("%s|%s" % (path, Path(path).stat().st_mtime)).encode()).hexdigest()[:16]
        target = THUMBS / (key + ".png")
        if target.is_file():
            return target
        work = THUMBS / ("tmp-" + key)
        work.mkdir(exist_ok=True)
        try:
            if shutil.which("qlmanage"):
                subprocess.run(["qlmanage", "-t", "-s", "320", "-o", str(work), path],
                               capture_output=True, timeout=12)
                made = next(work.glob("*.png"), None)
                if made:
                    os.replace(made, target)
            if not target.is_file():
                try:
                    from bridge.operations import ResolveOperations
                    ffmpeg = ResolveOperations._ffmpeg_path(HOME)
                except Exception:
                    ffmpeg = shutil.which("ffmpeg")
                if ffmpeg:
                    subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", "1", "-i", path, "-frames:v", "1",
                                    "-vf", "scale=320:-1", str(target)], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return target if target.is_file() else None

    def capture_frame(self):
        from bridge import transport
        transport.call("status", {}, timeout=20.0)  # Cheap "look first" handshake the bridge requires.
        result, _ = transport.call("timeline_frame", {}, timeout=60.0)
        data = base64.b64decode(result.get("image_base64") or "")
        if not data:
            raise ValueError("Resolve returned no image for the current frame.")
        ext = "png" if result.get("format") == "png" else "jpg"
        label = (result.get("timecode") or "frame").replace(":", "-")
        return self.save_attachment("frame-%s.%s" % (label, ext), data, "image/" + ("png" if ext == "png" else "jpeg"))

    def _expand_skills(self, text):
        """"$name" tokens become an explicit instruction to use that skill (Claude Code's Skill tool)."""
        known = {s["name"]: s for s in self.skills()}
        used = [name for name in re.findall(r"(?<![\w$])\$([A-Za-z0-9][\w.:-]*)", text) if name in known]
        if not used:
            return text
        lines = ["Use the %s skill (%s)." % (n, known[n]["path"]) for n in dict.fromkeys(used)]
        return "%s\n\n%s" % ("\n".join(lines), text)

    def _content(self, text, attachments, refs):
        """Build Claude message content: images inline, other files and media pool clips by reference."""
        blocks, notes = [], []
        for att in attachments:
            if att["type"] in IMAGE_TYPES and att["size"] <= MAX_INLINE_IMAGE:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": att["type"],
                               "data": base64.b64encode(Path(att["path"]).read_bytes()).decode("ascii")}})
                notes.append("Attached image %s (also saved at %s)" % (att["name"], att["path"]))
            else:
                notes.append("Attached file %s at %s (%s, %d bytes)" % (att["name"], att["path"], att["type"], att["size"]))
        for ref in refs:
            notes.append("Media pool clip %s (id %s, bin %s, file %s)" % (
                ref.get("name"), ref.get("id"), ref.get("bin") or "root", ref.get("file_path") or "unknown"))
        if notes:
            text = "<attachments>\n%s\n</attachments>\n\n%s" % ("\n".join("- " + n for n in notes), text)
        blocks.append({"type": "text", "text": text})
        return blocks if len(blocks) > 1 else text

    def compact(self, thread, instructions=""):
        """Summarize this chat's context (Claude Code /compact). The transcript stays; the agent keeps a summary."""
        agent = self._ensure_agent(thread)
        if not agent.compactable:
            raise ValueError("%s can't compact its context." % agent.label)
        if not thread.data.get("session"):
            raise ValueError("Nothing to compact yet.")
        agent.session = agent.session or thread.data.get("session")
        agent.compact(instructions)
        self.bus.publish({"type": "threads", "threads": self.state()["threads"]})
        return {"ok": True}

    def send(self, thread, text, attach=None, attachment_ids=(), refs=()):
        text = text.strip()
        command = re.match(r"^/compact(?:\s+(.*))?$", text, re.S)
        if command and not attachment_ids and not refs:  # Typed as a command: compact, don't send it as a message.
            return self.compact(thread, command.group(1) or "")
        attachments = [a for a in (self.find_attachment(i) for i in attachment_ids or []) if a]
        refs = [r for r in (refs or []) if isinstance(r, dict)][:20]
        if not text and not attachments and not refs:
            raise ValueError("Message is empty.")
        agent = self._ensure_agent(thread)
        steering = agent.running
        if steering and not agent.steerable:
            raise ValueError("%s can't take messages mid-turn. Stop it or wait for it to finish." % agent.label)
        emit = self._emitter(thread)
        if not thread.data.get("title"):
            title = text or (attachments[0]["name"] if attachments else refs[0].get("name", "Attachment"))
            thread.data["title"] = (title[:60] + "…") if len(title) > 60 else title
        attach = self.settings.get("attach_context", True) if attach is None else attach
        context = self.context_block() if attach and not steering else None
        changes = None if steering else self.changes_since_last_turn(thread)
        message_id = uuid.uuid4().hex[:12]
        if not steering:
            thread.checkpoint_for = message_id
        emit({"type": "user", "id": message_id, "text": text, "steer": steering,
              "attachments": [{k: a[k] for k in ("id", "name", "type", "size", "thumb_id") if k in a} for a in attachments],
              "rewind": bool(agent.rewindable and not steering),
              "anchor": thread.data.get("anchor") if not steering else None,
              "changes": changes or None,
              "refs": [{k: r.get(k) for k in ("id", "name", "bin")} for r in refs],
              "context": self.context.value if attach and not steering else None})
        body = self._expand_skills(text) if text else "See the attachments."
        if steering:
            body = "(Sent while you were working; take it into account from here.)\n\n" + body
        if changes:
            body = ("<changes_since_last_turn>\nChanged in Resolve since your last turn (by the user by hand, or "
                    "another chat):\n- %s\n</changes_since_last_turn>\n\n%s" % ("\n- ".join(changes), body))
        prompt = "%s\n\n%s" % (context, body) if context else body
        try:
            agent.send(self._content(prompt, attachments, refs))
        except Exception as exc:
            emit({"type": "turn_end", "ok": False, "error": str(exc)})
            emit({"type": "status", "state": "error", "detail": str(exc)})
        self.bus.publish({"type": "threads", "threads": self.state()["threads"]})

    # ------------------------------------------------------- change tracking
    def _baseline_path(self, thread):
        return SNAPSHOTS / ("%s.json" % thread.id)

    def _snapshot(self):
        from bridge import transport
        result, _ = transport.call("timeline_fingerprint", {}, timeout=60.0)
        return result

    def refresh_baseline(self, thread):
        """After a turn: remember the timeline as the agent left it, in the background."""
        if not self.settings.get("track_changes", True):
            return

        def run():
            try:
                snap = self._snapshot()
            except Exception:
                return  # Offline or busy: the next send simply reports nothing.
            SNAPSHOTS.mkdir(parents=True, exist_ok=True)
            _atomic_write(self._baseline_path(thread), snap)
        thread.baseline_job = threading.Thread(target=run, daemon=True)
        thread.baseline_job.start()

    def changes_since_last_turn(self, thread):
        """Sentences for what changed by hand since this thread's last turn ended (None if unknown)."""
        if not self.settings.get("track_changes", True):
            return None
        deadline = time.time() + CHANGES_BUDGET_SECONDS
        job = thread.baseline_job
        if job is not None and job.is_alive():
            job.join(max(0.0, deadline - time.time()))
        try:
            before = json.loads(self._baseline_path(thread).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # First message, or nothing saved yet.
        found = {}

        def run():
            try:
                found["after"] = self._snapshot()
            except Exception as exc:
                found["error"] = exc
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.time()))
        after = found.get("after")
        if after is None:
            return None
        _atomic_write(self._baseline_path(thread), after)  # So the same change isn't reported twice.
        return describe_changes(before, after)

    # ------------------------------------------------------------------ seek
    def seek(self, body):
        """Jump Resolve's playhead to a timecode, or to the start of a clip named in the chat."""
        from bridge import transport
        transport.call("status", {}, timeout=20.0)  # The bridge's "look first" handshake before seeking.
        result = {"ok": True}
        tc = str(body.get("timecode") or "").strip()
        if tc:
            if not re.fullmatch(r"\d{1,2}[:;]\d{2}[:;]\d{2}[:;.]\d{2}", tc):
                raise ValueError("Timecode must look like 01:00:12:04.")
        else:
            snap, _ = transport.call("timeline_fingerprint", {}, timeout=60.0)
            item, matches = find_clip(snap, body.get("clip"))
            if item is None:
                raise ValueError("No clip called “%s” on the current timeline." % body.get("clip"))
            tc = clip_timecode(item["start"], snap.get("fps"), snap.get("drop_frame"))
            result.update(clip=item.get("name"), track=item.get("track"), matches=matches)
        transport.call("set_playhead", {"timecode": tc}, timeout=30.0)
        self.context.refresh()
        result["timecode"] = tc
        return result

    # -------------------------------------------------------------- rewind
    def _user_event(self, thread, message_id):
        for index, event in enumerate(thread.data["events"]):
            if event["type"] == "user" and event.get("id") == message_id:
                return index, event
        raise ValueError("That message is no longer in this chat.")

    def checkpoint(self, thread):
        """Before the first project edit after a message: save a copy of the current timeline to rewind to."""
        message_id = thread.checkpoint_for
        if not message_id or not self.settings.get("checkpoints", True):
            return
        thread.checkpoint_for = None
        from bridge import transport
        _, event = self._user_event(thread, message_id)
        label = (event.get("text") or "attachment").strip().replace("\n", " ")
        transport.call("status", {}, timeout=20.0)  # The bridge's "look first" handshake.
        result, _ = transport.call("checkpoint_timeline", {"label": "before “%s”" % (label[:40] + ("…" if len(label) > 40 else ""))},
                                   timeout=180.0)
        info = {"id": result["checkpoint"]["id"], "name": result["checkpoint"]["name"],
                "timeline": result["timeline"]["name"], "bin": result.get("bin")}
        with thread.lock:
            event["checkpoint"] = info
        self._emitter(thread)({"type": "checkpoint", "message": message_id, **info})
        self.save_thread(thread)

    def rewind_preview(self, thread, message_id):
        """What rewinding to this message would undo, for the warning the user sees first."""
        index, event = self._user_event(thread, message_id)
        if not event.get("rewind"):
            raise ValueError("This message can't be rewound to (sent mid-turn, or the agent can't rewind).")
        later = thread.data["events"][index:]
        checkpoint = next((e["checkpoint"] for e in later if e["type"] == "user" and e.get("checkpoint")), None)
        edit_ids = {e.get("tool_id") for e in later if e["type"] == "tool_start"
                    and AgentBase.edits_project(str(e.get("name") or ""))}
        edits = sum(1 for e in later if e["type"] == "tool_result" and e.get("ok") and e.get("tool_id") in edit_ids)
        return {"messages": sum(1 for e in later if e["type"] == "user"), "project_edits": edits,
                "checkpoint": checkpoint, "checkpoints_enabled": bool(self.settings.get("checkpoints", True))}

    def rewind(self, thread, message_id, restore_project=False):
        """Cut the chat back to just before this message (the agent forgets everything after it). With
        restore_project, also open a copy of the timeline as it was then. Nothing in Resolve is deleted."""
        if thread.agent and thread.agent.running:
            raise ValueError("Stop the current turn before rewinding.")
        preview = self.rewind_preview(thread, message_id)
        index, event = self._user_event(thread, message_id)
        restored = None
        if restore_project and preview["checkpoint"]:
            from bridge import transport
            transport.call("status", {}, timeout=20.0)
            restored, _ = transport.call("restore_checkpoint", {"checkpoint_id": preview["checkpoint"]["id"]}, timeout=180.0)
        anchor = event.get("anchor")
        if thread.agent:
            thread.agent.close()
            thread.agent = None
        with thread.lock:
            thread.data["events"] = thread.data["events"][:index]
            thread.data["session"] = anchor["session"] if anchor else None
            thread.data["resume_at"] = anchor["uuid"] if anchor else None
            thread.data["anchor"] = anchor
            thread.data["updated"] = _now()
        thread.checkpoint_for = None
        self.save_thread(thread)
        self.bus.publish({"type": "thread_reset", "thread": thread.id})
        self.bus.publish({"type": "threads", "threads": self.state()["threads"]})
        self.context.refresh()
        return {"text": event.get("text", ""), "attachments": event.get("attachments") or [],
                "refs": event.get("refs") or [], "restored": restored,
                "project_restored": restored is not None}

    def update_settings(self, patch):
        restart = False
        if patch.get("theme") in THEMES:  # Saved here: the Mac app's web view keeps no browser storage.
            self.settings["theme"] = patch["theme"]
        if isinstance(patch.get("shortcuts"), dict):  # action -> key combo, e.g. {"send": "Enter"}
            clean = {k: str(v)[:40] for k, v in patch["shortcuts"].items() if k in SHORTCUT_ACTIONS and v}
            self.settings["shortcuts"] = clean
        for key in ("agent", "model", "effort", "mode", "safe_copy", "attach_context", "checkpoints", "track_changes"):
            if key in patch:
                if key in ("model", "effort", "safe_copy") and patch[key] != self.settings.get(key):
                    restart = True
                self.settings[key] = patch[key]
        if patch.get("clear_always_allow"):
            self.settings["always_allow"] = set()
        if self.settings.get("mode") not in ("ask", "auto"):
            self.settings["mode"] = "ask"
        if self.settings.get("effort") not in EFFORTS:
            self.settings["effort"] = "high"
        if restart:  # Model / system prompt are process flags: restart idle agents on next send.
            for thread in self.threads.values():
                if thread.agent and not thread.agent.running:
                    thread.agent.close()
                    thread.agent = None
        self.save_settings()
        self.bus.publish({"type": "settings", "settings": self.public_settings()})


class Handler(BaseHTTPRequestHandler):
    server_version = "ResolveAIChat/1"
    app = None  # set in main()

    def log_message(self, *_):
        pass

    # ----------------------------------------------------------- guards
    def _host_ok(self):
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").lower()
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin in (None, "null") or origin.lower().replace("http://", "") in allowed

    def _token_ok(self, query):
        supplied = self.headers.get("X-Chat-Token") or (query.get("t") or [""])[0]
        return hmac.compare_digest(supplied.encode(), self.app.token.encode())

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit=MAX_BODY):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("Request too large.")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _route(self, method):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._host_ok():
            return self._json(403, {"error": "Forbidden host"})
        if method == "GET" and url.path in ("/", "/index.html"):
            return self._index()
        if not url.path.startswith("/api/"):
            return self._json(404, {"error": "Not found"})
        if not self._token_ok(query):
            return self._json(401, {"error": "Missing or invalid token"})
        parts = [p for p in url.path[len("/api/"):].split("/") if p]
        try:
            return self._api(method, parts, query)
        except KeyError as exc:
            return self._json(404, {"error": "Not found: %s" % exc})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    # ---------------------------------------------------------- endpoints
    def _index(self):
        try:
            body = DIST.read_bytes()
        except OSError:
            body = b"<!doctype html><meta charset=utf-8><title>Resolve AI Chat</title><body style='font:14px system-ui;background:#161616;color:#ddd;padding:24px'>The chat UI is not built. Run <code>npm run build:chat</code> in the repository.</body>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        app = self.app
        q = app.bus.subscribe()
        app.viewers += 1
        app.context.refresh()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            app.viewers = max(0, app.viewers - 1)
            app.bus.unsubscribe(q)

    def _api(self, method, parts, query):
        app = self.app
        head = parts[0] if parts else ""
        if method == "GET" and head == "ping":
            return self._json(200, {"ok": True, "pid": os.getpid()})
        if method == "GET" and head == "events":
            return self._events()
        if method == "GET" and head == "state":
            return self._json(200, app.state())
        if method == "POST" and head == "context":
            app.context.refresh()
            return self._json(200, {"ok": True})
        if head == "notes" and method == "GET":
            return self._json(200, app.timeline_notes())
        if head == "seek" and method == "POST":
            return self._json(200, app.seek(self._body()))
        if head == "settings" and method == "POST":
            app.update_settings(self._body())
            return self._json(200, app.public_settings())
        if head == "attachments" and method == "POST" and len(parts) > 1 and parts[1] == "path":
            return self._json(200, app.attach_path(self._body().get("path", "")))
        if head == "attachments" and method == "POST":
            if len(parts) > 1 and parts[1] == "frame":
                return self._json(200, app.capture_frame())
            body = self._body(limit=MAX_UPLOAD * 4 // 3 + 4096)
            data = base64.b64decode(str(body.get("data", "")), validate=False)
            return self._json(200, app.save_attachment(str(body.get("name", "file")), data, body.get("type") or None))
        if head == "files" and method == "GET":
            return self._json(200, {"items": app.search_files((query.get("q") or [""])[0])})
        if head == "skills" and method == "GET":
            return self._json(200, {"items": app.skills()})
        if head == "media" and method == "GET":
            from bridge import transport
            payload, _ = transport.call("list_media", {"limit": 2000}, timeout=30.0)
            for item in payload.get("items", []):
                if item.get("id") and item.get("file_path"):
                    app.media_paths[str(item["id"])] = item["file_path"]
                    item["thumb"] = True
            return self._json(200, payload)
        if head == "thumb" and method == "GET":
            target = app.thumbnail((query.get("id") or [""])[0])
            if target is None:
                return self._json(404, {"error": "No thumbnail"})
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return None
        if head == "timeline" and method == "GET":
            from bridge import transport
            payload, _ = transport.call("timeline_overview", {}, timeout=15.0)
            return self._json(200, payload)
        if head == "threads":
            if len(parts) == 1 and method == "POST":
                thread = app.create_thread(self._body().get("agent"))
                return self._json(200, thread.data)
            thread = app.threads[parts[1]]
            if len(parts) == 2 and method == "GET":
                with thread.lock:
                    data = dict(thread.data, running=bool(thread.agent and thread.agent.running))
                return self._json(200, app.with_attachment_previews(data))
            if len(parts) == 2 and method == "DELETE":
                return self._json(200, {"ok": app.delete_thread(thread.id)})
            action = parts[2] if len(parts) > 2 else ""
            if action == "messages" and method == "POST":
                body = self._body()
                app.send(thread, str(body.get("text", ""))[:20000], body.get("attach_context"),
                         body.get("attachments") or [], body.get("refs") or [])
                return self._json(200, {"ok": True})
            if action == "rewind" and method == "POST":
                body = self._body()
                message_id = str(body.get("message", ""))
                if body.get("preview"):
                    return self._json(200, app.rewind_preview(thread, message_id))
                return self._json(200, app.rewind(thread, message_id, bool(body.get("restore_project"))))
            if action == "compact" and method == "POST":
                return self._json(200, app.compact(thread, str(self._body().get("instructions", ""))[:500]))
            if action == "interrupt" and method == "POST":
                if thread.agent:
                    thread.agent.interrupt()
                return self._json(200, {"ok": True})
            if action == "permissions" and method == "POST" and len(parts) == 4:
                decision = self._body().get("decision")
                if decision not in ("allow", "deny", "always"):
                    raise ValueError("decision must be allow, deny or always")
                ok = bool(thread.agent and thread.agent.resolve_permission(parts[3], decision))
                if ok and decision == "always":
                    app.save_settings()
                return self._json(200, {"ok": ok})
        return self._json(404, {"error": "Unknown endpoint"})


def _existing_server():
    """Return the URL of a healthy running instance, if any."""
    try:
        info = json.loads(SERVER_FILE.read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    import urllib.request
    try:
        request = urllib.request.Request("http://127.0.0.1:%d/api/ping" % info["port"],
                                         headers={"X-Chat-Token": info["token"]})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            if response.status == 200:
                return info["url"]
    except Exception:
        return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve AI Chat local server")
    parser.add_argument("--port", type=int, default=0, help="Port to bind on 127.0.0.1 (0 = pick a free one)")
    parser.add_argument("--open", action="store_true", help="Open the panel in the default browser")
    parser.add_argument("--new", action="store_true", help="Start a new server even if one is running")
    args = parser.parse_args(argv)

    if not args.new:
        url = _existing_server()
        if url:
            print(json.dumps({"url": url, "reused": True}), flush=True)
            if args.open:
                webbrowser.open(url)
            return 0

    app = App()
    Handler.app = app
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    url = "http://127.0.0.1:%d/#t=%s" % (port, app.token)
    _atomic_write(SERVER_FILE, {"pid": os.getpid(), "port": port, "token": app.token, "url": url})
    try:
        os.chmod(SERVER_FILE, 0o600)
    except OSError:
        pass
    app.context.start()
    print(json.dumps({"url": url, "port": port}), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for thread in app.threads.values():
            if thread.agent:
                thread.agent.close()
        try:
            if json.loads(SERVER_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                SERVER_FILE.unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
