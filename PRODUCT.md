# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One editor (the repository owner) cutting videos in DaVinci Resolve Free on macOS, who wants to talk to an AI agent while editing instead of switching to a terminal. Not published for other editors; onboarding can assume the owner's machine.

## Product Purpose

Resolve AI Chat is a chat panel docked beside DaVinci Resolve. The editor asks for changes in editor language ("slow push on this clip", "glitch transition into the intro") and Claude Code carries them out through the Resolve AI Bridge MCP tools, with an emphasis on Fusion effects, animation, transitions built in Fusion, and keyframes. Success: the editor stays in Resolve, sees what the agent understood (project, timeline, clip under the playhead), approves edits, and sees exactly what changed.

## Positioning

The upstream Resolve AI Bridge is an MCP server used from a separate AI client. This fork adds the panel that lives next to the timeline, always knows the editor's current context, and asks before each edit.

## Operating Context

- DaVinci Resolve Free 21.x on macOS; Resolve's own UI is dark charcoal and dense. The panel is used in a dim edit suite for long sessions.
- The Lua bridge worker is started from Workspace > Scripts > Resolve AI Bridge > Start AI Bridge (Lua).
- Free Resolve cannot host Workflow Integration panels (Studio only), so the panel is a native macOS window that docks to Resolve's window.

## Capabilities and Constraints

- Primary agent: Claude Code (headless stream-json, per-tool Allow/Deny approvals). Codex is secondary and untested on this machine; it has no per-tool approval channel.
- Resolve's scripting API cannot add Edit-page transitions; transitions are built in Fusion on a clip's head or tail.
- The agent must report only what tools confirm (verified flags), never claim unverified success.
- Placement preference: no preference given; default is docked inside Resolve's right edge, with a floating option.

## Product Principles

1. Stay in the edit: the panel should feel like part of Resolve, not a web page next to it.
2. Show what the agent sees and what it did, in editor terms (track, clip, timecode).
3. Ask before changing the project; make approving fast.
4. Honest results over confident ones.
