# Gaming Video Automation - Technical Breakdown

**Goal:** Reduce editing time from 5+ hours per video to under 1 hour.

**Approach:** Python + FFmpeg. No DaVinci Resolve needed once working.

---

## 1. How It Works (Overall Flow)

```
Raw gameplay recording (30 min)
        |
        v
[ YOU: mark good moments ]  <-- only manual step
        |
        v
[ 1. CUT ]      Extract marked clips
        |
        v
[ 2. NORMALIZE ] Make all clips same format
        |
        v
[ 3. JOIN ]     Merge with transitions
        |
        v
[ 4. COLOR ]    Apply your saved look
        |
        v
[ 5. VERTICAL ] Convert to 9:16
        |
        v
[ 6. OVERLAY ]  Add text templates + reaction clip
        |
        v
[ 7. AUDIO ]    Add music, balance volume
        |
        v
[ 8. CAPTIONS ] Auto subtitles (if voiceover)
        |
        v
[ 9. EXPORT ]   Render final + thumbnail
        |
        v
[ 10. METADATA ] AI writes title/description/tags
        |
        v
[ 11. UPLOAD ]  Push to YouTube + Instagram
```

---

## 2. Module Breakdown

### Module 1: Clip Marker (your manual step)

**What it does:** Lets you note timestamps of good moments.

**Simplest version:** A text file.
```
04:32 - 04:48  gunfight
12:10 - 12:25  car chase
19:03 - 19:20  explosion
```

**Better version later:** A small Flutter desktop app. Play video, press a key to mark in/out points.

**Output:** A list of start/end timestamps + a label for each.

---

### Module 2: Cutter

**What it does:** Extracts each marked segment from the raw file.

```bash
ffmpeg -ss 00:04:32 -to 00:04:48 -i raw.mp4 -c copy clip_01.mp4
```

**Important:** `-c copy` is fast but cuts only at keyframes, so your clip may start slightly off. For frame-accurate cuts, re-encode instead:

```bash
ffmpeg -ss 00:04:32 -to 00:04:48 -i raw.mp4 -c:v libx264 -c:a aac clip_01.mp4
```

Slower, but exact. Use this one.

---

### Module 3: Normalizer

**What it does:** Makes every clip identical in format before joining.

**Why this matters:** This is the single most common bug in FFmpeg pipelines. Joining clips with mismatched properties produces broken output that *looks fine in VLC but breaks on mobile or in browsers*, or has audio drifting out of sync after the first transition.

**Always check first:**
```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate \
  clip_01.mp4
```

**Then normalize everything to the same spec:**
```bash
ffmpeg -i clip_01.mp4 \
  -vf "scale=1920:1080,fps=30,settb=AVTB" \
  -c:v libx264 -c:a aac -ac 2 -ar 48000 \
  norm_01.mp4
```

**Two specific gotchas:**
- **Audio channel mismatch** (one clip stereo, one mono) causes sync drift and FFmpeg won't always warn you. Force `-ac 2` on everything.
- **Missing audio stream** crashes the concat filter, because it expects the same number of streams per segment. If a clip has no audio, add a silent track:
```bash
ffmpeg -f lavfi -i anullsrc=r=48000:cl=stereo -i silent_clip.mp4 \
  -shortest -c:v copy -c:a aac fixed.mp4
```

---

### Module 4: Joiner

Two options depending on whether you want transitions.

**Option A - No transitions (fast, hard cuts):**

Use the concat demuxer. It copies streams directly, so zero quality loss and near-instant processing. Requires all inputs already matched (which Module 3 guarantees).

```bash
# files.txt
file 'norm_01.mp4'
file 'norm_02.mp4'
file 'norm_03.mp4'
```
```bash
ffmpeg -f concat -safe 0 -i files.txt -c copy joined.mp4
```

**Option B - With transitions (slower, looks better):**

Use `xfade`. Note that xfade requires the timebase and frame rate of each input to be the same, which is why Module 3 sets `settb=AVTB` and a fixed fps.

```bash
ffmpeg -i norm_01.mp4 -i norm_02.mp4 \
  -filter_complex "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4" \
  -c:v libx264 joined.mp4
```

Available transitions include fade, wipeleft, slideup, circleopen, dissolve, pixelize, fadeblack, and about 40 others. For gaming content, `fade`, `fadeblack`, and `dissolve` look cleanest. Avoid the flashy ones, they date badly.

**Performance note:** The concat filter is roughly 2x the cost of a single-file encode because it processes every frame of every input. Structure your pipeline so files arrive already matched, and use the demuxer as the default. Re-encoding should be the exception.

---

### Module 5: Color Grade

**What it does:** Applies your saved cinematic look.

**Simple version (built-in filters):**
```bash
ffmpeg -i joined.mp4 \
  -vf "eq=contrast=1.15:saturation=1.2:brightness=0.02,unsharp=5:5:0.8" \
  graded.mp4
```

**Better version (LUT file):** Export a `.cube` LUT from DaVinci once, then reuse forever:
```bash
ffmpeg -i joined.mp4 -vf "lut3d=my_look.cube" graded.mp4
```

Store 2-3 LUTs so different series can have different looks.

---

### Module 6: Vertical Conversion (9:16)

Your gameplay is 16:9. Shorts and Reels need 9:16.

**Best approach - blurred background fill:**
```bash
ffmpeg -i graded.mp4 -filter_complex \
"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,\
crop=1080:1920,boxblur=20:5[bg];\
[0:v]scale=1080:-1[fg];\
[bg][fg]overlay=(W-w)/2:(H-h)/2" \
vertical.mp4
```

This puts your gameplay centered with a blurred version filling top and bottom. **Note:** in your uploaded Instagram screenshots, your video has plain black bars top and bottom. Blurred fill looks noticeably more professional and fills the frame better.

**Alternative - crop to action:** Crop the middle of the frame. Fills the screen fully, but you lose the sides of the image. Better for close combat, worse for driving/landscape shots.

---

### Module 7: Text Overlays

**Simple text (drawtext filter):**
```bash
ffmpeg -i vertical.mp4 -vf \
"drawtext=fontfile=/path/impact.ttf:\
text='MISSION FAILED':\
fontsize=90:fontcolor=white:\
bordercolor=black:borderw=6:\
x=(w-text_w)/2:y=200:\
enable='between(t,0,3)'" \
titled.mp4
```

`enable='between(t,0,3)'` shows the text only from 0 to 3 seconds. `t` is current time in seconds.

**Gotcha:** drawtext uses colons as delimiters, so escape any colon inside your text with a backslash.

**For your style specifically:** Your existing thumbnails use graphics like "MISSION FAILED" with an X icon, "CIVILIAN SAVED!" with highlighting. Those are too complex for drawtext. Better approach: design 5-10 PNG templates once with transparency, then overlay them:

```bash
ffmpeg -i vertical.mp4 -i overlay_mission_failed.png \
-filter_complex "[0:v][1:v]overlay=0:0:enable='between(t,0,3)'" \
titled.mp4
```

This gives you your existing visual style with zero per-video design work.

---

### Module 8: Reaction Clip Overlay

Insert your tagged reaction video (surprise, laugh, wince, etc.) as a corner bubble.

```bash
ffmpeg -i titled.mp4 -i reactions/surprise.mp4 \
-filter_complex \
"[1:v]scale=300:-1,format=yuva420p[react];\
[0:v][react]overlay=W-w-40:40:enable='between(t,5,12)'" \
with_reaction.mp4
```

**Circular mask version** (looks better) needs a circle PNG mask applied with `alphamerge` before overlay.

**Selection logic in Python:**
```python
reaction_map = {
    "gunfight":  "hype",
    "explosion": "surprise",
    "fail":      "disappointment",
    "close_call":"wince",
    "funny":     "laugh"
}
```
The label you typed in Module 1 picks the reaction automatically.

---

### Module 9: Audio

**Add background music at correct level:**
```bash
ffmpeg -i with_reaction.mp4 -i music.mp3 \
-filter_complex \
"[1:a]volume=0.15[music];\
[0:a][music]amix=inputs=2:duration=first" \
-c:v copy audio_done.mp4
```

**Better - ducking** (music drops when game audio is loud):
```bash
-filter_complex "[1:a][0:a]sidechaincompress=threshold=0.05:ratio=8[music];[0:a][music]amix=inputs=2"
```

**Music licensing rule (important):** Only pull from a fixed, pre-approved folder of royalty-free tracks. YouTube Audio Library, or a paid license like Epidemic Sound. Never let the software grab arbitrary tracks, that's what triggers Content ID claims and demonetization.

---

### Module 10: Auto Captions

Only needed if you add voiceover. Skip if your videos are gameplay audio only.

```python
import whisper
model = whisper.load_model("medium")
result = model.transcribe("audio.wav")
# write SRT
```

Then burn in:
```bash
ffmpeg -i video.mp4 -vf "subtitles=subs.srt:force_style='Fontsize=24,Bold=1'" -c:a copy captioned.mp4
```

**Notes:** Use the `medium` model as default, it balances accuracy and speed well; use `large` only for difficult audio. Keep lines under 42 characters, max 2 lines on screen, each cue visible 1-7 seconds. Always review before burning in, Whisper stumbles on game-specific proper nouns like "Nakahawa". Heavy background music and strong accents reduce accuracy.

---

### Module 11: Export + Thumbnail

**Final render settings for Shorts/Reels:**
```bash
ffmpeg -i final.mp4 \
  -c:v libx264 -preset medium -crf 20 \
  -pix_fmt yuv420p \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  output.mp4
```

`-movflags +faststart` moves the moov atom to the front. MP4 puts it at the end by default, which breaks streaming playback.

**Thumbnail extraction:**
```bash
ffmpeg -i output.mp4 -vf "thumbnail,scale=1280:720" -frames:v 1 thumb.png
```

The `thumbnail` filter picks the most representative frame automatically, better than grabbing a fixed timestamp.

---

### Module 12: Metadata Generation

Send 2-3 frames to a vision model, get back title, description, hashtags.

```python
prompt = """
Look at these gameplay frames.
Series: {series_name}
Return ONLY JSON:
{
  "title": "...",
  "description": "...",
  "hashtags": ["...", "..."]
}
Match this style: [paste your best-performing description]
"""
```

Feed it your actual Ghost Recon Wildlands description as the style example, it already performs well.

---

### Module 13: Upload

**YouTube:** Data API v3, `videos.insert`. Roughly 100 uploads/day under current quota, plenty for you.

**Instagram:** Graph API, requires a Business or Creator account. Two-step process: create media container, then publish. Reels via API cap at 90 seconds even though the app allows longer.

**Recommendation for v1:** Skip auto-upload entirely. Just export the file and upload manually. Auth setup is fiddly and it saves you maybe 3 minutes per video. Add it later.

---

## 3. Realistic Time Savings

| Stage | Now (DaVinci) | After automation |
|---|---|---|
| Watch + mark clips | ~1.5 hr | ~1 hr (still manual) |
| Cutting + joining | ~1 hr | seconds |
| Color grading | ~0.5 hr | seconds |
| Text overlays | ~1 hr | seconds |
| Music + audio | ~0.5 hr | seconds |
| Export | ~0.5 hr | ~5 min |
| **Total** | **~5 hr** | **~1.2 hr** |

Roughly **75% time saved**. Not 100%, and anyone promising that is overselling it.

---

## 4. Limitations (Honest List)

### Technical

1. **Highlight detection stays manual.** Automatically finding the exciting moment is genuinely hard. Audio-spike detection catches gunfire, but gunfire is constant in these games, so it produces mostly false positives. Don't build this for v1.

2. **Rendering is slow.** Every filter stage re-encodes. A 60-second vertical video with overlays and transitions may take 3-8 minutes on CPU. GPU encoding (`-c:v h264_nvenc`) is much faster if you have an NVIDIA card, at slight quality cost.

3. **Chaining stages loses quality.** Each re-encode degrades the image. Fix: build ONE big `filter_complex` chain and encode once, rather than writing intermediate files at every step. Harder to debug, but much better output.

4. **FFmpeg filter syntax is painful.** Complex filtergraphs are error-prone. `ffmpeg-python` wraps it in readable Python and is worth using.

5. **Templates create sameness.** If every video uses identical overlays and transitions, it starts looking mass-produced. Build in variation: rotate between 3-4 transition types, vary text position slightly, use different music.

### Platform / policy

6. **Automated-looking content risks demonetization.** YouTube renamed its old spam policy to "inauthentic content" and actively targets mass-produced material. Your reaction clips and manual moment-picking are what keep you on the safe side.

7. **Ubisoft Content ID.** Both Far Cry and Ghost Recon are Ubisoft. Even original gameplay can occasionally get claimed. Worth checking their creator policy before scaling.

8. **Music is the biggest claim risk.** Locked royalty-free folder only. No exceptions.

9. **Instagram API needs Business account approval.** Not instant, has a review step.

### Practical

10. **This won't make your channel grow.** It saves time, that's all. Growth comes from the angle, hook, and consistency. Don't expect the software to fix reach.

---

## 5. Ideas to Improve Later

**Near-term (worth building after v1 works):**

- **Preset system.** Save a JSON config per series: LUT, transition style, music folder, overlay templates. Pick series name, everything else is automatic.
- **Batch mode.** Process 5 videos overnight in one run.
- **Preview render.** Low-res 480p test render in 20 seconds so you can check before committing to the full 5-minute encode.
- **Auto hook detection for first 3 seconds.** Pick the highest-motion frame from your marked clips and put it first. Retention on Shorts is decided in the first 2 seconds.
- **Multiple aspect ratios in one run.** Export 9:16 for Shorts and 16:9 for long-form simultaneously, same source.

**Medium-term:**

- **Analytics feedback.** Pull view/retention data via YouTube API, tag which series and hook styles perform best. Over time this tells you what to make more of.
- **Semi-auto highlight detection.** Combine audio spikes with motion analysis to *suggest* moments, you approve or reject. Assistive, not autonomous. Much more achievable than full automation.
- **Long-form assembly.** Since your plan is shorts-as-funnel, add a mode that stitches a week of clips into one 10-minute compilation with chapters. Long-form gaming RPM is roughly 35x higher than Shorts.
- **A/B thumbnails.** Generate 3 thumbnail variants, test which gets clicks.

**Longer-term (if productizing):**

- Web dashboard instead of local scripts
- Multi-user accounts
- Preset marketplace, users share LUTs and overlay packs
- Game auto-detection from frames, so users don't have to tag manually

---

## 6. Suggested Build Order

Don't build all of this at once. Order matters:

1. **Week 1:** Modules 2, 3, 4 (cut, normalize, join). Text file input. Test that output isn't broken.
2. **Week 2:** Modules 5, 6 (color, vertical). Compare output against a DaVinci edit.
3. **Week 3:** Module 7, 8 (text overlays, reaction clips) using PNG templates.
4. **Week 4:** Module 9, 11 (audio, export). At this point you have a working v1.
5. **Later:** Metadata AI, upload API, Flutter UI.

Ship v1 as a plain Python script. No UI. Add the Flutter interface only once the pipeline actually works, otherwise you'll spend weeks on UI for something that might change completely.

---

## 7. Stack Summary

| Need | Tool |
|---|---|
| Video processing | FFmpeg |
| Scripting | Python 3 |
| Cleaner FFmpeg syntax | `ffmpeg-python` |
| Captions (optional) | `openai-whisper` or `faster-whisper` |
| Metadata AI | Gemini / Claude API |
| Upload | `google-api-python-client`, Instagram Graph API |
| UI (later) | Flutter desktop |

Everything above is free and open source except the AI API calls, which cost a few cents per video.
