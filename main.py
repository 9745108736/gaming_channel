#!/usr/bin/env python3
"""
GameClip - gameplay highlight automation

Usage:
    python main.py raw/session01.mp4 clips.txt
    python main.py raw/session01.mp4 clips.txt --series vehicle_test
    python main.py raw/session01.mp4 clips.txt --name "vehicle_test_04"

Output goes to output/<date>_<name>/ containing:
    video.mp4
    thumbnail.png
    seo.txt
    clips_used.txt
"""

import argparse
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import config
from core import audio as audio_mod
from core import export as export_mod
from core import hook as hook_mod
from core import join as join_mod
from core import process as process_mod
from core.clips import parse_clips_file, parse_title
from core.ffmpeg_utils import FFmpegError, probe


def log(step, message):
    print(f"[{step}] {message}", flush=True)


def build_video(source, clips_file, series="default", name=None,
                keep_work=False, title=None):
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Recording not found: {source}")

    preset = config.SERIES.get(series)
    if preset is None:
        available = ", ".join(config.SERIES.keys())
        raise ValueError(f"Unknown series '{series}'. Available: {available}")

    # Most specific source of truth first. The series fallback is last
    # because a preset only knows how the video should LOOK - it cannot
    # know what happens in the footage, and a hook that misdescribes the
    # clip costs more retention than no hook at all.
    title = title or parse_title(clips_file) or preset.get("hook_text")

    clips = parse_clips_file(clips_file)
    total_clip_time = sum(c.duration for c in clips)
    log("input", f"{len(clips)} clips, {total_clip_time:.1f}s total, series '{series}'")

    # Sanity check against the source length
    source_info = probe(source)
    for clip in clips:
        if clip.end > source_info["duration"]:
            raise ValueError(
                f"Clip {clip.index} ends at {clip.end:.1f}s but the recording "
                f"is only {source_info['duration']:.1f}s long."
            )

    # Output folder
    name = name or f"{series}_{int(time.time()) % 100000}"
    out_dir = config.OUTPUT_DIR / f"{date.today().isoformat()}_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    work = config.WORK_DIR / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    started = time.time()

    try:
        # --- 1. Cut + normalize + grade + vertical, one pass each ---
        # One reaction variant for the WHOLE video. Picking per clip would
        # change costume mid-short, which reads as broken editing.
        reaction_video = process_mod.pick_reaction_video()
        if reaction_video:
            log("input", f"reaction variant: {reaction_video.name}")
        else:
            log("input", "no reaction variants found "
                         f"({config.REACTION_DIR}/{config.REACTION_VARIANT_PREFIX}*)")

        for clip in clips:
            clip_path = work / f"clip_{clip.index:02d}.mp4"
            name, seg = process_mod.reaction_segment(clip.label)
            if reaction_video and seg:
                cam = f"{name} @ {seg[0]:.1f}s"
            else:
                cam = "no reaction"
            log("cut", f"clip {clip.index} ({clip.label}) "
                       f"{clip.duration:.1f}s  [{cam}]")
            process_mod.process_clip(source, clip, preset, clip_path,
                                     reaction_video)
            clip.path = clip_path

        # --- 2. Hook detection, strongest clip goes first ---
        if config.HOOK_DETECTION and len(clips) > 1:
            log("hook", "scoring clips by motion and loudness")
            clips = hook_mod.score_clips(clips)
            log("hook", f"opening with clip '{clips[0].label}' "
                        f"(score {clips[0].score:.2f})")

        paths = [c.path for c in clips]

        # --- 3. Join ---
        joined = work / "joined.mp4"
        if preset.get("transition") and len(paths) > 1:
            log("join", f"{len(paths)} clips with '{preset['transition']}' transitions")
            join_mod.join_with_transitions(paths, joined, preset)
        else:
            log("join", f"{len(paths)} clips, hard cuts")
            join_mod.join_hard_cuts(paths, joined, work)

        # --- 4. Music ---
        log("audio", f"adding '{preset.get('music_mood')}' music")
        with_music = work / "with_music.mp4"
        _, track_name = audio_mod.add_music(joined, with_music, preset)
        if track_name:
            log("audio", f"using track: {track_name}")
        else:
            log("audio", "no music found in library, skipping")

        # --- 5. Export ---
        # Where each clip lands on the joined timeline, so its caption
        # is on screen for exactly that clip. Transitions overlap, so
        # this is not a plain cumulative sum - join owns the maths.
        tdur = 0.0
        if preset.get("transition") and len(paths) > 1:
            tdur = float(preset.get("transition_duration", 0.4))
        spans = join_mod.clip_timeline([probe(p)["duration"] for p in paths], tdur)
        captions = [(s, e, c.caption)
                    for (s, e), c in zip(spans, clips) if c.caption]
        if captions:
            log("export", f"{len(captions)} of {len(clips)} clips have captions")

        log("export", "rendering final video")
        final = out_dir / "video.mp4"
        _, hook_note = export_mod.final_export(
            with_music, final,
            title=title,
            series=series,
            # Same plan process.py rendered the clips with, so the text
            # cannot land somewhere the gameplay already is.
            plan=process_mod.clip_plan(preset),
            captions=captions,
        )
        if hook_note == "none":
            # Silently shipping a Short with nothing in the first three
            # seconds is the whole retention problem, so say so loudly
            # rather than reporting "none" like it was a valid outcome.
            log("export", "WARNING: no hook text on this video. Nothing "
                          "holds the viewer in the first 3 seconds.")
            log("export", '         Add a "# title: ..." line to your '
                          'clips file, or pass --title "YOUR HOOK".')
            log("export", "         It must describe THIS clip - a title "
                          "that does not match loses the viewer.")
        else:
            log("export", f"hook text: {hook_note}")

        log("export", "extracting thumbnail")
        export_mod.extract_thumbnail(final, out_dir / "thumbnail.png")

        # --- 6. Reference files ---
        write_clip_log(out_dir / "clips_used.txt", source, clips, series, track_name)
        write_seo_stub(out_dir / "seo.txt", series, clips)

        info = probe(final)
        elapsed = time.time() - started
        log("done", f"{info['duration']:.1f}s video, "
                    f"{info['size'] / 1_000_000:.1f} MB, "
                    f"built in {elapsed:.0f}s")
        log("done", f"folder: {out_dir}")

        return out_dir

    finally:
        if not keep_work and work.exists():
            shutil.rmtree(work, ignore_errors=True)


def write_clip_log(path, source, clips, series, track):
    lines = [
        f"Source recording: {source.name}",
        f"Series: {series}",
        f"Music track: {track or 'none'}",
        "",
        "Clips used (in final order):",
    ]
    for i, clip in enumerate(clips, start=1):
        lines.append(
            f"  {i}. {clip.start:7.2f}s - {clip.end:7.2f}s  "
            f"[{clip.label}]  score={clip.score:.2f}"
        )
    path.write_text("\n".join(lines) + "\n")


def write_seo_stub(path, series, clips):
    """
    Placeholder SEO file. The AI metadata module will fill this
    automatically later - for now you write it yourself.
    """
    labels = ", ".join(sorted({c.label for c in clips}))
    content = f"""TITLE:


DESCRIPTION:


HASHTAGS:


---
Series: {series}
Moments in this video: {labels}
"""
    path.write_text(content)


def main():
    parser = argparse.ArgumentParser(description="Build a short from marked gameplay clips.")
    parser.add_argument("source", help="raw gameplay recording")
    parser.add_argument("clips", help="clips.txt with marked timestamps")
    parser.add_argument("--series", default="default", help="series preset name")
    parser.add_argument("--name", default=None, help="output folder name")
    parser.add_argument("--title", default=None,
                        help="hook text burned into the top of the video")
    parser.add_argument("--keep-work", action="store_true",
                        help="keep intermediate files for debugging")
    args = parser.parse_args()

    try:
        build_video(args.source, args.clips, args.series, args.name,
                    args.keep_work, args.title)
    except (FFmpegError, ValueError, FileNotFoundError) as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
