"""Pure planning for trim, slip, slide and roll edits. Standard library only; never touches Resolve.

The planner takes the current layout (one dict per timeline item) plus one edit and returns the
new layout. ``bridge/operations.py`` reads the layout from Resolve, then rebuilds only the
changed clips on a duplicate timeline and compares the read-back with ``expected_tracks``.

Clip dict keys (input):
    key, label, name, track_type ("video"/"audio"/"subtitle"), track_index,
    start, end          absolute timeline frames (end exclusive, as GetEnd reports)
    source              first source frame in the clip's own media frames (None if unknown)
    ratio               source frames per timeline frame (1.0 for stills or matching rates)
    media_frames        total media frames in source frames, or None when unlimited (stills)
    media_id            media pool item id, used to find linked audio/video
    linked              keys Resolve reports as linked (GetLinkedItems), optional
    rebuildable         False for titles, generators, compounds, subtitles and retimed clips
    reason              why it is not rebuildable
    locked              True when the clip's track is locked

All ``frames`` arguments are timeline frames. Positive moves an edit point later (to the right).
"""
import math


class PlanError(ValueError):
    """The edit cannot be made; nothing should change."""


def source_length(length, ratio):
    """Source frames that fill ``length`` timeline frames.

    Resolve floors the conformed length of a mixed-rate piece, so round UP (as remove_ranges does)
    and the piece lands at exactly ``length`` timeline frames.
    """
    return int(math.ceil(length * ratio - 1e-9))


def source_offset(frames, ratio):
    """Timeline frames -> source frames, mapped by time for mixed frame rates."""
    return int(round(frames * ratio))


def describe(clip):
    return "%s '%s'" % (clip.get("label") or clip.get("key"), clip.get("name") or "")


def track_name(clip):
    prefix = {"video": "V", "audio": "A"}.get(clip["track_type"], "S")
    return "%s%d" % (prefix, clip["track_index"])


def handles(clip):
    """Unused media before and after the clip, in timeline frames. None means unlimited or unknown."""
    if clip.get("source") is None:
        return None, None
    ratio = float(clip.get("ratio") or 1.0)
    head = int(math.floor(clip["source"] / ratio + 1e-9))
    if clip.get("media_frames") is None:
        return head, None
    used_end = clip["source"] + source_length(clip["end"] - clip["start"], ratio)
    tail = int(math.floor((clip["media_frames"] - used_end) / ratio + 1e-9))
    return head, max(0, tail)


def _by_key(clips):
    out = {}
    for clip in clips:
        if clip["key"] in out:
            raise PlanError("Two timeline items share the id %s; refresh timeline_overview." % clip["key"])
        out[clip["key"]] = clip
    return out


def find(clips, key):
    """Look a clip up by unique id or positional label (V1.2); a clip name only when it is unique."""
    key = str(key or "").strip()
    if not key:
        raise PlanError("Pass the clip's id from timeline_overview.")
    for clip in clips:
        if key in (str(clip["key"]), str(clip.get("label"))):
            return clip
    named = [clip for clip in clips if clip.get("name") == key]
    if len(named) == 1:
        return named[0]
    if named:
        raise PlanError("Clip name '%s' is ambiguous. Use an id from timeline_overview." % key)
    raise PlanError("Timeline item not found: %s. Call timeline_overview for valid ids." % key)


def group_for(clips, clip):
    """The clip plus its linked companions: Resolve's links, or same media at the same source and place."""
    keys = {clip["key"]} | set(clip.get("linked") or [])
    for other in clips:
        if other["key"] == clip["key"] or other["track_type"] == "subtitle":
            continue
        if clip["key"] in (other.get("linked") or []):
            keys.add(other["key"])
        elif (clip.get("media_id") and other.get("media_id") == clip["media_id"]
              and other["start"] == clip["start"] and other["end"] == clip["end"]
              and other.get("source") == clip.get("source")):
            keys.add(other["key"])
    by_key = _by_key(clips)
    return [by_key[k] for k in sorted(keys, key=lambda k: (by_key[k]["track_type"] != "video", by_key[k]["track_index"]))
            if k in by_key]


def _track_clips(clips, clip):
    return sorted((c for c in clips if c["track_type"] == clip["track_type"] and c["track_index"] == clip["track_index"]),
                  key=lambda c: c["start"])


def _neighbour(clips, clip, side):
    """The clip touching ``clip``'s head (side="left") or tail (side="right") on its own track."""
    for other in _track_clips(clips, clip):
        if other["key"] == clip["key"]:
            continue
        if side == "left" and other["end"] == clip["start"]:
            return other
        if side == "right" and other["start"] == clip["end"]:
            return other
    return None


class _Plan:
    def __init__(self, clips, tl_start):
        self.clips = clips
        self.by_key = _by_key(clips)
        self.tl_start = int(tl_start)
        self.new = {}  # key -> {"start", "end", "source"}
        self.notes = []
        self.marker_shift = None  # (from_frame, delta)
        self.overlap_hint = "Use roll_edit to move a shared cut, or ripple to push later clips."

    def state(self, clip):
        return self.new.get(clip["key"]) or {"start": clip["start"], "end": clip["end"], "source": clip.get("source")}

    def head(self, clip, frames):
        """Move the head edge by ``frames``; the source in point moves with it."""
        s = dict(self.state(clip))
        s["start"] += frames
        if s["source"] is not None:
            s["source"] += source_offset(frames, clip.get("ratio") or 1.0)
        self.new[clip["key"]] = s

    def tail(self, clip, frames):
        s = dict(self.state(clip))
        s["end"] += frames
        self.new[clip["key"]] = s

    def slip(self, clip, frames):
        s = dict(self.state(clip))
        if s["source"] is not None:
            s["source"] += source_offset(frames, clip.get("ratio") or 1.0)
        self.new[clip["key"]] = s

    def move(self, clip, frames):
        s = dict(self.state(clip))
        s["start"] += frames
        s["end"] += frames
        self.new[clip["key"]] = s

    def ripple(self, threshold, delta, exclude):
        """Shift every clip starting at or after ``threshold`` by ``delta`` (sync across tracks)."""
        if not delta:
            return
        skipped_locked = set()
        for clip in self.clips:
            if clip["key"] in exclude or clip["key"] in self.new:
                continue
            if clip["start"] >= threshold:
                if clip.get("locked"):
                    skipped_locked.add(track_name(clip))
                    continue
                self.move(clip, delta)
            elif clip["end"] > threshold:
                self.notes.append("%s on %s spans the edit point and was not moved; clips after it on its track "
                                  "moved by %+d frames." % (describe(clip), track_name(clip), delta))
        for name in sorted(skipped_locked):
            self.notes.append("Track %s is locked, so its clips were not rippled and may now be out of sync." % name)
        self.marker_shift = (threshold, delta)
        self.overlap_hint = ("A clip that spans the edit point blocks the ripple; trim without ripple, "
                             "or move that clip first.")

    def finish(self, edit, moved_keys):
        """Validate every change, then return the full plan."""
        changed = []
        for key, s in self.new.items():
            clip = self.by_key[key]
            if s == {"start": clip["start"], "end": clip["end"], "source": clip.get("source")}:
                continue
            changed.append((clip, s))
        if not changed:
            raise PlanError("The edit changes nothing. Nothing was changed.")
        for clip, s in changed:
            self._validate(clip, s)
        self._check_overlaps()
        entries = []
        for clip, s in sorted(changed, key=lambda pair: (pair[0]["track_type"] != "video", pair[0]["track_index"], s["start"])):
            entries.append({
                "key": clip["key"], "label": clip.get("label"), "name": clip.get("name"), "track": track_name(clip),
                "track_type": clip["track_type"], "track_index": clip["track_index"],
                "role": "edited" if clip["key"] in moved_keys else "follows",
                "before": _window(clip, clip["start"], clip["end"], clip.get("source")),
                "after": _window(clip, s["start"], s["end"], s["source"]),
            })
        return {"edit": edit, "changed": entries, "notes": sorted(set(self.notes)),
                "marker_shift": list(self.marker_shift) if self.marker_shift else None,
                "expected_tracks": expected_tracks(self.clips, self.new)}

    def _validate(self, clip, s):
        name = describe(clip)
        if not clip.get("rebuildable", True):
            raise PlanError("%s would have to change, but it cannot be rebuilt: %s. Nothing was changed."
                            % (name, clip.get("reason") or "it has no plain media source"))
        if clip.get("locked"):
            raise PlanError("%s is on locked track %s. Unlock it first. Nothing was changed." % (name, track_name(clip)))
        if s["end"] - s["start"] < 1:
            raise PlanError("%s would be %d frames long; it is only %d frames. Nothing was changed."
                            % (name, s["end"] - s["start"], clip["end"] - clip["start"]))
        if s["start"] < self.tl_start:
            raise PlanError("%s would start at frame %d, before the timeline start (%d). Nothing was changed."
                            % (name, s["start"], self.tl_start))
        if s["source"] is None:
            raise PlanError("%s has no readable source position. Nothing was changed." % name)
        head, tail = handles(clip)
        ratio = float(clip.get("ratio") or 1.0)
        if s["source"] < 0:
            need = int(math.ceil(-s["source"] / ratio - 1e-9))
            raise PlanError("%s needs %d more frames of media before its in point than exist: only %d frames of head "
                            "handle are available. Nothing was changed." % (name, need, head or 0))
        if clip.get("media_frames") is not None:
            used_end = s["source"] + source_length(s["end"] - s["start"], ratio)
            if used_end > clip["media_frames"]:
                need = int(math.ceil((used_end - clip["media_frames"]) / ratio - 1e-9))
                raise PlanError("%s would run %d frames past the end of its media: only %d frames of tail handle are "
                                "available. Nothing was changed." % (name, need, tail or 0))

    def _check_overlaps(self):
        tracks = {}
        for clip in self.clips:
            if clip["track_type"] == "subtitle":
                continue
            s = self.state(clip)
            tracks.setdefault((clip["track_type"], clip["track_index"]), []).append((s["start"], s["end"], clip))
        for entries in tracks.values():
            entries.sort(key=lambda e: (e[0], e[1]))
            for (a_start, a_end, a), (b_start, b_end, b) in zip(entries, entries[1:]):
                if a_end > b_start:
                    raise PlanError("The edit would make %s (%d-%d) overlap %s (%d-%d) on %s. Nothing was changed. %s"
                                    % (describe(a), a_start, a_end, describe(b), b_start, b_end, track_name(a),
                                       self.overlap_hint))


def _window(clip, start, end, source):
    out = {"start": start, "end": end, "duration": end - start, "source_in": source}
    if source is not None:
        out["source_out"] = source + source_length(end - start, float(clip.get("ratio") or 1.0)) - 1
    return out


def expected_tracks(clips, new):
    """Every track's clips after the edit: {"V1": [[start, end, source], ...]} sorted by start."""
    tracks = {}
    for clip in clips:
        if clip["track_type"] == "subtitle":
            continue
        s = new.get(clip["key"]) or {"start": clip["start"], "end": clip["end"], "source": clip.get("source")}
        tracks.setdefault(track_name(clip), []).append([s["start"], s["end"], s["source"]])
    return {name: sorted(entries) for name, entries in sorted(tracks.items())}


def _int(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PlanError("%s must be a whole number of timeline frames." % name)
    if isinstance(value, float) and not float(value).is_integer():
        raise PlanError("%s must be a whole number of timeline frames." % name)
    return number


# ------------------------------------------------------------------ edits

def plan_trim(clips, key, edge, frames, ripple=True, tl_start=0):
    """Move one clip's head or tail. Positive frames move the edge later.

    Ripple keeps everything after the edit in sync: a tail trim moves every clip that starts at or
    after the old tail; a head trim keeps the clip's start, removes or adds material at its head and
    moves every clip that starts at or after the first kept frame. Without ripple the clip's
    neighbours stay put: shortening leaves a gap and lengthening must fit into an existing gap.
    """
    frames = _int(frames, "frames")
    edge = str(edge or "").lower()
    if edge not in ("head", "tail"):
        raise PlanError("edge must be 'head' or 'tail'.")
    if not frames:
        raise PlanError("frames must not be 0.")
    clip = find(clips, key)
    plan = _Plan(clips, tl_start)
    group = group_for(clips, clip)
    keys = {c["key"] for c in group}
    if edge == "tail":
        for member in group:
            plan.tail(member, frames)
        if ripple:
            plan.ripple(clip["end"], frames, keys)
    elif ripple:
        for member in group:  # Start stays; the head material changes and the tail follows.
            plan.slip(member, frames)
            plan.tail(member, -frames)
        plan.ripple(clip["start"] + max(frames, 0), -frames, keys)
    else:
        for member in group:
            plan.head(member, frames)
    verb = "shortened" if (frames > 0) == (edge == "head") else "lengthened"
    return plan.finish({"type": "trim", "item_id": clip["key"], "edge": edge, "frames": frames, "ripple": bool(ripple),
                        "summary": "%s %s by %d frames at the %s%s." % (describe(clip), verb, abs(frames), edge,
                                                                       ", later clips rippled" if ripple else "")}, keys)


def plan_slip(clips, key, frames, tl_start=0):
    """Show different source material in the same place: positive frames show later material."""
    frames = _int(frames, "frames")
    if not frames:
        raise PlanError("frames must not be 0.")
    clip = find(clips, key)
    plan = _Plan(clips, tl_start)
    group = group_for(clips, clip)
    for member in group:
        plan.slip(member, frames)
    return plan.finish({"type": "slip", "item_id": clip["key"], "frames": frames,
                        "summary": "%s slipped %d frames %s in its source; position and length unchanged."
                                   % (describe(clip), abs(frames), "later" if frames > 0 else "earlier")},
                       {c["key"] for c in group})


def plan_slide(clips, key, frames, tl_start=0):
    """Move a clip along its track; the neighbours touching it give or take the frames."""
    frames = _int(frames, "frames")
    if not frames:
        raise PlanError("frames must not be 0.")
    clip = find(clips, key)
    plan = _Plan(clips, tl_start)
    group = group_for(clips, clip)
    keys = {c["key"] for c in group}
    for member in group:
        left, right = _neighbour(clips, member, "left"), _neighbour(clips, member, "right")
        plan.move(member, frames)
        if left is not None and left["key"] not in keys:
            plan.tail(left, frames)
        if right is not None and right["key"] not in keys:
            plan.head(right, frames)
        if left is None and right is None:
            plan.notes.append("%s on %s has no touching neighbours; it moved into the gap." % (describe(member), track_name(member)))
    return plan.finish({"type": "slide", "item_id": clip["key"], "frames": frames,
                        "summary": "%s slid %d frames %s; touching neighbours absorbed the change, total length unchanged."
                                   % (describe(clip), abs(frames), "later" if frames > 0 else "earlier")}, keys)


def plan_roll(clips, left_key, right_key, frames, tl_start=0):
    """Move the cut between two touching clips: one gets longer, the other shorter."""
    frames = _int(frames, "frames")
    if not frames:
        raise PlanError("frames must not be 0.")
    left, right = find(clips, left_key), find(clips, right_key)
    if (left["track_type"], left["track_index"]) != (right["track_type"], right["track_index"]):
        raise PlanError("%s and %s are on different tracks; roll_edit needs two clips that share a cut."
                        % (describe(left), describe(right)))
    if left["start"] > right["start"]:
        left, right = right, left
    if left["end"] != right["start"]:
        raise PlanError("%s ends at %d but %s starts at %d; there is no shared cut to roll (gap of %d frames). "
                        "Use trim_edit instead." % (describe(left), left["end"], describe(right), right["start"],
                                                     right["start"] - left["end"]))
    plan = _Plan(clips, tl_start)
    left_group, right_group = group_for(clips, left), group_for(clips, right)
    keys = {c["key"] for c in left_group} | {c["key"] for c in right_group}
    for member in left_group:
        plan.tail(member, frames)
    for member in right_group:
        plan.head(member, frames)
    return plan.finish({"type": "roll", "left_item_id": left["key"], "right_item_id": right["key"], "frames": frames,
                        "summary": "Cut between %s and %s rolled %d frames %s (was at %d, now %d)."
                                   % (describe(left), describe(right), abs(frames), "later" if frames > 0 else "earlier",
                                      left["end"], left["end"] + frames)}, keys)


# ------------------------------------------------------------ verification

def compare_tracks(expected, actual):
    """Expected vs read-back layouts ({"V1": [[start, end, source], ...]}). Returns mismatch strings."""
    mismatches = []
    for name in sorted(set(expected) | set(actual)):
        want, got = expected.get(name, []), actual.get(name, [])
        if len(want) != len(got):
            mismatches.append("%s: expected %d clips, found %d." % (name, len(want), len(got)))
        for index, (w, g) in enumerate(zip(want, got), 1):
            if w[0] != g[0] or w[1] != g[1] or (w[2] is not None and g[2] is not None and w[2] != g[2]):
                mismatches.append("%s clip %d: expected %d-%d (source in %s), found %s-%s (source in %s)."
                                  % (name, index, w[0], w[1], w[2], g[0], g[1], g[2]))
    return mismatches
