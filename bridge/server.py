"""MCP server for Resolve AI Bridge.

Talks to DaVinci Resolve over whichever transport is available: a direct attach
that needs nothing started inside Resolve, or the authenticated Console queue.
See ``bridge/transport.py``.

Never print to stdout from this process. MCP uses stdout for JSON-RPC.
"""

import json
import time
import base64
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge import client, transport
from bridge.operations import AGENT_VERSION

try:
    from mcp.server.fastmcp import FastMCP as _MCPServer
    _SERVER_KWARGS: dict[str, Any] = {}
except ImportError:  # mcp 2.x renamed FastMCP to MCPServer; the installer still pins 1.x.
    from mcp.server.mcpserver import MCPServer as _MCPServer
    _SERVER_KWARGS = {"version": AGENT_VERSION}
from mcp.types import TextContent, ImageContent

# Ensure local runtime folders and token exist immediately
client._ensure_dirs()
client._token()


mcp = _MCPServer(
    "Resolve AI Bridge",
    instructions=(
        "You work across the whole project, not just the open timeline. Start with project_overview "
        "(every timeline, media pool bins and clips, settings, render queue), switch timelines with "
        "open_timeline, and inspect the open one with timeline_overview. "
        "Inspect before editing. Call resolve_status, then timeline_overview. "
        "Prefer stable unique item ids from timeline_overview; V1.2 labels change after edits. Or pass item_id='playhead'. "
        "Use preview_timeline before a batch of edits, retaining the original. Verify after each change. Use add_image for stills and set_clip_transform "
        "to position them, animate_zoom for a scale that moves over time, "
        "split_clip to cut a clip in two, create_compound_clip to group clips into "
        "a compound container, and change_clip_speed for slow motion or speed changes. "
        "Fusion effects (zoom, blur, spotlight, saturation, color fx) share one chain before MediaOut; "
        "check it with inspect_fusion. For any other Fusion look, animation, keyframe or clip-level "
        "transition use fusion_build (a node graph inserted before MediaOut), fusion_keyframe_input "
        "(any input of any node) and fusion_inspect_tool (input ids); current_context tells you the "
        "clip under the playhead. Resolve's API cannot add Edit-page transitions directly; add_transitions does Cross Dissolves via FCPXML. "
        "trim_in_place / set_clip_length trim or extend a clip in place (no ripple); add_transitions builds "
        "Cross Dissolves on a new timeline; set_clip_volume sets clip volume. Clips cannot be moved in place. "
        "timeline_frame shows raw source on Resolve Free; render_frames (a short Deliver render, ask first) shows the "
        "finished picture with grades, Fusion and titles. Take snapshot_timeline before a risky step; restore_snapshot "
        "opens a copy of it and deletes nothing. "
        "After starting a render, follow it with render_status. "
        "When no structured tool fits, resolve_api calls any Resolve scripting API method (project, media "
        "pool, timelines, items, render settings); prefer structured tools because they verify results. "
        "Never delete clips or start a render without explicit user approval."
    ),
    **_SERVER_KWARGS,
)

try:  # Report our own version in the MCP handshake instead of the SDK's.
    mcp._mcp_server.version = AGENT_VERSION
except Exception:  # A future SDK may rename this; the handshake still works.
    pass


def _result(operation: str, params: Optional[dict[str, Any]] = None, timeout: float = 30.0) -> str:
    try:
        payload, used = transport.call(operation, params or {}, timeout=timeout)
        return json.dumps({"ok": True, "transport": used, "result": payload}, ensure_ascii=True, indent=2)
    except client.BridgeOffline as exc:
        return json.dumps(
            {"ok": False, "error": str(exc), "help": transport.offline_help()},
            ensure_ascii=True,
            indent=2,
        )
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, indent=2)
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=True,
            indent=2,
        )


def _frames(params: dict[str, Any], end_frame: Optional[int], timeline_start_frame: Optional[int],
            timeline_end_frame: Optional[int]) -> None:
    """Add the optional effect frame arguments that were supplied."""
    for key, value in (("end_frame", end_frame), ("timeline_start_frame", timeline_start_frame),
                       ("timeline_end_frame", timeline_end_frame)):
        if value is not None:
            params[key] = value


# --------------------------------------------------------------- inspection


@mcp.tool()
def resolve_status() -> str:
    """Check how the bridge is connected, plus the Resolve version, open project, and timeline. Always call this first."""
    report = {"bridge_version": AGENT_VERSION, "connection": transport.describe()}
    try:
        payload, used = transport.call("status", {}, timeout=15.0)
        report.update({"ok": True, "transport": used, "result": payload})
    except Exception as exc:
        report.update({"ok": False, "error": str(exc), "help": transport.offline_help()})
    return json.dumps(report, ensure_ascii=True, indent=2)


@mcp.tool()
def project_info() -> str:
    """Return the current project's name, timeline, frame rate, resolution, and timeline count."""
    return _result("project_info")


@mcp.tool()
def list_timelines() -> str:
    """List every timeline in the open project and identify the current one."""
    return _result("list_timelines")


@mcp.tool()
def timeline_overview(max_items: int = 500, detail: str = "compact") -> str:
    """Inspect clips (stable unique ids, positional labels V1.2, frame ranges), tracks, markers and transitions.

    detail: "compact" (default: id, label, name, start, end, plus enabled/color when set), "standard" (adds offsets,
    track, duration, media id) or "full" (adds src_in/src_out source frames, media_fps, audio_volume, Fusion comp
    count and transition_in/out names). Transitions (Cross Dissolve, Cross Fade 0 dB...) are listed separately under
    `transitions` and never counted as clips, so labels stay stable. Frames are absolute; end is exclusive.
    """
    return _result("timeline_overview", {"max_items": max_items, "detail": detail})


@mcp.tool()
def timeline_frame(
    timecode: Optional[str] = None,
    frame: Optional[int] = None,
    max_width: int = 1280,
    format: str = "jpg",
    mode: str = "auto",
) -> list[TextContent | ImageContent]:
    """Return a native MCP image of the requested absolute timeline frame or timecode.

    Auto attempts a composited still, then Free-compatible ffmpeg source extraction.
    Source fallback excludes timeline transforms, grades, Fusion and overlays.
    mode='composite' fails explicitly when a true timeline still is unavailable.
    """
    payload = json.loads(_result("timeline_frame", {"timecode": timecode, "frame": frame,
                          "max_width": max_width, "format": format, "mode": mode}, timeout=100))
    if not payload.get("ok"):
        return [TextContent(type="text", text=json.dumps(payload))]
    result = payload["result"]
    data = result.pop("image_base64", None)
    try:
        if not data:
            raise ValueError("empty image")
        base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "Bridge returned invalid image data."}))]
    mime = "image/png" if result.get("format") == "png" else "image/jpeg"
    return [TextContent(type="text", text=json.dumps(payload)), ImageContent(type="image", data=data, mimeType=mime)]


@mcp.tool()
def timeline_audio(
    action: str = "analyze",
    track_index: int = 1,
    item_id: Optional[str] = None,
    silence_threshold_db: float = -40.0,
    min_silence_duration: float = 0.3,
    max_duration_seconds: float = 300,
    whole_clip: bool = False,
    lead_offset_ms: float = 160,
    threshold_db: float = -34,
) -> str:
    """Analyze selected source PCM, excluding Fairlight gain, fades, mute and the timeline mix.

    Actions: analyze (per-channel peak/RMS), silence_cuts, energy_envelope,
    export_slice, onsets, vad_clusters. Defaults to the range from playhead to clip end;
    whole_clip starts at the clip in-point. Decode is capped at max_duration_seconds.
    Cut end frames are exclusive; timeline_* fields use absolute timeline coordinates.
    Known retiming is rejected rather than guessed. Needs ffmpeg or macOS afconvert.
    """
    return _result("timeline_audio", {"action": action, "track_index": track_index, "item_id": item_id,
                   "silence_threshold_db": silence_threshold_db, "min_silence_duration": min_silence_duration,
                   "max_duration_seconds": max_duration_seconds, "whole_clip": whole_clip,
                   "lead_offset_ms": lead_offset_ms, "threshold_db": threshold_db}, timeout=120)


@mcp.tool()
def list_media(limit: int = 1000, query: str = "", kind: str = "all") -> str:
    """List media pool items recursively with unique ids, bin paths, file paths, frame counts, and durations.

    query: case-insensitive text in the clip name or file path (exact name/file name first, then names starting
    with it). kind: "all", "clips" (media only, no timelines) or "timelines". When filtering, files that are in
    the pool more than once are grouped under same_file_entries.
    """
    params: dict[str, Any] = {"limit": limit}
    if query:
        params["query"] = query
    if kind and kind != "all":
        params["kind"] = kind
    return _result("list_media", params)


@mcp.tool()
def list_render_presets() -> str:
    """List the render presets and formats available in this project, for use with render_current_timeline."""
    return _result("list_render_presets")


@mcp.tool()
def preview_timeline(name: Optional[str] = None, bin: Optional[str] = None) -> str:
    """Duplicate and open the active timeline for reviewing edits while retaining the original.

    No edits are applied by this call. Get fresh clip IDs with timeline_overview,
    edit the preview, and compare_timelines before accepting it.
    """
    return _result("preview_timeline", {"name": name, "bin": bin}, timeout=90)


@mcp.tool()
def snapshot_timeline(label: str = "snapshot") -> str:
    """Mid-task undo point: save a verified copy of the open timeline as it is now, and stay on the original.

    Take one before a risky step. The copy is named "Checkpoint · <timeline> · <label>" and filed in the
    "Agent Work" bin (where every bridge-made timeline goes, chat checkpoints included). Returns its id for restore_snapshot.
    """
    return _result("checkpoint_timeline", {"label": str(label or "snapshot")[:60]}, timeout=180.0)


@mcp.tool()
def list_snapshots(limit: int = 100) -> str:
    """Read-only: snapshot/checkpoint timelines in this project (id, name, source timeline, label), oldest first."""
    return _result("list_checkpoints", {"limit": limit}, timeout=60.0)


@mcp.tool()
def restore_snapshot(snapshot_id: str) -> str:
    """Go back to a snapshot by opening a NEW copy of it ("<timeline> (rewound)"). Nothing is deleted.

    The timeline that was open stays exactly as it was, and the snapshot stays untouched so it can be restored
    again. Edits made after the snapshot are therefore still on the previous timeline. Tell the user which
    timeline is now open. snapshot_id comes from snapshot_timeline or list_snapshots.
    """
    return _result("restore_checkpoint", {"checkpoint_id": snapshot_id}, timeout=180.0)


@mcp.tool()
def list_ai_timelines() -> str:
    """Read-only: list the duplicate timelines this bridge created (previews, "(tightened)" copies,
    "(rewound)" copies, "Before split" checkpoints, chat panel "Checkpoint · …" restore points).

    Recognised by the bridge's "<name> — <8 hex>" naming; user timelines are never removable.
    Gives each one's unique ID, kind, whether it is open, clip count, duration and likely original.
    Opens, moves and deletes nothing. Call before cleanup_ai_timelines and show the user the list.
    """
    return _result("list_ai_timelines", timeout=120.0)


@mcp.tool()
def cleanup_ai_timelines(timeline_ids: list[str], action: str = "archive", include_checkpoints: bool = False,
                         user_approved: bool = False) -> str:
    """Tidy away bridge-made timelines by unique ID: action="archive" moves them into the "Agent Work" bin (the same bin every bridge-made timeline is filed into)
    (reversible; the media pool view is kept), action="delete" removes them from the project.

    First call list_ai_timelines, show the user which timelines would go, and ask. Deleting cannot be
    undone through the API and requires user_approved=true after the user said yes. Refuses user
    timelines and the open timeline. Chat panel checkpoints are skipped unless include_checkpoints=true
    (deleting one breaks Rewind's "restore the timeline" for that message). Reports exactly what was
    archived/deleted and what failed, verified by re-listing timelines afterwards.
    """
    action = str(action or "archive").strip().lower()
    if action == "delete" and not user_approved:
        return json.dumps(
            {
                "ok": False,
                "error": "Deleting timelines requires explicit user approval. List them with list_ai_timelines, "
                         "ask the user, then retry with user_approved=true. Or use action=\"archive\".",
            },
            indent=2,
        )
    return _result("cleanup_ai_timelines", {"timeline_ids": timeline_ids, "action": action,
                                            "include_checkpoints": include_checkpoints}, timeout=180.0)


@mcp.tool()
def compare_timelines(original: str, preview: str) -> str:
    """Compare two timelines by unique ID or exact name without changing the active timeline.

    Reports structural/property/marker changes; does not compare rendered pixels,
    internal Fusion graphs, full grades or Fairlight processing.
    """
    return _result("compare_timelines", {"original": original, "preview": preview})


@mcp.tool()
def project_health(expected_frame_rate: Optional[float] = None, expected_width: Optional[int] = None,
                   expected_height: Optional[int] = None) -> str:
    """Inspect active-timeline missing sources, gaps, disabled/locked tracks and clips.

    Optionally compare timeline resolution/rate against your delivery requirements.
    Gaps and disabled tracks can be intentional; this does not inspect Fairlight automation.
    """
    return _result("project_health", {"expected_frame_rate": expected_frame_rate,
                   "expected_width": expected_width, "expected_height": expected_height})


@mcp.tool()
def bridge_capabilities() -> str:
    """Report Resolve version, transport, API presence and source decoder availability.

    API presence does not guarantee operation success. New review workflows use
    ordinary timeline APIs. Python startup is verified on macOS Free 21.0.3.7.
    Free 21.1+ uses the Lua native-file worker; Python runs outside Resolve.
    """
    return _result("bridge_capabilities")


@mcp.tool()
def review_silence(item_id: Optional[str] = None, track_index: int = 1,
                   silence_threshold_db: float = -40, min_silence_duration: float = 0.3,
                   max_duration_seconds: float = 300) -> str:
    """Analyze source audio for a clip and place yellow review markers; never deletes clips.

    Analysis starts at the clip's in-point and excludes Fairlight processing.
    Audition the markers, then pass accepted marker IDs to apply_silence_cuts.
    For dialogue, tighten now covers this in one call (plan or apply, floor-aware word guards).
    """
    return _result("review_silence", {"item_id": item_id, "track_index": track_index,
                   "silence_threshold_db": silence_threshold_db, "min_silence_duration": min_silence_duration,
                   "max_duration_seconds": max_duration_seconds}, timeout=120)


@mcp.tool()
def apply_silence_cuts(marker_ids: list[str], name: Optional[str] = None) -> str:
    """Remove USER-ACCEPTED silence intervals on a new duplicate timeline, retaining the original.

    Supports an isolated normal-speed dialogue clip or one aligned video/audio pair.
    Complex timelines, Fusion clips, changed timelines and mixed frame rates are rejected.
    Requires explicit user approval of the selected cuts. Review the resulting preview:
    rebuilding does not guarantee preservation of fades, gain automation or all metadata.
    For dialogue, tighten now covers this in one call (plan or apply, floor-aware word guards).
    """
    return _result("apply_silence_cuts", {"marker_ids": marker_ids, "name": name}, timeout=120)


# ---------------------------------------------------------------- navigation


@mcp.tool()
def open_timeline(name: Optional[str] = None, index: Optional[int] = None, timeline_id: Optional[str] = None) -> str:
    """Open a timeline by unique ID, exact name or one-based index from list_timelines."""
    return _result("open_timeline", {"name": name, "index": index, "timeline_id": timeline_id})


@mcp.tool()
def create_timeline(name: str, fps: Optional[str] = None, width: Optional[int] = None, height: Optional[int] = None,
                    bin: Optional[str] = None) -> str:
    """Create and open a new empty timeline with an exact name, filed into `bin` ("" = the current bin).

    fps (a string: 23.976, 24, 25, 29.97, 30, 47.952, 48, 50, 59.94, 60) and width/height are set while it is still
    empty (the only time Free accepts it) and read back: on Resolve 21.2 Free only "30" was accepted in testing.
    Warns when the timeline rate differs from most of the media pool's video.
    """
    params: dict[str, Any] = {"name": name, "bin": bin}
    for key, value in (("fps", fps), ("width", width), ("height", height)):
        if value is not None:
            params[key] = value
    return _result("create_timeline", params)


@mcp.tool()
def set_playhead(timecode: str) -> str:
    """Move the current timeline playhead. Use HH:MM:SS:FF matching the timeline frame rate."""
    return _result("set_playhead", {"timecode": timecode})


@mcp.tool()
def open_page(page: str) -> str:
    """Switch the Resolve window to a page so the user can see a change: media, cut, edit, fusion, color, fairlight, or deliver."""
    return _result("open_page", {"page": page})


# --------------------------------------------------------------------- media


@mcp.tool()
def import_media(paths: list[str]) -> str:
    """Import existing local media files by absolute path into the Media Pool. This does not place them on the timeline."""
    return _result("import_media", {"paths": paths}, timeout=90.0)


@mcp.tool()
def append_media(
    media_ids: Optional[list[str]] = None,
    paths: Optional[list[str]] = None,
    track_index: Optional[int] = None,
    record_frame: Optional[int] = None,
    create_track: bool = True,
    media_type: Optional[int] = None,
) -> str:
    """Append video or audio to the timeline from media pool ids or absolute file paths.

    Omit track_index and record_frame to append at the end of the timeline. For still
    images use add_image instead, which controls how long the still lasts.
    Audio and video are retained by default. Use media_type=1 for video only
    or media_type=2 for audio only; track_index then selects that track type.
    """
    return _result(
        "append_media",
        {
            "media_ids": media_ids or [],
            "paths": paths or [],
            "track_index": track_index,
            "record_frame": record_frame,
            "create_track": create_track,
            "media_type": media_type,
        },
        timeout=90.0,
    )


@mcp.tool()
def add_image(
    path: str,
    duration_seconds: float = 5.0,
    duration_frames: Optional[int] = None,
    track_index: int = 1,
    record_frame: Optional[int] = None,
    at_playhead: bool = False,
    create_track: bool = True,
    pan_percent: Optional[float] = None,
    tilt_percent: Optional[float] = None,
    zoom: Optional[float] = None,
    rotation: Optional[float] = None,
    opacity: Optional[float] = None,
) -> str:
    """Put an image on the timeline: import the still, place it with a real duration, and optionally position it.

    This is the tool for logos, screenshots, overlays, title cards, and any generated
    picture. Use track_index=2 or higher to lay the image over existing footage; the
    track is created automatically when missing. Set at_playhead=True to place it at
    the current playhead instead of the end of the track.

    pan_percent and tilt_percent move the image as a percentage of frame width and
    height from centre. zoom is a scale multiplier where 1.0 is the original size.
    opacity runs from 0 to 100. Verify with timeline_overview afterwards, and read the
    returned actual_duration_frames: Resolve can shorten a still.
    """
    return _result(
        "add_image",
        {
            "path": path,
            "duration_seconds": duration_seconds,
            "duration_frames": duration_frames,
            "track_index": track_index,
            "record_frame": record_frame,
            "at_playhead": at_playhead,
            "create_track": create_track,
            "pan_percent": pan_percent,
            "tilt_percent": tilt_percent,
            "zoom": zoom,
            "rotation": rotation,
            "opacity": opacity,
        },
        timeout=90.0,
    )


@mcp.tool()
def place_still(path: str, frames: Optional[int] = None, seconds: Optional[float] = None, track_index: int = 2,
                record_frame: Optional[int] = None, at_playhead: bool = False, pan_percent: Optional[float] = None,
                tilt_percent: Optional[float] = None, zoom: Optional[float] = None, opacity: Optional[float] = None) -> str:
    """Place a photo/logo/still for exactly `frames` (or `seconds`) at record_frame (absolute) on track_index; the
    same as add_image (kept as an alias). The length is set with media In/Out marks, which are restored after."""
    return add_image(path, duration_seconds=5.0 if seconds is None else seconds, duration_frames=frames,
                     track_index=track_index, record_frame=record_frame, at_playhead=at_playhead,
                     pan_percent=pan_percent, tilt_percent=tilt_percent, zoom=zoom, opacity=opacity)


@mcp.tool()
def place_media(media_id: str, src_in: Optional[int] = None, src_out: Optional[int] = None,
                record_frame: Optional[int] = None, track_index: int = 1, kind: str = "av") -> str:
    """Place a media pool clip on the timeline. With src_in/src_out (source frames, src_out EXCLUSIVE, like
    AppendToTimeline's endFrame) and record_frame it drops exactly that range into empty space (insert_clip: overlaps
    are refused); without them it appends the whole clip at the end of the track (append_media). kind: av, v or a.
    """
    if src_in is not None or src_out is not None:
        if src_in is None or src_out is None or record_frame is None:
            return json.dumps({"ok": False, "error": "Pass src_in, src_out and record_frame together."})
        return insert_clip(media_id, src_in, src_out, record_frame, track_index=track_index, kind=kind)
    media_type = {"av": None, "v": 1, "a": 2}.get(str(kind).lower(), None)
    return append_media(media_ids=[media_id], track_index=track_index, record_frame=record_frame, media_type=media_type)


@mcp.tool()
def fusion_inspect(item_id: str = "playhead", tool: Optional[str] = None, inputs: Optional[list[str]] = None,
                   search: str = "", frame: int = 0, track_index: Optional[int] = None) -> str:
    """Read-only Fusion inspection in one tool. Without `tool`: the clip's nodes, connections, bridge chain and
    frame_format (comp width/height, render range, the MediaIn source file; mask and Merge coordinates are relative to
    this comp image, which is the source media's size). With `tool`: that node's inputs, values at clip offset
    `frame`, animation and keyframes (`inputs` exact ids, `search` substring). Replaces inspect_fusion +
    fusion_inspect_tool, which remain as aliases.
    """
    if tool:
        return fusion_inspect_tool(tool, item_id=item_id, frame=frame, inputs=inputs, search=search, track_index=track_index)
    return inspect_fusion(item_id=item_id, track_index=track_index)


@mcp.tool()
def get_preferences() -> str:
    """Read-only: this project's saved editor preferences (working timeline, agent bin, tighten defaults, loudness
    target, notes) from the bridge home. Tools default from them."""
    return _result("get_preferences", {}, timeout=20.0)


@mcp.tool()
def set_preferences(updates: dict[str, Any]) -> str:
    """Save per-project editor preferences in the bridge home (null removes one): working_timeline_id, agent_bin
    (bin for bridge-made timelines; "" = none), tighten_lead_frames, tighten_tail_frames, tighten_max_pause_frames,
    loudness_target_lufs, notes. Changes nothing in Resolve. Set them when the user states a preference."""
    return _result("set_preferences", {"updates": updates}, timeout=20.0)


# ------------------------------------------------------------ timeline edits


@mcp.tool()
def add_track(track_type: str = "video", count: int = 1) -> str:
    """Add one or more empty tracks. track_type is video, audio, or subtitle."""
    return _result("add_track", {"track_type": track_type, "count": count})


@mcp.tool()
def set_track_name(track_index: int, name: str, track_type: str = "video") -> str:
    """Rename one track, for example labelling V2 as Overlays."""
    return _result(
        "set_track_name", {"track_index": track_index, "name": name, "track_type": track_type}
    )


@mcp.tool()
def set_clip_transform(
    item_id: str,
    pan: Optional[float] = None,
    tilt: Optional[float] = None,
    pan_percent: Optional[float] = None,
    tilt_percent: Optional[float] = None,
    zoom: Optional[float] = None,
    zoom_x: Optional[float] = None,
    zoom_y: Optional[float] = None,
    rotation: Optional[float] = None,
    anchor_x: Optional[float] = None,
    anchor_y: Optional[float] = None,
    opacity: Optional[float] = None,
    composite_mode: Optional[str] = None,
    flip_x: Optional[bool] = None,
    flip_y: Optional[bool] = None,
    crop_left: Optional[float] = None,
    crop_right: Optional[float] = None,
    crop_top: Optional[float] = None,
    crop_bottom: Optional[float] = None,
    scaling: Optional[str] = None,
    resize_filter: Optional[str] = None,
    dynamic_zoom_ease: Optional[str] = None,
    retime_process: Optional[str] = None,
    motion_estimation: Optional[str] = None,
    track_index: Optional[int] = None,
) -> str:
    """Position, scale, rotate, fade, crop, or blend one timeline item.

    Use an id such as V2.1 from timeline_overview, or pass item_id="playhead" to
    act on the clip currently under the playhead so you need not look up the id.
    pan and tilt are in pixels from centre; pan_percent and tilt_percent are the
    same move as a percentage of the timeline width and height. zoom sets both
    axes at once, where 1.0 is original size; zoom_x and zoom_y scale one axis.
    opacity runs from 0 to 100. composite_mode accepts names such as normal,
    screen, multiply, add, overlay, or alpha. scaling is crop, fit, fill, or
    stretch. resize_filter, retime_process, and motion_estimation accept the
    Resolve mode names. This sets a static transform; for a scale that animates
    over time use animate_zoom instead.
    """
    return _result(
        "set_clip_transform",
        {
            "item_id": item_id,
            "pan": pan,
            "tilt": tilt,
            "pan_percent": pan_percent,
            "tilt_percent": tilt_percent,
            "zoom": zoom,
            "zoom_x": zoom_x,
            "zoom_y": zoom_y,
            "rotation": rotation,
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "opacity": opacity,
            "composite_mode": composite_mode,
            "flip_x": flip_x,
            "flip_y": flip_y,
            "crop_left": crop_left,
            "crop_right": crop_right,
            "crop_top": crop_top,
            "crop_bottom": crop_bottom,
            "scaling": scaling,
            "resize_filter": resize_filter,
            "dynamic_zoom_ease": dynamic_zoom_ease,
            "retime_process": retime_process,
            "motion_estimation": motion_estimation,
            "track_index": track_index,
        },
    )


@mcp.tool()
def get_clip_transform(item_id: str = "playhead", track_index: Optional[int] = None) -> str:
    """Read the current position, scale, rotation, crop, opacity, and blend mode of one timeline item.

    Pass an id such as V2.1, or leave item_id as "playhead" to read the clip under the playhead.
    """
    return _result("get_clip_transform", {"item_id": item_id, "track_index": track_index})


@mcp.tool()
def split_clip(
    item_id: str = "playhead",
    frame: Optional[int] = None,
    timecode: Optional[str] = None,
    track_index: Optional[int] = None,
) -> str:
    """Split a normal-speed clip at an absolute frame/timecode after creating a timeline checkpoint.

    Keeps static transforms, enabled state, color and current grade layer. Rejects Fusion
    compositions and mixed rates; audio links/fades/metadata are not guaranteed.
    On a failed rebuild, opens the checkpoint and reports the partial attempted timeline.
    Prefer unique IDs from timeline_overview. Verify the resulting halves.
    """
    return _result(
        "split_clip",
        {"item_id": item_id, "frame": frame, "timecode": timecode, "track_index": track_index},
    )


@mcp.tool()
def animate_zoom(
    item_id: str = "playhead",
    start_zoom: Optional[float] = None,
    end_zoom: Optional[float] = None,
    easing: str = "smootherstep",
    direction: str = "in",
    preset: str = "",
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    target_center_x: float = 0.5,
    target_center_y: float = 0.5,
    keyframes: Optional[list[dict[str, Any]]] = None,
    reset: bool = False,
    track_index: Optional[int] = None,
    timeline_start_frame: Optional[int] = None,
    timeline_end_frame: Optional[int] = None,
) -> str:
    """Animate a clip's scale and framing over time (smooth zooms, punch-ins, rebounds, pull-outs, multi-keyframe paths).

    Supports full Zoom In and Zoom Out (`direction='out'`), all standard easing curves
    (`linear`, `ease_in`, `cubic_in`, `ease_out`, `cubic_out`, `ease`, `cubic_ease`,
    `circular_ease`, `rebound_in`, `rebound_out`, `elastic_out`), multi-keyframe arrays,
    and automated AI presets (`punch_in`, `pop_in`, `slow_push`, `dramatic`, `reveal`, `cinematic`).
    Joins the bridge's Fusion chain: blur, spotlight and your own Fusion nodes stay connected.
    reset=True removes only the bridge zoom node.
    Frames: start_frame/end_frame and keyframe 'frame' count from the clip's first frame (0).
    timeline_start_frame/timeline_end_frame and keyframe 'timeline_frame' take absolute timeline frames.
    """
    params: dict[str, Any] = {
        "item_id": item_id,
        "easing": easing,
        "direction": direction,
        "preset": preset,
        "start_frame": start_frame,
        "target_center_x": target_center_x,
        "target_center_y": target_center_y,
        "reset": reset,
    }
    if keyframes is not None:
        params["keyframes"] = keyframes
    if start_zoom is not None:
        params["start_zoom"] = start_zoom
    if end_zoom is not None:
        params["end_zoom"] = end_zoom
    _frames(params, end_frame, timeline_start_frame, timeline_end_frame)
    if track_index is not None:
        params["track_index"] = track_index
    return _result("animate_zoom", params, timeout=60.0)


@mcp.tool()
def change_clip_speed(
    item_id: Optional[str] = "playhead",
    speed: Optional[float] = None,
    speed_percent: Optional[float] = None,
    slow_down_percent: Optional[float] = None,
    speed_up_percent: Optional[float] = None,
    interpolate_frames: bool = True,
    reverse: bool = False,
    method: str = "fusion",
) -> str:
    """Change constant video speed through a bridge-owned Fusion TimeSpeed node.

    Existing Fusion connections are preserved. Timeline duration and linked audio are unchanged.
    Speed 1 neutralizes only bridge-owned nodes. Reverse is not currently verified.
    Clip-attributes mode changes EVERY use of the source media, relative to its current FPS.

    Args:
        item_id: Target clip id (e.g. 'V1.1', 'playhead', or clip name). Defaults to active clip under playhead.
        speed: Speed multiplier (e.g. 0.75 for 75% speed / 25% slower, 0.5 for half-speed, 2.0 for 2x speed, 1.0 for normal).
        speed_percent: Speed as a percentage (e.g. 75.0 for 75% speed).
        slow_down_percent: Slowdown percentage (e.g. 25.0 to slow down by 25% -> 0.75x speed).
        speed_up_percent: Speedup percentage (e.g. 50.0 to speed up by 50% -> 1.5x speed).
        interpolate_frames: Whether to enable smooth frame interpolation/blending (default True).
        reverse: Must be False; use Resolve manually for reverse playback.
        method: Implementation method: 'fusion' (native TimeSpeed node, default) or 'clip_attributes' (MediaPoolItem FPS).
    """
    return _result(
        "change_clip_speed",
        {
            "item_id": item_id,
            "speed": speed,
            "speed_percent": speed_percent,
            "slow_down_percent": slow_down_percent,
            "speed_up_percent": speed_up_percent,
            "interpolate_frames": interpolate_frames,
            "reverse": reverse,
            "method": method,
        },
    )


@mcp.tool()
def create_compound_clip(
    item_ids: Optional[list[str]] = None,
    item_id: Optional[str] = "playhead",
    name: Optional[str] = None,
    start_timecode: Optional[str] = None,
) -> str:
    """Create a compound clip combining one or more timeline items into a single container clip.

    Args:
        item_ids: List of timeline item ids to combine (e.g. ['V1.1', 'A1.1'] or ['A1.1']). If omitted, item_id is used.
        item_id: Single timeline item id to turn into a compound clip (e.g. 'V1.1' or 'playhead'). Defaults to 'playhead'.
        name: Name for the newly created compound clip (e.g. 'Voice Compound Clip').
        start_timecode: Optional custom starting timecode for the compound clip container.
    """
    return _result(
        "create_compound_clip",
        {
            "item_ids": item_ids,
            "item_id": item_id,
            "name": name,
            "start_timecode": start_timecode,
        },
    )


@mcp.tool()
def insert_title(
    title_name: str = "Text+",
    text: Optional[str] = None,
    opacity: Optional[float] = None,
) -> str:
    """Insert a title at the playhead on the current video track, best effort.

    Available title names depend on the Resolve version and installed templates;
    Text+ and Text are the common ones. Setting the text through scripting is not
    supported on every build, so check text_set in the result and tell the user to
    type it in the Inspector if it is false. For designed animated typography,
    Remotion produces a better result.
    """
    return _result("insert_title", {"title_name": title_name, "text": text, "opacity": opacity})


@mcp.tool()
def add_marker(
    name: str,
    note: str = "",
    color: str = "Blue",
    frame: Optional[int] = None,
    duration: int = 1,
) -> str:
    """Add a timeline marker. Omit frame to use the current playhead; frame values are relative to timeline start."""
    return _result(
        "add_marker",
        {"name": name, "note": note, "color": color, "frame": frame, "duration": duration},
    )


@mcp.tool()
def delete_marker(frame: int) -> str:
    """Delete the timeline marker at a frame relative to timeline start."""
    return _result("delete_marker", {"frame": frame})


@mcp.tool()
def set_clip_property(item_id: str, property_name: str, value: Any) -> str:
    """Set any documented Resolve timeline item property directly. Prefer set_clip_transform for common changes."""
    return _result(
        "set_clip_property",
        {"item_id": item_id, "property_name": property_name, "value": value},
    )


@mcp.tool()
def set_clip_enabled(item_id: str, enabled: bool) -> str:
    """Enable or disable one timeline item without deleting it."""
    return _result("set_clip_enabled", {"item_id": item_id, "enabled": enabled})


@mcp.tool()
def set_clip_color(item_id: str, color: str = "") -> str:
    """Set a standard Resolve clip color, or pass an empty string to clear it."""
    return _result("set_clip_color", {"item_id": item_id, "color": color})


@mcp.tool()
def match_shot(reference_item_id: str, target_item_id: str, strength: float = 1.0, apply: bool = False) -> list:
    """Make one clip's color match another ("make clip 12 match clip 3") by measuring, not eyeballing.

    Samples ~10 source frames from each clip's used range, compares per-channel 5/50/95 percentiles and
    saturation, and solves an ASC CDL (slope/offset/power/saturation) for the target. strength 0-1 blends
    toward no change. Returns the numbers, residual error before and predicted after, and a preview image:
    reference | target before | target after, from SOURCE frames with the proposed CDL (not Resolve's render).
    apply=False (default) changes nothing. ASK THE USER before calling with apply=True, which sets the CDL on the
    target's node 1 and reads it back to verify. Limits: it matches source media, not existing grades; a graded
    reference is matched by its ungraded look; lighting and white-balance differences beyond a CDL remain.
    """
    from bridge import colormatch

    def _bridge(op: str, params: dict[str, Any]) -> Any:
        # Each op scans the timeline to find clips by id; long timelines over the Lua transport take a while.
        return transport.call(op, params, timeout=150.0)[0]

    try:
        report, preview = colormatch.match_shot(_bridge, _ffmpeg(), reference_item_id, target_item_id,
                                                strength=strength, apply=apply)
    except colormatch.MatchError as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": str(exc)}))]
    except client.BridgeError as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))]
    content: list = [TextContent(type="text", text=json.dumps({"ok": True, "result": report}, ensure_ascii=False))]
    if preview:
        content.append(ImageContent(type="image", data=base64.b64encode(Path(preview).read_bytes()).decode("ascii"),
                                    mimeType="image/png"))
    return content


@mcp.tool()
def get_clip_grade(item_id: str) -> str:
    """Inspect color grading nodes, versions, and Fusion compositions on a clip."""
    return _result("get_clip_grade", {"item_id": item_id})


@mcp.tool()
def set_clip_grade(
    item_id: str = "",
    saturation: Optional[float] = None,
    slope: str = "1.0 1.0 1.0",
    offset: str = "0.0 0.0 0.0",
    power: str = "1.0 1.0 1.0",
    node_index: int = 1,
    reset: bool = False,
    item_ids: Optional[list[str]] = None,
    range: Optional[dict[str, Any]] = None,
) -> str:
    """Apply ASC-CDL color grading (saturation, slope/gain, offset/lift, power/gamma) to one clip, or to many with
    item_ids or range {start_frame, end_frame (exclusive), track}. Pass saturation=0.0 for black & white. Each clip
    reports `applied` and `verified` (GetCDL read-back; null when Resolve can't report it).
    """
    return _result(
        "set_clip_grade",
        {
            "item_ids": item_ids,
            "range": range,
            "item_id": item_id,
            "saturation": saturation,
            "slope": slope,
            "offset": offset,
            "power": power,
            "node_index": node_index,
            "reset": reset,
        },
        timeout=180.0,
    )


@mcp.tool()
def keyframe_clip_saturation(
    item_id: str,
    start_saturation: float = 1.0,
    end_saturation: float = 0.0,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    easing: str = "linear",
    reset: bool = False,
    timeline_start_frame: Optional[int] = None,
    timeline_end_frame: Optional[int] = None,
) -> str:
    """Animate saturation (e.g. 1.0 full color to 0.0 black and white) with a bridge-owned Fusion node.

    Your own ColorCorrector/BrightnessContrast nodes are never changed. reset=True removes the bridge node.
    Frames: start_frame/end_frame and keyframe 'frame' count from the clip's first frame (0).
    timeline_start_frame/timeline_end_frame and keyframe 'timeline_frame' take absolute timeline frames.
    """
    params: dict[str, Any] = {
        "item_id": item_id,
        "start_saturation": start_saturation,
        "end_saturation": end_saturation,
        "start_frame": start_frame,
        "easing": easing,
        "reset": reset,
    }
    _frames(params, end_frame, timeline_start_frame, timeline_end_frame)
    return _result("keyframe_clip_saturation", params, timeout=60.0)


@mcp.tool()
def animate_color_fx(
    item_id: str,
    rainbow: bool = True,
    rainbow_cycles: float = 1.5,
    tint_strength: float = 0.70,
    flicker: bool = True,
    flicker_amplitude: float = 0.05,
    flicker_frequency: float = 2.2,
    saturation: float = 1.3,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    reset: bool = False,
    timeline_start_frame: Optional[int] = None,
    timeline_end_frame: Optional[int] = None,
) -> str:
    """Animate rainbow hue rotation and a subtle luminance flicker with a bridge-owned Fusion ColorCorrector.

    Reports any part this build's ColorCorrector cannot do. reset=True removes the bridge node.
    Frames: start_frame/end_frame and keyframe 'frame' count from the clip's first frame (0).
    timeline_start_frame/timeline_end_frame and keyframe 'timeline_frame' take absolute timeline frames.
    """
    params: dict[str, Any] = {
        "item_id": item_id,
        "rainbow": rainbow_cycles if rainbow else 0.0,
        "tint_strength": tint_strength,
        "flicker": flicker_amplitude if flicker else 0.0,
        "flicker_frequency": flicker_frequency,
        "saturation": saturation,
        "start_frame": start_frame,
        "reset": reset,
    }
    _frames(params, end_frame, timeline_start_frame, timeline_end_frame)
    return _result("animate_color_fx", params, timeout=60.0)


@mcp.tool()
def apply_blur_effect(
    item_id: str = "playhead",
    blur_type: str = "gaussian",
    blur_size: float = 20.0,
    center_x: float = 0.50,
    center_y: float = 0.50,
    width: float = 1.0,
    height: float = 1.0,
    corner_radius: float = 0.0,
    soft_edge: float = 0.015,
    mask_shape: str = "rectangle",
    mask_name: str = "1",
    animate: bool = False,
    start_blur: float = 0.0,
    end_blur: float = 30.0,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    easing: str = "smootherstep",
    reset: bool = False,
    track_index: Optional[int] = None,
    keyframes: Optional[list[dict[str, Any]]] = None,
    timeline_start_frame: Optional[int] = None,
    timeline_end_frame: Optional[int] = None,
) -> str:
    """Apply a static or gradual blur to the full frame or a masked region (gaussian, defocus, directional).

    Each mask_name is its own blur; reusing a name updates it. Keyframes may set frame, blur_size,
    center_x, center_y, width, height and easing. Joins the bridge's Fusion chain without cutting out
    your own nodes. reset=True removes every bridge blur and leaves other nodes connected.
    Frames: start_frame/end_frame and keyframe 'frame' count from the clip's first frame (0).
    timeline_start_frame/timeline_end_frame and keyframe 'timeline_frame' take absolute timeline frames.
    """
    params: dict[str, Any] = {
        "item_id": item_id,
        "blur_type": blur_type,
        "blur_size": blur_size,
        "center_x": center_x,
        "center_y": center_y,
        "width": width,
        "height": height,
        "corner_radius": corner_radius,
        "soft_edge": soft_edge,
        "mask_shape": mask_shape,
        "mask_name": mask_name,
        "animate": animate,
        "start_blur": start_blur,
        "end_blur": end_blur,
        "start_frame": start_frame,
        "easing": easing,
        "reset": reset,
    }
    if keyframes is not None:
        params["keyframes"] = keyframes
    _frames(params, end_frame, timeline_start_frame, timeline_end_frame)
    if track_index is not None:
        params["track_index"] = track_index
    return _result("apply_blur_effect", params, timeout=60.0)


@mcp.tool()
def apply_spotlight_mask(
    item_id: str = "playhead",
    center_x: float = 0.50,
    center_y: float = 0.50,
    radius: float = 0.20,
    width: Optional[float] = None,
    height: Optional[float] = None,
    soft_edge: float = 0.08,
    ambient_brightness: float = 0.08,
    spotlight_gain: float = 1.0,
    shape: str = "ellipse",
    mask_name: str = "1",
    keyframes: Optional[list[dict[str, Any]]] = None,
    animate: bool = False,
    expand: bool = False,
    start_radius: float = 0.20,
    end_radius: float = 2.0,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    easing: str = "cubic_out",
    reset: bool = False,
    track_index: Optional[int] = None,
    timeline_start_frame: Optional[int] = None,
    timeline_end_frame: Optional[int] = None,
) -> str:
    """Apply a static or animated spotlight: dim everything outside a soft mask, with sweep keyframes.

    Joins the bridge's Fusion chain without cutting out your own nodes. reset=True removes every bridge spotlight.
    Frames: start_frame/end_frame and keyframe 'frame' count from the clip's first frame (0).
    timeline_start_frame/timeline_end_frame and keyframe 'timeline_frame' take absolute timeline frames.
    """
    params: dict[str, Any] = {
        "item_id": item_id,
        "center_x": center_x,
        "center_y": center_y,
        "radius": radius,
        "soft_edge": soft_edge,
        "ambient_brightness": ambient_brightness,
        "spotlight_gain": spotlight_gain,
        "shape": shape,
        "mask_name": mask_name,
        "animate": animate,
        "expand": expand,
        "start_radius": start_radius,
        "end_radius": end_radius,
        "start_frame": start_frame,
        "easing": easing,
        "reset": reset,
    }
    if width is not None:
        params["width"] = width
    if height is not None:
        params["height"] = height
    if keyframes is not None:
        params["keyframes"] = keyframes
    _frames(params, end_frame, timeline_start_frame, timeline_end_frame)
    if track_index is not None:
        params["track_index"] = track_index
    return _result("apply_spotlight_mask", params, timeout=60.0)


@mcp.tool()
def inspect_fusion(item_id: str = "playhead", track_index: Optional[int] = None) -> str:
    """Read-only: list a clip's Fusion nodes, their connections, and the bridge effect chain before MediaOut.

    Use after a Fusion effect to confirm what is connected. Never creates or changes a composition.
    """
    params: dict[str, Any] = {"item_id": item_id}
    if track_index is not None:
        params["track_index"] = track_index
    return _result("inspect_fusion", params, timeout=60.0)


@mcp.tool()
def current_context() -> str:
    """Read-only, fast: what the editor is looking at right now.

    Returns project, timeline (name and id), fps, resolution, current page, playhead frame and
    timecode, and the clip under the playhead on every video track (id, label, name, track,
    start, end, offset into the clip, has_fusion). Also timeline_count and the first 20
    timelines (index, name, id, is_current; names may lag up to 30 s). Never fails for a missing
    project or timeline: those fields are null. Use it to resolve "this clip" / "here" before
    editing; use project_overview for the whole project.
    """
    return _result("current_context", {}, timeout=10.0)


@mcp.tool()
def project_overview(timeline_limit: int = 40, clip_limit: int = 50, folder_limit: int = 100) -> str:
    """Read-only: the whole project in one call. Call this first for project-wide work.

    Returns project name, page, product/version, current_timeline {name, id}, settings {fps,
    resolution}, timeline_count and timelines[] (index, name, id, is_current, start/end/duration
    frames, start_timecode, fps, tracks {video, audio, subtitle}), media_pool {bins[] (path,
    clip_count, subfolder_count), clips[] (id, name, bin, type, duration, resolution, file_path),
    clip_total}, render_jobs (count) and truncated (which lists were cut short, or null).
    Limits keep it quick: raise them, or use list_media / list_timelines, for more.
    Switch to another timeline with open_timeline before using timeline tools on it.
    """
    return _result("project_overview", {"timeline_limit": timeline_limit, "clip_limit": clip_limit,
                                        "folder_limit": folder_limit}, timeout=60.0)


@mcp.tool()
def resolve_api(steps: list[dict[str, Any]], allow_destructive: bool = False,
                return_steps: Optional[list[int]] = None, quiet: bool = False, full_lists: bool = False) -> str:
    """Escape hatch: call any DaVinci Resolve scripting API method, anywhere in the project.

    Prefer the structured tools: they validate input and verify what Resolve did. Use this
    when none fits (project/timeline settings, bins, markers on media, render settings, clip
    properties, page switching, anything in Resolve's scripting README).

    steps: up to 50 of {"on": target, "call": "MethodName", "args": [...]}, run in order.
      on: "resolve", "project_manager", "project" (default), "media_pool", "media_storage",
          "current_timeline", "fusion", "$N" = the result of step N (steps count from 0), or by
          unique id: "item:<id>" (current timeline), "media:<id>" (any bin), "timeline:<id>".
          Prefer ids over list positions. "$N[i]" picks item i (from 0) of a list result;
          "$N.key" picks a dict key; both chain.
      args: JSON values; any string "$N..." in args (also inside lists/dicts) passes that
          earlier result, e.g. a MediaPoolItem. Write "$$text" for a literal "$text".
      {"get": "media:<id>"} returns that object itself, to pass as "$N" in a later step's args.
      {"on": "resolve", "getattr": "EXPORT_FCPXML_1_10"} reads a constant (strings, numbers,
          booleans or null only; methods need "call").
      {"for_each": "$N[*]", "range": [start, stop], "call": ..., "args": [...]} runs the call on
          each element of list result N ("on" defaults to "$item"). range is optional and works
          like a Python slice (stop exclusive). In "on"/args, "$item" (also "$item.key") is the
          element and "$index" its index in the full list. It stops at the first failure (earlier
          elements are not undone) unless "continue_on_error": true. It counts as one step, but a
          request makes at most 500 calls in all. Its result: {count, ok_count, first_index,
          last_index, failed: [{index, error}], returned_false_or_none, results}; "$N[i]" in a
          later step reads element i's result.
    Returns steps[] with each step's result (Resolve objects appear as {object, name, id}).
      Lists longer than 20 come back as {count, items: first 20 as {name, id}, truncated};
      full_lists=true returns them whole. References always see the whole list.
      return_steps=[n, ...]: only those steps return results; the others give {step, ok, summary}.
      quiet=true: no per-step results, only {ok, steps_run, result} with the last step's result.
    On failure the error names failed_step and lists the steps that already ran (nothing is
    undone). Resolve often returns False/None rather than raising: check each result.

    Deleting projects, timelines, clips, bins, media, stills or render jobs, closing the project
    and quitting are refused unless allow_destructive=true, which needs the user's explicit
    approval in this conversation.

    Examples:
      Timeline names: [{"on":"project","call":"GetTimelineByIndex","args":[2]},
                       {"on":"$0","call":"GetName"}]
      Project setting: [{"on":"project","call":"SetSetting","args":["timelineFrameRate","25"]}]
      Clip to a new timeline: [{"on":"media_pool","call":"GetRootFolder"},
                               {"on":"$0","call":"GetClipList"},
                               {"on":"media_pool","call":"CreateTimelineFromClips","args":["Selects",["$1[0]"]]}]
    """
    params: dict[str, Any] = {"steps": steps, "allow_destructive": allow_destructive}
    if return_steps is not None:
        params["return_steps"] = return_steps
    if quiet:
        params["quiet"] = True
    if full_lists:
        params["full_lists"] = True
    return _result("resolve_api", params, timeout=120.0)


@mcp.tool()
def fusion_inspect_tool(
    tool: str,
    item_id: str = "playhead",
    frame: int = 0,
    inputs: Optional[list[str]] = None,
    search: str = "",
    max_inputs: int = 120,
    track_index: Optional[int] = None,
) -> str:
    """Read-only: list one Fusion tool's inputs (id, name, data type, value at a clip frame,
    connection, modifier/animation and keyframe frames) plus its outputs.

    Use it before fusion_keyframe_input or fusion_build to learn exact input ids, e.g.
    Transform: Center, Size, Angle, Pivot, Aspect, FlipHoriz; Merge: Background, Foreground,
    Blend, Center, Size, Angle, ApplyMode; Background: TopLeftRed/Green/Blue/Alpha, Type;
    TextPlus: StyledText, Font, Size, Center, Red1/Green1/Blue1, Tracking; Blur: XBlurSize;
    Glow: Glow, Blend; BrightnessContrast: Gain, Brightness, Contrast, Saturation, Alpha.
    Get tool names from inspect_fusion. `frame` is a clip offset (0 = first frame).
    `search` filters ids/names by substring; `inputs` lists exact ids to return.
    """
    params: dict[str, Any] = {"tool": tool, "item_id": item_id, "frame": frame, "search": search,
                              "max_inputs": max_inputs}
    if inputs:
        params["inputs"] = inputs
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_inspect_tool", params, timeout=90.0)


@mcp.tool()
def fusion_build(
    name: str,
    nodes: Optional[list[dict[str, Any]]] = None,
    chain: Optional[list[Any]] = None,
    item_id: str = "playhead",
    reset: bool = False,
    track_index: Optional[int] = None,
) -> str:
    """Build a Fusion node graph on a clip and insert it into the bridge chain before MediaOut.

    The graph becomes one named custom stage ("custom:<name>"). Reusing a name replaces that
    stage; reset=True removes it. Your own Fusion nodes and other bridge effects stay connected.
    Custom stages run after zoom/blur/spotlight/saturation/color fx, in name order.

    nodes: [{"name": "Slide", "type": "<Fusion RegID>", "inputs": {InputID: spec}}]
      Common RegIDs: Transform, Merge, Background, TextPlus, Blur, Glow, SoftGlow, ColorCorrector,
      BrightnessContrast, EllipseMask, RectangleMask, Polygon, DirectionalBlur, FastNoise,
      CornerPositioner, Letterbox, ChannelBooleans, FilmGrain, Dissolve, Crop, Resize.
      spec forms:
        number | "text" | [x, y]                    static value (points are 0..1 frame coordinates)
        {"keyframes": [{"frame": 0, "value": 0}, {"frame": 12, "value": 1, "easing": "ease_out"}],
         "interpolation": "fusion" | "baked"}       animation; frame = clip offset, or use
                                                    "timeline_frame" for absolute frames
        {"from": "OtherNode" | "MediaIn" | "chain"} connect an input to another node's main
                                                    output, the clip's MediaIn, or whatever
                                                    feeds this stage ("chain")
      "fusion" interpolation sets keys only at your frames with Fusion's spline (stays editable);
      any key with "easing" (linear, ease_in, ease_out, ease, cubic_ease, smootherstep, rebound_out,
      elastic_out, ...) or interpolation="baked" bakes one key per frame with that curve.
    chain: node names from input to output, e.g. ["Slide", "Glow"], or {"node": "Over",
      "input": "Foreground"} to feed a non-main input (default is each node's main input).
      Optional for a single node.

    Example fade-in from black over 12 frames:
      nodes=[{"name": "Black", "type": "Background", "inputs": {"TopLeftAlpha": 1}},
             {"name": "Over", "type": "Merge", "inputs": {"Background": {"from": "Black"},
              "Blend": {"keyframes": [{"frame": 0, "value": 0}, {"frame": 12, "value": 1}]}}}]
      chain=[{"node": "Over", "input": "Foreground"}]
    Check `verified` (null = the build would not read values back) and confirm with inspect_fusion.
    """
    params: dict[str, Any] = {"name": name, "item_id": item_id, "reset": reset}
    if nodes is not None:
        params["nodes"] = nodes
    if chain is not None:
        params["chain"] = chain
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_build", params, timeout=90.0)


@mcp.tool()
def fusion_keyframe_input(
    tool: str,
    input: str,
    keyframes: Optional[list[dict[str, Any]]] = None,
    value: Optional[Any] = None,
    interpolation: str = "fusion",
    replace: bool = True,
    item_id: str = "playhead",
    track_index: Optional[int] = None,
    clear: bool = False,
    frame: int = 0,
) -> str:
    """Set, animate, re-animate or clear one input of any Fusion tool on a clip, including your own nodes.

    keyframes: [{"frame" | "timeline_frame", "value": number or [x, y], "easing"?}]. frame is a
    clip offset (0 = first frame). Or pass a static `value` (number, string or [x, y]).
    interpolation "fusion" keys only your frames; "baked" (or any per-key easing) writes one key
    per frame with the easing curve. Already-animated inputs: replace=True (default) replaces the
    existing animation; replace=False adds your keys to it (a number's BezierSpline or a point's
    XYPath; other modifiers such as the Fusion page's PolyPath are refused, nothing changed).
    clear=True removes the input's animation and leaves it static at `value` if given, else at its
    value at clip offset `frame` (default 0). The reply says `mode` / `previous_modifier`. Get names
    from inspect_fusion and input ids from fusion_inspect_tool. Reports `verified` from a read-back.
    """
    params: dict[str, Any] = {"tool": tool, "input": input, "interpolation": interpolation,
                              "replace": replace, "item_id": item_id}
    if clear:
        params.update(clear=True, frame=frame)
        if value is not None:
            params["value"] = value
    elif keyframes is not None:
        params["keyframes"] = keyframes
    elif value is not None:
        params["value"] = value
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_keyframe_input", params, timeout=60.0)


@mcp.tool()
def fusion_apply_setting(
    setting: str,
    name: str = "Setting",
    insert_into_chain: Optional[dict[str, Any]] = None,
    item_id: str = "playhead",
    track_index: Optional[int] = None,
) -> str:
    """Paste Fusion .setting / macro / copied-node text ({ Tools = ordered() { ... } }) into a clip's comp.

    Reports the names of the pasted tools. They stay unconnected unless insert_into_chain is
    {"entry": "<pasted tool fed by the clip>", "exit": "<pasted tool that feeds MediaOut>",
    "entry_input"?: "Foreground"}; then they become custom stage "custom:<name>" before MediaOut
    (reusing the name replaces it). Limit 200 KB. Needs the Lua connector or Studio direct attach.
    Resolve can convert file Loaders (a photo) when pasting or importing comps: the reply lists
    `converted_tools` and a `warning` when a declared Loader did not survive as a Loader.
    """
    params: dict[str, Any] = {"setting": setting, "name": name, "item_id": item_id}
    if insert_into_chain is not None:
        params["insert_into_chain"] = insert_into_chain
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_apply_setting", params, timeout=90.0)


@mcp.tool()
def fusion_run_lua(
    code: str,
    item_id: str = "playhead",
    create_comp: bool = True,
    track_index: Optional[int] = None,
) -> str:
    """Advanced escape hatch: run Lua inside a clip's Fusion comp and return its result.

    `comp` is bound to the named clip's composition. Never use fusion:GetCurrentComp() (that is
    whatever the Fusion page shows); code calling it is refused. If Resolve runs the script in a
    different comp, a guard stops it before anything runs and the call fails ("Nothing was changed").
    Prefer fusion_build, fusion_keyframe_input and fusion_apply_setting: they tag, verify and roll
    back; this does none of that. `return` a number, string, boolean or table to get it back as
    JSON. Errors are reported, but changes made before an error stay. Explain the script to the
    user before running anything that changes the comp. Needs the Lua connector or Studio direct attach.
    """
    params: dict[str, Any] = {"code": code, "item_id": item_id, "create_comp": create_comp}
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_run_lua", params, timeout=90.0)


@mcp.tool()
def fusion_graph(
    nodes: Optional[list[dict[str, Any]]] = None,
    links: Optional[list[Any]] = None,
    output: Optional[str] = None,
    name: str = "Graph",
    item_id: str = "playhead",
    reset: bool = False,
    track_index: Optional[int] = None,
) -> str:
    """Build a Fusion node graph wired by explicit links, as one custom stage before MediaOut.

    The graph becomes stage "custom:<name>" between whatever feeds the bridge chain (your own
    nodes, earlier bridge effects) and MediaOut. Reusing a name replaces that stage (also one made
    by fusion_build); reset=True removes it. Your own nodes are never rewired.

    nodes: [{"name": "Over", "type": "<Fusion RegID>", "inputs": {InputID: spec}}]; spec takes the
      same values and {"keyframes": [...], "interpolation"} as fusion_build, but no {"from": ...}:
      every connection is a link.
    links: [["Source.Output", "Node.Input"], ...] or {"from", "to"}. Sources: a graph node, an
      existing tool in the comp, "MediaIn" (the clip's untouched source) or "chain" (the clip as it
      reaches this stage; at least one link must come from "chain"). Targets must be graph nodes;
      leaving out ".Input" means the node's main input. Only main outputs can be linked.
    output: the node that feeds MediaOut (optional for one node).

    Example, a title over a softened clip with a vignette mask on the merge:
      nodes=[{"name": "Soft", "type": "Blur", "inputs": {"XBlurSize": 2}},
             {"name": "Words", "type": "TextPlus", "inputs": {"StyledText": "Hello"}},
             {"name": "Vig", "type": "EllipseMask", "inputs": {"SoftEdge": 0.3}},
             {"name": "Over", "type": "Merge"}]
      links=[["chain", "Soft.Input"], ["Soft.Output", "Over.Background"],
             ["Words.Output", "Over.Foreground"], ["Vig.Mask", "Over.EffectMask"]]
      output="Over"
    Everything structural is checked before the comp is touched; input ids are checked on the
    created nodes, and any failure deletes them, restores a replaced stage and removes a comp this
    call created (the reply says if Resolve would not remove it). Reply: nodes (actual tool
    names), links_made (each read back), feeds_media_out, verified, fusion_chain, route.
    """
    params: dict[str, Any] = {"name": name, "item_id": item_id, "reset": reset}
    if nodes is not None:
        params["nodes"] = nodes
    if links is not None:
        params["links"] = links
    if output is not None:
        params["output"] = output
    if track_index is not None:
        params["track_index"] = track_index
    return _result("fusion_graph", params, timeout=90.0)


@mcp.tool()
def fusion_copy_comp(
    from_item_id: str,
    to_item_ids: list[str],
    mode: str = "replace",
    user_approved: bool = False,
    comp_index: int = 1,
) -> str:
    """Copy one clip's Fusion comp onto other clips (a look, title or graph built once).

    Goes through ExportFusionComp -> ImportFusionComp with a .comp file in the bridge work folder;
    there is no verified file-free route (no comp-copy API, and comp:Execute scripts can land in
    the wrong comp). mode="replace" works on a target with no comp or exactly one: it backs the old
    comp up to a file, imports the copy, deletes the old comp and keeps the original name. It is
    refused when the target's comp holds your nodes or bridge effects unless the user approved
    (user_approved=true), and refused for a target with several comps; it never silently stacks a
    "Composition 2". mode="add" stacks the copy as another comp on the clip.
    Resolve converts file Loaders (photos) on import: each target lists `converted_tools` and a
    `warning`; rebuild those Loaders instead of calling the copy intact. Per target: imported,
    comp_count_before/after, converted_tools, warning, backup_file. Not live-verified: whether the
    copy's MediaIn shows each target's own media; check the targets on the Fusion page.
    """
    return _result("fusion_copy_comp", {"from_item_id": from_item_id, "to_item_ids": to_item_ids, "mode": mode,
                                        "user_approved": user_approved, "comp_index": comp_index}, timeout=180.0)


@mcp.tool()
def delete_clips(item_ids: list[str], ripple: bool = False, user_approved: bool = False) -> str:
    """Delete timeline items. Refuses unless the user explicitly approved this destructive action in the current conversation."""
    if not user_approved:
        return json.dumps(
            {
                "ok": False,
                "error": "Deletion requires explicit user approval. Ask first, then retry with user_approved=true.",
            },
            indent=2,
        )
    return _result("delete_clips", {"item_ids": item_ids, "ripple": ripple})


# ------------------------------------------------------------------ delivery


@mcp.tool()
def save_project() -> str:
    """Save the current Resolve project."""
    return _result("save_project")


@mcp.tool()
def render_current_timeline(
    output_dir: str,
    name: str = "",
    preset: str = "",
    start: bool = False,
    user_approved: bool = False,
) -> str:
    """Add a render job for the current timeline. Starting the render requires explicit user approval."""
    if start and not user_approved:
        return json.dumps(
            {
                "ok": False,
                "error": "Starting a render requires explicit user approval. Ask first, then retry with user_approved=true.",
            },
            indent=2,
        )
    return _result(
        "render_current_timeline",
        {"output_dir": output_dir, "name": name, "preset": preset, "start": start},
        timeout=180.0,
    )


@mcp.tool()
def render_status(job_id: str = "") -> str:
    """Report render progress: whether Resolve is rendering, and each job's status, percent and errors.

    Pass the job_id from render_current_timeline, or leave it empty for every job in the queue.
    """
    return _result("render_status", {"job_id": job_id})


def _observe_for_render(call: Any) -> Any:
    """A render check edits nothing it does not put back (Deliver settings, marks, page, its own job and file),
    so it may look at Resolve itself instead of being refused after a bridge or worker restart. One read-only
    current_context call records this client's observation; the result names the timeline that was rendered."""
    try:
        context = call("current_context", {}, 30.0)
    except Exception:
        return None  # The render's own call reports any real problem.
    if not isinstance(context, dict):
        return None
    timeline = context.get("timeline")
    return {"project": context.get("project"),
            "timeline": timeline.get("name") if isinstance(timeline, dict) else timeline}


@mcp.tool()
def render_check(start_frame: Optional[int] = None, end_frame: Optional[int] = None, width: int = 640,
                 mark: bool = False, keep_file: bool = False) -> str:
    """Render a small preview of the open timeline and scan it for problems stills and transcripts miss.

    This RENDERS: Resolve is busy until it finishes (minutes for a long timeline). Ask the user first.
    start_frame/end_frame are absolute timeline frames (end exclusive); omit them for the whole timeline.
    Reports black frames, 1-2 frame flashes, frozen picture, clipping, digital-silence dropouts, long quiet
    gaps, loudness jumps and loud sections, plus clicks and room-tone jumps at audio edit points, each with
    severity, timeline frame and timecode, and integrated loudness/LRA/true peak. mark=True adds QC: markers
    (Red errors, Yellow warnings). The Deliver page settings are saved and restored around the job, and the
    job (and file, unless keep_file) is removed afterwards. Findings are things to check: fades and holds
    can be intentional. Limits: a low-res render (no fine detail or grain), Free renders at most UHD/60 fps,
    burned-in caption collisions are not detected.
    """
    from bridge import qc
    used: dict[str, str] = {}

    def call(operation: str, params: dict[str, Any], timeout: float) -> Any:
        payload, used["transport"] = transport.call(operation, params, timeout=min(timeout, 590.0))
        return payload

    observed = _observe_for_render(call)
    try:
        result = qc.run_check(call, _ffmpeg(), Path(client.HOME) / "renders" / "check", start_frame=start_frame,
                              end_frame=end_frame, width=width, mark=mark, keep_file=keep_file)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    result["transport"] = used.get("transport")
    result["rendered_from"] = observed
    return json.dumps(result, ensure_ascii=False, indent=1)


@mcp.tool()
def render_frames(frames: Optional[list[int]] = None, start_frame: Optional[int] = None,
                  end_frame: Optional[int] = None, count: int = 6, width: int = 960, sheet: bool = True,
                  columns: Optional[int] = None) -> list:
    """SEE the finished picture (grades, Fusion, titles, transforms) as images, via a short Deliver render.

    timeline_frame on Resolve Free shows the raw source only; use this to check a look or an effect.
    frames: up to 10 absolute timeline frames, or start_frame/end_frame (end exclusive) with `count` evenly
    spaced frames. This RENDERS (a few seconds to a minute per span of up to 20 s; far-apart frames make
    several small renders), so ask the user first. Deliver settings and In/Out marks are saved and restored
    and the temporary jobs and files removed; if `settings_restored` is false tell the user to check the
    Deliver page and In/Out marks. Resolve is put back on the page it was on. It looks at Resolve itself first
    (no timeline_overview needed after a restart); rendered_from names the timeline it rendered.
    Frames are H.264-compressed and at most `width` px wide.
    sheet (default on) returns ONE contact-sheet image instead: tiles in timeline order, left to right then
    top to bottom (`columns` across, default about square), each labelled with its timecode and absolute
    frame; grey tiles were not rendered. Set sheet=false for one full-size image per frame.
    """
    from bridge import qc
    used: dict[str, str] = {}

    def call(operation: str, params: dict[str, Any], timeout: float) -> Any:
        payload, used["transport"] = transport.call(operation, params, timeout=min(timeout, 590.0))
        return payload

    observed = _observe_for_render(call)
    try:
        result = qc.render_frames(call, _ffmpeg(), Path(client.HOME) / "renders" / "check" / "frames", frames=frames,
                                  start_frame=start_frame, end_frame=end_frame, count=count, width=width,
                                  sheet=sheet, columns=columns)
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))]
    images = []
    contact = result.get("sheet") or {}
    if contact.get("png_base64"):
        images.append(ImageContent(type="image", data=contact.pop("png_base64"), mimeType="image/png"))
    for entry in result.get("frames") or []:  # only frames the sheet could not show remain when it is present
        data = entry.pop("png_base64", None)
        if data:
            images.append(ImageContent(type="image", data=data, mimeType="image/png"))
    result["transport"] = used.get("transport")
    result["rendered_from"] = observed
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=1))] + images


# ------------------------------------------------------------ transcription


def _transcripts(item_id: Optional[str], scope: str, track_index: Optional[int], language: str, prompt: str):
    """Read clip sources from Resolve (fast), then transcribe locally outside the Resolve lease."""
    from bridge import transcribe
    from bridge.operations import ResolveOperations
    params: dict[str, Any] = {"scope": scope}
    if item_id:
        params["item_id"] = item_id
    if track_index:
        params["track_index"] = track_index
    payload, used = transport.call("transcription_source", params, timeout=300.0)
    ffmpeg = ResolveOperations._ffmpeg_path(client.HOME)
    results = [transcribe.transcribe_source(src, client.HOME, language=language or "auto", prompt=prompt, ffmpeg=ffmpeg)
               for src in payload["sources"]]
    return payload, used, results


def _transcript_error(exc: Exception) -> str:
    from bridge import transcribe
    status = transcribe.engine_status(client.HOME)
    return json.dumps({"ok": False, "error": str(exc), "engine": status}, ensure_ascii=True, indent=2)


@mcp.tool()
def transcribe(
    item_id: Optional[str] = None,
    scope: str = "clip",
    track_index: Optional[int] = None,
    language: str = "auto",
    prompt: str = "",
    include_words: bool = False,
    max_segments: int = 400,
) -> str:
    """Transcribe speech locally (whisper.cpp on this computer; nothing is uploaded; works on Resolve Free).

    scope: "clip" (item_id, or the dialogue clip under the playhead, preferring audio tracks),
    "track" (every clip on track_index), or "timeline" (the audio track with the most material).
    Returns segments with absolute timeline frames and timecodes; include_words adds word-level
    frames (needed to cut filler words or exact phrases). Results are cached, so asking again is instant.
    Use it to answer "where do I say X", plan cuts, build chapters, or write subtitles.
    language: "auto" or a code like "en"; prompt can list names/jargon to spell correctly.
    Each word {w, s, e} (s/e absolute timeline frames, e exclusive) also carries item_id (the clip it came
    from) and src_frame: the source-media frame of the word start, counted from the start of the media file
    (not its embedded timecode), assuming normal speed. Each clip's src_fps_basis says the rate used:
    "media" when the clip entry carried the media frame rate, else "timeline" (the timeline rate; then
    src_frame is source time in timeline-rate frames and is off if the media rate differs).
    """
    try:
        payload, used, results = _transcripts(item_id, scope, track_index, language, prompt)
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    budget = max(1, min(int(max_segments), 3000))
    clips = []
    for r in results:
        segs = r["segments"][:budget]
        budget = max(0, budget - len(segs))
        entry = {k: r[k] for k in ("item_id", "name", "track", "language", "model", "cached")}
        entry["src_fps"], entry["src_fps_basis"] = r.get("src_fps"), r.get("src_fps_basis")
        entry["segments"] = [{"start_frame": s["start_frame"], "end_frame": s["end_frame"], "timecode": s["timecode"],
                              "text": s["text"]} for s in segs]
        entry["segments_total"] = len(r["segments"])
        if include_words:
            entry["words"] = [{"w": w["word"], "s": w["start_frame"], "e": w["end_frame"],
                               "item_id": w.get("item_id", r["item_id"]), "src_frame": w.get("src_frame")}
                              for w in r["words"]]
        clips.append(entry)
    return json.dumps({"ok": True, "transport": used, "result": {
        "timeline": payload["timeline"], "fps": payload["fps"], "fingerprint": payload["fingerprint"],
        "clips": clips, "skipped": payload["skipped"],
        "note": "Frames are absolute timeline frames. Pass fingerprint to review_transcript_cuts."}}, ensure_ascii=False, indent=1)


@mcp.tool()
def generate_subtitles(
    item_id: Optional[str] = None,
    scope: str = "timeline",
    track_index: Optional[int] = None,
    language: str = "auto",
    prompt: str = "",
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    import_to_timeline: bool = True,
) -> str:
    """Auto-generate subtitles from speech (local transcription; Resolve Free compatible).

    Writes an .srt with readable cues (sentence/pause aware, balanced two-line wrap, 6 s max) and,
    when import_to_timeline is true, imports it and places it on a subtitle track at the timeline start.
    Reports the .srt path and whether Resolve placed it; if not, the file is in the media pool to drag in.
    Default scope "timeline" covers the main dialogue track; use scope="clip" for one clip.
    """
    from bridge import transcribe as tx
    try:
        payload, used, results = _transcripts(item_id, scope, track_index, language, prompt)
        fps = float(payload["fps"])
        words = sorted((w for r in results for w in r["words"]), key=lambda w: w["start_frame"])
        if not words:
            return json.dumps({"ok": False, "error": "No speech was found to subtitle."}, indent=2)
        cues = tx.subtitle_cues(words, fps, max_chars=max(16, int(max_chars_per_line)), max_lines=max(1, min(3, int(max_lines))))
        out_dir = Path(client.HOME) / "subtitles"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = "".join(c if c.isalnum() else "_" for c in str(payload["timeline"].get("name") or "timeline"))[:40]
        path = out_dir / ("%s-%s.srt" % (stem, time.strftime("%Y%m%d-%H%M%S")))
        path.write_text(tx.to_srt(cues, fps, float(payload["timeline_start_frame"])), encoding="utf-8")
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    result: dict[str, Any] = {"srt_path": str(path), "cues": len(cues), "language": results[0]["language"] if results else None,
                              "preview": [{"timecode": tx.frames_to_tc(c["start_frame"], fps), "text": c["text"]} for c in cues[:8]]}
    if import_to_timeline:
        imported = json.loads(_result("import_subtitles", {"path": str(path), "first_cue_frame": cues[0]["start_frame"]}, timeout=60.0))
        result["import"] = imported.get("result") if imported.get("ok") else {"error": imported.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def find_filler_words(
    item_id: Optional[str] = None,
    scope: str = "clip",
    track_index: Optional[int] = None,
    fillers: Optional[list[str]] = None,
    padding_frames: int = 2,
    mark_for_review: bool = True,
    language: str = "auto",
) -> str:
    """Find "um", "uh", "erm"… (or your own words/phrases) and mark them as reviewable cuts.

    Uses word-level local transcription. With mark_for_review, adds pink "Review cut" markers;
    nothing is cut. Then pass the marker_ids you approve to apply_silence_cuts, which cuts on a
    new duplicate timeline and keeps the original.
    tighten(fillers=True) now covers this together with pause removal in one call.
    """
    from bridge import transcribe as tx
    try:
        payload, used, results = _transcripts(item_id, scope, track_index, language, "Umm, uh, so, like, you know.")
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    fps = float(payload["fps"])
    found = []
    for r in results:
        ranges = tx.filler_ranges(r["words"], fps, tuple(fillers) if fillers else tx.FILLERS, max(0, int(padding_frames)))
        entry: dict[str, Any] = {"item_id": r["item_id"], "name": r["name"],
                                 "ranges": [dict(x, timecode=tx.frames_to_tc(x["start_frame"], fps)) for x in ranges]}
        if mark_for_review and ranges:
            marked = json.loads(_result("review_transcript_cuts", {"item_id": r["item_id"], "ranges": ranges,
                                                                     "fingerprint": payload["fingerprint"]}))
            entry["review"] = marked.get("result") if marked.get("ok") else {"error": marked.get("error")}
        found.append(entry)
    return json.dumps({"ok": True, "transport": used, "result": {"clips": found, "total": sum(len(c["ranges"]) for c in found)}},
                      ensure_ascii=False, indent=1)


@mcp.tool()
def review_transcript_cuts(item_id: str, ranges: list[dict[str, Any]], fingerprint: str = "") -> str:
    """Mark transcript-chosen ranges (e.g. a fluffed take, a repeated sentence) as reviewable cuts.

    ranges: [{start_frame, end_frame, label}] in absolute timeline frames (from transcribe with
    include_words). Pass the fingerprint transcribe returned so stale ranges are refused. Adds pink
    markers only; apply the approved marker_ids with apply_silence_cuts (on a duplicate timeline).
    For pause and filler cuts, tighten now covers this in one call.
    """
    params: dict[str, Any] = {"item_id": item_id, "ranges": ranges}
    if fingerprint:
        params["fingerprint"] = fingerprint
    return _result("review_transcript_cuts", params)



# ------------------------------------------------- search, pauses, timeline edits


@mcp.tool()
def search_transcript(query: str, scope: str = "timeline", item_id: Optional[str] = None,
                      track_index: Optional[int] = None, max_hits: int = 30, language: str = "auto") -> str:
    """Find where something is said: exact then fuzzy word/phrase matches with timecodes and context.

    Transcribes (cached) first if needed. For meaning-based questions ("where do I explain X"), call
    transcribe and read the segments instead. Jump there with set_playhead or mark with add_markers.
    Each hit has item_id, start_frame/end_frame (absolute timeline frames, end exclusive) and src_frame: the
    source-media frame of the match start from the start of the media file, at src_fps (src_fps_basis
    "media" = the media's rate, "timeline" = the timeline rate used because the clip entry had no media rate).
    """
    from bridge import transcribe as tx
    try:
        payload, used, results = _transcripts(item_id, scope, track_index, language, "")
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    hits = tx.search(results, query, max_hits=max(1, min(int(max_hits), 200)))
    return json.dumps({"ok": True, "transport": used, "result": {"query": query, "hits": hits, "count": len(hits)}},
                      ensure_ascii=False, indent=1)


@mcp.tool()
def remove_pauses(min_pause_seconds: float = 0.7, keep_seconds: float = 0.15, scope: str = "timeline",
                  item_id: Optional[str] = None, apply: bool = False, mark: bool = True, language: str = "auto") -> str:
    """Tighten speech: find pauses between spoken words longer than min_pause_seconds across the timeline.

    Speech-based (word-level transcript), so music or room tone doesn't hide a pause. keep_seconds of air
    stays on each side. apply=False (default) only reports the pauses and, with mark=True, adds purple
    "Pause" markers. apply=True builds a NEW tightened timeline with every track cut (remove_ranges);
    the original stays untouched. Ask the user before apply=True. For one or two pauses at a clip's head or
    tail, trim_in_place (no ripple, no rebuild) keeps transitions, fades and hand edits intact.
    tighten now covers this with energy-checked word guards, exclude_ranges and per-cut context.
    """
    from bridge import transcribe as tx
    try:
        payload, used, results = _transcripts(item_id, scope, None, language, "")
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    fps = float(payload["fps"])
    words = [w for r in results for w in r["words"]]
    ranges = tx.pause_ranges(words, fps, max(0.2, float(min_pause_seconds)), max(0.0, float(keep_seconds)))
    result: dict[str, Any] = {"pauses": len(ranges), "seconds_saved": round(sum(r["seconds"] for r in ranges), 2),
                              "ranges": [dict(r, timecode=tx.frames_to_tc(r["start_frame"], fps)) for r in ranges[:300]]}
    if ranges and apply:
        done = json.loads(_result("remove_ranges", {"ranges": ranges}, timeout=590.0))
        result["applied"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    elif ranges and mark:
        marks = [{"frame": r["start_frame"], "color": "Purple", "name": "Pause %.1fs" % r["seconds"],
                  "duration": r["end_frame"] - r["start_frame"], "note": "Would be removed by remove_pauses(apply=True)"}
                 for r in ranges]
        done = json.loads(_result("add_markers", {"markers": marks}, timeout=120.0))
        result["markers"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def tighten(scope: str = "timeline", item_id: Optional[str] = None, mode: str = "plan",
            lead_frames: Optional[int] = None, tail_frames: Optional[int] = None, max_pause_frames: Optional[int] = None,
            exclude_ranges: Optional[list[dict[str, Any]]] = None, fillers: bool = False, lcut_frames: int = 0,
            audio_crossfade_frames: int = 0, name: str = "", language: str = "auto", mark: bool = True,
            track_index: Optional[int] = None) -> str:
    """Tighten dialogue in one call: plan or apply every pause cut (and optionally um/uh) at once.

    Words come from the cached local transcript, each guarded only where its SOURCE audio is above the
    room-noise floor, with a phrase-final word's tail capped ~0.35 s past its last loud hop (Whisper stretches
    it across the pause). Every pause longer than max_pause_frames (default ~0.5 s) between guarded words is
    cut down to tail_frames after the previous word + lead_frames before the next. fillers=True also removes
    filler words the transcript caught, with the pause around them. Cuts never enter exclude_ranges
    [{start_frame, end_frame}] (intentional silences) or a guarded word. Frames are absolute timeline frames,
    end exclusive. scope: "timeline" (main dialogue track), "track" (track_index) or "clip" (item_id).
    mode="plan" (default) returns cuts [{start_frame, end_frame, frames, seconds, kind: pause|filler,
    words_before, words_after, timecode, ...}] and totals, and with mark=True adds purple "Tighten" markers
    (at most 300). mode="apply" makes ONE remove_ranges call with every cut: a NEW timeline with all tracks
    cut, original kept; returns it with carried / not_carried. Ask the user before mode="apply".
    lcut_frames / audio_crossfade_frames are accepted but not applied (no verified API); see not_applied.
    """
    from bridge import audio_regions, tighten as tg
    if lead_frames is None or tail_frames is None or max_pause_frames is None:
        prefs = (json.loads(_result("get_preferences", {}, timeout=20.0)).get("result") or {}).get("preferences") or {}
        lead_frames = prefs.get("tighten_lead_frames", 4) if lead_frames is None else lead_frames
        tail_frames = prefs.get("tighten_tail_frames", 6) if tail_frames is None else tail_frames
        max_pause_frames = prefs.get("tighten_max_pause_frames") if max_pause_frames is None else max_pause_frames
    try:
        settings = tg.validate(scope, mode, lead_frames, tail_frames, max_pause_frames, exclude_ranges,
                               lcut_frames, audio_crossfade_frames)
    except tg.TightenError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)
    prompt = "Umm, uh, so, like, you know." if fillers else ""
    try:
        payload, used, results = _transcripts(item_id, settings["scope"], track_index, language, prompt)
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    fps = float(payload["fps"])
    ffmpeg = _ffmpeg()
    clips = []
    for source, transcript in zip(payload["sources"], results):
        entry: dict[str, Any] = {"source": source, "words": transcript["words"], "analysis": None, "error": None}
        try:
            entry["analysis"] = audio_regions.speech_regions(source, transcript["words"], ffmpeg)
        except Exception as exc:
            entry["error"] = str(exc)
        clips.append(entry)
    words, warnings = tg.collect_words(clips)
    if not words:
        return json.dumps({"ok": False, "error": "No speech was found to tighten.", "skipped": payload.get("skipped")},
                          indent=2)
    if settings["max_pause_frames"] is None:
        settings["max_pause_frames"] = max(1, int(round(tg.DEFAULT_MAX_PAUSE_SEC * fps)))
    plan = tg.plan_cuts(words, fps, lead_frames=settings["lead_frames"], tail_frames=settings["tail_frames"],
                        max_pause_frames=settings["max_pause_frames"], exclude_ranges=settings["exclude_ranges"],
                        fillers=bool(fillers))
    cuts = plan["cuts"]
    result: dict[str, Any] = {
        "mode": settings["mode"], "timeline": payload.get("timeline"), "fps": fps, "totals": plan["totals"],
        "cuts": cuts[:500], "cuts_listed": min(len(cuts), 500), "skipped": plan["skipped"][:100],
        "settings": plan["settings"], "not_applied": tg.not_applied(settings["lcut_frames"],
                                                                     settings["audio_crossfade_frames"]),
        "clips": [{"item_id": c["source"].get("item_id"), "name": c["source"].get("name"),
                   "audio_checked": c["analysis"] is not None, "error": c["error"]} for c in clips],
        "skipped_clips": payload.get("skipped"), "warnings": warnings[:30],
        "note": ("Analysed on SOURCE audio, not the Fairlight mix. Only pauses between words are cut (not a clip's "
                 "head or tail); fillers only where the transcript wrote them. Cuts remove the range from EVERY "
                 "track (B-roll and music too).")}
    ok = True
    if settings["mode"] == "apply":
        if not cuts:
            result["applied"] = None
            result["note"] += " Nothing to cut, so no timeline was created."
        elif len(cuts) > 5000:
            ok, result["applied"] = False, {"error": "More than 5000 cuts; narrow the scope or raise max_pause_frames."}
        else:
            ranges = [{"start_frame": c["start_frame"], "end_frame": c["end_frame"]} for c in cuts]
            done = json.loads(_result("remove_ranges", {"ranges": ranges, "name": name}, timeout=590.0))
            if done.get("ok"):
                applied = done.get("result") or {}
                result["applied"] = applied
                result["new_timeline"] = applied.get("result_timeline")
                result["carried"], result["not_carried"] = applied.get("carried"), applied.get("not_carried")
            else:
                ok, result["applied"] = False, {"error": done.get("error"), "help": done.get("help")}
    elif mark and cuts:
        markers, truncated = tg.review_markers(cuts)
        done = json.loads(_result("add_markers", {"markers": markers}, timeout=120.0))
        result["markers"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
        result["markers_not_placed"] = truncated
    return json.dumps({"ok": ok, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def speech_regions(scope: str = "clip", item_id: Optional[str] = None, track_index: Optional[int] = None,
                   threshold_db: Optional[float] = None, pad_seconds: float = 0.08, merge_gap_seconds: float = 0.35,
                   language: str = "auto", words: str = "compact") -> str:
    """Where speech really starts and stops: Whisper word timings snapped to the source audio's energy.

    Read-only. Word times from the (cached) transcript drift into pauses; this refines each word to the nearest
    energy edge (within 0.2 s), merges words into regions (gaps under merge_gap_seconds), pads them, and returns
    regions, pauses and a suggested cut frame at the quietest point of each pause, all in absolute timeline frames
    (end exclusive). Also lists sound with no words (breaths, laughs, music) and words with no sound under them
    (likely Whisper phantoms). threshold_db=None adapts to the clip. Source audio, not the Fairlight mix.
    Use the pauses/cut points with remove_ranges or review_transcript_cuts.
    words: "compact" (default) lists each word as [word, start_frame, end_frame] plus `unsupported_words`;
    "full" adds every word's original/trusted timings and snap flags; "none" leaves words out (regions keep
    their text). Long clips return thousands of words, so ask for "full" only for a short range.
    """
    from bridge import audio_regions
    if words not in ("compact", "full", "none"):
        return json.dumps({"ok": False, "error": 'words must be "compact", "full" or "none".'})
    try:
        payload, used, results = _transcripts(item_id, scope, track_index, language, "")
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return _transcript_error(exc)
    clips = []
    for source, transcript in zip(payload["sources"], results):
        try:
            clip = audio_regions.speech_regions(source, transcript["words"], _ffmpeg(), threshold_db=threshold_db,
                                                pad_seconds=pad_seconds, merge_gap_seconds=merge_gap_seconds)
            if words != "full" and isinstance(clip.get("words"), list):
                listed = clip.pop("words")
                if words == "compact":
                    clip["words"] = [[w.get("word"), w.get("start_frame"), w.get("end_frame")] for w in listed]
                    clip["words_format"] = "[word, start_frame, end_frame]; words=\"full\" for snap details"
                clip["word_count"] = len(listed)
            clips.append(clip)
        except Exception as exc:
            clips.append({"item_id": source.get("item_id"), "name": source.get("name"), "error": str(exc)})
    return json.dumps({"ok": any("error" not in c for c in clips), "transport": used,
                       "result": {"fps": payload.get("fps"), "clips": clips, "skipped": payload.get("skipped")}},
                      ensure_ascii=False, indent=1)


@mcp.tool()
def measure_loudness(scope: str = "timeline", item_id: Optional[str] = None, track_index: Optional[int] = None,
                     target_lufs: float = -14.0, true_peak_ceiling_db: float = -1.0, apply: bool = False,
                     gate: str = "none") -> str:
    """Read-only loudness check: integrated LUFS, loudness range and true peak per clip and for the whole
    program (clips back to back), plus the dB change each needs to reach target_lufs (-14 YouTube/Spotify,
    -16 Apple/podcasts, -23 EBU broadcast), limited so true peak stays under true_peak_ceiling_db.

    Measured on SOURCE audio (ignores clip volume, faders, fades and the Fairlight mix), so each gain is the clip
    volume that reaches the target. apply=True (ask first) sets each measured clip's volume to that gain with
    set_clip_volume (the peak-limited gain where the ceiling applies; clips that couldn't be measured are left
    alone) and reports before/after per clip. Track faders and effects still change the final mix: confirm with
    render_check. If the write fails, volumes are read back and ok is false; never assume they changed.
    gate="speech" (use it for dialogue): loudness is measured over dialogue-level sound only, with one threshold for
    the whole programme, so pauses and room tone don't drag a clip's number down; a clip with under 1 s of dialogue
    (40% of a shorter clip; b-roll, a laptop beat) is left unmeasured and gets no gain instead of a huge boost of room noise. True peak is
    still the whole clip's. Each clip reports integrated_lufs (gated) and integrated_lufs_all.
    scope="track" with track_index is the fastest scan.
    """
    from bridge import audio_regions
    params: dict[str, Any] = {"scope": scope}
    if item_id:
        params["item_id"] = item_id
    if track_index:
        params["track_index"] = track_index
    params["fingerprint"] = False  # Loudness never compares timelines; skipping it saves ~a third of the scan.
    try:
        payload, used = transport.call("transcription_source", params, timeout=300.0)
        result = audio_regions.measure_loudness(payload["sources"], _ffmpeg(), target_lufs=target_lufs,
                                                true_peak_ceiling_db=true_peak_ceiling_db, gate=gate)
    except client.BridgeOffline as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    result["skipped"] = payload.get("skipped")
    if apply:
        items, capped = [], []
        for clip in result.get("clips") or []:
            gain = clip.get("peak_limited_gain_db") if clip.get("peak_limited") else clip.get("gain_db")
            if clip.get("item_id") and gain is not None:
                db = max(-60.0, min(24.0, round(float(gain), 1)))  # Resolve's clip volume range.
                if db != round(float(gain), 1):
                    capped.append({"item_id": clip["item_id"], "wanted_db": round(float(gain), 1), "set_db": db})
                items.append({"item_id": clip["item_id"], "db": db})
        ok = True
        if items:
            applied = json.loads(_result("set_clip_volume", {"items": items}, timeout=180.0))
            done = applied.get("result") if applied.get("ok") else None
            if isinstance(done, dict) and done.get("verified"):
                result["applied"] = done
                result["note"] = ("Clip volumes were set to the gains above (Inspector > Audio > Volume) and read back; "
                                  "see applied.clips for before/after. Track faders and effects are unchanged.")
            else:
                # Never report success without a read-back: a transport failure leaves the outcome unknown.
                ok = False
                wanted = {entry["item_id"]: entry["db"] for entry in items}
                check = json.loads(_result("read_clip_volumes", {"item_ids": list(wanted)}, timeout=180.0))
                read_back = (check.get("result") or {}).get("clips") if check.get("ok") else None
                result["applied"] = {"verified": False, "error": None if done else applied.get("error"),
                                     "clips": (done or {}).get("clips"), "read_back": read_back}
                if read_back is None:
                    result["note"] = ("Setting clip volumes FAILED (%s) and they could not be read back (%s): the outcome "
                                      "is unknown. Read the volumes (timeline_overview detail='full') before retrying."
                                      % (applied.get("error") or "not verified", check.get("error")))
                else:
                    at_target = [c for c in read_back if c.get("db") is not None and c.get("requested_for") in wanted
                                 and abs(float(c["db"]) - wanted[c["requested_for"]]) <= 0.05]
                    result["applied"]["at_target"] = len(at_target)
                    result["note"] = ("Setting clip volumes FAILED or did not verify (%s). Read back now: %d of %d audio "
                                      "item(s) are at the wanted volume (applied.read_back). Set the rest with "
                                      "set_clip_volume and read them back." % (applied.get("error") or "see applied.clips",
                                                                               len(at_target), len(read_back)))
            if capped:
                result["capped"] = capped
                result["note"] += (" %d clip(s) needed more than Resolve's +24 dB clip volume and were capped, so they "
                                   "stay below the target: raise the track fader or fix the source." % len(capped))
        else:
            result["applied"] = {"clips": [], "note": "No clip had a measurable gain; nothing was changed."}
        return json.dumps({"ok": ok, "transport": used, "result": result}, ensure_ascii=False, indent=1)
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def edit_points(ops: list[dict[str, Any]], mode: str = "in_place", dry_run: bool = False, name: str = "",
                bin: Optional[str] = None) -> str:
    """Trim, roll, slip or slide edit points in ONE call. Frames are timeline frames; positive = later.

    ops: [{"op": "trim", "item_id", "head": n, "tail": n}  (positive trims, negative extends)
          {"op": "roll", "left_item_id", "right_item_id", "frames": n}  (moves the shared cut)
          {"op": "slip" | "slide", "item_id", "frames": n}]
    mode="in_place" (default): trims and rolls applied directly with Set*Offset, in order, each read back; no copy, no
    ripple, neighbours never move, linked audio follows. Stops at the first refusal (earlier ops stay applied).
    Slip and slide are impossible in place on Resolve 21.2 Free (no API moves a clip) and are refused there.
    mode="new_timeline": exactly one op, built on a rebuilt copy (the original is kept) like trim_edit / slip_clip /
    slide_clip / roll_edit, filed into `bin`. dry_run=True plans only. Ask before editing; snapshot_timeline first
    for several in-place ops.
    """
    return _result("edit_points", {"ops": ops, "mode": mode, "dry_run": dry_run, "name": name, "bin": bin}, timeout=300.0)


@mcp.tool()
def insert_clip(media_id: str, src_in: int, src_out: int, at_frame: int, track_index: int = 1, kind: str = "av",
                ripple: bool = False) -> str:
    """Place a media pool clip's source range [src_in, src_out) (src_out EXCLUSIVE) at an absolute timeline frame,
    into EMPTY space, in place. kind: "av" (video and audio), "v" or "a". Overlaps are refused. ripple=True is refused:
    nothing can move clips on Resolve 21.2 Free, so open room first (trim_in_place, or remove_ranges on a copy).
    """
    return _result("insert_clip", {"media_id": media_id, "src_in": src_in, "src_out": src_out, "at_frame": at_frame,
                                   "track_index": track_index, "kind": kind, "ripple": ripple}, timeout=60.0)


@mcp.tool()
def ripple_delete_ranges(ranges: list[dict[str, Any]], name: str = "", bin: Optional[str] = None) -> str:
    """Cut [{start_frame, end_frame}] (absolute, end exclusive) out of EVERY track and close the gaps, in ONE new
    timeline (the original is kept). Replaces chaining trim_edit. Same engine as remove_ranges: see its carried /
    not_carried report; existing transitions are listed in transitions_dropped (a rebuild cannot recreate them;
    add_transitions can add Cross Dissolves back). Ask first.
    """
    return _result("remove_ranges", {"ranges": ranges, "name": name, "bin": bin}, timeout=590.0)


@mcp.tool()
def set_clip_properties(properties: dict[str, Any], item_ids: Optional[list[str]] = None,
                        range: Optional[dict[str, Any]] = None) -> str:
    """Set the same properties on many clips at once, each read back: {"AudioVolume": -3, "Opacity": 80, "ZoomX":
    1.1, "Pan": 20, ...} (Resolve's TimelineItem property keys: transform/crop/composite/opacity/retime keys plus
    AudioVolume, AudioPan, AudioVolumeEnabled). Pick clips by item_ids or range {start_frame, end_frame (exclusive),
    track: "V1" | "A2" | omitted for all tracks}. Audio keys skip video clips. Ask first.
    """
    params: dict[str, Any] = {"properties": properties}
    if item_ids:
        params["item_ids"] = item_ids
    if range:
        params["range"] = range
    return _result("set_clip_properties", params, timeout=180.0)


@mcp.tool()
def copy_grade(from_item_id: str, item_ids: Optional[list[str]] = None, range: Optional[dict[str, Any]] = None) -> str:
    """Copy one clip's current grade onto many clips (Resolve's CopyGrades), with node 1 CDL read back where Resolve
    reports it. Targets by item_ids or range {start_frame, end_frame, track}. Ask first.
    """
    params: dict[str, Any] = {"from_item_id": from_item_id}
    if item_ids:
        params["item_ids"] = item_ids
    if range:
        params["range"] = range
    return _result("copy_grade", params, timeout=180.0)


@mcp.tool()
def diff_since(snapshot_id: Optional[str] = None, baseline: Optional[dict[str, Any]] = None, include_state: bool = False,
               max_changes: int = 300) -> str:
    """Read-only: structured changes on the open timeline since a snapshot (snapshot_timeline / list_snapshots id) or
    since a `baseline` state returned earlier with include_state=True. Lists added / removed / moved / trimmed clips,
    enabled, color and property changes (AudioVolume, transforms...), transitions added/removed and markers. Call it
    before any rebuild to see the user's hand edits. Fades, keyframes and grade/Fusion contents are not compared.
    """
    params: dict[str, Any] = {"include_state": include_state, "max_changes": max_changes}
    if snapshot_id:
        params["snapshot_id"] = snapshot_id
    if baseline is not None:
        params["baseline"] = baseline
    return _result("diff_since", params, timeout=120.0)


@mcp.tool()
def trim_in_place(item_id: str, head_frames: int = 0, tail_frames: int = 0, dry_run: bool = False) -> str:
    """Trim or extend one clip's head and/or tail IN PLACE on the open timeline: no copy, no ripple.

    Positive frames trim (the head moves later / the tail earlier), negative frames extend. Linked audio follows
    its video. Neighbours never move: a trim leaves a gap (reported in gaps_left) and an extension that would
    overlap the next/previous clip, or reach past the media's first/last frame, is refused with the room
    available. Reads everything back: mixed-frame-rate clips can land a frame off (`exact`). This edits the
    user's timeline directly, so ask first and consider snapshot_timeline; use trim_edit for a rippled edit on a
    copy. For stills, titles and generators use set_clip_length.
    """
    return _result("trim_in_place", {"item_id": item_id, "head_frames": head_frames, "tail_frames": tail_frames,
                                     "dry_run": dry_run}, timeout=60.0)


@mcp.tool()
def set_clip_length(item_id: str, frames: int, dry_run: bool = False) -> str:
    """Set a clip's length in timeline frames in place by moving its tail (stills such as add_image photos,
    titles and generators too). Refuses to overlap the next clip or run past a video's media; reads back.
    Verified on stills on Resolve 21.2 Free; titles/generators are reported as unverified. Ask first.
    """
    return _result("set_clip_length", {"item_id": item_id, "frames": frames, "dry_run": dry_run}, timeout=60.0)


@mcp.tool()
def set_clip_volume(item_ids: list[str], db: float) -> str:
    """Set clip volume (Inspector > Audio > Volume) in dB on audio clips, each read back. A video clip id is
    mapped to its linked audio. Range -60..+24 dB. Track faders and Fairlight effects are not touched. Ask first.
    """
    return _result("set_clip_volume", {"item_ids": item_ids, "db": db}, timeout=60.0)


@mcp.tool()
def add_transitions(cuts: Any = "all", duration_frames: Optional[int] = None, audio_fades: bool = False,
                    fade_frames: Optional[int] = None, dry_run: bool = False, bin: Optional[str] = None) -> str:
    """Add Cross Dissolves at V1 cuts on a NEW timeline "<name> (transitions)"; the original is untouched.

    cuts: "all" or a list of absolute timeline frames where two V1 clips touch. duration_frames defaults to one
    second, centred on the cut; cuts without enough media handles are skipped with the reason. Works through an
    FCPXML export, edit and import (verified on Resolve 21.2 Free); then re-applies transforms, grades, Fusion comps,
    clip properties (volume), clip markers, timeline markers, enabled state and clip color from the original and
    reads the layout back. Refuses timelines it can't represent safely: anything beyond one video track with its
    own audio on A1, titles/generators/compound clips, retimed clips, subtitles, mismatched frame rate or start.
    audio_fades=True also writes fade in/out per clip into the FCPXML, but that is UNVERIFIED (it did not survive
    a re-export in testing): check it. Audio stays a hard cut under each dissolve: Resolve 21.2 Free drops audio
    transitions on FCPXML import (live-tested), so tell the user to add audio crossfades by hand (select the audio
    edit points, Timeline > Add Audio Only Transition). dry_run=True returns the plan. Ask first; then look at the
    dissolves with render_frames.
    """
    params: dict[str, Any] = {"cuts": cuts, "audio_fades": audio_fades, "dry_run": dry_run, "bin": bin}
    if duration_frames is not None:
        params["duration_frames"] = duration_frames
    if fade_frames is not None:
        params["fade_frames"] = fade_frames
    return _result("add_transitions", params, timeout=300.0)


@mcp.tool()
def trim_edit(item_id: str, edge: str = "tail", frames: int = 0, ripple: bool = True, name: str = "",
              dry_run: bool = False) -> str:
    """Move one clip's head or tail edit point by `frames` timeline frames (positive = later, e.g. -6 = 6 earlier).

    item_id: unique id from timeline_overview. edge: "head" or "tail". ripple=True keeps sync: a tail trim
    moves every clip (all tracks) that starts at or after the old tail; a head trim keeps the clip's start
    and moves everything after its first kept frame. ripple=False leaves a gap, or extends into an existing gap.

    Builds a NEW timeline (original kept untouched; only changed clips are rebuilt) and opens it. Ask the
    user first; dry_run=True returns the plan and changes nothing. Linked audio follows its video. Refuses,
    changing nothing, if the edit needs media beyond the handles (it says how many frames exist), would
    overlap a clip, or touches a retimed, title, generator or compound clip. Returns the new timeline's
    name/id, every clip that moved (before/after position and source in/out) and an expected-vs-actual check.
    """
    return _result("trim_edit", {"item_id": item_id, "edge": edge, "frames": frames, "ripple": ripple,
                                 "name": name, "dry_run": dry_run}, timeout=590.0)


@mcp.tool()
def slip_clip(item_id: str, frames: int = 0, name: str = "", dry_run: bool = False) -> str:
    """Slip a clip: shift its source in and out by `frames` (timeline frames; positive shows later material).

    The clip keeps its timeline position and length; nothing else moves. Linked audio slips with it.
    Builds a NEW timeline (original kept) and opens it. Ask the user first; dry_run=True only plans.
    Refuses if the slip needs media beyond the handles. Returns the new timeline, what moved and verification.
    """
    return _result("slip_clip", {"item_id": item_id, "frames": frames, "name": name, "dry_run": dry_run},
                   timeout=590.0)


@mcp.tool()
def slide_clip(item_id: str, frames: int = 0, name: str = "", dry_run: bool = False) -> str:
    """Slide a clip along its track by `frames` timeline frames (positive = later), keeping its content.

    The touching neighbour before it lengthens or shortens its tail and the one after it its head, so the
    track's total length stays the same. Into a gap, the clip just moves. Linked audio slides with it.
    Builds a NEW timeline (original kept) and opens it. Ask the user first; dry_run=True only plans.
    Refuses if a neighbour would need media beyond its handles or vanish. Returns the new timeline,
    what moved and verification.
    """
    return _result("slide_clip", {"item_id": item_id, "frames": frames, "name": name, "dry_run": dry_run},
                   timeout=590.0)


@mcp.tool()
def roll_edit(left_item_id: str, right_item_id: str, frames: int = 0, name: str = "", dry_run: bool = False) -> str:
    """Roll the cut between two touching clips by `frames` timeline frames (positive = later).

    One clip gets longer and the other shorter; nothing else moves. "Nudge this cut 6 frames" is roll_edit.
    Their linked audio rolls too. Builds a NEW timeline (original kept) and opens it. Ask the user first;
    dry_run=True only plans. Refuses if the longer clip lacks the handle frames (it says how many exist).
    Returns the new timeline, what moved and verification.
    """
    return _result("roll_edit", {"left_item_id": left_item_id, "right_item_id": right_item_id, "frames": frames,
                                 "name": name, "dry_run": dry_run}, timeout=590.0)


@mcp.tool()
def remove_ranges(ranges: list[dict[str, Any]], name: str = "", bin: Optional[str] = None) -> str:
    """Cut time ranges out of the WHOLE timeline (all video and audio tracks) on a new duplicate timeline.

    ranges: [{start_frame, end_frame}] in absolute timeline frames (from transcribe, search_transcript,
    detect_shots...). The copy closes the gaps and opens; the original stays untouched. Transforms,
    grades, Fusion comps and markers are carried over; fades, transitions and audio keyframes are not.
    For a small fix to one clip's head or tail that should NOT ripple, trim_in_place edits it directly
    and keeps everything else on the clip. Ask the user before calling this.
    """
    return _result("remove_ranges", {"ranges": ranges, "name": name, "bin": bin}, timeout=590.0)


@mcp.tool()
def timeline_notes(include_done: bool = False) -> str:
    """Read the user's timeline notes: markers they left with a name or comment ("cut this um", "zoom in here").

    Read-only. Markers the bridge made (pauses, dead air, shots, beats, QC, review cuts) are never notes.
    Returns {notes: [{frame (absolute timeline frame), timecode, duration, end_frame, name, note, color,
    status (open/done/skipped), reply, clips: [{name, track, item_id}] under the note's start}], open, done,
    skipped}, sorted by frame. Open notes only unless include_done. Mark each one with resolve_timeline_note.
    """
    return _result("timeline_notes", {"include_done": bool(include_done)}, timeout=60.0)


@mcp.tool()
def resolve_timeline_note(frame: int, status: str = "done", reply: str = "") -> str:
    """Mark one timeline note done or skipped on the timeline itself (status "open" reopens it).

    frame is the absolute frame from timeline_notes. Done turns the marker Green, skipped Cream (Sand if
    refused); the one-line reply is appended to the note as "— AI: ..." and the status is kept in the
    marker's customData. Resolve can't recolor in place, so the marker is deleted and re-added with the same
    frame, name and duration, then read back; if the re-add fails the original is restored. Never deletes notes.
    """
    return _result("resolve_timeline_note", {"frame": frame, "status": status, "reply": reply}, timeout=30.0)


@mcp.tool()
def add_markers(markers: list[dict[str, Any]]) -> str:
    """Add many timeline markers at once: [{frame (absolute timeline frame), color, name, note, duration}].

    Colors: Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream.
    Use for chapters from a transcript, beat grids, shot changes, or review notes.
    """
    return _result("add_markers", {"markers": markers}, timeout=120.0)


# ---------------------------------------------------- vision, shots, beats, captions


def _ffmpeg():
    from bridge.operations import ResolveOperations
    import shutil as _shutil
    return ResolveOperations._ffmpeg_path(client.HOME) or _shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _sources(scope: str, item_id: Optional[str], track_index: Optional[int], kind: str, bin_name: str = "", limit: int = 120):
    params: dict[str, Any] = {"scope": scope, "limit": limit}
    if item_id:
        params["item_id"] = item_id
    if track_index:
        params["track_index"] = track_index
    if bin_name:
        params["bin"] = bin_name
    op = "media_sources" if kind == "video" else "transcription_source"
    return transport.call(op, params, timeout=60.0)


@mcp.tool()
def visual_diff(original: str, revised: str, max_rows: int = 8) -> list:
    """SEE what changed between two versions of a timeline: one before/after image grid plus a JSON list.

    original / revised: timeline ids or names, as compare_timelines takes them (e.g. the original and a
    preview_timeline copy). Read-only: neither timeline is opened and the current timeline stays as it is.
    Each row is one change point (removed, inserted, replaced, moved, gap opened/closed or clip settings
    changed), the most significant max_rows of them with nearby changes merged, captioned like
    "Removed 2.3 s at 01:00:40:18". Left column: the original at the change; right: the revised version at
    the same point. Frames are absolute timeline frames. Frames come from SOURCE media, so grades, Fusion
    effects, transforms and titles are not shown; audio-only edits appear only in structural_changes.
    """
    try:
        payload, used = transport.call("visual_diff", {"original": original, "revised": revised,
                                                       "max_rows": max(1, min(int(max_rows), 16))}, timeout=90.0)
    except client.BridgeOffline as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}))]
    except client.BridgeError as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": str(exc)}))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))]
    content: list = []
    if payload.get("rows"):
        try:
            from bridge import visual_diff as _visual_diff
            grid = _visual_diff.render_grid(payload["rows"], client.HOME, _ffmpeg())
            content.append(ImageContent(type="image", data=base64.b64encode(Path(grid["path"]).read_bytes()).decode("ascii"),
                                        mimeType="image/jpeg"))
            payload["grid"] = grid
        except Exception as exc:
            payload["grid_error"] = "No image grid: %s: %s" % (type(exc).__name__, exc)
    content.insert(0, TextContent(type="text", text=json.dumps({"ok": True, "transport": used, "result": payload},
                                                                ensure_ascii=False, indent=1)))
    return content


@mcp.tool()
def styled_title(text: str, start_frame: int, duration_frames: int, font: str = "", style: str = "",
                 size: float = 0.08, color: str = "#FFFFFF", position: str = "lower_third", align: str = "auto",
                 safe_area: str = "title", track: Optional[int] = None, background: Optional[str] = None,
                 dry_run: bool = False) -> str:
    """Add a precisely styled Text+ title at an absolute timeline frame. ASK THE USER BEFORE INSERTING.

    Run with dry_run=True first and show the plan (track, timecode, font, position); insert only after the
    user agrees, ideally on a preview_timeline copy. start_frame is absolute (as timeline_overview reports).
    font: family name, checked against fonts installed on this Mac; when missing, the fallback actually used is
    reported (style: e.g. "Bold"; empty picks Regular). size: Text+ Size, relative to frame width (0.08 is the
    Text+ default). color / background: hex like "#FFFFFF" or "#000000AA"; background draws a box behind the
    text (Text+ shading element 2). position: lower_third, center, top, bottom_center, upper_left, upper_right,
    or "x,y" (0..1, 0,0 bottom-left), inside safe_area "title" (80%), "action" (90%) or "none". align: auto,
    left, center or right (horizontal anchor; presets other than lower_third fix it). track: an existing empty
    video track, or omit for a new top track. Resolve's ordinary API inserts titles at the playhead and picks
    the track and length itself, so check placement (track_matched, start_matched, duration_matched) and
    other_clips_changed, plus text_set, font_set, size_set, center_set and readback, and tell the user exactly
    what Resolve did.
    """
    return _result("styled_title", {"text": text, "start_frame": start_frame, "duration_frames": duration_frames,
                                    "font": font, "style": style, "size": size, "color": color, "position": position,
                                    "align": align, "safe_area": safe_area, "track": track,
                                    "background": background, "dry_run": dry_run}, timeout=60.0)


@mcp.tool()
def contact_sheet(scope: str = "timeline", item_id: Optional[str] = None, track_index: Optional[int] = None,
                  bin: str = "", frames_per_clip: int = 3, max_cells: int = 24) -> list:
    """SEE the footage: labelled grids of frames sampled from clips, so you can find shots visually.

    scope: "timeline" (every video clip), "track", "clip", or "media_pool" (filter with bin).
    Each cell has a big number; the JSON legend maps numbers to item ids, names, tracks and timecodes.
    Use it for "find clips where I look at the camera", "which take is sharpest", "where's the wide shot".
    Frames come from source media (no grades/effects). Several sheets are returned for many clips.
    """
    try:
        from bridge import media_render
        payload, used = _sources(scope, item_id, track_index, "video", bin)
        sheets = media_render.contact_sheets(payload["sources"], client.HOME, _ffmpeg(),
                                             frames_per_clip=max(1, min(int(frames_per_clip), 8)),
                                             max_cells=max(4, min(int(max_cells), 36)))
    except client.BridgeError as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))]
    content: list = []
    legend = []
    for sheet in sheets[:6]:
        data = Path(sheet["path"]).read_bytes()
        legend.append({"sheet": len(legend) + 1, "cells": sheet.get("cells", [])})
        content.append(ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mimeType="image/jpeg"))
    skipped = payload.get("skipped", []) + (sheets[0].get("skipped", []) if sheets else [])
    content.insert(0, TextContent(type="text", text=json.dumps({"ok": True, "transport": used, "sheets": legend,
                                                                  "skipped": skipped[:30], "more_sheets": max(0, len(sheets) - 6)},
                                                                 ensure_ascii=False)))
    return content


@mcp.tool()
def detect_shots(scope: str = "timeline", item_id: Optional[str] = None, track_index: Optional[int] = None,
                 threshold: float = 0.3, mark: bool = False) -> str:
    """Find camera cuts / angle changes inside clips (ffmpeg scene detection on the source media).

    Returns cut points as absolute timeline frames per clip. threshold 0.2 (sensitive) to 0.5 (hard cuts
    only). mark=True adds Cyan "Shot" markers. Useful before cutting a long recording into shots.
    """
    try:
        from bridge import scenes
        payload, used = _sources(scope, item_id, track_index, "video")
        ff = _ffmpeg()
        clips = [scenes.detect_shots(src, ff, threshold=float(threshold)) for src in payload["sources"]]
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    result: dict[str, Any] = {"clips": clips, "total_cuts": sum(len(c.get("cuts", [])) for c in clips),
                              "skipped": payload.get("skipped", [])}
    if mark and result["total_cuts"]:
        marks = [{"frame": cut["timeline_frame"], "color": "Cyan", "name": "Shot", "note": "%s (score %.2f)" % (c.get("name"), cut.get("score") or 0)}
                 for c in clips for cut in c.get("cuts", [])]
        done = json.loads(_result("add_markers", {"markers": marks}, timeout=120.0))
        result["markers"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def detect_beats(item_id: Optional[str] = None, track_index: Optional[int] = None, mark: str = "none",
                 min_bpm: float = 60, max_bpm: float = 200) -> str:
    """Find the tempo, beats and downbeats of a music clip (default: the clip under the playhead, audio first).

    Returns BPM, confidence and beat/downbeat timeline frames. mark: "none", "beats" (Green markers) or
    "downbeats" (Red markers, every bar). Use the frames to cut or place clips on the beat.
    """
    try:
        from bridge import beats
        params_scope = "clip"
        payload, used = _sources(params_scope, item_id, track_index, "audio")
        if not payload["sources"]:
            return json.dumps({"ok": False, "error": "No music clip with a readable file in that selection."})
        analysis = beats.detect_beats(payload["sources"][0], _ffmpeg(), min_bpm=float(min_bpm), max_bpm=float(max_bpm))
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    result = dict(analysis)
    result["beats"] = analysis.get("beats", [])[:2000]
    if mark in ("beats", "downbeats"):
        chosen = analysis.get("downbeats" if mark == "downbeats" else "beats", [])
        marks = [{"frame": b["timeline_frame"], "color": "Red" if mark == "downbeats" else "Green",
                  "name": "Bar" if mark == "downbeats" else "Beat"} for b in chosen]
        done = json.loads(_result("add_markers", {"markers": marks}, timeout=180.0))
        result["markers"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)


@mcp.tool()
def animated_captions(scope: str = "timeline", item_id: Optional[str] = None, style: str = "pop",
                      position: str = "bottom", max_words: int = 4, uppercase: bool = False,
                      place_on_timeline: bool = True, language: str = "auto") -> str:
    """Burn-in style animated captions with the spoken word highlighted (TikTok/Reels look), Resolve Free compatible.

    Transcribes (cached), renders a transparent ProRes 4444 overlay natively on this Mac, imports it and
    places it on a new top video track aligned to the speech. style: "pop" (active word highlighted with a
    bounce), "karaoke" (words fill in as spoken), "minimal" (clean white, active word underlined).
    position: bottom, center or top. To restyle, call again (the old overlay can be disabled or deleted).
    For editable plain subtitles use generate_subtitles instead.
    """
    try:
        from bridge import media_render
        payload, used, results = _transcripts(item_id, scope, None, language, "")
        words = sorted((w for r in results for w in r["words"]), key=lambda w: w["start_frame"])
        if not words:
            return json.dumps({"ok": False, "error": "No speech found to caption."})
        info, _ = transport.call("project_info", {}, timeout=30.0)
        width, height = _resolution(info)
        fps = float(payload["fps"])
        start, end = int(words[0]["start_frame"]), int(words[-1]["end_frame"]) + int(fps)
        out_dir = Path(client.HOME) / "captions"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / ("captions-%s-%s.mov" % (style, time.strftime("%Y%m%d-%H%M%S")))
        rendered = media_render.render_captions(words, fps, width, height, start, end, str(out), client.HOME,
                                                style=style, position=position, max_words=max(1, min(int(max_words), 8)),
                                                uppercase=bool(uppercase))
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    result: dict[str, Any] = {"overlay": rendered}
    if place_on_timeline:
        placed = json.loads(_result("append_media", {"paths": [rendered["path"]], "record_frame": rendered["start_frame"],
                                                     "track_index": None, "create_track": True, "media_type": 1,
                                                     "new_top_track": True, "alpha_mode": "Premultiplied"}, timeout=120.0))
        result["placed"] = placed.get("result") if placed.get("ok") else {"error": placed.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": result}, ensure_ascii=False, indent=1)



@mcp.tool()
def index_footage(scope: str = "timeline", item_id: Optional[str] = None, track_index: Optional[int] = None,
                  bin: str = "", interval_seconds: float = 1.0) -> str:
    """Index what is VISIBLE in footage, on this Mac (Apple Vision): on-screen text, scene labels, faces.

    Samples a frame every interval_seconds per clip (cached per file, so it runs once). Returns a summary per
    clip with its most common scene labels (use those words with search_footage). search_footage indexes on
    demand, so call this first only to pre-warm long footage or to learn what labels exist.
    """
    try:
        from bridge import footage
        payload, used = _sources(scope, item_id, track_index, "video", bin)
        ff = _ffmpeg()
        summaries, failed = [], []
        for src in payload["sources"]:
            try:
                summaries.append(footage.summary(footage.index_source(src, client.HOME, ff, float(interval_seconds))))
            except Exception as exc:
                failed.append({"name": src.get("name"), "error": str(exc)})
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    return json.dumps({"ok": True, "transport": used, "result": {"clips": summaries, "failed": failed,
                       "skipped": payload.get("skipped", [])}}, ensure_ascii=False, indent=1)


@mcp.tool()
def search_footage(text: str = "", labels: Optional[list[str]] = None, facing_camera: bool = False, min_faces: int = 0,
                   scope: str = "timeline", item_id: Optional[str] = None, track_index: Optional[int] = None,
                   bin: str = "", interval_seconds: float = 1.0) -> str:
    """Find WHEN something is visible: on-screen text ("error", "Visual Studio Code"), scene labels
    ("screenshot", "outdoor", "computer", "whiteboard"), a face looking at the camera, or N people.

    All given conditions must hold. Text matching ignores case and tolerates OCR typos. Returns time spans
    with timeline frames/timecodes (or source seconds for media pool clips) and the evidence seen. Indexes
    on demand (cached). Confirm visually with contact_sheet or timeline_frame before editing.
    """
    try:
        from bridge import footage
        payload, used = _sources(scope, item_id, track_index, "video", bin)
        ff = _ffmpeg()
        indexes, failed = [], []
        for src in payload["sources"]:
            try:
                indexes.append(footage.index_source(src, client.HOME, ff, float(interval_seconds)))
            except Exception as exc:
                failed.append({"name": src.get("name"), "error": str(exc)})
        hits = footage.search(indexes, text=text, labels=labels or [], facing_camera=facing_camera, min_faces=int(min_faces))
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    return json.dumps({"ok": True, "transport": used, "result": {"hits": hits, "count": len(hits),
                       "clips_searched": len(indexes), "failed": failed}}, ensure_ascii=False, indent=1)



@mcp.tool()
def gaming_cleanup(preset: str = "youtube", scope: str = "timeline", item_id: Optional[str] = None,
                   mic_stream: Optional[int] = None, game_stream: Optional[int] = None, check_picture: bool = True,
                   mark: bool = True, apply: bool = False, language: str = "auto") -> str:
    """Tighten gameplay: cut only dead air, never talking and never moments where something is happening.

    Kept: your speech (always), game-audio events (judged against the game track's own baseline, so steady
    music doesn't count but gunfire over it does), and on-screen action spikes (flashes, explosions, kill-feed
    pops). Cut: loading/black screens, frozen menus, and quiet stretches with no speech or action.
    OBS multi-track recordings are read per track: the mic track is found automatically (or pass mic_stream /
    game_stream, 0-based audio track numbers); single-track recordings work too, less precisely. Whisper's
    invented words on silence are filtered by checking the mic actually has sound.
    preset: "vod" (gentle, cuts 6 s+), "youtube" (3 s+), "shorts" (1.5 s+). apply=False (default) reports and,
    with mark=True, adds purple "Dead air" markers. apply=True builds a NEW tightened timeline (every track)
    and leaves the original untouched — ask the user first.
    """
    from bridge import activity
    if preset not in activity.PRESETS:
        return json.dumps({"ok": False, "error": "preset must be one of: %s" % ", ".join(activity.PRESETS)})
    try:
        params: dict[str, Any] = {"scope": scope}
        if item_id:
            params["item_id"] = item_id
        payload, used = transport.call("transcription_source", params, timeout=300.0)
        ff = _ffmpeg()
        clips, ranges = [], []
        for src in payload["sources"]:
            streams = activity.audio_streams(ff, src["file_path"])
            mic, game, scores = mic_stream, game_stream, []
            if mic is None and len(streams) > 1:
                mic, scores = activity.pick_mic_stream(src, client.HOME, ff, streams)
            if game is None and len(streams) > 1:
                game = next((s["stream"] for s in streams if s["stream"] != mic), None)
            words = _tx().transcribe_source(src, client.HOME, language=language, ffmpeg=ff, stream=mic)["words"]
            result = activity.analyze(src, ff, words, preset=preset, game_stream=game, use_picture=check_picture,
                                      mic_stream=mic if len(streams) > 1 else None)
            result.update({"audio_tracks": len(streams), "mic_track": mic, "game_track": game, "mic_scores": scores})
            clips.append(result)
            ranges += result["dead"]
    except client.BridgeError as exc:
        return json.dumps({"ok": False, "error": str(exc), "help": transport.offline_help()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2)
    fps = float(payload["fps"])
    summary: dict[str, Any] = {
        "preset": preset, "cuts": len(ranges), "seconds_cut": round(sum(r["seconds"] for r in ranges), 1),
        "by_reason": {why: round(sum(r["seconds"] for r in ranges if r["why"] == why), 1) for why in {r["why"] for r in ranges}},
        "clips": [{k: c[k] for k in ("name", "analyzed_sec", "seconds_by_reason", "audio_tracks", "mic_track",
                                     "game_track", "phantom_words_dropped", "picture_checked")} for c in clips],
        "first_cuts": [dict(r, timecode=_tx().frames_to_tc(r["start_frame"], fps)) for r in ranges[:40]],
    }
    if ranges and apply:
        done = json.loads(_result("remove_ranges", {"ranges": ranges, "name": ""}, timeout=590.0))
        summary["applied"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    elif ranges and mark:
        marks = [{"frame": r["start_frame"], "color": "Purple", "name": "Dead air %.1fs" % r["seconds"],
                  "duration": r["end_frame"] - r["start_frame"], "note": r["why"]} for r in ranges]
        done = json.loads(_result("add_markers", {"markers": marks}, timeout=180.0))
        summary["markers"] = done.get("result") if done.get("ok") else {"error": done.get("error")}
    return json.dumps({"ok": True, "transport": used, "result": summary}, ensure_ascii=False, indent=1)


def _tx():
    from bridge import transcribe as module
    return module


def _resolution(info: dict) -> tuple[int, int]:
    if info.get("resolution_width") and info.get("resolution_height"):
        return int(info["resolution_width"]), int(info["resolution_height"])
    for key in ("resolution", "timeline_resolution"):
        value = info.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return int(value[0]), int(value[1])
        if isinstance(value, dict) and value.get("width"):
            return int(value["width"]), int(value["height"])
    try:
        return int(info.get("width") or 1920), int(info.get("height") or 1080)
    except (TypeError, ValueError):
        return 1920, 1080


@mcp.resource("resolve://guide")
def editing_guide() -> str:
    """Workflow and safety guide for editing with Resolve AI Bridge."""
    return """# Resolve AI Bridge editing guide

1. Call `resolve_status`. It reports which transport is live. Stop if no project or
   timeline is open, and pass the `help` text on to the user rather than guessing.
2. Call `timeline_overview` and refer to clips by ids such as `V1.2`, never by an
   ambiguous name.
3. Explain the edit plan and assumptions before changing anything.
4. Make one small change at a time and inspect again after each change.
5. Ask before deleting clips, using ripple delete, or starting a render.
6. Images: use `add_image`. Put overlays on `track_index` 2 or higher so footage on V1
   stays visible, then position with `set_clip_transform` or the pan/tilt/zoom
   arguments of `add_image`. Always check `actual_duration_frames` in the result,
   because Resolve can shorten a still to its default length.
7. Scale and reframe clips already on the timeline with `set_clip_transform` (a static
   size) or `animate_zoom` (a size that changes over time, built as a Fusion
   composition). Cut a clip in two with `split_clip`. All three accept
   `item_id="playhead"` to act on the clip under the playhead, so you need not look up
   the id first. `split_clip` rebuilds the clip and does not copy color grades or
   Fusion comps onto the halves; `animate_zoom` reports `keyframes_created` and falls
   back to a static zoom on builds that will not keyframe from scripting.
8. `insert_title` is best effort. If `text_set` is false, say so and ask the user to
   type the text in the Inspector.
9. Group multi-layer or related clips into a clean single block using `create_compound_clip`.
   Slow down or speed up playback using `change_clip_speed`.
10. Timeline item frames are absolute Resolve timeline frames unless a tool says
   otherwise. Marker frames are relative to the timeline start.
11. Prefer Remotion for designed motion graphics and animated typography. Render a
    clip, import it, then ask where to place it.
12. Resolve's public scripting API cannot perform every interactive Edit page action.
    If a tool is absent, explain the limitation instead of pretending it worked.
"""


@mcp.prompt(name="edit_video")
def edit_video_prompt(goal: str) -> str:
    """Create a cautious, verifiable Resolve editing workflow for a user goal."""
    return (
        "Goal: %s\n\n"
        "First call resolve_status and timeline_overview. Summarize the current timeline, then "
        "propose a short plan. Wait if the goal is ambiguous. Apply small edits, verify each "
        "result with a fresh timeline_overview, and request approval before deletion or rendering."
        % goal
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
