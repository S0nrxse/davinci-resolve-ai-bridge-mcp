"""Agent adapters. Each turns a CLI agent's stream into normalized chat events.

Events emitted through ``emit(event_dict)``:

    status          {state: idle|running|error|auth_required, detail?}
    assistant_start {msg}
    text_delta      {msg, text, parent?}       streamed reply text
    text            {msg, text, parent}        whole text block (subagents)
    thought_delta   {msg, text, parent?}       streamed reasoning
    thought         {msg, text, parent}        whole reasoning block (subagents)
    tool_start      {tool_id, name, input, parent?}
    tool_input      {tool_id, input}           full input once the call is complete
    tool_result     {tool_id, ok, summary, image?, parent?}
    tasks           {tasks: [{id, subject, status, active_form?}]}  the agent's own task list, whole, on change
    context_usage   {used, window?, model?}    tokens in the context window after the latest step
    compacting      {}                         the context is being summarized
    compacted       {trigger: manual|auto, pre_tokens?, post_tokens?}
    permission      {request, tool, input}
    turn_end        {ok, error?, cost_usd?, duration_ms?, session}

``parent`` is the tool_use id of the subagent (Task/Agent call) that produced
the event, so the panel can nest subagent work under its call.

Only the standard library is used so the chat server runs from the bridge's
private Python environment without extra packages.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

RESOLVE_SERVER = "resolve-ai-bridge"
RESOLVE_PREFIX = "mcp__%s__" % RESOLVE_SERVER

# Read-only bridge tools never need approval.
READ_ONLY_TOOLS = frozenset({
    "resolve_status", "bridge_capabilities", "current_context", "project_overview", "timeline_overview", "project_info",
    "list_timelines", "timeline_frame", "timeline_audio", "list_media", "list_render_presets",
    "compare_timelines", "project_health", "review_silence", "get_clip_transform", "get_clip_grade",
    "inspect_fusion", "fusion_inspect_tool", "render_status", "transcribe", "transcription_source",
    "search_transcript", "contact_sheet", "media_sources", "index_footage", "search_footage",
    "list_ai_timelines", "visual_diff", "timeline_notes", "list_snapshots", "speech_regions",  # measure_loudness can apply gain: asks.
})
# The agent's task list (TaskCreate/TaskUpdate/...) only tracks its own progress; it never touches the project.
TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"})
READ_ONLY_BUILTINS = frozenset({"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch",
                               "Task", "Agent"}) | TASK_TOOLS  # Subagents ask for their own edits.
TASK_STATUSES = ("pending", "in_progress", "completed")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

AUTH_MARKERS = ("Failed to authenticate", "OAuth", "Invalid API key", "/login", "not logged in", "Please run")


def find_executable(name):
    found = shutil.which(name)
    if found:
        return found
    for folder in ("~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
                   "~/.bun/bin", "~/.volta/bin"):
        candidate = Path(folder).expanduser() / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def summarize_result(content, limit=4000):
    """Shorten an MCP/tool result to something the panel can show in a step row."""
    image = None
    if isinstance(content, list):
        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "image" and image is None:
                source = block.get("source") or {}
                data = source.get("data") or block.get("data")
                media = source.get("media_type") or block.get("mimeType") or "image/png"
                if data and len(data) < 2_500_000:
                    image = "data:%s;base64,%s" % (media, data)
        text = "\n".join(texts)
    else:
        text = "" if content is None else str(content)
    ok = True
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            ok = False
    except (ValueError, TypeError):
        pass
    if len(text) > limit:
        text = text[:limit] + "…"
    return ok, text, image


class AgentBase:
    kind = "base"
    label = "Agent"
    steerable = False
    rewindable = False  # Can the conversation be cut back to an earlier turn?
    compactable = False  # Can the conversation be summarized to free context (compact)?

    def __init__(self, emit, workdir, mcp_entry, system_prompt, settings):
        self.emit = emit
        self.workdir = str(workdir)
        self.mcp_entry = mcp_entry
        self.system_prompt = system_prompt
        self.settings = settings  # shared dict: mode, model, always_allow (set)
        self.session = None
        self.running = False
        self.anchor = None  # Last main-thread transcript message at the end of a turn (rewind point).
        self.resume_at = None  # One-shot: resume the session cut back to this message, as a fork.
        self.before_edit = None  # Called with the tool name before a project-changing tool is allowed.
        self.tasks = []  # The agent's task list as the panel last showed it (seeded by the server on restart).
        self._lock = threading.Lock()

    @staticmethod
    def edits_project(tool):
        """True for Resolve tools that can change the project (everything but the read-only ones)."""
        return tool.startswith(RESOLVE_PREFIX) and tool[len(RESOLVE_PREFIX):] not in READ_ONLY_TOOLS

    @classmethod
    def available(cls):
        return False

    def send(self, text):
        raise NotImplementedError

    def interrupt(self):
        pass

    def compact(self, instructions=""):
        raise ValueError("%s can't compact its context." % self.label)

    def resolve_permission(self, request_id, decision):
        pass

    def close(self):
        pass


class ClaudeCodeAgent(AgentBase):
    """Persistent ``claude -p`` process speaking the stream-json control protocol."""

    kind = "claude"
    label = "Claude Code"
    rewindable = True
    compactable = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.proc = None
        self.pending = {}  # request_id -> tool_name
        self._msg = None
        self._blocks = {}
        self._seen_tools = set()
        self._reader = None
        self._stderr_tail = []
        self._task_calls = {}  # tool_use id -> (tool name, input) for the agent's task tools
        self._used = None  # Tokens in context after the latest main-thread step.
        self._window = None  # The model's context window, from the last result.

    @classmethod
    def available(cls):
        return find_executable("claude") is not None

    def _command(self):
        exe = find_executable("claude")
        if not exe:
            raise RuntimeError("Claude Code is not installed. Install it from https://claude.com/claude-code, then sign in with `claude`.")
        mcp = {"mcpServers": {RESOLVE_SERVER: dict(self.mcp_entry, type="stdio")}}
        allowed = ",".join(RESOLVE_PREFIX + name for name in sorted(READ_ONLY_TOOLS))
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--forward-subagent-text", "--permission-prompt-tool", "stdio",
               "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
               "--allowedTools", allowed,
               "--append-system-prompt", self.system_prompt]
        model = (self.settings.get("model") or "").strip()
        if model and model != "default":
            cmd += ["--model", model]
        effort = (self.settings.get("effort") or "").strip()
        if effort in EFFORTS:
            cmd += ["--effort", effort]
        if self.session:
            cmd += ["--resume", self.session]
            if self.resume_at:  # Rewind: drop everything after this message, in a new session.
                cmd += ["--resume-session-at", self.resume_at, "--fork-session"]
        return cmd

    def _start(self):
        env = dict(os.environ)
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "resolve-ai-chat")
        env.setdefault("CLAUDE_CODE_ENABLE_TODO_TOOLS", "1")  # TaskCreate/TaskUpdate/TaskList in print mode.
        command = self._command()
        self.resume_at = None
        self.proc = subprocess.Popen(command, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        self._write({"type": "control_request", "request_id": "init-" + uuid.uuid4().hex[:8],
                     "request": {"subtype": "initialize", "hooks": None}})
        self._reader = threading.Thread(target=self._read_loop, args=(self.proc,), daemon=True)
        self._reader.start()
        threading.Thread(target=self._stderr_loop, args=(self.proc,), daemon=True).start()

    def _write(self, obj):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            raise RuntimeError("Claude Code is not running.")
        with self._lock:
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

    steerable = True

    def send(self, content):
        """Send a user turn. While a turn is running this steers it: Claude Code reads the
        message at its next step instead of waiting for the turn to end."""
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        self.running = True
        self.emit({"type": "status", "state": "running"})
        self._write({"type": "user", "session_id": self.session or "", "parent_tool_use_id": None,
                     "message": {"role": "user", "content": content}})

    def compact(self, instructions=""):
        """Summarize the conversation so far to free context (Claude Code's /compact, optionally focused)."""
        if self.running:
            raise ValueError("Wait for Claude to finish (or stop it) before compacting.")
        if not self.session:
            raise ValueError("Nothing to compact yet.")
        command = "/compact"
        instructions = " ".join(str(instructions or "").split())[:500]
        if instructions:
            command += " " + instructions
        self.send(command)

    def _usage(self, usage):
        """Tokens the model saw on its latest step: everything it read plus what it wrote."""
        if not isinstance(usage, dict):
            return None
        total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens", "output_tokens"))
        return total or None

    def _emit_usage(self, model=None):
        if self._used is not None:
            self.emit({"type": "context_usage", "used": self._used, "window": self._window, "model": model})

    def interrupt(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self._write({"type": "control_request", "request_id": "int-" + uuid.uuid4().hex[:8],
                         "request": {"subtype": "interrupt"}})
        except Exception:
            self.proc.terminate()
        for request_id in list(self.pending):
            self.resolve_permission(request_id, "deny", note="Interrupted")

    def resolve_permission(self, request_id, decision, note=None):
        entry = self.pending.pop(request_id, None)
        if entry is None:
            return False
        tool, tool_input = entry
        if decision in ("allow", "always") and self.before_edit and self.edits_project(tool):
            try:
                self.before_edit(tool)
            except Exception as exc:  # A missing checkpoint must not block the edit; say so.
                self.emit({"type": "notice", "level": "warn",
                           "text": "Couldn't save a checkpoint before this edit (%s). Rewind can't restore the project "
                                   "for it." % exc})
        if decision == "always":
            self.settings.setdefault("always_allow", set()).add(tool)
        if decision in ("allow", "always"):
            response = {"behavior": "allow", "updatedInput": tool_input}
        else:
            response = {"behavior": "deny", "message": note or "The user declined this action in the chat panel."}
        try:
            self._write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                                    "response": response}})
        except Exception:
            pass
        self.emit({"type": "permission_resolved", "request": request_id, "decision": decision})
        return True

    def _auto_allow(self, tool):
        if self.settings.get("mode") == "auto":
            return True
        bare = tool[len(RESOLVE_PREFIX):] if tool.startswith(RESOLVE_PREFIX) else None
        if bare in READ_ONLY_TOOLS or tool in READ_ONLY_BUILTINS:
            return True
        return tool in self.settings.get("always_allow", set())

    def _control_request(self, obj):
        request = obj.get("request") or {}
        request_id = obj.get("request_id")
        if request.get("subtype") != "can_use_tool":
            self._write({"type": "control_response", "response": {"subtype": "error", "request_id": request_id,
                                                                    "error": "Unsupported request"}})
            return
        tool = request.get("tool_name", "")
        tool_input = request.get("input") or {}
        self.pending[request_id] = (tool, tool_input)
        if self._auto_allow(tool):
            self.resolve_permission(request_id, "allow")
            return
        self.emit({"type": "permission", "request": request_id, "tool": tool, "input": tool_input,
                   "tool_id": request.get("tool_use_id")})

    def _stream_event(self, event):
        kind = event.get("type")
        if kind == "message_start":
            self._msg = uuid.uuid4().hex[:12]
            self._blocks = {}
            self.emit({"type": "assistant_start", "msg": self._msg})
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            self._blocks[event.get("index")] = block.get("type")
            if block.get("type") == "tool_use" and block.get("id") not in self._seen_tools:
                # Emit at the start so tool calls sit exactly where they happen between text and thoughts.
                self._seen_tools.add(block.get("id"))
                self.emit({"type": "tool_start", "tool_id": block.get("id"), "name": block.get("name"), "input": {}})
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.emit({"type": "text_delta", "msg": self._msg, "text": delta["text"]})
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                self.emit({"type": "thought_delta", "msg": self._msg, "text": delta["thinking"]})

    def _handle(self, obj):
        kind = obj.get("type")
        parent = obj.get("parent_tool_use_id")
        if kind in ("assistant", "user") and not parent and obj.get("uuid"):
            self._last_uuid = obj["uuid"]
        if kind == "stream_event":
            if not parent:  # Subagent output arrives as whole messages (forward-subagent-text).
                self._stream_event(obj.get("event") or {})
        elif kind == "control_request":
            self._control_request(obj)
        elif kind == "control_cancel_request":
            request_id = obj.get("request_id")
            if self.pending.pop(request_id, None) is not None:
                self.emit({"type": "permission_resolved", "request": request_id, "decision": "cancelled"})
        elif kind == "system" and obj.get("subtype") == "status" and obj.get("status") == "compacting":
            self.emit({"type": "compacting"})
        elif kind == "system" and obj.get("subtype") == "compact_boundary":
            meta = obj.get("compact_metadata") or {}
            if meta.get("post_tokens"):
                self._used = int(meta["post_tokens"])
            self.emit({"type": "compacted", "trigger": meta.get("trigger") or "auto",
                       "pre_tokens": meta.get("pre_tokens"), "post_tokens": meta.get("post_tokens")})
            self._emit_usage()
        elif kind == "system" and obj.get("subtype") == "init":
            self.session = obj.get("session_id") or self.session
            servers = {s.get("name"): s.get("status") for s in obj.get("mcp_servers") or []}
            self.emit({"type": "session", "session": self.session, "model": obj.get("model"),
                       "resolve_mcp": servers.get(RESOLVE_SERVER)})
        elif kind == "assistant":
            message = obj.get("message") or {}
            if not parent:
                self._used = self._usage(message.get("usage")) or self._used
            for block in message.get("content") or []:
                btype = block.get("type")
                if btype == "tool_use":
                    if block.get("name") in TASK_TOOLS and not parent:
                        self._task_calls[block.get("id")] = (block.get("name"), block.get("input") or {})
                    if block.get("id") not in self._seen_tools:
                        self._seen_tools.add(block.get("id"))
                        self.emit({"type": "tool_start", "tool_id": block.get("id"), "name": block.get("name"),
                                   "input": block.get("input") or {}, "parent": parent})
                    else:
                        self.emit({"type": "tool_input", "tool_id": block.get("id"), "input": block.get("input") or {}})
                elif parent and btype == "text" and block.get("text"):
                    self.emit({"type": "text", "msg": uuid.uuid4().hex[:12], "text": block["text"], "parent": parent})
                elif parent and btype == "thinking" and block.get("thinking"):
                    self.emit({"type": "thought", "msg": uuid.uuid4().hex[:12], "text": block["thinking"], "parent": parent})
                elif btype == "text" and any(m in block.get("text", "") for m in AUTH_MARKERS) \
                        and message.get("model") == "<synthetic>":
                    self.emit({"type": "status", "state": "auth_required", "detail": block.get("text")})
        elif kind == "user":
            content = (obj.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    ok, summary, image = summarize_result(block.get("content"))
                    if block.get("is_error"):
                        ok = False
                    self.emit({"type": "tool_result", "tool_id": block.get("tool_use_id"), "ok": ok,
                               "summary": summary, "image": image, "parent": parent})
                    call = self._task_calls.pop(block.get("tool_use_id"), None)
                    if call and ok:
                        self._apply_task(call[0], call[1], obj.get("tool_use_result"))
        elif kind == "result":
            self.running = False
            self.session = obj.get("session_id") or self.session
            if getattr(self, "_last_uuid", None):
                self.anchor = {"session": self.session, "uuid": self._last_uuid}
            error = None
            if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
                error = obj.get("result") or obj.get("subtype")
            models = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
            main = max(models.items(), key=lambda kv: (kv[1] or {}).get("outputTokens") or 0, default=(None, {}))
            if (main[1] or {}).get("contextWindow"):
                self._window = int(main[1]["contextWindow"])
            self._emit_usage(main[0])
            self.emit({"type": "turn_end", "ok": error is None, "error": error, "session": self.session,
                       "cost_usd": obj.get("total_cost_usd"), "duration_ms": obj.get("duration_ms"),
                       "anchor": self.anchor})
            auth = error and any(m in str(error) for m in AUTH_MARKERS)
            self.emit({"type": "status", "state": "auth_required" if auth else "idle",
                       "detail": str(error) if auth else None})

    def _apply_task(self, tool, tool_input, result):
        """Fold one successful task-tool call into ``self.tasks`` and emit the whole list if it changed."""
        result = result if isinstance(result, dict) else {}
        tasks = [dict(t) for t in self.tasks]
        by_id = {t["id"]: t for t in tasks}
        if tool == "TaskCreate":
            created = result.get("task") or {}
            task_id = str(created.get("id") or "")
            if not task_id:
                return
            entry = {"id": task_id, "subject": created.get("subject") or tool_input.get("subject") or "Task",
                     "status": "pending"}
            if tool_input.get("activeForm"):
                entry["active_form"] = tool_input["activeForm"]
            if task_id in by_id:
                by_id[task_id].update(entry)
            else:
                tasks.append(entry)
        elif tool == "TaskUpdate":
            task_id = str(tool_input.get("taskId") or result.get("taskId") or "")
            status = tool_input.get("status")
            if status == "deleted":
                tasks = [t for t in tasks if t["id"] != task_id]
            else:
                entry = by_id.get(task_id)
                if entry is None:
                    entry = {"id": task_id, "subject": tool_input.get("subject") or "Task #%s" % task_id,
                             "status": "pending"}
                    tasks.append(entry)
                if status in TASK_STATUSES:
                    entry["status"] = status
                if tool_input.get("subject"):
                    entry["subject"] = tool_input["subject"]
                if tool_input.get("activeForm"):
                    entry["active_form"] = tool_input["activeForm"]
        elif tool == "TaskList" and isinstance(result.get("tasks"), list):
            listed = []
            for item in result["tasks"]:
                task_id = str(item.get("id") or "")
                if not task_id:
                    continue
                entry = {"id": task_id, "subject": item.get("subject") or "Task #%s" % task_id,
                         "status": item.get("status") if item.get("status") in TASK_STATUSES else "pending"}
                if (by_id.get(task_id) or {}).get("active_form"):
                    entry["active_form"] = by_id[task_id]["active_form"]
                listed.append(entry)
            tasks = listed
        else:
            return
        if tasks != self.tasks:
            self.tasks = tasks
            self.emit({"type": "tasks", "tasks": tasks})

    def _read_loop(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            try:
                self._handle(obj)
            except Exception as exc:  # Never let one odd event kill the stream.
                self.emit({"type": "notice", "level": "warn", "text": "Could not read an agent event: %s" % exc})
        code = proc.wait()
        if proc is self.proc and self.running:
            self.running = False
            detail = " ".join(self._stderr_tail[-3:]).strip() or "Claude Code exited (code %s)." % code
            self.emit({"type": "turn_end", "ok": False, "error": detail, "session": self.session})
            auth = any(m in detail for m in AUTH_MARKERS)
            self.emit({"type": "status", "state": "auth_required" if auth else "error", "detail": detail})

    def _stderr_loop(self, proc):
        for line in proc.stderr:
            line = line.strip()
            if line:
                self._stderr_tail = (self._stderr_tail + [line])[-20:]

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
            proc.wait()
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


class CodexAgent(AgentBase):
    """One ``codex exec --json`` process per turn, resumed by thread id.

    Codex's non-interactive mode has no per-tool approval channel, so edits run
    without the panel's Allow/Deny prompt. The panel says so before a turn starts.
    """

    kind = "codex"
    label = "Codex"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.proc = None

    @classmethod
    def available(cls):
        return find_executable("codex") is not None

    def _command(self, text):
        exe = find_executable("codex")
        if not exe:
            raise RuntimeError("Codex CLI is not installed. Install it with `npm i -g @openai/codex`, then run `codex login`.")
        entry = self.mcp_entry
        prefix = "mcp_servers.%s." % RESOLVE_SERVER
        overrides = ["-c", prefix + "command=" + json.dumps(entry["command"]),
                     "-c", prefix + "args=" + json.dumps(entry.get("args", []))]
        for key, value in (entry.get("env") or {}).items():
            overrides += ["-c", prefix + "env.%s=%s" % (key, json.dumps(value))]
        model = (self.settings.get("model") or "").strip()
        if model and model != "default":
            overrides += ["-m", model]
        prompt = text if self.session else "%s\n\n%s" % (self.system_prompt, text)
        if self.session:
            return [exe, "exec", "resume", self.session, "--json", "--skip-git-repo-check"] + overrides + [prompt]
        return [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write"] + overrides + [prompt]

    def send(self, content):
        if isinstance(content, list):  # Codex exec takes text; images are referenced by path in the prompt.
            content = "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        text = content
        self.running = True
        self.emit({"type": "status", "state": "running"})
        self.proc = subprocess.Popen(self._command(text), cwd=self.workdir, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read_loop, args=(self.proc,), daemon=True).start()

    def interrupt(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _item(self, item, done):
        kind = item.get("type") or item.get("item_type")
        item_id = item.get("id")
        if kind == "agent_message" and done:
            msg = uuid.uuid4().hex[:12]
            self.emit({"type": "assistant_start", "msg": msg})
            self.emit({"type": "text_delta", "msg": msg, "text": item.get("text", "")})
        elif kind == "reasoning":
            self.emit({"type": "thinking", "msg": None, "active": not done})
        elif kind in ("mcp_tool_call", "command_execution", "file_change", "web_search"):
            if kind == "mcp_tool_call":
                name = "mcp__%s__%s" % (item.get("server", "mcp"), item.get("tool", "tool"))
                tool_input = item.get("arguments") or {}
            elif kind == "command_execution":
                name, tool_input = "Bash", {"command": item.get("command", "")}
            elif kind == "file_change":
                name, tool_input = "Edit", {"changes": item.get("changes", [])}
            else:
                name, tool_input = "WebSearch", {"query": item.get("query", "")}
            if not done:
                self.emit({"type": "tool_start", "tool_id": item_id, "name": name, "input": tool_input})
            else:
                result = item.get("result")
                if isinstance(result, dict):
                    result = result.get("content", result)
                if result is None:
                    result = item.get("aggregated_output", "")
                ok, summary, image = summarize_result(result)
                if item.get("status") in ("failed", "declined") or item.get("error"):
                    ok = False
                    summary = summary or str(item.get("error"))
                self.emit({"type": "tool_start", "tool_id": item_id, "name": name, "input": tool_input})
                self.emit({"type": "tool_result", "tool_id": item_id, "ok": ok, "summary": summary, "image": image})

    def _read_loop(self, proc):
        started = time.time()
        error = None
        for line in proc.stdout:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            kind = obj.get("type", "")
            if kind == "thread.started":
                self.session = obj.get("thread_id") or self.session
                self.emit({"type": "session", "session": self.session})
            elif kind in ("item.started", "item.completed"):
                self._item(obj.get("item") or {}, kind == "item.completed")
            elif kind in ("turn.failed", "error"):
                error = (obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("message")
        code = proc.wait()
        stderr = (proc.stderr.read() or "").strip()
        if code != 0 and not error:
            error = stderr.splitlines()[-1] if stderr else "Codex exited (code %s)." % code
        self.running = False
        self.emit({"type": "turn_end", "ok": error is None, "error": error, "session": self.session,
                   "duration_ms": int((time.time() - started) * 1000)})
        auth = bool(error) and ("login" in error.lower() or "auth" in error.lower())
        self.emit({"type": "status", "state": "auth_required" if auth else "idle", "detail": error if auth else None})


AGENTS = {cls.kind: cls for cls in (ClaudeCodeAgent, CodexAgent)}
