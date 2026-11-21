# Design: Resolve AI Chat panel

Scope: `chat/ui` (the docked chat panel, about 400px wide). The landing page in `src/` is a separate surface with its own look.

## Critique (November 2026, before the "one sheet" redesign)

What made the panel read as AI-made, from screenshots of the real states (running with tasks, a Fusion build, an
approval, a finished reply with a table, the model menu, the empty state and a real long chat):

- **Boxes inside boxes.** The Fusion receipt was a bordered card holding a bordered dotted-grid strip of bordered
  tiles with pips, then rulers. The approval was a tinted panel with an args row and three buttons. The task list was
  a tinted pill, the step detail a bordered well with a header bar and a footer bar, tables and `details` had frames,
  menu rows had glyphs inside bordered tiles. Every idea got its own container, so nothing had a place on a page.
- **Decoration posing as information.** ⓘ buttons on every menu row and on the restore-point line; chevrons and
  durations ("<1s", "2s") on every step; a breathing red tally lamp with a 24 fps "Rolling 00:20:01" in mono; a
  sweeping mini-ruler; a progress ring; a rail threading the steps. Each was defensible alone; together they were
  the texture of a generated dashboard.
- **The chat-app kit.** "Thought for <1s" rows (whose brain glyph read as another ⓘ), Copy and Rewind icons under
  every message, the editor's words in a grey bubble. That is ChatGPT's furniture, not an editor's tool.
- **No typesetting.** Six text sizes, three greys used interchangeably, mono for costume, medium weights everywhere.
  Nothing set the editor's brief apart from the agent's log, so the eye had no order to follow.

## Direction: one sheet on the glass

The night outside stays exactly as the editor chose it (fog, fine rain, grain, the warm window, the tuned top-bar
blur). On the glass there is one sheet of type, set the way a script supervisor keeps a log: the brief, the
work in a quiet column, the answer. References that shaped it (via Mobbin): **Things 3** (checklists drawn as rings
and checks with no boxes, headings and hairlines doing the structure), **Linear** (activity as small single lines,
property changes written inline as "No priority → Urgent", no per-event chrome), **Halide** and **Kino** (dark
instrument UI with one accent and quiet tabular readouts instead of decorative gauges). AI chat apps were treated as
anti-references.

### Principles

1. **One surface.** Nothing in the transcript sits in a card. Containers are allowed only for things that really
   float or hold input: menus, the composer, the media sheet, the lightbox, raw JSON in a well.
2. **Hierarchy is type and space, not borders.** Two text sizes in the transcript (13.5–14px for words, 12.5px for
   the log), three weights (400, 500 for the editor's brief, 600 only for the empty-state question), four greys
   with fixed jobs: ink-strong for the brief and what needs you, ink for answers, muted for finished steps, faint for
   metadata. Mono only inside raw JSON and code.
3. **Steps are a quiet log.** One 24px line per step: a glyph in a 22px margin and an editor-language label. No rail,
   no durations, no chevrons at rest. A running step shows only its ticking seconds; a failed one says "Failed".
4. **Colour must mean something.** The warm accent is the next action (Allow, Send, the current task) and the
   selection. Steel marks Fusion, lilac a subagent, red a failure. Nothing breathes or pulses.
5. **Ask in a sentence.** An approval is what will change, where, and two actions: Allow and Decline. "Always
   allow" is a quiet link; the raw request is one click away.
6. **Show the work as the editor would sketch it.** The Fusion receipt is a line of type (In ─ ● Shake ─ ● Split …
   ─ Out) and a keyframe ruler per animated input, under the step, not a card.
7. **The editor has a voice.** Your own words are warm ink on a warm rule; Claude's are neutral. Nothing else in
   the transcript uses that rule, so it is the one landmark when skimming.
8. **Fewer controls, findable.** Copy and Rewind show on the newest turn; any other message reveals them on click
   (the panel may never get hover). Explanations are second lines in menus, never ⓘ buttons.

## World

**The cabin at night.** A blue-grey fog sky, fine rain, one warm window as the only light, a still film grain. On it,
one sheet of type. Radii are small and few: 7px for buttons and menu rows, 10px menus, 14px composer, 4–6px for
images and the JSON well. No glow on any control. Circles only where natural (Send and Stop, toggles, the slider
thumb, task rings, the Fusion family dots).

## Tokens (`chat/ui/src/styles.css` `@theme`)

One look; the theme picker, the old Steel look and the mockups are gone (the server's old `theme` setting is ignored).

| Role | Token | Value |
| --- | --- | --- |
| Deepest well (code, JSON, inputs) | `well` | #050608 |
| Page | `base` | #08090c |
| Context popover, step glyphs, docked strip | `panel` | #0d0f12 |
| Raised controls, menus | `raised` / `raised-2` | #14161a / #1a1d22 |
| Hairlines | `line` / `line-2` / `line-3` | #1a1d22 / #23272d / #363b43 |
| Text | `ink-strong` / `ink` / `muted` / `faint` | #eceef1 / #d6d8dc / #a0a5ad / #878c94 |
| The one warm light (Allow, Send, focus ring, the reasoning slider) | `accent` / `accent-hi` / `accent-ink` | #ead9ae / #f3e6c4 / #1c170b |
| Live work only, muted | `live` | #d4685c |
| Status | `ok` / `warn` / `err` | #9cbf8f / #d8b56a / #d97a6c |
| Fusion node tints | `node-*` | desaturated steel, ochre, sage, lilac, slate teal, clay, brass |

Contrast on `base`: ink 13.9:1, muted 8.0:1, faint 5.9:1, accent 14.3:1; body text stays ≥ 11.8:1 on every
surface. Accent-ink on accent is 12.8:1.

Radii: 7px buttons and menu rows, `lg` 10px menus, `2xl` 14px composer, 4–6px images and the JSON well. `.tag` is a
small softly rounded tag on a tinted fill ("Demo"). Transitions are 150–200ms ease-out.
`--gutter: clamp(16px, 5.4vw, 24px)`; `--log-indent: 22px` is the log's glyph margin, and everything nested under a
step (details, approvals, receipts, subagent steps) starts at that text edge. `live` is no longer used in the
transcript (there is no lamp).

### The night outside (behind the content)

- **Sky:** blue-grey fog glowing at the top (radial, 27% → 8%), falling into the dark, plus a heavy lens vignette.
- **Rain:** hairline streaks (0.6px and 0.8px, 12–40px long) over the whole panel at 7% and 10% opacity, slanted
  16°, one tile per 3.4s and 2.5s, never jumping; texture, not objects. No beads, drips or bokeh. Off under Reduce
  Motion or Settings › Rain on the glass.
- **Grain:** 3.5% fractal noise, overlay, held still (a jittering grain flickers).
- **Fog edges:** one continuous background behind the whole panel (no header fill, no hairlines). The conversation
  dissolves into it at the top and bottom through an opacity mask on the scroller (a soft two-step falloff over
  ~30px), with matching padding so resting content is fully opaque. There is no band element and no blur rectangle,
  so nothing has an edge, at any scroll position or conversation length. Reduce Transparency drops the remaining
  backdrop blurs (composer, header menus).
- **Menus:** 10px radius, faint 7% white border, soft diffuse shadow, a slight backdrop blur (header menus); the
  composer's own menus are solid, because a blur nested in the frosted composer can't see past it.

## Type

System UI stack (SF Pro), one family. In the transcript: the editor's message 14px/1.5 weight 500 in ink-strong;
replies 13.5px/1.6 in ink; the log 12.5px (muted when done, ink-strong while running or waiting); metadata and
secondary lines 11.5–12px in faint. Menus 12.5px titles with 11.5px faint second lines. The empty-state question is
the only 600. Numbers that line up use tabular figures, not mono. SF Mono (`.tc`) is only for raw JSON, code and the
sign-in command.

## Layout, top to bottom

- **Header (44px, z-30):** thread switcher (title + chevron, popover list), a small "Demo" tag in the demo (its
  tooltip says the data is made up), new chat, settings, and a collapse button only inside the native shell
  (`IN_SHELL`; the shell owns the bubble). Icon buttons are 32px with tooltips that show their shortcut.
- **No context row while connected.** The timecode, clip name and status lamp up top are gone (they read as noise);
  the empty state names the clip under the playhead in one muted line and every message carries it to the agent. A row appears under the
  header only when Resolve isn't connected: the exact menu path to start the bridge, and Retry. (The live-timecode
  ticker and the per-message timecode/clip line went with it.)
- **Seamless top bar.** The header (and the offline/sign-in rows) float over the transcript (`.t-topbar`, z-30) with
  no border or hairline. A progressive blur: `::before` blurs what's behind (8px, brightness 0.82, saturate 1.1) under a
  mask that is solid through the top 55% and clears 10px below the bar, and `::after` lays a base-colour gradient (62% at the
  top, 34% mid-bar, clear at the bottom) over it for legible text. The conversation and the rain show through
  blurred, with no edge. Its menus are solid and sit above everything. Reduce Transparency: no blur, a solid base
  that fades out below the bar. Its height is measured into `--top-h`, which pads the transcript's top.
- **Transcript** (below the bar, starting under it), padded by the gutter; only its bottom fades (an opacity mask)
  above the composer, which is already frosted itself. Its scrollbar shows only while it scrolls. It stays pinned
  to the newest step whenever anything resizes (a ResizeObserver on the content as well as the scroller), except
  while a disclosure the user opened is animating: opening something (a step, the rewind panel) anchors the reading
  position there and stops following the bottom until you scroll back down or send a message.
- **Footer (z-20):** seek toast, the live task line, the timeline-notes chip, the rewind notice, the error line, the
  docked approval fallback, the "A step needs your approval" pill (shown only while no part of the waiting approval
  is on screen, by IntersectionObserver), composer. All set as text over the night; only the composer is a container.
- **Signed out:** a warm line under the header ("Claude Code is signed out"), the command to run and "Copy the
  command" as text. No bordered banner.

Stacking: the header (z-30), context line and footer (z-20) are positioned layers above the transcript, so every
menu, tooltip, the approval pill and the media sheet (z-50) render crisp.

**One menu at a time:** every popover registers itself when it opens and closes whichever was open (thread list,
settings, context, attach, access, model, the "/" and "$" pickers). One tooltip at a time: a tooltip shows only on
the control that owns it (hover, or focus reached with Tab), never on focus that returns after a menu closes.
A hidden tooltip is `display: none` (it fades in with an animation when shown), so it never adds scroll height: an
invisible tooltip under the last step once left a large empty gap to scroll into.

## Transcript

Claude Code's own bookkeeping tools (ToolSearch, TodoWrite, plan mode, MCP resource listing, the Task* tools) never
show as steps.

- **The editor's message: the editor's voice.** No bubble, no box. The words are set in a warm ink (`--color-you`:
  the paper white mixed with the window light, 14px/1.55, regular weight) hung on a 1px warm rule (accent at 62%) in
  a 14px margin, the way a script sets dialogue apart from action lines; replies stay neutral ink at the text edge.
  Skim a long chat by its rules. Everything that belongs to the message hangs at the same 14px indent: the "You
  changed 8 things in Resolve since the last reply" note above the rule, attachments and picture tiles inside it
  above the text, then Copy / Rewind and the rewind confirmation below. A steer sent mid-turn keeps the voice on a
  fainter rule (30%) with "Sent while it worked" as its first faint line, and sits close under the step it
  interrupted. 18px of extra space opens each new message; there is no hairline between turns. References: Linear's
  quoted property changes on a narrow rule, a screenplay's dialogue indent, Things 3's tinted headings.
- **Replies:** plain prose (markdown.tsx), accent block caret while streaming.
  - **Tables (GFM):** set like a book table: faint 11.5px header, a 10% rule under it, 4.5% rules between rows, no
    frame and no fill, tabular figures, alignment from the separator row; a wide table scrolls sideways.
  - **Inline HTML, allowlisted:** `kbd` (a small key on a 7% fill), `sub`, `sup`, `mark` (a warm wash), `br`,
    `details`/`summary` (a small drawn disclosure angle, no frame; the content indents 11px), `b`, `i`, `em`,
    `strong`, `code` (a 6% fill, no border), `u`, `s`, `small`. Everything else stays literal text. React elements
    only, never HTML strings.
- **The log:** consecutive steps, thoughts, restore points and the finished task list form one log (`.log-row`):
  24px lines, a 13px glyph in a 22px margin (`toolGlyph` in tools.ts), the label in editor language (present tense
  while running, past when done). No rail, no box, no duration on finished steps, no chevron at rest (it appears on
  hover/focus and stays while open). Glyph colour: faint, steel for Fusion, lilac for subagents, warm while waiting,
  red on failure, ink-strong while running. Right side: a running step's seconds ticking ("4s", "1:12"), "Failed",
  "Declined", "Needs approval" or a collapsed subagent's step count. The step's argument is a mono tooltip on hover.
  - **Opened step:** indented text, not a box: "Done. Resolve confirmed it. 2s" (or "Failed." in red with the
    error), what it did in editor terms, then "Request and response" and "Copy" as faint text links. The JSON opens
    in a well (6px radius, no border).
  - **Thoughts:** no "Thought for" row. The thought itself, one faint italic line that fades at its end, with a
    small three-bead glyph in the margin that opens it (so does clicking the line). While thinking it shows in full.
  - **Restore point:** a faint log line, "Restore point saved · Main"; clicking it explains the AI Checkpoints bin.
  - **Subagents (Task/Agent):** the title is the task; their steps nest at the text edge; collapsible.
  - **Frames:** a 160px 16:9 thumbnail at the text edge, no border.
  - **Failed steps:** the error as one red line under the row.
- **Fusion receipt:** under a `fusion_build` step, the chain as a line of type: "In ─ ● Shake ─ ● Split ─ ● Smear …
  ─ Out", each node its name after a 6px dot in its family colour (transform steel, merge ochre, filter sage, colour
  brass, mask lilac, generator teal, text clay); 12px hairline wires draw in left to right. It wraps like a sentence.
  Then one ruler per animated input (up to four): "Shake Center", a hairline with 6px diamonds in the node's colour
  landing in sequence, and the span ("6f") at the end of the last ruler. `fusion_keyframe_input` shows only its
  ruler. No card, no grid, no tiles, no pips.
- **Approval:** under the step that asks, at the text edge, no card or tint: one human sentence in ink-strong of what
  will change and where, its numbers in tabular figures: "Cut pauses longer than 0.6 s on a copy of “Main”",
  "Trim the tail of Interview_A.mov by +12 frames on a copy of “Main”", "Build “GlitchIn” on Interview_A.mov: 7
  nodes, 2 animated on “Main”" (with the chain line under it), "Set Interview_A.mov’s zoom 115%, rotate 5° on “Main”",
  "Add 3 markers on “Main”", "Call timeline.SetSetting(“timelineFrameRate”) and 2 more calls in Resolve’s API".
  The phrasing lives in `approvalSentence` in tools.ts, next to the labels; tools without a phrasing fall back to
  their arguments as words ("min pause 0.6 · apply yes"), never `key=value`. A faint line follows only when it says
  something: "Hard to undo." in red for risky tools, where (when the sentence didn't already say it), "1 of 3
  waiting". Then Allow (warm fill, 7px radius, ⌘↵ at 55%) and Decline (text button), and flush right two faint links:
  "Request" (the raw JSON, in a well) and "Always allow pause cuts". Keyboard shortcuts apply to the first waiting request only. If it's scrolled out
  of view, a small pill above the composer (a warm dot, "A step needs your approval") scrolls to it. A request with
  no step falls back to the same sentence docked above the composer, titled with the step's label.
- **Working line:** while the agent runs and nothing waits on you inline, "Working 0:14" ends the transcript: muted
  text and minutes:seconds in faint tabular figures since you sent the message in this thread. No lamp, no timecode.
- **Short transcripts sit on the composer:** a conversation shorter than the panel is anchored to the bottom.
- **Task list:** drawn like a Things checklist: a hollow ring for pending, a warm ring for the task in hand, a quiet
  green check for done (its text faint). While the turn runs, one line sits above the composer: the ring, the task
  in its "…ing" form, "2 of 5" and a chevron; it folds open into the list. Once the turn ends, the final list is a log
  line where it last changed ("All 5 tasks done"), folding open the same way. No card, ring gauge or breathing.

## Composer

A writing surface, not a cramped input: a softly rounded (14px) field of frosted fog over the rain, with a faint
7% border, a soft shadow and no glow, attachment chips (one horizontally scrollable row; "Attaching…" as plain
text while a file uploads), a 14px textarea with 16px side padding that shows three lines at rest and grows with the text (immediately, so
the caret never scrolls the first line under the border) up to 40% of the panel before it scrolls, easing back down
(200ms) after a send, and one light toolbar row of icons:

`+` · Access (shield, or the warn-colored unlock for Full access; a warm dot when Work on a copy is on) · (space)
· "Opus · High" (model and reasoning as text, the level dropped first if the row is tight) · Stop (a 32px round
button on a faint fill with a muted red square) / Send (32px round, flat warm fill; faint fill when disabled)

Every control is icon-only with a tooltip that names it, its state and its shortcut. `useToolbarFit` drops the model
name (dial stays) only if the row still overflows; verified at 340/400/480. A draft never carries into another chat:
opening or starting a chat clears the text, attachments and any picked suggestion.

- **Menu rows** (Settings, Access, Attach, model): set like a macOS menu. An optional 13px glyph with no tile, the
  label (12.5px), its explanation as a faint 11.5px second line when it needs one, and the control (switch, check)
  flush right. The whole row is one button. Separators are 6% hairlines inset 16px. Switches are 30×18 with a 14px
  thumb and an equal 2px inset at both ends.
- **Attach (+):** Current frame, Media pool clip… (bottom sheet with search, arrow keys, 16:9 posters), Files…
  Drag-and-drop and ⌘V paste still work; nothing in the menu says so.
- **Access:** Ask first / Full access (radio, warn glyph for Full access), a hairline, then **Work on a copy** and
  **Restore points before edits** (switches), each with its explanation as a second line. Agents without approvals
  show Full access with a second line that explains why.
- **Model:** each model with its nature flush right in faint ("Opus · Deep, careful work") and a check column, a
  hairline, then **Reasoning**: a five-stop slider (Low, Med, High, X-High, Max) on a 2px track with hairline ticks,
  built on a native range input (drag snaps, arrow keys step, focus ring on the thumb), then the context meter.
- **"/" file picker:** typing "/" at the start of a word opens a list above the composer (a faint heading, rows with
  a poster or a bare glyph, no glyph tiles): media pool clips, recent
  attachments and matching files in Movies, Desktop, Downloads and Documents (`GET /api/files?q=`, debounced
  120ms), each with a faint detail (bin and duration, folder, or size). It filters as you type; ↑↓ move,
  ↵ or ⇥ choose, esc dismisses (and stays dismissed for that "/"). Each row shows a 44×26 poster (4px radius, cover,
  lazy) from the item's `thumb_id` via `/api/thumb` (demo posters in the demo), on a neutral tile while it loads and
  falling back to the type icon on error. Choosing removes the typed "/query" and attaches the item by path
  (`POST /api/attachments/path`, no copy) as a chip that keeps the poster.
- **"$" skill picker:** typing "$" opens the skills list (`GET /api/skills`, loaded once) with each skill's
  description; the Pick skill shortcut opens it too. Choosing inserts `$name ` into the text; known skill tokens are
  drawn as warm hairline chips by a mirror layer behind the textarea, and the text is sent with `$name` intact.
- **Chips:** 38px tall with 5px inner padding: a 50×28 poster (frame, clip poster or the "/" result's thumbnail;
  a type-icon tile when there is none), a readable label ("Frame 01:00:12:04"), one line of meta (size, or "Video" /
  "Image" when the size isn't known), and remove.
- **Steering:** while the agent works the placeholder reads "Steer it: add a note or a correction…"; with text, a
  hint names the Send shortcut and says Claude reads it before its next step, and Send sits next to Stop.
- **No glow, ever.** No warm halo, focus ring, corner brackets or viewfinder marks around the composer, at rest,
  focused or busy. Focus only lifts the border from 7% to 12% white. The textarea never shows a focus outline.

## Settings

A menu of plain rows, each explanation a faint second line (no ⓘ anywhere): Agent for new chats (segmented, no
border); **Project location**, **Tell Claude what I changed**, **Rain on the glass** (switch rows); Always allowed
(a comma list and Reset, or a sentence when empty); **Shortcuts**; the version.

**Shortcuts** lists Send ↵, New chat ⌘N, Allow ⌘↵, Decline esc, Focus composer ⌘L, Stop ⌘., Collapse to bubble ⌘⇧W
(⌘W is macOS close), Attach file ⌘U, Pick skill ⌘K, plus "Show or hide the panel ⌥⌘A" shown read-only as set by the
app. Combos are quiet text buttons (a warm wash while recording). Allow never fires while the composer has text (a
correction in progress must not approve the edit it corrects); Decline never fires from inside a text field. Esc in
the composer closes a picker or leaves the composer; it never clears the draft. Click a combo, press the new keys: a
combo another action already uses is refused with a note naming that action, esc cancels, and "Reset to defaults"
appears once anything differs. Changes save through `api.settings({ shortcuts })` and come back in
`state.settings.shortcuts`. Every key handler reads the same merged map (`shortcuts.ts`), and while a combo is being
recorded no other handler acts.

## Empty state

Almost nothing (the direction the user picked): one question, "What are we cutting?" (25px semibold), optically
centred over the night, and one muted line that says honestly where you are: "<clip> in <timeline>", "Nothing under
the playhead in <timeline>", "No timeline open in <project>" or "No project open". Not connected: "Start the bridge
to begin" and no line (the row under the header explains). No suggestions, no recent list: recent chats live in the
title menu, and the composer is the only action. The timeline-notes chip still sits above the composer when there are
open notes.

## Message controls (Copy, Rewind)

- **Out of sight except where they're wanted.** The newest turn always shows its controls (Copy and Rewind under
  your latest message; Copy under the finished reply). Any other message reveals its own by a click on it (a click
  that doesn't select text or hit a link), by hover, or by tabbing into it. Nothing depends on hover: the panel is a
  non-activating NSPanel and may never get mouse-moved events.
- **Replies:** Copy copies every text block of that turn as markdown. A turn still running has none.
- **Copied** swaps the glyph for a green check for 1.4s (an `aria-live` "Copied" for screen readers). Copy and Rewind
  are 24px boxes with 12px faint glyphs, pulled out by the box inset so the glyphs line up with the text edge.
  Clipboard: `navigator.clipboard`, falling back to a hidden textarea + `execCommand("copy")` for WKWebView.
- **Rewind confirms inline under the message** (never `confirm()`), as text with no fill or box. The preview
  (`POST /threads/{id}/rewind {preview:true}`) is read before it opens (the Rewind glyph turns while it loads), so it
  opens once at its final height (220ms), and re-reads whenever the thread changes. "Rewind to here?" in ink-strong,
  what it does in muted, then:
  - with project edits and a restore point: the count in red ("The AI made 4 changes to your project since."), what
    "Chat and timeline" does (opens a copy of the timeline as it was; the current one is kept; imports, new timelines
    and project settings aren't undone) and that "Chat only" leaves the project. Buttons: **Rewind chat and
    timeline** (warm), Chat only, Cancel.
  - without a restore point: the changes "can't be undone" in red; **Rewind the chat** and Cancel.
  - none: **Rewind** and Cancel.
  - while a turn runs: "Stop the current turn first." with Stop the turn and Cancel.
  - Buttons are disabled while a rewind is in flight; Esc or Cancel closes it and focus returns to Rewind.
- After a rewind, the message and everything after it fade out (200ms, 4px down) before the thread reloads; your
  text, attachments and refs go back in the composer and a quiet line above it says "Rewound. Opened “<name>”; the
  previous timeline is kept." or "Rewound the chat."
- **Restore points:** a `checkpoint` event shows just before the step it protects, as a faint log line (see
  Transcript). The Access menu has "Restore points before edits" (setting `checkpoints`, default on).

## Lightbox

Every picture in the chat opens in a lightbox inside the panel (click, or Enter on it; `cursor: zoom-in`): attachment
images and video posters in messages and the composer, "Looked at the frame" grabs, contact sheets, match previews and
other step-result images (`Zoomable` in lightbox.tsx). The image is fit to the panel over the well dimmed to 78% and
blurred 14px, with its caption and "n / N" under it and a close button top right. Esc, the close button or a click
outside closes it; ← / → step through the chat's other pictures in page order. Opens with a 180ms fade and a 200ms
scale from 96%, closes in 160ms; Reduce Motion drops both, Reduce Transparency drops the blur. Focus moves to the
close button and returns to the picture that opened it.

## Seek links (timecodes and clip names)

Timecodes (`\b\d{1,2}[:;]\d{2}[:;]\d{2}[:;]\d{2}\b`) in replies, thoughts, step results and plans, inline-code
clip names that look like media files (mov mp4 m4v mxf mkv avi wav mp3 aif aiff png jpg jpeg tif tiff exr dpx braw
r3d), bare media file names in step text, and the clips in the notes list are buttons that call `POST /api/seek`
(`{timecode}` as written, or `{clip}`). Style (`.seek` in styles.css): the text's own colour with a dotted underline
at 45%; on hover or keyboard focus it warms and a 7px warm playhead mark appears above its first character. Success
flashes it once (700ms warm wash); an error ("No clip called “x” on the current timeline.") shows as a small toast
above the composer for 3.6s. Never blue. Demo mode fakes the responses (`demoSeek`).

## Timeline notes

`current_context.open_notes > 0` shows a quiet chip above the composer: a warm tick, "5 timeline notes ⌄ · Do them".
The count opens a list (GET /api/notes, open notes only): each row is the marker's timecode (a seek link, mono), the
note, and the clips under it (seek links). If the list can't be read (the bridge op isn't installed yet) it says so
quietly. "Do them" sends `$timeline-notes Do my open timeline notes.` through the normal send path without touching
the draft.

## Changes since the last reply

When `track_changes` is on (Settings: "Tell Claude what I changed", default on), the server compares the timeline with
how the agent left it and sends the agent what the editor changed by hand. A user event's `changes: string[]` shows as
one faint line above that message, "You changed 8 things in Resolve since the last reply ⌄", which folds open into
the sentences in muted text. Informational, never a warning colour.

## Context window and compacting

- **Meter, out of sight until wanted:** inside the model menu, under Reasoning: "Context  41k of 200k · 20%" (tokens
  only when the window is unknown) and a 2px hairline bar (accent; amber past 70%, red past 90%). Fed by the thread's
  `context_usage` and the live `context_usage` bus event. Past 70% the model chip adds " · 82%" in amber (red past
  90%); there is no ring.
- **Compact:** "Compact now" (a warm text action) and "Compact with a focus…" (opens a one-line field: what to
  keep, then ↵), with the hint "Summarizes the chat so far to free space; the transcript stays." While a turn runs,
  or before the first message, it's disabled and the hint says why. Only for agents with `compact` (Claude).
- **Slash command:** at the start of a message, "/" offers **Compact context** (`/compact`) as the first row of the
  picker; picking it writes `/compact ` (a focus can follow), and `/compact` typed out plus Enter sends as typed.
- **Transcript:** while it summarizes, "Compacting the context…" replaces the working line; afterwards a quiet divider
  marks the spot: "Context compacted · 37k → 9k" ("automatically" when the agent did it on its own).

## Nothing hover-only

- There are no ⓘ buttons. Every explanation is visible text (a menu row's second line) or opens on click (a restore
  point). Icon tooltips stay hover/focus extras on controls that already say what they do.
- Message controls reveal on click; step rows open on click; the thread menu's Delete is shown quietly at rest.

## Motion

Motion says something changed; nothing loops for decoration (transform, opacity, clip-path; only open / close
animates height):

- **Open / close.** Everything that expands (step details, the request JSON, subagent groups, thoughts, one-line
  results, the changes list, the rewind confirmation, the task list) animates height with its content: opening
  220ms, closing 190ms, expo-out, fading and sliding 3px when it folds to nothing. Content that folds to one line
  keeps its text wrapped until the box has shrunk. `useSmoothOpen` / `Fold` in components.tsx (Web Animations, with a
  timer so a close completes without rendered frames); reduced motion switches instantly.
- **Steps landing.** A new log line slides 10px in from the left under a clip-path wipe (380ms expo-out) and its glyph
  settles from 55% scale (460ms settle curve). Lines already there when a chat opens appear in place. The editor's
  message rises 3px into place.
- **Fusion.** Chain wires draw in left to right (70ms apart) and keyframe diamonds land in sequence.
- **Clocks.** A running step's seconds and the working line's minutes:seconds tick once a second (text only).
- **Send.** The Send arrow launches up and a new one rises in.
- **Menus** open fully opaque from their trigger's edge: 6px travel and 96% scale on the settle curve (300ms). One at
  a time.
- **Night ambience:** rain falls slowly and the grain holds still (rain off under Reduce Motion or the Settings
  switch). Tooltips fade in after 380ms.

Under `prefers-reduced-motion` every animation and the textarea's height transition are off.

## Rules

- Speak editor language in labels ("Animating a zoom on Interview_A.mov"), never tool names; tool names appear
  only inside the opened request.
- Never show success the tools didn't confirm. Failed steps say "Failed" in red with the error underneath.
- One place per signal: an approval appears once, inline, at the step that asked.
- Accent is for the next action and selection; don't use it for decoration.
- No new containers in the transcript. If something new needs a box to be understood, the typography isn't done.
- Progressive disclosure: one line per step, details one click away. Nothing important is removed.

