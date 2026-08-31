# GameClip - Project Context

## What this is

A Python + FFmpeg pipeline that turns marked gameplay moments into finished
vertical shorts for YouTube Shorts and Instagram Reels.

Built to replace a manual DaVinci Resolve workflow that took 5+ hours per
video. Target is under 1.5 hours, most of that being the owner watching
footage and marking timestamps (which stays manual by design).

## Owner context

- 6+ years Flutter developer, also writes Python
- Channel: Malabari Gamer (YouTube + Instagram)
- Games: Far Cry, Ghost Recon Wildlands (both Ubisoft)
- All footage is his own gameplay - no scraped streams, no copyright risk
- Runs this on a separate SSD; deletes raw recordings manually after export

## How it works

```
raw recording (30 min)
  -> owner marks timestamps in clips.txt      [MANUAL - stays manual]
  -> cut + normalize + grade + 9:16  (one ffmpeg pass per clip)
  -> hook detection reorders (best clip first)
  -> join with transitions
  -> mix music with sidechain ducking
  -> export + thumbnail
  -> output/<date>_<name>/ folder
```

Run: `python main.py raw/session01.mp4 clips.txt --series vehicle_test`

## Architecture rules - do not break these

**1. One encode per stage, not per operation.**
Cut, colour grade, and vertical conversion happen in a SINGLE ffmpeg pass
per clip via one filter chain. Every extra encode degrades quality. Do not
refactor these into separate steps that write intermediate files.

**2. Always normalize before joining.**
Mismatched clips joined with concat demuxer SUCCEED silently and produce
video that plays fine in VLC but breaks on mobile, or has audio drift
after the first cut. `join.verify_matching()` hard-fails on mismatch by
design. Do not remove or soften this check.

**3. Force stereo and fixed sample rate on every clip.**
Audio channel mismatch causes sync drift with no ffmpeg warning.

**4. -ss goes BEFORE -i, with -t for the duration.**
An input seek jumps to the nearest keyframe instead of decoding the whole
file from the start, which is roughly 6x faster on a large recording. It
is still frame accurate because we re-encode: ffmpeg decodes from that
keyframe and discards frames up to the exact timestamp. The old "-ss after
-i" rule only applies to stream copy. Use -t, never -to: an input seek
rebases timestamps to zero, so -to would encode far too much.

**5. settb=AVTB and fixed fps are required.**
xfade rejects inputs with mismatched timebase or frame rate.

**6. Music comes ONLY from assets/music/.**
That folder holds royalty-free tracks only (YouTube Audio Library or
Pixabay). Never widen this to arbitrary paths. Copyrighted music triggers
Content ID claims and kills monetization. CC-BY-NC is not safe for
monetized content.

**7. Quality goes through video_quality_args(), never -crf directly.**
-crf is a libx264/libx265 private option. h264_nvenc does not have it,
and ffmpeg only WARNS when an option never reaches a stream - it does not
fail - so passing -crf to nvenc silently encoded every render at its
~2 Mbps default. nvenc needs -rc vbr -cq N -b:v 0. A new encoder must be
added to _CRF_ENCODERS or _CQ_ENCODERS in ffmpeg_utils.py; the function
raises rather than guessing, because guessing is how this stayed hidden.

**8. run_ffmpeg uses -loglevel warning and prints warnings on success.**
ffmpeg reports genuinely broken settings as warnings and still exits 0.
At -loglevel error a run looks clean while producing wrong output - that
is how both the -crf bug and a malformed thumbnail call survived. Do not
turn this back down to error. Add genuinely benign lines to
_WARNING_NOISE instead, with a comment saying why they are benign.

**9. Layout comes from core/layout.py, and nothing goes below the safe line.**
There are two layout modes, chosen per series with a "layout" key,
because different footage wants different framing:

  blur_band    gameplay band centred, blurred filler above and below,
               reaction cam as a bordered bubble in the bottom zone.
               Keeps ~75% of the source width - use it when the scene
               matters (driving, landscape, vehicle tests).
  facecam_top  full width reaction strip across the top, gameplay full
               bleed below it, text over the top of the gameplay. No
               blur, face always on screen. Keeps only ~40% of the
               source width - use it where the action is centred.

facecam_top needs a LANDSCAPE reaction recording. A 2.81:1 strip takes
63% of a 16:9 source but only 20% of a 720x1280 portrait one, which
crops the face to an eyes-only band. process.warn_if_portrait_strip()
says so at render time rather than letting it ship looking wrong.

Shorts and Reels draw their own UI - handle, caption, sound, action
buttons - over the bottom SAFE_BOTTOM pixels of the frame. Nothing put
there is ever seen. So the gameplay band is centred in
(HEIGHT - SAFE_BOTTOM), not in HEIGHT, and the reaction cam sits above
that line. Centring on the full frame once left 63% of the reaction cam
behind YouTube's own interface: correct in a desktop player, invisible
on a phone.

Both process.py and export.py read the numbers from layout.py. If either
computes its own they drift - build_vertical_filter once pinned the band
with overlay=(H-h)/2 while zones() placed it 170px higher, which would
have dropped the reaction cam straight onto the gameplay. Hook text and
captions go in the top zone, reaction cam in the bottom zone, gameplay
in the band.

**10. All top-zone text at export, reaction cam at cut.**
Hook detection reorders clips after they are cut, so process.py cannot
know which clip opens the video, nor where a clip lands on the finished
timeline - export.py can. The opening hook AND the per-clip captions are
both drawn there, with join.clip_timeline() supplying the spans:
transitions overlap, so a clip's position is not a cumulative sum of
durations. The reaction cam is per-clip and label-driven, so it stays in
process.py. Both stages were already re-encoding, so neither adds a pass.

**11. The pipeline never invents a title or a caption.**
Nothing in the code can see what happens in the footage. A series preset
controls how a video LOOKS - grade, music, transitions - and says
nothing about its content: running --series vehicle_test on a gunfight
clip for its grade is legitimate and must not relabel the clip. A
hardcoded series hook once put "ULTIMATE GETAWAY VEHICLE TEST" over a
gunfight in Fall's End. That is worse than no hook - a title that
misdescribes the clip makes the viewer feel baited and bounce, costing
the retention it was meant to buy. Titles and captions come from
clips.txt or --title only. With neither, export warns loudly instead of
filling the gap with a guess.

**12. Never put a path inside a filtergraph on Windows without checking it.**
The drive colon reads as an option separator. `metadata=print:file=C:/...`
fails to parse, the whole chain dies, and a wrapper that ignores the exit
code sees an empty result rather than an error - that is exactly how
hook.py scored 0.0 motion for every clip since it was written, so the
"best clip first" reorder never once reordered anything. Prefer a form
with no path at all (`metadata=print` writes to stderr). Where a path is
unavoidable, as with drawtext's fontfile, escape it and confirm the
filter actually ran.

**13. Score on mean_volume, never max_volume.**
One gunshot pins peak level at 0.0 dB, so max_volume returns 1.0 for
every gameplay clip and contributes nothing to the score.

## Current state

**Built and tested:**
- `core/clips.py` - parses clips.txt: timestamps, labels, `# title:`
  and per-clip quoted captions
- `core/ffmpeg_utils.py` - ffmpeg wrapper, probe, timestamp parsing
- `core/process.py` - cut + normalize + grade + vertical + reaction cam
  (one pass)
- `core/layout.py` - frame zone maths and the platform safe area,
  shared by process and export
- `core/hook.py` - scores clips by motion (scdet mafd, parsed from
  stderr) and mean loudness; raises rather than scoring silently
- `core/join.py` - concat demuxer and xfade paths, match verification,
  and clip_timeline() for anything that must align to a clip after joining
- `core/audio.py` - music selection and sidechain ducking
- `core/export.py` - final render with faststart, hook text,
  per-clip captions, thumbnail
- `main.py` - orchestration and CLI

Verified output: 1080x1920, 30fps, stereo, High profile, ~12 Mbps, blur
fill correct, transition duration math correct.
Gameplay band fills 42% of frame height (806px), hook text and reaction
cam land in the top and bottom zones.

**Built, waiting on assets:**
1. Hook text - `export.py` looks for `assets/overlays/<title-slug>.png`,
   then `assets/overlays/<series>.png`, then falls back to drawtext with
   `--title`. The PNG path is for the complex layered style ("MISSION
   FAILED", "CIVILIAN SAVED!") that drawtext cannot reproduce; drawtext
   handles a plain title line. `assets/overlays/` is still empty, so
   every render currently takes the drawtext path.
2. Reaction cam - ONE continuous recording per costume holds every
   reaction in sequence. Variants are `assets/reactions/reactions_*.mp4`;
   the prefix keeps them apart from anything else in the folder.
   `REACTION_SEGMENTS` maps an emotion to its (start, duration) inside a
   variant, and `REACTION_MAP` maps a clip label to an emotion.
   `pick_reaction_video()` chooses ONE variant per video and it is reused
   for every clip - picking per clip would change costume mid-short,
   which reads as broken editing, not variety. Every variant must hold
   its reactions at the SAME timestamps or the segment table stops
   matching. The cam is part of the format, not a punchline: with
   `REACTION_DISPLAY_SECONDS = None` it stays up for the whole clip,
   and with `REACTION_MATCH_LABELS = False` every clip uses the one
   long `idle` segment. Label matching is only worth switching on
   once a recording really holds distinct emotions at known times -
   a mismatched "hype" over a death is worse than a neutral face. Segments are short, so the cam is scaled and then looped;
   scaling first keeps the loop buffer small, since it holds raw frames.

**Not built yet (folders and config entries exist, code does not):**
3. Auto captions - Whisper, only needed if voiceover is added
4. AI metadata generation - vision model reads frames, writes title /
   description / hashtags into seo.txt
5. Auto upload - YouTube Data API v3 + Instagram Graph API

## Deliberate decisions - don't re-litigate these

- **Auto upload is last, not first.** Fiddly OAuth setup, saves ~3 min/video.
  Owner uploads manually from the output folder for now.
- **Full auto highlight detection is NOT wanted yet.** In Far Cry and Ghost
  Recon gunfire is constant, so audio-spike detection produces mostly false
  positives. Reviewing 50 bad suggestions is slower than just watching the
  footage. Revisit after ~10 videos of real usage data.
- **Analytics feedback loop is deferred.** Useless until there are 20-30
  videos to compare. Build around month two.
- **Vary the output.** If every video uses identical transitions, overlays
  and pacing it starts looking mass-produced, which is a real demonetization
  risk under YouTube's "inauthentic content" policy. Rotate transitions,
  vary text position, use different music. The reaction clips and manual
  moment-picking are what keep this on the right side of that line.

## Content strategy context

Shorts are a discovery funnel, not the revenue source. Gaming Shorts RPM is
roughly $0.03-0.10 per 1000 views; gaming long-form is ~$3.50 median. The
plan is Shorts to build audience, long-form for actual revenue. A future
feature should stitch a week of clips into a 10-minute compilation.

Series concept matters more than raw gameplay. Generic "man plays Far Cry"
gets no views. Narrow angles work: "Ultimate Getaway Vehicle Test" (his
best performer - 1.4k views with only 5 followers), stealth-only runs,
worst-weapon challenges. Series presets in `config.py` exist to keep each
series visually consistent.

## Conventions

- Settings go in `config.py`, never hardcoded in modules
- Every ffmpeg call goes through `ffmpeg_utils.run_ffmpeg()` so errors
  surface consistently
- Fail loudly with a clear message rather than producing broken video
- Comments explain WHY (especially ffmpeg gotchas), not what
- No external pip dependencies for the core pipeline

## Testing

Generate a synthetic source instead of using real footage for quick tests:

```bash
ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=30" \
       -f lavfi -i "sine=frequency=300:duration=30" \
       -c:v libx264 -preset ultrafast -c:a aac -pix_fmt yuv420p \
       raw/test_session.mp4
```

Always verify output with ffprobe (expect 1080x1920, 30fps, 2 channels)
and visually check a thumbnail frame. Use `--keep-work` to inspect
intermediate files.

Set `PRESET = "ultrafast"` in config.py for fast test renders.
