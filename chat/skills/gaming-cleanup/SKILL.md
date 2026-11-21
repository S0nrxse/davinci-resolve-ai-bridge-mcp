---
name: gaming-cleanup
description: Tighten gameplay footage in DaVinci Resolve. Cuts only dead air (loading screens, frozen menus, quiet stretches) and never cuts talking or moments where something is happening in the game. Use when the user wants a stream VOD, let's play or gaming clip "cut up", "tightened" or "ready for YouTube/Shorts".
---

# Gaming cleanup (Resolve)

Rules that never bend:
- **Talking is never cut.** Every real word keeps air around it, even over a menu.
- **Action is never cut.** A game-audio burst, or a spike in on-screen motion, keeps the run-up and the payoff.
- Cut only **dead air**: black or loading screens, frozen menus, and stretches with no speech and no action.
- Work on a **copy**. The original timeline stays untouched.

1. `resolve_status`, then `current_context`. Name the timeline. Ask what it's for if that isn't clear.
   Pick the preset from the answer: `vod` for streams and long-form (cuts 6 s and longer), `youtube` for edited
   videos (3 s and longer), `shorts` for vertical clips (1.5 s and longer).
2. `gaming_cleanup(preset=..., mark=True)`. This reports and adds purple "Dead air" markers, nothing else.
   Report:
   - the seconds you'd save, with the before and after durations
   - the reasons, as loading/menu or quiet
   - which audio track it took as the mic, and how many phantom words it dropped
   If there is only one audio track, say the result is less precise. Voice and game share a track, so loud
   game audio can hide quiet talk, and the reverse. Suggest recording with separate OBS tracks: mic on one,
   game on another.
3. Invite the user to scrub the purple markers. If a cut looks wrong, look before deciding:
   - `contact_sheet` around the range shows what's on screen.
   - `search_transcript` shows whether something was said there.
   Then adjust. Use a gentler preset, pass `mic_stream` or `game_stream` when the wrong track was picked, or set
   `check_picture=False` when a menu genuinely matters (shop, loadout, story choice).
4. On approval, run `gaming_cleanup(preset=..., apply=True)`. It builds the tightened copy across every track.
   Report the new timeline's name and its duration.
5. Optional finishing, offered rather than done:
   - `generate_subtitles` for the commentary, or the $animated-captions skill for Shorts.
   - `detect_beats` on a music bed, if the user wants the action beats in time.
6. Close with what still needs a human: clutch moments that were silent on both tracks, and any phantom-word
   drops that could have been real whispers.
