# GameClip

Turns marked gameplay moments into finished vertical shorts.

Tested and working. Cut, normalize, grade, vertical convert, hook-order,
join with transitions, mix music, export, thumbnail.

---

## Setup

Requires Python 3.8+ and FFmpeg.

```bash
# Check ffmpeg is installed
ffmpeg -version

# If not (Windows): download from ffmpeg.org and add to PATH
# Mac:   brew install ffmpeg
# Linux: sudo apt install ffmpeg
```

No pip packages needed for the core pipeline.

---

## Folder layout

```
gameclip/
├── main.py              run this
├── config.py            all settings - edit this, not the code
├── clips.txt            your marked timestamps
├── core/                pipeline modules
├── raw/                 put gameplay recordings here
├── output/              finished videos appear here
└── assets/
    ├── music/
    │   ├── tense/       royalty-free tracks only
    │   ├── hype/
    │   └── chill/
    ├── luts/            .cube colour grade files (optional)
    ├── overlays/        PNG text templates (not wired up yet)
    └── reactions/       your reaction clips (not wired up yet)
```

---

## How to use

**1. Record gameplay.** Put the file in `raw/`.

**2. Watch it and mark the good moments** in `clips.txt`:

```
# START  END    LABEL
4:32     4:48   gunfight
12:10    12:25  chase
19:03    19:20  explosion
```

Timestamps accept `4:32`, `1:04:32`, or plain seconds like `272`.
Lines starting with `#` are ignored.

**3. Run it:**

```bash
python main.py raw/session01.mp4 clips.txt --series vehicle_test --name vehicle_test_04
```

**4. Open the output folder** and upload manually:

```
output/2026-08-28_vehicle_test_04/
├── video.mp4          ready to upload
├── thumbnail.png
├── seo.txt            write your title/description here
└── clips_used.txt     which timestamps were used
```

---

## Series presets

Each series has its own look. Defined in `config.py`:

```python
"stealth": {
    "eq": "contrast=1.2:saturation=0.9:brightness=-0.02",
    "music_mood": "tense",
    "music_volume": 0.12,
    "transition": "fadeblack",
    "vertical_mode": "blur",
}
```

Add your own by copying a block and changing the values. Run with
`--series stealth`.

---

## Music - read this

Only put **royalty-free** tracks in `assets/music/`. Safe sources:

- **YouTube Audio Library** (in YouTube Studio) - safest. Check the
  License column, most need no attribution but some marked CC BY do.
- **Pixabay Music** - commercial use, no attribution. Keep your download
  proof in case another library filed a Content ID claim on the same track.

Avoid anything marked **CC-BY-NC**. Only CC0 and CC-BY are safe for
monetized videos.

Download 10-15 tracks once, sort into the mood folders. One-time job.

---

## Hook detection

Automatically scores each clip on motion and loudness, then puts the
highest-scoring clip first. Retention on Shorts is decided in the first
2 seconds, so your best moment should open the video regardless of when
it happened in the recording.

Turn it off in `config.py` with `HOOK_DETECTION = False`.

Check `clips_used.txt` to see the scores it gave.

---

## Performance

Rendering is CPU-bound. Rough guide on a normal machine:
a 60-second vertical short takes 2-5 minutes.

**If you have an NVIDIA GPU**, change in `config.py`:

```python
VIDEO_CODEC = "h264_nvenc"
```

Much faster, slight quality cost.

**For quick test renders**, temporarily set `PRESET = "ultrafast"`.

---

## Not built yet

- Text overlay templates (Module 7)
- Reaction clip overlay (Module 8)
- Auto captions (Module 10)
- AI metadata generation (Module 12)
- Auto upload (Module 13)

The folders and config entries exist, the code doesn't yet.

---

## Troubleshooting

**"Clip properties do not match"** - this is a safety check, not a bug.
It means clips have different resolution/fps/audio channels. Should not
happen since processing normalizes everything, but if it does, delete
`.work/` and rerun.

**Output plays in VLC but breaks on phone** - almost always mismatched
clips joined with stream copy. The `verify_matching()` check exists to
prevent exactly this.

**Video is slightly shorter than expected** - correct behaviour.
Transitions overlap clips, so each join shortens total duration by the
transition duration. Three clips with 0.3s transitions loses 0.6s.

**Debugging a bad render** - add `--keep-work` to keep intermediate
files and inspect each stage.
