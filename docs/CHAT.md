# Resolve AI Chat panel

A chat panel that docks on the right edge of DaVinci Resolve's window. You ask for edits in editor language
and Claude Code makes them through this bridge's MCP tools, asking before each change to your project.

## How it fits together

```
Resolve AI Chat.app (macOS, Swift)        docks on Resolve's window, hotkey ⌥⌘A, resolveaichat://
  └─ WKWebView ── chat/dist/index.html    the panel UI (React, built to one file)
       ⇅ HTTP + Server-Sent Events (127.0.0.1, per-launch token)
     chat/server.py                       threads, settings, context polling, approvals
       └─ claude -p --input-format stream-json --permission-prompt-tool stdio
            └─ MCP: bridge/server.py ── Lua worker inside Resolve (Start AI Bridge (Lua))
```

- **Why a separate window?** Resolve Free can't host Workflow Integration panels; those are Studio only.
  The panel is a native, non-activating macOS panel. It shows only while Resolve (or the panel) is in
  front, and works over a full-screen Resolve. Drag and resize it anywhere: its place is remembered
  relative to Resolve's window and follows it. **Collapse to bubble** (header button or ⌘W) turns it into a
  draggable circle; click the bubble to open it again. While the agent works, a slow pale arc circles the
  bubble's rim. A warm dot means something waits on you: an edit to approve, or a reply that finished while
  the panel was collapsed. The dot clears when you open the panel. ⌥⌘A switches between panel and bubble. The menu bar icon has Follow Resolve, Reset
  Position, Open in Browser, Restart Server and Quit. ⌘A, ⌘C, ⌘V, ⌘X and ⌘Z work in the text box.
- **Context.** While the panel is open, the server polls `current_context` every 2 s (paused during
  agent turns). It reads the project, timeline, page, playhead timecode and the clip under the playhead on
  each track. Each message carries this as a `<resolve_context>` block, so "this clip" means the top clip
  under the playhead. Turn it off with **Extra › Send where I am**.
- **Whole project.** The agent isn't limited to the open timeline. `project_overview` shows every timeline,
  the media pool and settings, `open_timeline` switches, and `resolve_api` reaches anything else in Resolve's
  scripting API.
- **Everything is shown.** Reasoning, replies and every tool call appear in the order they happened. Each
  call shows its arguments and the start of its output, and expands to the full input and output. Work
  done by subagents (the Task/Agent tool) is nested under its call.
- **Steering.** You can type while the agent works. The message reaches it at its next step.
- **Approvals.** With **Ask first** on, every tool that changes the project (and any shell or file
  write) shows an approval card: **Allow** (⌘↵), **Decline** (esc) or **Always allow**. Read-only bridge
  tools never ask. **Full access** skips the cards, so the agent can change anything in the project without
  asking.
- **Model and reasoning.** The picker at the bottom sets the model (Default, Fable, Opus, Sonnet or Haiku)
  and the reasoning effort (low to max). Changes apply from the next message.
- **Context.** The model menu shows how full the chat's context window is (tokens from Claude's latest step
  out of the model's window). **Compact context** there, or typing `/compact` (optionally `/compact <what to
  keep>`), runs Claude Code's `/compact`: the conversation so far is summarized to free space, the transcript
  stays, and a divider marks the point. Claude also compacts on its own when the window fills, and the panel
  marks that too. Codex can't compact.
- **Attachments.** **+** adds the current frame from Resolve, a media pool clip (by reference), or files.
  You can also drop or paste files. Images go to the agent directly; other files go by path, saved under
  the chat workspace.
- **Extra menu.** **Work on a copy** makes the agent duplicate the timeline before its first edit, so the
  original stays untouched. **Send where I am** adds your project, timeline, playhead and the clip under it
  to each message, so "this clip" works.
- **Speech.** "Transcribe this", "add subtitles", "cut my ums": local transcription with whisper.cpp
  (`python3 install.py --with-whisper`). Transcribing is read-only and never asks; placing subtitles and
  marking cuts ask first; cuts are applied on a duplicate timeline.
- **Copy and rewind.** Hover a message to copy it. **Rewind** on one of your messages cuts the chat back
  to just before it: Claude resumes the session at that point as a fork (`--resume-session-at`), so it
  forgets what came after, and your message returns to the box. The original session is kept.
- **Restore points.** With **Restore points before edits** on (the default, in the Access menu), the
  panel saves a verified copy of the current timeline before the AI's first project edit after each
  message: a "Checkpoint · …" timeline in an **Agent Work** bin. When you rewind past edits, the
  warning offers to restore the timeline too: that opens a fresh copy of the checkpoint as "… (rewound)".
  Nothing is deleted. The timeline that was open and the checkpoint both stay. Media imports, new
  timelines and project settings are not undone, and edits made without a restore point can't be.
  Rewind isn't available for messages sent mid-turn or for Codex.
- **Task list.** For multi-step requests the agent lays out a plan with Claude Code's task tools
  (`TaskCreate`/`TaskUpdate`/`TaskList`, enabled with `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`) and ticks it off as it
  goes. The panel shows the list live while it works and keeps the final list in the chat. The tools only
  track progress, so they never ask for approval. A rewind takes the panel's list back with the chat.
- **Rich replies.** Replies render Markdown with GitHub tables and a small allowlist of inline HTML (`kbd`,
  `sub`, `sup`, `mark`, `details`/`summary` and similar); anything else is shown as text.
- **Fusion receipts.** When the agent runs `fusion_build` or `fusion_keyframe_input`, the conversation shows
  the node chain it built and a keyframe ruler for each animated input.

## Install

`python3 install.py` (or the npm/one-line installers) copies `chat/` into `~/.resolve-ai-bridge/chat` and
builds `~/Applications/Resolve AI Chat.app` with the Xcode command line tools. It also adds
**Workspace › Scripts › Resolve AI Bridge › Open AI Chat**. Skip the app with `--no-chat-app`.

Claude Code must be installed and signed in: run `claude` once and type `/login`. The panel shows a
banner if the CLI reports that it is signed out.

## Run from a checkout

```bash
npm ci && npm run build:chat
python3 -m chat.server --open        # opens the panel in your browser
```

Append `?demo` to the URL (before the `#t=` token) to see the panel with synthetic data. The
working directory for agent turns is `~/.resolve-ai-bridge/chat-data/workspace`. Threads and settings
are stored next to it.

## What was verified

- Unit tests: `tests/test_chat.py` covers the token/Host/Origin guards, the thread flow, and the full
  approve/deny round trip. It uses a fake `claude` that speaks the stream-json control protocol.
- Live, on macOS with Claude Code 2.1.318 and Resolve **not** running:
  - streaming replies, the offline banner, and an approval card for a file write approved with ⌘↵
  - the native app starting its server, the `resolveaichat://` link opening it, and quitting it stopping
    the server.

## Not yet verified

- Rewind's project restore against live Resolve. Saving a checkpoint was checked on Resolve 21.2 Free
  (it stays on the open timeline and files the copy into the Agent Work bin); restoring is covered by
  unit tests only. Resolve's UI can briefly show the checkpoint as open while the copy is verified.

- The docked panel's placement over a live Resolve window, and the panel against a live bridge (needs
  Resolve running with Start AI Bridge (Lua)).
- Whether Resolve Free's Lua allows `bmd.openurl` for the **Open AI Chat** menu entry. It falls back to
  `os.execute` and then prints instructions.
- Mac App Store Resolve is sandboxed; the installer then puts the runtime inside Resolve's container
  (see TROUBLESHOOTING.md).
- The bubble, following Resolve, and the keyboard shortcuts inside the native panel have been compiled
  but not watched on screen.
- Compaction: `/compact` over stream-json was checked with Claude Code 2.1.318 (Haiku), including the summary
  carrying the earlier request. The panel side is covered by unit tests with a fake Claude.
- The task list with the real Claude Code: the tool events were captured from Claude Code 2.1.318 (Haiku) and
  replayed in unit tests, not watched in the panel during a real edit.
- Codex: the adapter (`codex exec --json`) is untested here because Codex isn't installed. Codex's
  headless mode has no per-tool approval channel, so its edits run without cards; the panel labels it
  **No approvals**.
