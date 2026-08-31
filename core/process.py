"""
Cut + normalize + color grade + vertical conversion + reaction cam.

All of it happens in ONE ffmpeg pass per clip. This matters: every extra
encode degrades quality, so we build one filter chain instead of writing
an intermediate file at each step.

The 9:16 arrangement depends on the layout mode in the series preset -
core/layout.py owns those numbers, this module just renders them.
"""

import random

import config
from . import layout
from .ffmpeg_utils import run_ffmpeg, probe, video_quality_args


def clip_plan(preset):
    """The layout geometry for a clip rendered with this series preset."""
    return layout.plan(
        preset.get("layout"),
        preset.get("gameplay_height"),
        preset.get("facecam_height"),
    )


def build_filter_chain(preset, plan):
    """
    Full video filter chain for one clip: grade, frame rate, then the
    9:16 arrangement for this layout.
    Order matters: grade first, then resize, then set timebase.
    """
    W, H = config.WIDTH, config.HEIGHT
    parts = []

    # 1. Colour grade
    if preset.get("lut"):
        lut_path = config.LUT_DIR / preset["lut"]
        if lut_path.exists():
            parts.append(f"lut3d='{lut_path}'")
    if preset.get("eq"):
        parts.append(f"eq={preset['eq']}")

    # 2. Frame rate, must be set before xfade will accept the clip
    parts.append(f"fps={config.FPS}")
    prefix = ",".join(parts) + ","

    # 3. Pixel format and timebase, required for joining
    tail = "format=yuv420p,settb=AVTB"

    band_y, band_h = plan["band"]

    if plan["mode"] == config.LAYOUT_FACECAM_TOP:
        _, cam_h = plan["cam_zone"]
        # The strip is blurred gameplay. It only shows through if the
        # reaction file is missing - the cam overlays it otherwise - so
        # a missing reaction degrades to filler instead of a black bar.
        return (
            f"{prefix}split=2[bg][fg];"
            f"[bg]scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{cam_h},boxblur=25:4[strip];"
            f"[fg]scale={W}:{band_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{band_h}[gp];"
            f"[strip][gp]vstack,{tail}"
        )

    if preset.get("vertical_mode") == "crop":
        # Crop the middle. Fills the frame fully but loses the sides.
        # Good for close combat, bad for driving or landscape shots.
        return (f"{prefix}scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},{tail}")

    # blur_band: gameplay punched in past full width so it fills
    # GAMEPLAY_HEIGHT of the frame. A plain scale=W:-2 fits the whole 16:9
    # frame into the width and only fills 32% of the height, leaving two
    # thirds as blurred filler that reads as reposted landscape video.
    return (
        f"{prefix}split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=25:4[blurred];"
        # increase-then-crop IS the punch in: scale until both dimensions
        # cover the target, then trim the overhanging sides.
        f"[fg]scale={W}:{band_h}:force_original_aspect_ratio=increase,"
        f"crop={W}:{band_h}[front];"
        # Pinned to layout's band_y, NOT centred with (H-h)/2. The band
        # sits centred in the usable height, higher than the centre of the
        # full frame, and the cam is placed from the same numbers.
        f"[blurred][front]overlay=(W-w)/2:{band_y},{tail}"
    )


def list_reaction_videos():
    """
    Every reaction variant on disk: assets/reactions/reactions_*.<ext>.

    The prefix matters. It keeps variants apart from anything else living
    in that folder, so leftover single-emotion recordings are ignored
    rather than being picked as if they were a full sequence.
    """
    if not config.REACTION_ENABLED or not config.REACTION_DIR.exists():
        return []

    exts = (".mp4", ".mov", ".mkv", ".webm")
    return sorted(
        p for p in config.REACTION_DIR.iterdir()
        if p.suffix.lower() in exts
        and p.stem.startswith(config.REACTION_VARIANT_PREFIX)
    )


def segments_need_seconds():
    """How long a variant must be to contain every segment."""
    if not config.REACTION_SEGMENTS:
        return 0.0
    return max(start + dur for start, dur in config.REACTION_SEGMENTS.values())


def usable_reaction_videos():
    """
    Variants long enough to hold the whole segment table.

    REACTION_SEGMENTS is shared by every variant, so a short one cannot
    satisfy it - the later segments would read past its end. Rejecting it
    here, once, beats failing partway through a render on whichever clip
    happened to want the last emotion.
    """
    needed = segments_need_seconds()
    usable = []
    for path in list_reaction_videos():
        have = probe(path)["duration"]
        if have + 0.01 < needed:
            print(f"  ! skipping {path.name}: {have:.1f}s long but the "
                  f"segment table runs to {needed:.1f}s. Every variant must "
                  f"hold its reactions at the same timestamps.", flush=True)
            continue
        usable.append(path)
    return usable


def pick_reaction_video(rng=None):
    """
    One usable variant at random, or None if there are none.

    Call this ONCE per video and reuse the result for every clip. Picking
    per clip would change costume mid-short, which reads as broken
    editing rather than as variety.
    """
    variants = usable_reaction_videos()
    if not variants:
        return None
    return (rng or random).choice(variants)


def reaction_segment(label):
    """
    (name, (start, duration)) of the reaction for this clip label, or
    (name, None) when that emotion has no segment defined.

    With REACTION_MATCH_LABELS off, every clip gets DEFAULT_REACTION -
    label matching is only worth having when the recording really does
    hold distinct emotions at known timestamps.
    """
    if config.REACTION_MATCH_LABELS:
        name = config.REACTION_MAP.get(label, config.DEFAULT_REACTION)
    else:
        name = config.DEFAULT_REACTION
    return name, config.REACTION_SEGMENTS.get(name)


def build_reaction_filter(plan, segment):
    """
    Cut one reaction out of the variant file and place it where the
    layout says. Returns (filter_chain, overlay_x, overlay_y).

    "strip"  - full width band across the top.
    "bubble" - portrait bubble with a border, in the bottom zone.

    The segments are short - about a second each - so the clip is looped
    after scaling. Scaling first keeps the loop buffer small: it holds
    raw frames, and buffering them at source resolution would cost
    hundreds of megabytes per clip.
    """
    start, seg_dur = segment
    frames = max(1, int(round(seg_dur * config.FPS)))
    bias = config.REACTION_FACE_BIAS

    # trim to the segment, then rebase timestamps to zero so the loop and
    # the overlay both start counting from the beginning of the clip.
    head = (f"[1:v]trim=start={start:.3f}:duration={seg_dur:.3f},"
            f"setpts=PTS-STARTPTS,fps={config.FPS},")
    # loop=-1 repeats forever; the output -t and the overlay enable bound it.
    loop = (f"loop=loop=-1:size={frames}:start=0,"
            f"setpts=N/FRAME_RATE/TB,settb=AVTB[cam]")

    zone_y, zone_h = plan["cam_zone"]

    if plan["cam_style"] == "strip":
        w, h = config.WIDTH, zone_h
        aspect = w / h
        z = config.REACTION_ZOOM
        cw = f"min(iw,ih*{aspect:.4f})/{z}"
        ch = f"min(ih,iw/{aspect:.4f})/{z}"
        chain = (
            f"{head}"
            f"crop='{cw}':'{ch}':'(iw-{cw})/2':'(ih-{ch})*{bias}',"
            f"scale={w}:{h},{loop}"
        )
        return chain, "0", zone_y

    a = config.REACTION_ASPECT
    b = config.REACTION_BORDER
    cam_h = config.REACTION_HEIGHT
    total_h = cam_h + 2 * b

    if total_h > zone_h:
        # Shrink rather than overflow into the covered strip, but say so -
        # silently resizing is how a layout drifts without anyone noticing.
        cam_h = max(120, zone_h - 2 * b - 20)
        total_h = cam_h + 2 * b
        print(f"  ! REACTION_HEIGHT {config.REACTION_HEIGHT} does not fit the "
              f"{zone_h}px visible bottom zone, using {cam_h}. Lower "
              f"GAMEPLAY_HEIGHT or SAFE_BOTTOM to make room.", flush=True)

    y = zone_y + (zone_h - total_h) // 2

    # min() keeps the crop window inside the source whatever its aspect,
    # so an odd-sized reaction recording degrades instead of erroring.
    # Dividing by the zoom takes a smaller window of the same shape, which
    # trims the sides and enlarges the face without resizing the bubble.
    z = config.REACTION_ZOOM
    cw = f"min(iw,ih*{a})/{z}"
    ch = f"min(ih,iw/{a})/{z}"

    chain = (
        f"{head}"
        f"crop='{cw}':'{ch}':'(iw-{cw})/2':'(ih-{ch})*{bias}',"
        f"scale=-2:{cam_h},"
        f"pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:{config.REACTION_BORDER_COLOR},"
        f"{loop}"
    )
    return chain, "(W-w)/2", y


def check_reaction_source(video, info, segment, plan):
    """
    Warn about a reaction source that will render wrong. All of these
    still produce a video, which is exactly why they need saying out loud.
    """
    if plan["cam_style"] == "strip" and info["height"] > info["width"]:
        _, cam_h = plan["cam_zone"]
        rows = info["width"] / (config.WIDTH / cam_h)
        print(f"  ! {video.name} is portrait ({info['width']}x{info['height']}). "
              f"A full width strip keeps only {rows / info['height']:.0%} of it "
              f"- roughly an eyes-only band. Record in landscape (16:9) for "
              f"the facecam_top layout.", flush=True)

    start, seg_dur = segment
    if start + seg_dur > info["duration"] + 0.01:
        raise ValueError(
            f"Reaction segment {start:.1f}s-{start + seg_dur:.1f}s runs past "
            f"the end of {video.name} ({info['duration']:.1f}s).\n"
            f"Fix REACTION_SEGMENTS in config.py - every variant has to "
            f"hold its reactions at the same timestamps."
        )


def process_clip(source, clip, preset, out_path, reaction_video=None):
    """
    Cut one clip out of the raw recording and fully normalize it.

    reaction_video is one variant chosen for the whole video by
    pick_reaction_video(); the segment inside it comes from the clip's
    label.

    Note: -ss is placed BEFORE -i. That makes it an input seek, so ffmpeg
    jumps to the nearest keyframe instead of decoding the whole file from
    the start just to throw it away. On a 24 GB recording a 50 minute seek
    goes from minutes to seconds.

    It is still frame accurate because we re-encode: ffmpeg decodes from
    that keyframe and discards frames until the exact timestamp. The
    "-ss must come after -i" advice only applies to stream copy, where
    there is no decode step to discard with.

    -t, not -to: an input seek rebases output timestamps to zero, so -to
    would be measured from that new zero and encode the entire rest of
    the recording.
    """
    plan = clip_plan(preset)
    vf = build_filter_chain(preset, plan)

    name, segment = reaction_segment(clip.label)
    use_cam = bool(reaction_video and segment)
    if reaction_video and not segment:
        print(f"  ! no REACTION_SEGMENTS entry for '{name}' "
              f"(label '{clip.label}') - skipping the reaction cam.", flush=True)

    # -t goes after EVERY -i. Options placed before an -i attach to that
    # input, so putting -t between the two inputs would trim the reaction
    # clip and leave the output length unbounded.
    args = ["-ss", f"{clip.start:.3f}", "-i", str(source)]
    if use_cam:
        args += ["-i", str(reaction_video)]
    args += ["-t", f"{clip.duration:.3f}"]

    if use_cam:
        info = probe(reaction_video)
        check_reaction_source(reaction_video, info, segment, plan)
        cam_chain, cam_x, cam_y = build_reaction_filter(plan, segment)

        enable = ""
        if plan["cam_style"] != "strip" and config.REACTION_DISPLAY_SECONDS:
            # A window only when one is asked for. REACTION_DISPLAY_SECONDS
            # of None means the cam is part of the format and stays up for
            # the whole clip; the strip always does.
            seconds = min(config.REACTION_DISPLAY_SECONDS, clip.duration)
            enable = f":enable='between(t,0,{seconds:.3f})'"

        chain = (
            f"[0:v]{vf}[base];"
            f"{cam_chain};"
            # eof_action=pass so the gameplay keeps running if the cam
            # ever runs dry, instead of freezing or ending the output.
            f"[base][cam]overlay={cam_x}:{cam_y}:eof_action=pass{enable},"
            f"format=yuv420p,settb=AVTB[v]"
        )
    else:
        chain = f"[0:v]{vf}[v]"

    args += [
        "-filter_complex", chain,
        "-map", "[v]",
    ]

    # Audio: force identical spec on every clip. Mismatched channel counts
    # cause sync drift that ffmpeg will not warn you about. The reaction
    # cam's own audio is deliberately not mapped - a third source would
    # have to be folded into the sidechain ducking in audio.py.
    args += [
        "-map", "0:a?",
        "-ac", str(config.AUDIO_CHANNELS),
        "-ar", str(config.SAMPLE_RATE),
        "-c:a", config.AUDIO_CODEC,
        "-b:a", config.AUDIO_BITRATE,
    ]

    args += [
        # Codec-aware: -crf is meaningless to nvenc. See
        # ffmpeg_utils.video_quality_args().
        *video_quality_args(config.VIDEO_CODEC, config.PRESET,
                            config.QUALITY, config.MAXRATE, config.BUFSIZE),
        str(out_path),
    ]

    run_ffmpeg(args, description=f"processing clip {clip.index} ({clip.label})")
    return out_path


def ensure_audio(path, out_path, duration):
    """
    Add a silent audio track if a clip has none.
    A missing audio stream crashes the concat filter, because it expects
    the same number of streams in every segment.
    """
    args = [
        "-i", str(path),
        "-f", "lavfi",
        "-i", f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo",
        "-shortest",
        "-c:v", "copy",
        "-c:a", config.AUDIO_CODEC,
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    run_ffmpeg(args, description="adding silent audio track")
    return out_path
