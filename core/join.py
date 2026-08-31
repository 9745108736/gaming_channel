"""
Join processed clips into one video.

Two methods:
  - demuxer: stream copy, near instant, zero quality loss. Requires all
    inputs to already match exactly (process.py guarantees this).
  - xfade:   re-encodes, slower, but gives smooth transitions.
"""

import config
from .ffmpeg_utils import run_ffmpeg, probe, video_quality_args


def verify_matching(paths):
    """
    Confirm every clip has identical properties before joining.

    This check exists because concat with mismatched files SUCCEEDS but
    produces broken output - frozen frames, missing audio after the first
    clip, or audio drift. It plays fine in VLC and breaks on phones.
    Fail loudly here instead.
    """
    specs = []
    for p in paths:
        info = probe(p)
        specs.append((info["width"], info["height"], info["fps"], info["channels"]))

    first = specs[0]
    for path, spec in zip(paths[1:], specs[1:]):
        if spec != first:
            raise ValueError(
                f"Clip properties do not match, cannot join safely.\n"
                f"  Expected (w,h,fps,channels): {first}\n"
                f"  Got from {path.name}: {spec}"
            )
    return True


def join_hard_cuts(paths, out_path, work_dir):
    """Fast join with no transitions. Stream copy, no re-encode."""
    verify_matching(paths)

    list_file = work_dir / "concat_list.txt"
    lines = [f"file '{p.resolve()}'" for p in paths]
    list_file.write_text("\n".join(lines) + "\n")

    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    run_ffmpeg(args, description="joining clips (hard cuts)")
    return out_path


def join_with_transitions(paths, out_path, preset):
    """
    Join with crossfade transitions using xfade.

    xfade only handles two inputs at a time, so we chain them. Each
    transition overlaps the clips, meaning total duration shrinks by
    (transition_duration) for every join.
    """
    verify_matching(paths)

    if len(paths) == 1:
        args = ["-i", str(paths[0]), "-c", "copy", str(out_path)]
        run_ffmpeg(args, description="single clip passthrough")
        return out_path

    transition = preset.get("transition", "fade")
    tdur = float(preset.get("transition_duration", 0.4))

    durations = [probe(p)["duration"] for p in paths]

    inputs = []
    for p in paths:
        inputs += ["-i", str(p)]

    filters = []
    current_video = "0:v"
    current_audio = "0:a"
    # Offset is where the transition starts, measured on the accumulated
    # timeline. Each join shortens the total by tdur.
    offset = durations[0] - tdur

    for i in range(1, len(paths)):
        v_out = f"v{i}"
        a_out = f"a{i}"

        filters.append(
            f"[{current_video}][{i}:v]"
            f"xfade=transition={transition}:duration={tdur}:offset={offset:.3f}"
            f"[{v_out}]"
        )
        filters.append(
            f"[{current_audio}][{i}:a]"
            f"acrossfade=d={tdur}[{a_out}]"
        )

        current_video = v_out
        current_audio = a_out
        offset += durations[i] - tdur

    args = inputs + [
        "-filter_complex", ";".join(filters),
        "-map", f"[{current_video}]",
        "-map", f"[{current_audio}]",
        # Codec-aware: -crf is meaningless to nvenc. See
        # ffmpeg_utils.video_quality_args().
        *video_quality_args(config.VIDEO_CODEC, config.PRESET,
                            config.QUALITY, config.MAXRATE, config.BUFSIZE),
        "-c:a", config.AUDIO_CODEC,
        "-b:a", config.AUDIO_BITRATE,
        str(out_path),
    ]
    run_ffmpeg(args, description="joining clips with transitions")
    return out_path


def clip_timeline(durations, tdur=0.0):
    """
    Where each clip lands on the joined timeline, as (start, end).

    join_with_transitions overlaps every join by tdur, so clip i starts
    at sum(durations[:i]) - i*tdur rather than at the plain cumulative
    sum. Anything that has to line up with a clip after joining - the
    per-clip captions - needs this exact maths, so it lives next to the
    code that defines it instead of being re-derived elsewhere.
    """
    spans = []
    start = 0.0
    for i, d in enumerate(durations):
        end = start + d
        # Stop a caption when the next transition starts, so it does not
        # hang over the crossfade into the following clip.
        if i < len(durations) - 1:
            end -= tdur
        spans.append((start, end))
        start += d - tdur
    return spans
