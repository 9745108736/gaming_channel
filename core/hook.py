"""
Hook detection.

Retention on Shorts is decided in the first 2 seconds, so the most
exciting clip should go first regardless of when it happened in your
recording.

This scores each clip on two things:
  - motion:  how much the picture changes frame to frame
  - loudness: average audio level

No AI needed. Just ffmpeg measurements.
"""

import re
import subprocess

import config
from .ffmpeg_utils import FFmpegError


def _run(args):
    """
    Run ffmpeg at -loglevel info and hand back stderr.

    Not run_ffmpeg(): the measurements below are printed by filters at
    INFO level, so this deliberately needs a noisier log than the rest of
    the pipeline, and it reads stderr rather than caring about output.
    """
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats"] + args,
        capture_output=True, text=True,
    )


def measure_motion(path):
    """
    Average frame-to-frame difference. Higher means more action.
    Uses scdet's mafd (mean absolute frame difference) metric.

    metadata=print writes to stderr and is parsed from there. The
    file= variant needs a path inside the filtergraph, and on Windows
    the drive colon breaks the parser - "C:/..." reads as an option
    separator, the whole chain fails, and the metadata file is never
    written. That failure returned 0.0 for every clip.
    """
    result = _run([
        "-i", str(path),
        "-vf", "scdet=threshold=0,metadata=print",
        "-an", "-f", "null", "-",
    ])

    values = [
        float(m.group(1))
        for m in re.finditer(r"lavfi\.scd\.mafd=([\d.]+)", result.stderr)
    ]
    if not values:
        raise FFmpegError(
            f"No motion data from {getattr(path, 'name', path)}.\n"
            f"ffmpeg exited {result.returncode}: "
            f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'no output'}"
        )
    return sum(values) / len(values)


def measure_loudness(path):
    """
    Average volume as a 0-1 score, louder is higher.

    Deliberately mean_volume, not max_volume. Peak saturates at 0.0 dB on
    essentially every gameplay clip - one gunshot is enough - so scoring
    on it gave 1.0 for everything and contributed nothing.
    """
    result = _run(["-i", str(path), "-af", "volumedetect", "-f", "null", "-"])

    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    if not match:
        raise FFmpegError(
            f"No loudness data from {getattr(path, 'name', path)}.\n"
            f"ffmpeg exited {result.returncode}"
        )

    # Roughly -30 dB average is quiet, 0 dB is as loud as it gets. Kept
    # absolute rather than normalised across the batch: with two or three
    # clips, normalising would blow a 0.3 dB gap up into a 0-to-1 spread
    # and invent a difference that is not really there.
    db = float(match.group(1))
    return max(0.0, min(1.0, (db + 30.0) / 30.0))


def score_clips(clips):
    """
    Score every clip, then reorder so the highest scoring clip is first.
    Everything after keeps its original order, so your story still flows.

    A measurement failure is reported and leaves the order alone - losing
    the reorder is survivable, silently pretending to have scored is not.
    """
    if not config.HOOK_DETECTION or len(clips) < 2:
        return clips

    measured = []
    for clip in clips:
        try:
            measured.append((clip, measure_motion(clip.path),
                             measure_loudness(clip.path)))
        except FFmpegError as exc:
            print(f"  ! hook scoring failed, keeping the original clip "
                  f"order: {exc}", flush=True)
            return clips

    # Normalize motion against the busiest clip so the scale is relative
    # to this video, not an arbitrary absolute number.
    max_motion = max(m for _, m, _ in measured) or 1.0

    for clip, motion, loud in measured:
        clip.score = (
            config.HOOK_MOTION_WEIGHT * (motion / max_motion)
            + config.HOOK_AUDIO_WEIGHT * loud
        )

    if config.COLD_OPEN_ENABLED:
        # Leave the order alone. The strongest clip becomes the cold open
        # teaser instead of being moved to the front - moving it opened
        # the video well but took the moment out of sequence, so it never
        # arrived again and the running order stopped being chronological.
        return clips

    best = max(clips, key=lambda c: c.score)
    if best is clips[0]:
        return clips

    reordered = [best] + [c for c in clips if c is not best]
    return reordered
