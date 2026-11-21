#!/usr/bin/env python3
"""Live probes for Resolve scripting calls that are unverified on Resolve 21.2 Free.

SAFETY RULES (read before any live run)
- Live runs need a SCRATCH project. --run refuses unless the open project's name equals --scratch-project
  exactly and contains "scratch" or "probe" (any case). It also refuses while a render is in progress.
- Run ONE probe per invocation. Calls go one at a time, never in parallel, with a 120 s timeout. Do not use
  Resolve or another bridge client (the chat panel, an agent) during a run. Resolve has crashed under
  concurrent load.
- A probe only creates things. It makes new timelines named "AI probe · <probe> — <8 hex>", which
  list_ai_timelines and cleanup_ai_timelines classify as bridge-made, and a media bin named
  "AI probe media — <8 hex>". It never edits or deletes existing timelines and never re-opens one,
  except to re-open the originally open timeline at the end. It deletes nothing and never sends
  allow_destructive. It restores the media pool's current folder. Remove the probe timelines and bin
  yourself when you are done.
- Every Resolve call goes through bridge.transport.call, the same authenticated route the MCP server uses.
  An uncertain outcome (a timeout, a lost or refused Lua reply, a changed observed context) stops the probe
  at once. Nothing is retried and the original timeline is NOT re-opened, so inspect Resolve yourself.
- Each chain that edits the current timeline starts with a guard step that reads the current timeline's
  name. A mismatch stops the probe. The guard detects a mismatch after the chain has run; it cannot
  prevent one. What does prevent one on the Lua and Console routes is the bridge's observed-context
  check, which refuses an edit when the open timeline changed between calls.
- Only --run contacts Resolve. --list and --plan are offline. --plan replays the probe against a simulated
  Resolve (24 fps, every setter returns True, nothing reads back changed), so its indices and names are
  placeholders. Each live run uses fresh 8-hex suffixes.

USAGE (from the repository root; NAME is one of the probes below)
  python3 tools/probes/live_probes.py --list
  python3 tools/probes/live_probes.py --plan NAME
  python3 tools/probes/live_probes.py --run still_duration     --scratch-project "API probe scratch" [--image STILL.png]
  python3 tools/probes/live_probes.py --run item_offsets       --scratch-project "API probe scratch" --video CLIP.mov
  python3 tools/probes/live_probes.py --run set_position       --scratch-project "API probe scratch" --video CLIP.mov
  python3 tools/probes/live_probes.py --run move_items         --scratch-project "API probe scratch" --video CLIP.mov
  python3 tools/probes/live_probes.py --run fcpxml_roundtrip   --scratch-project "API probe scratch" --video CLIP.mov
  python3 tools/probes/live_probes.py --run audio_volume       --scratch-project "API probe scratch" --video CLIP.mov
  python3 tools/probes/live_probes.py --run clip_marker_frames --scratch-project "API probe scratch" --video CLIP.mov
Use a video of at least 4 seconds with an audio track. When --image is omitted, a small PNG is generated.
--work-dir sets where the PNG and the FCPXML files go. On the sandboxed Mac App Store build, Resolve can only
read and write inside its container. The default is therefore <container>/Data/resolve-ai-probes/<run>, a
sibling of the bridge runtime folder, never inside it; elsewhere it is a temporary folder. The video must
also be readable by Resolve: use a file Resolve has already imported, or copy one into the work dir.
For fcpxml_roundtrip, --export-type N skips the constant lookup, for example after you ran
print(resolve.EXPORT_FCPXML_1_10) in Workspace > Console (Lua).

OUTPUT: one JSON report on stdout with every call (op, params, raw result or error), the read-backs, a
verdict per question ("works", "returns True but readback differs", "rejected", "unavailable", "error",
"check visually") and "look_in_resolve" where a visual check is needed. Exit code 0 means the probe ran,
1 that it stopped early, 2 that it was refused before any edit, 3 that it halted on an uncertain outcome.
"""

import argparse
import copy
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.operations import (  # noqa: E402  (stdlib-only module, never touches Resolve)
    OperationError, RESOLVE_API_DESTRUCTIVE, RESOLVE_API_MAX_STEPS)

TIMEOUT = 120.0
ITEM_READS = ("GetStart", "GetEnd", "GetDuration", "GetLeftOffset", "GetRightOffset")
SOURCE_READS = ("GetSourceStartFrame", "GetSourceEndFrame")
FIELDS = {"GetStart": "start", "GetEnd": "end", "GetDuration": "duration", "GetLeftOffset": "left_offset",
          "GetRightOffset": "right_offset", "GetSourceStartFrame": "source_start", "GetSourceEndFrame": "source_end",
          "GetMediaPoolItem": "media_pool_item"}
CLIP_KEYS = ("Frames", "Duration", "End", "Out", "In", "Start", "FPS")
EXPORT_CONSTANTS = ("EXPORT_FCPXML_1_10", "EXPORT_FCPXML_1_11", "EXPORT_FCPXML_1_9", "EXPORT_FCPXML_1_8")
CROSS_DISSOLVE_UID = "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"  # Final Cut Pro's Cross Dissolve.
ITEM = "{item}"  # Placeholder in try_call args for the probed timeline item.
UNCERTAIN = ("outcome unknown", "not confirmed", "no automatic replay", "lua rpc", "inspect resolve_status",
             "in this client before editing", "context changed", "deadline", "timed out", "uncertain")


class Halt(Exception):
    """An uncertain outcome or a guard mismatch: stop sending anything."""


class ProbeStop(Exception):
    """The probe cannot continue, but the bridge state is known; restoring is still safe."""


def bridge_call(operation, params, timeout):
    """The live route. Imported lazily so --list, --plan and the tests never load a transport."""
    from bridge import transport
    result, route = transport.call(operation, params, timeout=timeout)
    bridge_call.route = route
    return result


CONFIRMED_SCRATCH = set()  # Project names the user said are scratch (--user-confirmed-scratch); exact match still required.


def scratch_problem(open_project, wanted):
    if not wanted:
        return "--scratch-project is required for a live run."
    if wanted not in CONFIRMED_SCRATCH and not re.search("scratch|probe", wanted, re.IGNORECASE):
        return "Refused: %r does not contain 'scratch' or 'probe'. Use a scratch project." % wanted
    if open_project != wanted:
        return "Refused: the open project is %r, not %r. Nothing was changed." % (open_project, wanted)
    return None


def as_list(value):
    """A resolve_api list result; a Lua table may arrive as a dict keyed 1..n."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and all(str(key).isdigit() for key in value):
        return [value[key] for key in sorted(value, key=int)]
    return []


def timecode(frame, fps):
    """Non-drop-frame HH:MM:SS:FF at the nominal rate (approximate for 23.976/29.97 drop-frame)."""
    base, frame = max(1, int(round(fps))), int(frame)
    return "%02d:%02d:%02d:%02d" % (frame // (3600 * base), frame // (60 * base) % 60, frame // base % 60, frame % base)


def write_png(path, width=160, height=90):
    """A small RGB gradient PNG using only zlib and struct."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes((x * 255 // (width - 1), y * 255 // (height - 1), 128))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
    return Path(path)


# ------------------------------------------------------------------ FCPXML editing (pure)

STORY_TAGS = ("asset-clip", "clip", "ref-clip", "sync-clip", "mc-clip", "video", "audio")


def _time(text):
    text = (text or "0s").strip().rstrip("s")
    if "/" in text:
        top, bottom = text.split("/", 1)
        return Fraction(int(top), int(bottom))
    return Fraction(text)


def _fmt(value):
    value = Fraction(value)
    return "%ds" % value.numerator if value.denominator == 1 else "%d/%ds" % (value.numerator, value.denominator)


def _next_resource_id(root):
    numbers = [int(match.group(1)) for element in root.iter() for match in [re.fullmatch(r"r(\d+)", element.get("id") or "")]
               if match]
    return "r%d" % (max(numbers or [0]) + 1)


def _add_fades(clip, fade):
    volume = clip.find("adjust-volume")
    if volume is None:
        volume = ET.Element("adjust-volume")
        children = list(clip)
        leading = [i for i, child in enumerate(children)
                   if child.tag in ("note", "conform-rate", "timeMap")
                   or (child.tag.startswith("adjust-") and child.tag != "adjust-panner")]
        clip.insert(leading[-1] + 1 if leading else 0, volume)
    param = volume.find("param[@name='amount']")
    if param is None:
        param = ET.SubElement(volume, "param", {"name": "amount"})
    ET.SubElement(param, "fadeIn", {"type": "linear", "duration": _fmt(fade)})
    ET.SubElement(param, "fadeOut", {"type": "linear", "duration": _fmt(fade)})


def edit_fcpxml(text, fps, dissolve_frames=0, fade_frames=0, move_frames=0):
    """Return (edited FCPXML text, list of changes) for the first two clips of the first sequence's spine.

    dissolve_frames adds a Cross Dissolve centred on their cut (even, and the clips must touch).
    fade_frames adds an audio fade-in and fade-out to clip 2 and to any connected audio under it.
    move_frames moves clip 2 (and everything after it) later by inserting a gap.
    A dissolve cannot sit between clips separated by a gap, so dissolve and move are separate edits.
    Frame lengths come from the sequence format's frameDuration, else from fps.
    """
    if dissolve_frames and move_frames:
        raise ValueError("A dissolve needs the clips to touch; make the dissolve and the move separate edits.")
    if dissolve_frames % 2:
        raise ValueError("dissolve_frames must be even so the dissolve is centred on a frame boundary.")
    start = text.find("<fcpxml")
    if start < 0:
        raise ValueError("Not an FCPXML document.")
    root = ET.fromstring(text[start:])
    sequence = root.find(".//sequence")
    spine = sequence.find("spine") if sequence is not None else None
    if spine is None:
        raise ValueError("No sequence spine found.")
    clips = [element for element in spine if element.tag in STORY_TAGS]
    if len(clips) < 2:
        raise ValueError("The spine has %d clip(s); two are needed." % len(clips))
    first, second = clips[:2]
    form = root.find("./resources/format[@id='%s']" % sequence.get("format")) if sequence.get("format") else None
    if form is not None and form.get("frameDuration"):
        frame = _time(form.get("frameDuration"))
    else:
        frame = Fraction(1001, int(round(fps * 1000))) if abs(fps - round(fps)) > 0.001 else Fraction(1, int(round(fps)))
    changes = []
    if dissolve_frames:
        cut = _time(second.get("offset"))
        if _time(first.get("offset")) + _time(first.get("duration")) != cut:
            raise ValueError("Clip 1 does not end where clip 2 starts; a dissolve needs touching clips.")
        length = frame * dissolve_frames
        effect = _next_resource_id(root)
        ET.SubElement(root.find("resources"), "effect", {"id": effect, "name": "Cross Dissolve", "uid": CROSS_DISSOLVE_UID})
        transition = ET.Element("transition", {"name": "Cross Dissolve", "offset": _fmt(cut - length / 2),
                                               "duration": _fmt(length)})
        ET.SubElement(transition, "filter-video", {"ref": effect, "name": "Cross Dissolve"})
        spine.insert(list(spine).index(second), transition)
        changes.append("cross dissolve of %d frames centred on the cut at %s" % (dissolve_frames, _fmt(cut)))
    if fade_frames:
        targets = [second] + [child for child in second if child.tag in STORY_TAGS and int(child.get("lane", "0")) < 0]
        for target in targets:
            _add_fades(target, frame * fade_frames)
        changes.append("audio fade in/out of %d frames on clip 2 (%d element(s))" % (fade_frames, len(targets)))
    if move_frames:
        delta = frame * move_frames
        index = list(spine).index(second)
        spine.insert(index, ET.Element("gap", {"name": "Gap", "offset": _fmt(_time(second.get("offset"))),
                                               "duration": _fmt(delta)}))
        for element in list(spine)[index + 1:]:
            if element.get("offset") is not None:
                element.set("offset", _fmt(_time(element.get("offset")) + delta))
        if sequence.get("duration"):
            sequence.set("duration", _fmt(_time(sequence.get("duration")) + delta))
        changes.append("clip 2 moved later by %d frames (gap inserted)" % move_frames)
    return text[:start] + ET.tostring(root, encoding="unicode"), changes


def sniff_export(path):
    path = Path(path)
    if path.is_dir() and (path / "Info.fcpxml").is_file():  # FCPXML 1.10 exports a bundle folder.
        path = path / "Info.fcpxml"
    if not path.is_file():
        return {"file": None}
    head = path.read_bytes()[:4096].decode("utf-8", "replace")
    match = re.search(r'<fcpxml\b[^>]*\bversion="([\d.]+)"', head)
    if match:
        return {"file": str(path), "format": "fcpxml", "version": match.group(1)}
    return {"file": str(path), "format": "other", "starts_with": head[:80]}


# ------------------------------------------------------------------ calls and chains

class Chain:
    """resolve_api steps built with readable labels; add() returns the "$N" reference of the new step."""

    def __init__(self, guard=None):
        self.steps, self.labels, self.guard, self.cache = [], [], guard, {}
        if guard is not None:
            self.add("current_timeline", "GetName", label="guard")

    def add(self, on, method, *args, label=None):
        self.labels.append(label or "_%d" % len(self.steps))
        self.steps.append({"on": on, "call": method, "args": list(args)})
        return "$%d" % (len(self.steps) - 1)

    def reads(self, ref, methods, prefix="", skip=()):
        for method in methods:
            if method not in skip:
                self.add(ref, method, label=prefix + method)


class Reply:
    def __init__(self, ok, values=None, error=None):
        self.ok, self.values, self.error = ok, dict(values or {}), error
        match = re.search(r"has no method (\w+)", error or "")
        self.absent = match.group(1) if match else None

    def get(self, key, default=None):
        return self.values.get(key, default)


def _uncertain(exc):
    if type(exc).__name__ in ("BridgeTimeout", "BridgeOffline"):
        return True
    if isinstance(exc, OperationError):
        return False
    return any(marker in str(exc).lower() for marker in UNCERTAIN)


def _fill(value, item):
    if value == ITEM:
        return item
    if isinstance(value, list):
        return [_fill(entry, item) for entry in value]
    return value


def item_fields(values, prefix=""):
    return {field: values[prefix + method] for method, field in FIELDS.items() if prefix + method in values}


def clip_keys(properties):
    return {key: properties.get(key) for key in CLIP_KEYS if key in properties} if isinstance(properties, dict) else properties


def changes_between(before, after):
    """Which items moved or changed between two read_tracks snapshots, matched by unique id."""
    old = {}
    for track, entries in before.items():
        for entry in entries:
            old[entry.get("id") or (track, entry["index"])] = (track, entry)
    found = []
    for track, entries in after.items():
        for entry in entries:
            previous = old.get(entry.get("id") or (track, entry["index"]))
            label = "%s[%d]" % (track, entry["index"])
            if previous is None:
                found.append({"item": label, "new": True})
                continue
            delta = {field: [previous[1].get(field), entry.get(field)] for field in FIELDS.values()
                     if previous[1].get(field) != entry.get(field)}
            if previous[0] != track:
                delta["track"] = [previous[0], track]
            if delta:
                found.append({"item": label, "id": entry.get("id"), "changed": delta})
    return found


class ProbeRun:
    def __init__(self, call, timeout=TIMEOUT, video=None, image=None, work_dir=None, export_type=None,
                 sniff_export_types=False):
        self._call, self.timeout = call, timeout
        self.video, self.image, self.work_dir = video, image, Path(work_dir) if work_dir else None
        self.export_type, self.sniff_export_types = export_type, sniff_export_types
        self.calls, self.questions, self.created, self.restored = [], [], [], {}
        self.halted, self.touched, self.timeline, self.fps = None, False, None, None
        self.listings, self.one_based, self.media, self.unavailable = {}, False, {}, set()

    @property
    def second(self):
        return int(round(self.fps))

    def op(self, operation, params=None, label=None):
        if self.halted:
            raise Halt(self.halted)
        params = dict(params or {})
        if "allow_destructive" in params:
            raise ValueError("Probes never send allow_destructive.")
        if operation == "create_timeline" or (operation == "resolve_api" and any(
                step["call"] in ("ImportTimelineFromFile", "AppendToTimeline") for step in params["steps"])):
            self.touched = True
        entry = {"n": len(self.calls) + 1, "op": operation, "params": params}
        if label:
            entry["label"] = label
        self.calls.append(entry)
        try:
            result = self._call(operation, params, self.timeout)
        except Exception as exc:
            entry["error"] = "%s: %s" % (type(exc).__name__, exc)
            if _uncertain(exc):
                self.halted = "Uncertain outcome at call %d (%s): %s" % (entry["n"], operation, entry["error"])
                raise Halt(self.halted)
            return False, entry["error"]
        entry["result"] = result
        return True, result

    def api(self, chain, label):
        if len(chain.steps) > RESOLVE_API_MAX_STEPS:
            raise ValueError("%s: %d steps exceed the resolve_api limit." % (label, len(chain.steps)))
        refused = [step["call"] for step in chain.steps if step["call"] in RESOLVE_API_DESTRUCTIVE]
        if refused:
            raise ValueError("Probes never call %s." % ", ".join(refused))
        ok, result = self.op("resolve_api", {"steps": chain.steps, "full_lists": True}, label)
        if ok:
            steps = result.get("steps") if isinstance(result, dict) else None
            if not isinstance(steps, list) or len(steps) != len(chain.steps):
                reply = Reply(False, error="Unexpected resolve_api reply: %r" % (result,))
            else:
                reply = Reply(True, {name: step.get("result") for name, step in zip(chain.labels, steps)})
        else:
            partial = {}
            match = re.search(r"not undone: (\[.*\])\s*$", result, re.DOTALL)
            try:
                for step in json.loads(match.group(1)) if match else []:
                    partial[chain.labels[step["step"]]] = step.get("result")
            except (ValueError, KeyError, IndexError, TypeError):
                pass  # Truncated record: keep the raw error only.
            reply = Reply(False, partial, result)
        if chain.guard is not None and "guard" in reply.values and reply.get("guard") != chain.guard:
            self.halted = ("Guard: the open timeline was %r, not the probe timeline %r, and the chain %r ran on it. "
                           "Inspect Resolve." % (reply.get("guard"), chain.guard, label))
            raise Halt(self.halted)
        for name, value in reply.values.items():
            if name.startswith("list:"):
                _, kind, index = name.split(":")
                self.listings[(kind, int(index))] = as_list(value)
        return reply

    def ask(self, question, verdict, evidence=None, look=None):
        entry = {"question": question, "verdict": verdict,
                 "evidence": {key: value for key, value in (evidence or {}).items() if value is not None}}
        if look:
            entry["look_in_resolve"] = look
        self.questions.append(entry)
        return entry

    @staticmethod
    def verdict(reply, method, check):
        if reply.absent == method:
            return "unavailable"
        if "returned" not in reply.values:
            return "error"
        if reply.get("returned") in (False, None):
            return "rejected"
        try:
            good = bool(check(reply.values))
        except Exception:
            good = False
        return "works" if good else "returns True but readback differs"

    # -- references

    def at(self, ref, index):
        return "%s[%d]" % (ref, index + 1 if self.one_based else index)

    def media_ref(self, chain, kind):
        if kind not in chain.cache:
            root = chain.add("media_pool", "GetRootFolder")
            folders = chain.add(root, "GetSubFolderList")
            clips = chain.add(self.at(folders, self.media[kind]["folder_index"]), "GetClipList")
            chain.cache[kind] = self.at(clips, self.media[kind]["clip_index"])
        return chain.cache[kind]

    def item(self, chain, kind, track, index):
        return self.at(chain.add("current_timeline", "GetItemListInTrack", kind, track), index)

    @staticmethod
    def list_tracks(chain, tracks):
        for kind, index in tracks:
            chain.add("current_timeline", "GetItemListInTrack", kind, index, label="list:%s:%d" % (kind, index))

    def locate(self, item_id, default):
        for (kind, track), listing in self.listings.items():
            for index, summary in enumerate(listing):
                if item_id and isinstance(summary, dict) and summary.get("id") == item_id:
                    return kind, track, index
        return default

    def append(self, chain, kind, infos, label="appended"):
        media = self.media_ref(chain, kind)
        return chain.add("media_pool", "AppendToTimeline", [dict(info, mediaPoolItem=media) for info in infos], label=label)

    # -- setup

    def new_timeline(self, probe):
        name = "AI probe · %s — %s" % (probe, uuid.uuid4().hex[:8])
        ok, result = self.op("create_timeline", {"name": name}, "create the probe timeline")
        if ok and isinstance(result, dict) and result.get("created"):
            self.created.append(result["created"])
        if not ok or not isinstance(result, dict) or result.get("created") != name or not result.get("active"):
            raise ProbeStop("Could not create and open the probe timeline %r: %s" % (name, result))
        self.timeline, self.listings = name, {}
        return name

    def prepare_media(self, kinds):
        paths = {}
        if "image" in kinds:
            paths["image"] = Path(self.image) if self.image else write_png(self.work_dir / "ai-probe-still.png")
        if "video" in kinds:
            if not self.video:
                raise ProbeStop("This probe needs --video: a short clip (at least 4 s) with audio.")
            paths["video"] = Path(self.video)
        bin_name = "AI probe media — %s" % uuid.uuid4().hex[:8]
        chain = Chain()
        before = chain.add("media_pool", "GetCurrentFolder", label="folder_before")
        root = chain.add("media_pool", "GetRootFolder")
        new_bin = chain.add("media_pool", "AddSubFolder", root, bin_name, label="bin")
        chain.add("media_pool", "ImportMedia", [str(path) for path in paths.values()], label="imported")
        chain.add("media_pool", "SetCurrentFolder", before, label="folder_restore_returned")
        chain.add("media_pool", "GetCurrentFolder", label="folder_after")
        chain.add(root, "GetSubFolderList", label="folders")
        chain.add(new_bin, "GetClipList", label="clips")
        reply = self.api(chain, "make the probe bin, import media, restore the current folder")
        self.restored["media_pool_folder"] = {
            "before": reply.get("folder_before"), "after": reply.get("folder_after"),
            "SetCurrentFolder returned": reply.get("folder_restore_returned"),
            "restored": reply.get("folder_after") == reply.get("folder_before") and "folder_after" in reply.values}
        if not reply.ok:
            raise ProbeStop("Media import failed: %s" % reply.error)
        self.one_based = isinstance(reply.get("folders"), dict) or isinstance(reply.get("clips"), dict)
        folders = [entry.get("name") if isinstance(entry, dict) else None for entry in as_list(reply.get("folders"))]
        clips = [entry.get("name") if isinstance(entry, dict) else None for entry in as_list(reply.get("clips"))]
        if bin_name not in folders:
            raise ProbeStop("The probe bin %r is not under the root folder: %s" % (bin_name, folders))
        for kind, path in paths.items():
            if path.name not in clips:
                raise ProbeStop("Resolve did not import %s (bin holds %s). The App Store build can only read files "
                                "inside its container or ones already granted; copy it into --work-dir." % (path, clips))
            self.media[kind] = {"path": str(path), "folder_index": folders.index(bin_name),
                                "clip_index": clips.index(path.name)}
        chain = Chain()
        for kind in paths:
            chain.add(self.media_ref(chain, kind), "GetClipProperty", label=kind)
        reply = self.api(chain, "read the probe media's clip properties")
        for kind in paths:
            properties = reply.get(kind) if isinstance(reply.get(kind), dict) else {}
            try:
                frames = int(str(properties.get("Frames", "")).strip())
            except ValueError:
                frames = None
            self.media[kind].update(frames=frames, clip_properties=clip_keys(properties))
        return self.media

    def unit(self):
        """One second in frames, shortened when the video is under 4 seconds."""
        frames = self.media.get("video", {}).get("frames")
        unit = self.second if frames is None else min(self.second, frames // 4)
        if unit < 8:
            raise ProbeStop("The video is too short (%s frames); use one of at least 4 seconds." % frames)
        return unit - unit % 2

    # -- reads and attempts

    def read_tracks(self, tracks, label, methods=ITEM_READS + SOURCE_READS):
        """Read every item on the given tracks of the current timeline, skipping methods this build lacks."""
        skip = set(self.unavailable)
        for _ in range(len(methods) + 1):
            chain = Chain(self.timeline)
            for kind, track in tracks:
                listing = chain.add("current_timeline", "GetItemListInTrack", kind, track, label="list:%s:%d" % (kind, track))
                for index in range(len(self.listings.get((kind, track), []))):
                    chain.reads(self.at(listing, index), methods, "%s%d.%d." % (kind[0].upper(), track, index), skip)
            reply = self.api(chain, label)
            if reply.ok or not reply.absent or reply.absent in skip:
                break
            skip.add(reply.absent)
            self.unavailable.add(reply.absent)
        snapshot = {}
        for kind, track in tracks:
            key = "%s%d" % (kind[0].upper(), track)
            snapshot[key] = []
            for index, summary in enumerate(as_list(reply.get("list:%s:%d" % (kind, track)))):
                entry = {"index": index, "id": summary.get("id") if isinstance(summary, dict) else None}
                entry.update(item_fields(reply.values, "%s.%d." % (key, index)))
                snapshot[key].append(entry)
        if not reply.ok:
            snapshot["error"] = reply.error
        return snapshot

    def try_call(self, question, where, method, args, check, tracks, on_timeline=False, look=None):
        kind, track, index = where
        chain = Chain(self.timeline)
        item = self.item(chain, kind, track, index)
        chain.add("current_timeline" if on_timeline else item, method, *_fill(list(args), item), label="returned")
        chain.reads(item, ITEM_READS)
        self.list_tracks(chain, tracks)
        reply = self.api(chain, question)
        verdict = self.verdict(reply, method, check)
        self.ask(question, verdict, {"call": "%s.%s" % ("Timeline" if on_timeline else "TimelineItem", method),
                                     "args": list(args), "returned": reply.get("returned"),
                                     "readback": item_fields(reply.values), "error": reply.error}, look)
        return reply, verdict


# ------------------------------------------------------------------ probes

PROBES = {}


def probe(name, needs, purpose):
    def register(function):
        PROBES[name] = {"name": name, "needs": needs, "purpose": purpose, "run": function}
        return function
    return register


@probe("still_duration", ("image",), "Find an API route that gives a still image a chosen length (add_image got 150 frames).")
def still_duration(run):
    run.prepare_media(("image",))
    n, fps = 2 * run.second, run.fps
    run.new_timeline("still_duration")
    chain = Chain()
    chain.add("project", "GetSetting", label="settings")
    reply = run.api(chain, "read project settings")
    settings = reply.get("settings")
    if isinstance(settings, dict):
        run.ask("Project settings whose name mentions still or duration", "works",
                {"settings": {key: value for key, value in settings.items()
                              if "still" in key.lower() or "duration" in key.lower()}})
    else:
        run.ask("Project settings whose name mentions still or duration", "error",
                {"returned": settings, "error": reply.error})

    def place(question, extra, expected):
        chain = Chain(run.timeline)
        appended = run.append(chain, "image", [dict(extra, trackIndex=1, mediaType=1)])
        chain.reads("%s[0]" % appended, ITEM_READS)
        chain.add(run.media_ref(chain, "image"), "GetClipProperty", label="clip_properties")
        run.list_tracks(chain, [("video", 1)])
        reply = run.api(chain, question)
        duration = reply.get("GetDuration")
        if "GetDuration" not in reply.values:
            verdict = "rejected" if re.search("out of range|not a Resolve object", reply.error or "") else "error"
        else:
            verdict = "works" if expected is None or duration in expected else "returns True but readback differs"
        run.ask(question, verdict, {"clip_info": extra, "requested_frames": n if expected else None,
                                    "readback": item_fields(reply.values), "error": reply.error,
                                    "media_pool_item": clip_keys(reply.get("clip_properties"))})
        return reply

    place("a) AppendToTimeline startFrame 0, endFrame %d (N-1, N=%d)" % (n - 1, n), {"startFrame": 0, "endFrame": n - 1}, (n - 1, n))
    place("a) AppendToTimeline startFrame 0, endFrame %d (N)" % n, {"startFrame": 0, "endFrame": n}, (n, n + 1))
    default = place("d) Default still length: AppendToTimeline without startFrame/endFrame", {}, None)
    last = len(run.listings.get(("video", 1), [])) - 1
    duration, right = default.get("GetDuration"), default.get("GetRightOffset")
    if isinstance(duration, int) and duration > n and last >= 0:
        target = (right or 0) + duration - n
        run.try_call("d) TimelineItem.SetRightOffset(%d) to shorten the default still to %d frames" % (target, n),
                     ("video", 1, last), "SetRightOffset", [target],
                     lambda v: v.get("GetDuration") == n or v.get("GetRightOffset") == target, [("video", 1)])
        chain = Chain(run.timeline)
        chain.add(run.item(chain, "video", 1, last), "GetProperty", label="properties")
        reply = run.api(chain, "d) TimelineItem.GetProperty() keys")
        properties = reply.get("properties") if isinstance(reply.get("properties"), dict) else {}
        keys = [key for key in properties if re.search("dur|length|end|out", key, re.IGNORECASE)]
        if not keys:
            run.ask("d) TimelineItem.SetProperty with a duration-like key", "unavailable",
                    {"property_keys": sorted(properties), "error": reply.error})
        for key in keys[:4]:
            run.try_call("d) TimelineItem.SetProperty(%r, %d)" % (key, n), ("video", 1, last), "SetProperty", [key, n],
                         lambda v: v.get("GetDuration") == n, [("video", 1)])
    else:
        run.ask("d) Shorten the default placement", "error", {"note": "No default placement longer than N to shorten.",
                                                                "duration": duration})

    chain = Chain()
    media = run.media_ref(chain, "image")
    chain.add(media, "SetMarkInOut", 0, n - 1, "all", label="returned")
    chain.add(media, "GetClipProperty", label="clip_properties")
    chain.add(media, "GetMarkInOut", label="mark_in_out")
    reply = run.api(chain, "c) MediaPoolItem.SetMarkInOut(0, %d, 'all')" % (n - 1))
    run.ask("c) MediaPoolItem.SetMarkInOut(0, %d, 'all') on the still" % (n - 1),
            run.verdict(reply, "SetMarkInOut", lambda v: bool(v.get("mark_in_out"))),
            {"returned": reply.get("returned"), "mark_in_out": reply.get("mark_in_out"), "error": reply.error,
             "media_pool_item": clip_keys(reply.get("clip_properties"))})
    place("c) Placement after SetMarkInOut, without startFrame/endFrame", {}, (n - 1, n))
    chain = Chain()
    media = run.media_ref(chain, "image")
    chain.add(media, "ClearMarkInOut", "all", label="returned")
    chain.add(media, "GetMarkInOut", label="mark_in_out")
    reply = run.api(chain, "c) MediaPoolItem.ClearMarkInOut('all')")
    run.ask("c) MediaPoolItem.ClearMarkInOut('all') afterwards", run.verdict(reply, "ClearMarkInOut", lambda v: not v.get("mark_in_out")),
            {"returned": reply.get("returned"), "mark_in_out": reply.get("mark_in_out"), "error": reply.error})

    for key, value in (("Duration", timecode(n, fps)), ("Frames", str(n)), ("End", str(n - 1)), ("Out", timecode(n - 1, fps))):
        chain = Chain()
        media = run.media_ref(chain, "image")
        chain.add(media, "SetClipProperty", key, value, label="returned")
        chain.add(media, "GetClipProperty", label="clip_properties")
        reply = run.api(chain, "b) MediaPoolItem.SetClipProperty(%r, %r)" % (key, value))
        run.ask("b) MediaPoolItem.SetClipProperty(%r, %r) on the still" % (key, value),
                run.verdict(reply, "SetClipProperty", lambda v, k=key, w=value: str((v.get("clip_properties") or {}).get(k)) == w),
                {"returned": reply.get("returned"), "error": reply.error,
                 "media_pool_item": clip_keys(reply.get("clip_properties"))})
    place("b) Placement after the SetClipProperty attempts, without startFrame/endFrame", {}, (n,))


def _three_clips(run, probe_name):
    """A probe timeline with clips A, B, C on V1+A1; B has handles on both sides."""
    run.prepare_media(("video",))
    unit = run.unit()
    run.new_timeline(probe_name)
    chain = Chain(run.timeline)
    run.append(chain, "video", [{"startFrame": 0, "endFrame": 2 * unit - 1, "trackIndex": 1},
                                {"startFrame": unit, "endFrame": 3 * unit - 1, "trackIndex": 1},
                                {"startFrame": 0, "endFrame": unit - 1, "trackIndex": 1}])
    run.list_tracks(chain, [("video", 1), ("audio", 1)])
    reply = run.api(chain, "place clips A, B (with handles) and C")
    if len(run.listings.get(("video", 1), [])) < 3:
        raise ProbeStop("Resolve did not place the three clips: %s" % (reply.error or run.listings))
    return unit


@probe("item_offsets", ("video",), "Do TimelineItem.SetLeftOffset/SetRightOffset exist, what do they change, do neighbours move?")
def item_offsets(run):
    unit = _three_clips(run, "item_offsets")
    tracks = [("video", 1), ("audio", 1)]
    before = run.read_tracks(tracks, "baseline")
    step = max(1, unit // 4)
    for method, getter in (("SetLeftOffset", "GetLeftOffset"), ("SetRightOffset", "GetRightOffset")):
        current = (before.get("V1") or [{}, {}])[1].get(FIELDS[getter]) or 0
        target = current + step
        reply, verdict = run.try_call(
            "TimelineItem.%s(%d) on clip B (was %d)" % (method, target, current), ("video", 1, 1), method, [target],
            lambda v, g=getter, t=target: v.get(g) == t, tracks,
            look="Clip B is the middle clip on V1/A1 of %r; compare its edges with clips A and C." % run.timeline)
        if verdict == "unavailable":
            continue
        after = run.read_tracks(tracks, "after %s" % method)
        entry = run.questions[-1]["evidence"]
        entry.update(after=after, changes=changes_between(before, after),
                     hint="If the offset grew by %d plus the old value, the argument is a delta, not an absolute value." % target)
        before = after


def _two_clips_two_tracks(run, probe_name):
    run.prepare_media(("video",))
    unit = run.unit()
    run.new_timeline(probe_name)
    chain = Chain(run.timeline)
    chain.add("current_timeline", "AddTrack", "video", label="add_track")
    run.append(chain, "video", [{"startFrame": 0, "endFrame": unit - 1, "trackIndex": 1, "mediaType": 1},
                                {"startFrame": unit, "endFrame": 2 * unit - 1, "trackIndex": 1, "mediaType": 1}])
    tracks = [("video", 1), ("video", 2)]
    run.list_tracks(chain, tracks)
    reply = run.api(chain, "add V2 and place clips A and B on V1 (video only)")
    if len(run.listings.get(("video", 1), [])) < 2:
        raise ProbeStop("Resolve did not place the two clips: %s" % (reply.error or run.listings))
    before = run.read_tracks(tracks, "baseline", ITEM_READS)
    return unit, tracks, before


def _moved_to(run, item_id, frame, track):
    def check(values):
        return values.get("GetStart") == frame and run.locate(item_id, ("video", 1, 1))[:2] == ("video", track)
    return check


@probe("set_position", ("video",), "Does TimelineItem.SetPosition exist, and with which signature (stops at first success)?")
def set_position(run):
    unit, tracks, before = _two_clips_two_tracks(run, "set_position")
    clip = before["V1"][1]
    target = (clip.get("start") or 0) + 2 * unit
    for signature, args, track in (("frame", [target], 1), ("frame, track type, track index", [target, "video", 2], 2),
                                   ("frame, track index", [target, 2], 2)):
        where = run.locate(clip.get("id"), ("video", 1, 1))
        _, verdict = run.try_call("TimelineItem.SetPosition(%s) -> start %d on V%d" % (signature, target, track), where,
                                  "SetPosition", args, _moved_to(run, clip.get("id"), target, track), tracks)
        after = run.read_tracks(tracks, "after SetPosition(%s)" % signature, ITEM_READS)
        run.questions[-1]["evidence"].update(after=after, changes=changes_between(before, after))
        before = after
        if verdict in ("works", "unavailable"):
            break


@probe("move_items", ("video",), "Does Timeline.MoveItems exist, and does it take a frame delta and/or a track delta?")
def move_items(run):
    unit, tracks, before = _two_clips_two_tracks(run, "move_items")
    clip = before["V1"][1]
    start = clip.get("start") or 0
    for signature, args, frame, track in (("items, frame delta", [[ITEM], unit], start + unit, 1),
                                          ("items, frame delta, track delta", [[ITEM], 0, 1], start + unit, 2)):
        where = run.locate(clip.get("id"), ("video", 1, 1))
        _, verdict = run.try_call("Timeline.MoveItems(%s) -> start %d on V%d" % (signature, frame, track), where,
                                  "MoveItems", args, _moved_to(run, clip.get("id"), frame, track), tracks, on_timeline=True)
        after = run.read_tracks(tracks, "after MoveItems(%s)" % signature, ITEM_READS)
        run.questions[-1]["evidence"].update(after=after, changes=changes_between(before, after))
        before = after
        if verdict == "unavailable":
            break


FUSION_CONSTANTS_LUA = """
local candidates = {
  function() return resolve end,
  function() return Resolve and Resolve() end,
  function() return bmd and bmd.scriptapp and bmd.scriptapp("Resolve") end,
}
local r, how = nil, nil
for i, get in ipairs(candidates) do
  local ok, value = pcall(get)
  if ok and value then r, how = value, i break end
end
if not r then return {error = "no Resolve object is reachable from the comp's Lua"} end
local out = {accessor = how}
for _, name in ipairs({%s}) do
  local ok, value = pcall(function() return r[name] end)
  if ok and type(value) == "number" then out[name] = value end
end
return out
""" % ", ".join('"%s"' % name for name in EXPORT_CONSTANTS)


def _export_type(run):
    """Find the Timeline.Export type number for FCPXML. Returns (number or None, how)."""
    chain = Chain()
    chain.add("resolve", EXPORT_CONSTANTS[0], label="constant")
    reply = run.api(chain, "read resolve.%s through resolve_api" % EXPORT_CONSTANTS[0])
    value = reply.get("constant")
    run.ask("Can resolve_api read resolve.%s?" % EXPORT_CONSTANTS[0],
            "works" if reply.ok else "unavailable" if reply.absent else "error",
            {"returned": value, "error": reply.error,
             "note": "resolve_api only calls methods. An export constant is a plain attribute, so it reports 'has no "
                     "method'. A dedicated operation in operations.py could read it with getattr; the Lua proxy "
                     "forwards attribute reads through get_attribute."})
    if reply.ok and isinstance(value, int) and not isinstance(value, bool):
        return value, "resolve_api"
    if run.export_type is not None:
        return run.export_type, "--export-type"
    # A one-clip helper timeline keeps the Fusion comp off the round-trip timeline.
    run.new_timeline("fcpxml_constants")
    chain = Chain(run.timeline)
    run.append(chain, "video", [{"startFrame": 0, "endFrame": run.second - 1, "trackIndex": 1, "mediaType": 1}])
    run.list_tracks(chain, [("video", 1)])
    run.api(chain, "place one clip for the Fusion constant read")
    listing = run.listings.get(("video", 1)) or [{}]
    item_id = (listing[0] or {}).get("id") or "V1.1"
    ok, result = run.op("fusion_run_lua", {"item_id": item_id, "code": FUSION_CONSTANTS_LUA},
                        "read the export constants from the clip's Fusion Lua")
    constants = result.get("result") if ok and isinstance(result, dict) else None
    found = [name for name in EXPORT_CONSTANTS if isinstance(constants, dict) and isinstance(constants.get(name), int)]
    run.ask("Can fusion_run_lua read the FCPXML export constants?", "works" if found else "error",
            {"returned": constants if ok else None, "error": None if ok else result,
             "note": "accessor 1 = resolve, 2 = Resolve(), 3 = bmd.scriptapp('Resolve')"})
    if found:
        return constants[found[0]], "fusion_run_lua %s" % found[0]
    return None, None


def _sniff_export_types(run, folder):
    """Opt-in: export with types 0-24 and read each file's header. Each attempt writes one file into folder."""
    results, pending = {}, list(range(25))
    while pending:
        chain = Chain(run.timeline)
        for number in pending:
            chain.add("current_timeline", "Export", str(folder / ("export_type_%02d.fcpxml" % number)), number,
                      label="type:%d" % number)
        reply = run.api(chain, "Timeline.Export for types %d-%d" % (pending[0], pending[-1]))
        for number in pending:
            if "type:%d" % number in reply.values:
                results[number] = {"returned": reply.get("type:%d" % number)}
        if reply.ok:
            break
        match = re.search(r"failed_step (\d+)", reply.error or "")
        if not match or int(match.group(1)) < 1:
            break
        failed = pending[int(match.group(1)) - 1]
        results[failed] = {"error": reply.error[:300]}
        pending = pending[pending.index(failed) + 1:]
    for number in results:
        results[number].update(sniff_export(folder / ("export_type_%02d.fcpxml" % number)))
    found = {number: entry["version"] for number, entry in results.items() if entry.get("format") == "fcpxml"}
    run.ask("Which Timeline.Export type numbers write FCPXML (file sniffing)?", "works" if found else "error",
            {"types": results})
    if not found:
        return None
    exact = [number for number, version in found.items() if version == "1.10"]
    return exact[0] if exact else max(found, key=lambda number: tuple(int(p) for p in found[number].split(".")))


def _relative(snapshot, start):
    return {track: [(entry.get("start") - start if isinstance(entry.get("start"), int) and isinstance(start, int) else None,
                     entry.get("duration")) for entry in entries]
            for track, entries in snapshot.items() if isinstance(entries, list)}


@probe("fcpxml_roundtrip", ("video",),
       "Export a 2-clip timeline to FCPXML, add a dissolve, audio fades and a 1 s move in Python, re-import, compare.")
def fcpxml_roundtrip(run):
    run.prepare_media(("video",))
    unit = run.unit()
    folder = run.work_dir / "fcpxml"
    folder.mkdir(parents=True, exist_ok=True)
    export_type, how = _export_type(run)
    if export_type is None and not run.sniff_export_types:
        raise ProbeStop("No FCPXML export type found. In Workspace > Console (Lua) run print(resolve.EXPORT_FCPXML_1_10) "
                        "and pass --export-type N, or pass --sniff-export-types to try types 0-24.")
    run.new_timeline("fcpxml_roundtrip")
    chain = Chain(run.timeline)
    run.append(chain, "video", [{"startFrame": 0, "endFrame": 2 * unit - 1, "trackIndex": 1},
                                {"startFrame": unit, "endFrame": 3 * unit - 1, "trackIndex": 1}])
    chain.add("current_timeline", "GetStartFrame", label="timeline_start")
    tracks = [("video", 1), ("audio", 1)]
    run.list_tracks(chain, tracks)
    reply = run.api(chain, "place clips 1 and 2 (video+audio, with handles)")
    base_start = reply.get("timeline_start")
    if len(run.listings.get(("video", 1), [])) < 2:
        raise ProbeStop("Resolve did not place the two clips: %s" % (reply.error or run.listings))
    base = run.read_tracks(tracks, "baseline", ITEM_READS)
    if export_type is None:
        export_type, how = _sniff_export_types(run, folder), "file sniffing"
        if export_type is None:
            raise ProbeStop("No export type wrote an FCPXML file into %s. Resolve may not be allowed to write there; "
                            "pass a --work-dir inside its container." % folder)
    path = folder / "exported.fcpxml"
    chain = Chain(run.timeline)
    chain.add("current_timeline", "Export", str(path), export_type, label="returned")
    reply = run.api(chain, "Timeline.Export(%s, %d)" % (path, export_type))
    sniffed = sniff_export(path)
    run.ask("Timeline.Export with type %d (%s) writes FCPXML" % (export_type, how),
            "works" if sniffed.get("format") == "fcpxml" else "rejected" if reply.get("returned") is False else "error",
            {"returned": reply.get("returned"), "file": sniffed, "error": reply.error})
    if sniffed.get("format") != "fcpxml":
        raise ProbeStop("The export did not produce FCPXML at %s." % path)
    path = Path(sniffed["file"])
    text = path.read_text(encoding="utf-8")
    half, fade = unit // 2, unit // 2
    cut_rel = (base["V1"][1].get("start") or 0) - (base_start or 0)
    for variant, edits in (("dissolve_fades", {"dissolve_frames": unit, "fade_frames": fade}),
                           ("moved_fades", {"move_frames": unit, "fade_frames": fade})):
        try:
            edited, changes = edit_fcpxml(text, run.fps, **edits)
        except (ValueError, ET.ParseError) as exc:
            run.ask("Edit the exported FCPXML (%s)" % variant, "error", {"error": str(exc)})
            continue
        edited_path = folder / ("edited_%s.fcpxml" % variant)
        edited_path.write_text(edited, encoding="utf-8")
        name = "AI probe · fcpxml_roundtrip %s — %s" % (variant, uuid.uuid4().hex[:8])
        chain = Chain()
        imported = chain.add("media_pool", "ImportTimelineFromFile", str(edited_path),
                             {"timelineName": name, "importSourceClips": False}, label="imported")
        chain.add(imported, "GetName", label="name")
        chain.add(imported, "GetStartFrame", label="timeline_start")
        for kind in ("video", "audio"):
            chain.add(imported, "GetTrackCount", kind, label="tracks:" + kind)
            chain.add(imported, "GetItemListInTrack", kind, 1, label="items:" + kind)
        reply = run.api(chain, "MediaPool.ImportTimelineFromFile(%s)" % variant)
        if isinstance(reply.get("imported"), dict):
            run.created.append(reply.get("name") or name)
        if not reply.ok:
            run.ask("Import the edited FCPXML (%s)" % variant, "rejected" if "imported" in reply.values else "error",
                    {"file": str(edited_path), "returned": reply.get("imported"), "error": reply.error})
            continue
        ok, listing = run.op("list_timelines", None, "find the imported timeline's index")
        timelines = listing.get("timelines", []) if ok and isinstance(listing, dict) else []
        index = next((entry.get("index") for entry in timelines if entry.get("name") == reply.get("name")), None)
        snapshot = {}
        if index is not None:
            chain = Chain()
            timeline = chain.add("project", "GetTimelineByIndex", index)
            for kind in ("video", "audio"):
                items = chain.add(timeline, "GetItemListInTrack", kind, 1)
                for position in range(len(as_list(reply.get("items:" + kind)))):
                    chain.reads(run.at(items, position), ITEM_READS + ("GetMediaPoolItem",),
                                "%s1.%d." % (kind[0].upper(), position))
            read = run.api(chain, "read the imported timeline's items (%s)" % variant)
            for kind in ("video", "audio"):
                key = kind[0].upper() + "1"
                snapshot[key] = [dict({"index": position}, **item_fields(read.values, "%s.%d." % (key, position)))
                                 for position in range(len(as_list(reply.get("items:" + kind))))]
        start = reply.get("timeline_start")
        expected = _relative(base, base_start)
        if variant == "moved_fades":
            expected = {track: [(rel + unit if i == 1 and rel is not None else rel, dur) for i, (rel, dur) in enumerate(entries)]
                        for track, entries in expected.items()}
        got = _relative(snapshot, start)
        run.ask("Imported %s: clip positions, durations and tracks match the edit" % variant,
                "works" if got == expected else "returns True but readback differs",
                {"changes": changes, "edited_file": str(edited_path), "imported_timeline": reply.get("name"),
                 "track_counts": {kind: reply.get("tracks:" + kind) for kind in ("video", "audio")},
                 "expected_relative_start_and_duration": expected, "imported_relative_start_and_duration": got,
                 "imported_items": snapshot})
        if start is None:
            continue
        cut = start + cut_rel + (unit if variant == "moved_fades" else 0)
        clip2_end = cut + (base["V1"][1].get("duration") or 0)
        fades = ("A1 clip 2 shows a fade-in over %s-%s and a fade-out over %s-%s (fade handles at the clip corners, "
                 "Inspector > Audio)." % (timecode(cut, run.fps), timecode(cut + fade, run.fps),
                                          timecode(clip2_end - fade, run.fps), timecode(clip2_end, run.fps)))
        if variant == "dissolve_fades":
            look = ("Open %r. V1 has a Cross Dissolve centred on the cut at %s (frames %d-%d, %s-%s). %s To see the "
                    "dissolve without scrubbing, run render_frames on that timeline at frames %d, %d and %d."
                    % (reply.get("name"), timecode(cut, run.fps), cut - half, cut + half, timecode(cut - half, run.fps),
                       timecode(cut + half, run.fps), fades, cut - half, cut, cut + half))
        else:
            look = ("Open %r. Clip 2 starts at %s, leaving a 1 s gap from %s. %s"
                    % (reply.get("name"), timecode(cut, run.fps), timecode(cut - unit, run.fps), fades))
        run.ask("Imported %s: transition and audio fades survived" % variant, "check visually",
                {"note": "The scripting API cannot read transitions or audio fades."}, look)
    run.ask("FCPXML files", "works", {"exported": str(path), "folder": str(folder)})


@probe("audio_volume", ("video",), "Does TimelineItem.SetProperty('AudioVolume', dB) change the clip, and does it read back?")
def audio_volume(run):
    run.prepare_media(("video",))
    unit = run.unit()
    run.new_timeline("audio_volume")
    chain = Chain(run.timeline)
    run.append(chain, "video", [{"startFrame": 0, "endFrame": 2 * unit - 1, "trackIndex": 1, "mediaType": 2}])
    run.list_tracks(chain, [("audio", 1)])
    reply = run.api(chain, "place the clip's audio only on A1")
    if not run.listings.get(("audio", 1)):
        raise ProbeStop("Resolve placed no audio item: %s" % (reply.error or run.listings))
    for value in (0.0, -6.0, 6.0):
        chain = Chain(run.timeline)
        item = run.item(chain, "audio", 1, 0)
        chain.add(item, "GetProperty", "AudioVolume", label="before")
        chain.add(item, "SetProperty", "AudioVolume", value, label="returned")
        chain.add(item, "GetProperty", "AudioVolume", label="after")
        chain.add(item, "GetProperty", label="all")
        reply = run.api(chain, "SetProperty('AudioVolume', %s)" % value)
        everything = reply.get("all") if isinstance(reply.get("all"), dict) else {}
        verdict = run.verdict(reply, "SetProperty", lambda v, w=value: isinstance(v.get("after"), (int, float))
                              and abs(v["after"] - w) < 0.01)
        look = None
        if value == 6.0:
            look = ("Select the clip on A1 of %r and open Inspector > Audio: Volume should read +6.00 dB, the last value "
                    "sent, and the clip's volume line should sit above 0 dB. Play it: it should be clearly louder than "
                    "the source. If Volume reads 0.00 dB, the property did nothing." % run.timeline)
        run.ask("TimelineItem.SetProperty('AudioVolume', %s) on an audio item" % value, verdict,
                {"before": reply.get("before"), "returned": reply.get("returned"), "after": reply.get("after"),
                 "audio_or_volume_keys": {key: val for key, val in everything.items()
                                          if re.search("audio|volume", key, re.IGNORECASE)},
                 "error": reply.error}, look)


@probe("clip_marker_frames", ("video",), "Are TimelineItem.AddMarker frames relative to the item start or to the source?")
def clip_marker_frames(run):
    run.prepare_media(("video",))
    unit = run.unit()
    run.new_timeline("clip_marker_frames")
    chain = Chain(run.timeline)
    run.append(chain, "video", [{"startFrame": unit, "endFrame": 2 * unit - 1, "trackIndex": 1, "mediaType": 1}])
    run.list_tracks(chain, [("video", 1)])
    run.api(chain, "place one clip whose left offset is %d" % unit)
    if not run.listings.get(("video", 1)):
        raise ProbeStop("Resolve placed no clip.")
    clip = run.read_tracks([("video", 1)], "the clip's position and offsets")["V1"][0]
    near = max(1, unit // 3)
    frames = ((0, "Blue"), (near, "Green"), (unit + near, "Yellow"))
    chain = Chain(run.timeline)
    item = run.item(chain, "video", 1, 0)
    for frame, color in frames:
        chain.add(item, "AddMarker", frame, color, "AI probe f=%d" % frame, "clip_marker_frames probe", 1, "",
                  label="add:%d" % frame)
    chain.add(item, "GetMarkers", label="markers")
    reply = run.api(chain, "TimelineItem.AddMarker at frames %s" % [frame for frame, _ in frames])
    accepted = {frame: reply.get("add:%d" % frame) is True for frame, _ in frames}
    markers = reply.get("markers") if isinstance(reply.get("markers"), dict) else {}
    if accepted[0] and accepted[near] and not accepted[unit + near]:
        reading = "relative to the item start (frames past the clip's length were refused)"
    elif accepted[unit + near] and not accepted[0] and not accepted[near]:
        reading = "relative to the source media start (frames before the in-point were refused)"
    elif all(accepted.values()):
        reading = "ambiguous: Resolve accepted all three, so decide from where they appear"
    else:
        reading = "unclear from the return values"
    start, left = clip.get("start"), clip.get("left_offset")
    look = None
    if isinstance(start, int):
        look = ("On %r the clip on V1 starts at %s with left offset %s and is %s frames long. Item-relative frames would put "
                "Blue at %s and Green at %s, with Yellow missing because it is past the clip end. Source-relative frames "
                "would put Yellow at %s, with Blue and Green missing because they are before the in-point."
                % (run.timeline, timecode(start, run.fps), left, clip.get("duration"), timecode(start, run.fps),
                   timecode(start + near, run.fps), timecode(start + near, run.fps)))
    run.ask("Frame base of TimelineItem.AddMarker", "works" if any(accepted.values()) else "rejected",
            {"clip": clip, "accepted": {str(frame): value for frame, value in accepted.items()},
             "GetMarkers_keys": sorted(markers, key=lambda key: (len(str(key)), str(key))),
             "reading": reading, "error": reply.error}, look)


# ------------------------------------------------------------------ running

def run_probe(name, scratch_project, call=bridge_call, video=None, image=None, work_dir=None, timeout=TIMEOUT,
              export_type=None, sniff_export_types=False):
    """Run one probe against a scratch project and return its report. Every Resolve call goes through call."""
    spec = PROBES[name]
    run = ProbeRun(call, timeout, video, image, work_dir, export_type, sniff_export_types)
    report = {"probe": name, "purpose": spec["purpose"], "scratch_project": scratch_project,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "work_dir": str(work_dir) if work_dir else None,
              "questions": run.questions, "created_timelines": run.created, "restored": run.restored, "calls": run.calls}
    original = None
    try:
        ok, status = run.op("status", None, "check the open project")
        problem = scratch_problem(status.get("project") if ok and isinstance(status, dict) else None, scratch_project)
        if not ok or problem:
            report["refused"] = problem or "status failed: %s" % status
            return report
        report.update(resolve_version=status.get("resolve_version"), product=status.get("product"),
                      transport=status.get("transport"))
        chain = Chain()
        chain.add("project", "IsRenderingInProgress", label="rendering")
        reply = run.api(chain, "check that no render is running")
        if reply.get("rendering") is not False:
            report["refused"] = "A render is in progress, or Resolve did not say otherwise (%s)." % (
                reply.error or reply.get("rendering"))
            return report
        ok, info = run.op("project_info", None, "read the frame rate")
        try:
            run.fps = float(info.get("frame_rate"))
        except (AttributeError, TypeError, ValueError):
            report["stopped"] = "Could not read the project frame rate: %s" % (info,)
            return report
        ok, listing = run.op("list_timelines", None, "remember the open timeline")
        timelines = listing.get("timelines", []) if ok and isinstance(listing, dict) else []
        current = [entry for entry in timelines if entry.get("current")]
        original = {"id": current[0].get("id"), "name": current[0].get("name")} if current else None
        report["original_timeline"] = original
        run.work_dir = run.work_dir or Path(tempfile.mkdtemp(prefix="resolve-ai-probe-"))
        report["work_dir"] = str(run.work_dir)
        run.work_dir.mkdir(parents=True, exist_ok=True)
        spec["run"](run)
    except Halt:
        pass
    except ProbeStop as exc:
        report["stopped"] = str(exc)
    except Exception as exc:  # A bug in this script, not an uncertain bridge outcome.
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        _restore(run, original, report)
    report["halted"] = run.halted
    report["media"] = run.media
    report["unavailable_methods"] = sorted(run.unavailable)
    report["summary"] = {entry["question"]: entry["verdict"] for entry in run.questions}
    return report


def _restore(run, original, report):
    if "refused" in report:
        return
    if run.halted:
        run.restored["timeline"] = "Not attempted: the probe halted on an uncertain outcome. Inspect Resolve."
        return
    if not run.touched:
        run.restored["timeline"] = "Nothing to restore: no timeline was created or imported."
        return
    if not original:
        run.restored["timeline"] = "No timeline was open before the probe; the last probe timeline stays open."
        return
    target = {"timeline_id": original["id"]} if original.get("id") else {"name": original["name"]}
    try:
        ok, result = run.op("open_timeline", target, "re-open the original timeline")
        seen, status = run.op("status", None, "confirm the original timeline is open")
    except Halt:
        run.restored["timeline"] = "Halted while re-opening %r; inspect Resolve." % original.get("name")
        return
    run.restored["timeline"] = {"reopened": original, "returned": result,
                                "now_open": status.get("timeline") if seen and isinstance(status, dict) else None,
                                "restored": bool(ok and seen and isinstance(status, dict)
                                                 and status.get("timeline") == original.get("name"))}


# ------------------------------------------------------------------ offline simulator (--plan and tests)

SIM_FCPXML_TYPE = 7
SAMPLE_FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat1080p24" frameDuration="1/24s" width="1920" height="1080"/>
    <asset id="r2" name="clip.mov" start="0s" duration="240/24s" hasVideo="1" hasAudio="1" format="r1">
      <media-rep kind="original-media" src="file:///clip.mov"/>
    </asset>
  </resources>
  <library>
    <event name="Probe">
      <project name="Probe">
        <sequence format="r1" duration="96/24s" tcStart="3600s" tcFormat="NDF">
          <spine>
            <asset-clip name="clip.mov" ref="r2" offset="3600s" duration="48/24s" start="0s" format="r1"/>
            <asset-clip name="clip.mov" ref="r2" offset="86448/24s" duration="48/24s" start="1s" format="r1"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


class SimulatedResolve:
    """Offline stand-in for bridge.transport.call. It never contacts Resolve.

    It answers like an empty scratch project: setters return True and change nothing, a still placed
    without a range gets 150 frames, and Export with SIM_FCPXML_TYPE writes SAMPLE_FCPXML. Methods named
    in absent fail the way resolve_api reports a method this build lacks.
    """

    def __init__(self, project="API probe scratch", fps=24.0, rendering=False, absent=(), video_frames=None):
        self.project, self.fps, self.rendering, self.absent = project, fps, rendering, set(absent)
        self.video_frames = video_frames or int(round(fps)) * 10
        self.sent, self.items, self.markers = [], {}, {}
        self.timelines = [{"id": "sim-tl-1", "name": "User edit", "tracks": {}}]
        self.current, self.folder, self.bins = self.timelines[0], "Master", {"Master": []}

    def __call__(self, operation, params, timeout):
        self.sent.append({"op": operation, "params": copy.deepcopy(params)})
        handler = getattr(self, "_op_" + operation, None)
        if handler is None:
            raise OperationError("Unknown operation '%s'." % operation)
        return handler(params)

    def _summary(self, timeline):
        return {"object": "Timeline", "name": timeline["name"], "id": timeline["id"]}

    def _op_status(self, _params):
        return {"online": True, "transport": "simulated", "product": "DaVinci Resolve", "resolve_version": "simulated",
                "project": self.project, "timeline": self.current["name"] if self.current else None}

    def _op_project_info(self, _params):
        return {"name": self.project, "frame_rate": self.fps}

    def _op_list_timelines(self, _params):
        return {"timelines": [{"id": t["id"], "index": i, "name": t["name"], "current": t is self.current}
                              for i, t in enumerate(self.timelines, 1)]}

    def _op_create_timeline(self, params):
        if any(t["name"] == params["name"] for t in self.timelines):
            raise OperationError("Resolve could not create the timeline. The name may already be in use.")
        self.timelines.append({"id": "sim-tl-%d" % (len(self.timelines) + 1), "name": params["name"], "tracks": {}})
        self.current = self.timelines[-1]
        return {"created": params["name"], "active": True}

    def _op_open_timeline(self, params):
        found = [t for t in self.timelines if t["id"] == params.get("timeline_id") or t["name"] == params.get("name")]
        self.current = found[0]
        return {"opened": found[0]["name"]}

    def _op_fusion_run_lua(self, params):
        return {"item": params["item_id"], "finished": True, "result": {"accessor": 3, EXPORT_CONSTANTS[0]: SIM_FCPXML_TYPE}}

    def _ref(self, text, raw):
        match = re.fullmatch(r"\$(\d+)((?:\[\d+\])*)", text)
        value = raw[int(match.group(1))]
        for index in re.findall(r"\[(\d+)\]", match.group(2)):
            value = value[int(index)]
        return value

    def _arg(self, value, raw):
        if isinstance(value, str) and value.startswith("$") and value[1:2].isdigit():
            return self._ref(value, raw)
        if isinstance(value, list):
            return [self._arg(entry, raw) for entry in value]
        if isinstance(value, dict):
            return {key: self._arg(entry, raw) for key, entry in value.items()}
        return value

    def _op_resolve_api(self, params):
        raw, reported = [], []
        for number, step in enumerate(params["steps"]):
            on, method = step.get("on", "project"), step["call"]
            roots = {"resolve": {"object": "Resolve"}, "project": {"object": "Project", "name": self.project},
                     "media_pool": {"object": "MediaPool"}, "current_timeline": self._summary(self.current)}
            target = roots[on] if on in roots else self._ref(on, raw)
            if method in self.absent or method.startswith("EXPORT_"):
                done = ("Steps 0-%d already ran and were not undone: %s" % (number - 1, json.dumps(reported))
                        if reported else "Nothing ran.")
                raise OperationError("failed_step %d (%s.%s): %s has no method %s in this Resolve build.. %s"
                                     % (number, on, method, on, method, done))
            value = self._step(target, method, [self._arg(arg, raw) for arg in step.get("args", [])])
            raw.append(value)
            reported.append({"step": number, "on": on, "call": method, "result": value})
        return {"ok": True, "steps": reported, "note": "simulated"}

    def _timeline(self, summary):
        return next(t for t in self.timelines if t["id"] == summary.get("id"))

    def _append(self, infos):
        made = []
        for info in infos:
            media = info["mediaPoolItem"]
            still = media["name"].lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))
            if "startFrame" in info and "endFrame" in info:
                left, duration = info["startFrame"], info["endFrame"] - info["startFrame"] + 1
            else:
                left, duration = 0, 150 if still else self.video_frames
            kinds = ["video"] if still or info.get("mediaType") == 1 else ["audio"] if info.get("mediaType") == 2 else ["video", "audio"]
            for position, kind in enumerate(kinds):
                track = self.current["tracks"].setdefault((kind, int(info.get("trackIndex", 1))), [])
                start = 86400 + sum(self.items[entry["id"]]["duration"] for entry in track)
                item_id = "sim-item-%d" % (len(self.items) + 1)
                self.items[item_id] = {"start": start, "duration": duration, "left": left, "media": media["name"],
                                       "right": 0 if still else max(0, self.video_frames - left - duration)}
                summary = {"object": "TimelineItem", "name": media["name"], "id": item_id}
                track.append(summary)
                if position == 0:
                    made.append(summary)
        return made

    def _step(self, target, method, args):
        state = self.items.get(target.get("id"), {})
        reads = {"GetStart": lambda: state["start"], "GetEnd": lambda: state["start"] + state["duration"],
                 "GetDuration": lambda: state["duration"], "GetLeftOffset": lambda: state["left"],
                 "GetRightOffset": lambda: state["right"], "GetSourceStartFrame": lambda: state["left"],
                 "GetSourceEndFrame": lambda: state["left"] + state["duration"] - 1}
        if method in reads and state:
            return reads[method]()
        if method == "GetName":
            return target.get("name")
        if method == "IsRenderingInProgress":
            return self.rendering
        if method in ("GetCurrentFolder", "GetRootFolder"):
            return {"object": "Folder", "name": self.folder if method == "GetCurrentFolder" else "Master"}
        if method == "AddSubFolder":
            self.bins[args[1]], self.folder = [], args[1]
            return {"object": "Folder", "name": args[1]}
        if method == "SetCurrentFolder":
            self.folder = args[0]["name"]
            return True
        if method == "GetSubFolderList":
            return [{"object": "Folder", "name": name} for name in self.bins if name != "Master"]
        if method == "GetClipList":
            return list(self.bins.get(target.get("name"), []))
        if method == "ImportMedia":
            items = [{"object": "MediaPoolItem", "name": os.path.basename(path), "id": "sim-mp-%s" % os.path.basename(path)}
                     for path in args[0]]
            self.bins[self.folder].extend(items)
            return items
        if method == "GetClipProperty":
            still = target.get("name", "").lower().endswith(".png")
            frames = 1 if still else self.video_frames
            properties = {"Frames": str(frames), "Duration": timecode(frames, self.fps), "FPS": self.fps, "In": "", "Out": ""}
            return properties.get(args[0]) if args else properties
        if method == "GetSetting":
            return {"timelineFrameRate": str(self.fps), "timelineResolutionWidth": "1920"} if not args else None
        if method == "AppendToTimeline":
            return self._append(args[0])
        if method == "GetItemListInTrack":
            return list(self._timeline(target)["tracks"].get((args[0], int(args[1])), []))
        if method == "GetStartFrame":
            return 86400
        if method == "GetTrackCount":
            return max([index for kind, index in self._timeline(target)["tracks"] if kind == args[0]] or [1])
        if method == "AddTrack":
            self.current["tracks"].setdefault((args[0], 1 + max([i for k, i in self.current["tracks"] if k == args[0]] or [1])), [])
            return True
        if method == "GetTimelineByIndex":
            return self._summary(self.timelines[int(args[0]) - 1])
        if method == "Export":
            if args[1] != SIM_FCPXML_TYPE:
                return False
            Path(args[0]).write_text(SAMPLE_FCPXML, encoding="utf-8")
            return True
        if method == "ImportTimelineFromFile":
            self.timelines.append({"id": "sim-tl-%d" % (len(self.timelines) + 1), "name": args[1]["timelineName"],
                                   "tracks": copy.deepcopy(self.current["tracks"])})
            return self._summary(self.timelines[-1])
        if method == "GetProperty":
            return 0.0 if args else {"AudioVolume": 0.0, "Pan": 0.0, "ZoomX": 1.0}
        if method == "AddMarker":
            self.markers.setdefault(target.get("id"), {})[str(args[0])] = {"color": args[1], "name": args[2]}
            return True
        if method == "GetMarkers":
            return dict(self.markers.get(target.get("id"), {}))
        if method == "GetMarkInOut":
            return {}
        if method == "GetMediaPoolItem":
            return {"object": "MediaPoolItem", "name": state.get("media")}
        return True


# ------------------------------------------------------------------ CLI

def default_work_dir():
    from bridge import lifecycle
    container = lifecycle.app_store_container()
    base = container / "resolve-ai-probes" if container else Path(tempfile.gettempdir()) / "resolve-ai-probes"
    return base / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4]))


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="See the module docstring for the safety rules.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="list probes (offline)")
    mode.add_argument("--plan", metavar="NAME", help="print the calls a probe would send (offline, simulated Resolve)")
    mode.add_argument("--run", metavar="NAME", help="run one probe live on the scratch project")
    parser.add_argument("--scratch-project", help="exact name of the open scratch project (must contain scratch/probe)")
    parser.add_argument("--user-confirmed-scratch", action="store_true",
                        help="the user confirmed --scratch-project is a scratch project, so its name need not contain "
                             "scratch/probe (the open project must still match it exactly)")
    parser.add_argument("--video", help="short video with audio, at least 4 s")
    parser.add_argument("--image", help="still image for still_duration (default: a generated PNG)")
    parser.add_argument("--work-dir", help="folder for the PNG and FCPXML files (must be writable by Resolve)")
    parser.add_argument("--export-type", type=int, help="fcpxml_roundtrip: Timeline.Export type number to use")
    parser.add_argument("--sniff-export-types", action="store_true",
                        help="fcpxml_roundtrip: if the constant cannot be read, try Export types 0-24 and sniff the files")
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    parser.add_argument("--fps", type=float, default=24.0, help="--plan only: simulated frame rate")
    args = parser.parse_args(argv)
    if args.user_confirmed_scratch and args.scratch_project:
        CONFIRMED_SCRATCH.add(args.scratch_project)
    if args.list:
        for spec in PROBES.values():
            print("%-20s needs %-6s %s" % (spec["name"], "+".join(spec["needs"]), spec["purpose"]), file=out)
        return 0
    name = args.plan or args.run
    if name not in PROBES:
        parser.error("unknown probe %r; choose from %s" % (name, ", ".join(PROBES)))
    if args.plan:
        simulated = SimulatedResolve(project=args.scratch_project or "API probe scratch", fps=args.fps)
        with tempfile.TemporaryDirectory(prefix="resolve-probe-plan-") as folder:
            report = run_probe(name, simulated.project, simulated, args.video or "/path/to/short-clip-with-audio.mov",
                               args.image, Path(folder), export_type=args.export_type,
                               sniff_export_types=args.sniff_export_types)
        print(json.dumps({"probe": name, "offline": True,
                          "assumptions": "Simulated Resolve at %s fps: a fresh scratch project, setters return True, "
                                         "a 10 s video. Live indices, names, hex suffixes and paths differ." % args.fps,
                          "would_send": [{key: call[key] for key in ("op", "params", "label") if key in call}
                                         for call in report["calls"]],
                          "simulated_outcome": report.get("stopped") or report.get("error") or "completed"},
                         indent=2, ensure_ascii=False), file=out)
        return 0
    problems = [message for message in (
        scratch_problem(args.scratch_project, args.scratch_project) if args.scratch_project else "--scratch-project is required.",
        "This probe needs --video." if "video" in PROBES[name]["needs"] and not args.video else None,
        "--video %s does not exist." % args.video if args.video and not Path(args.video).expanduser().is_file() else None,
        "--image %s does not exist." % args.image if args.image and not Path(args.image).expanduser().is_file() else None,
    ) if message]
    if problems:
        parser.error(" ".join(problems))
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else default_work_dir()
    report = run_probe(name, args.scratch_project, bridge_call,
                       str(Path(args.video).expanduser().resolve()) if args.video else None,
                       str(Path(args.image).expanduser().resolve()) if args.image else None,
                       work_dir, args.timeout, args.export_type, args.sniff_export_types)
    report["route"] = getattr(bridge_call, "route", None)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str), file=out)
    return 2 if report.get("refused") else 3 if report.get("halted") else 1 if report.get("stopped") or report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
