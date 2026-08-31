"""
Final render, top-zone text and thumbnail.

All top-zone text is drawn here - the opening hook and the per-clip
captions both. Hook detection reorders clips after they are cut, so
process.py cannot know which clip opens the video or where any clip
lands on the finished timeline. export.py can, and it was already
re-encoding, so none of this adds a pass.
"""

import re
import tempfile
from pathlib import Path

import config
from . import layout
from .ffmpeg_utils import run_ffmpeg, probe, video_quality_args


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def find_hook_overlay(title, series):
    """
    Look for a pre-made hook graphic, most specific first:

      1. assets/overlays/<title-slug>.png   a graphic for this one video
      2. assets/overlays/<series>.png       the series brand plate

    Returns a Path, or None to fall back to drawtext. Pre-made PNGs win
    because the channel's established style uses layered graphics that
    drawtext cannot reproduce.
    """
    if not config.HOOK_TEXT_ENABLED:
        return None

    candidates = []
    if title:
        candidates.append(config.OVERLAY_DIR / f"{slugify(title)}.png")
    if series:
        candidates.append(config.OVERLAY_DIR / f"{slugify(series)}.png")

    for path in candidates:
        if path.exists():
            return path
    return None


def _greedy_fill(words, width):
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and len(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def wrap_title(text, width=None, max_lines=None):
    """
    Break text into short, balanced lines. drawtext does not wrap, so a
    long line would otherwise run off both edges of the frame.

    Filling greedily to a fixed width orphans the last word - "TAKING
    BACK FALL'S / END" - which looks like a mistake on screen. So try
    the fewest lines that fit, balancing each attempt around an even
    target width.
    """
    width = width or config.HOOK_TEXT_WRAP
    max_lines = max_lines or config.HOOK_TEXT_MAX_LINES

    words = str(text).split()
    if not words:
        return []

    longest_word = max(len(w) for w in words)
    total = len(" ".join(words))

    for n_lines in range(1, max_lines + 1):
        # Even split, but never narrower than the longest single word or
        # that word could never be placed.
        target = max(longest_word, -(-total // n_lines))
        lines = _greedy_fill(words, target)
        if len(lines) <= n_lines and max(len(l) for l in lines) <= width:
            return lines

    return _greedy_fill(words, width)[:max_lines]


def escape_filter_path(path):
    """
    Windows paths inside a filtergraph need the drive colon escaped, or
    the parser reads "C:" as the end of the option value and drawtext
    fails to load the file.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def _drawtext(text, y_expr, size, wrap, max_lines, border, enable, what):
    """
    Build one drawtext filter, positioned by the layout plan.
    Returns (filter_string, temp_file_path) or (None, None).

    The text is passed through textfile= rather than text= because
    drawtext values get unescaped twice - once by the filtergraph parser
    and again by the option parser - so an apostrophe or a colon
    silently truncates the line. A file sidesteps escaping completely
    and gives multi-line rendering for free.
    """
    font = Path(config.HOOK_TEXT_FONT)
    if not font.exists():
        print(f"  ! font not found, skipping {what}: {font}", flush=True)
        return None, None

    lines = wrap_title(str(text).upper(), wrap, max_lines)
    if not lines:
        return None, None

    # The line cap can drop the tail of a long line. Say so - text that
    # stops mid sentence is worse than short text, and dropping it
    # silently is how it ships without anyone noticing.
    kept = len(" ".join(lines).split())
    given = len(str(text).split())
    if kept < given:
        dropped = " ".join(str(text).upper().split()[kept:])
        print(f"  ! {what} too long, dropped: {dropped!r}", flush=True)
        print(f"  ! shorten it, or raise the *_MAX_LINES / *_WRAP "
              f"values in config.py", flush=True)

    # newline="" or Python's text mode turns every \n into \r\n on
    # Windows, and drawtext renders the stray carriage return as an
    # extra blank line - the wrapped text comes out double spaced.
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8", newline=""
    )
    handle.write("\n".join(lines))
    handle.close()

    text_filter = (
        f"drawtext=fontfile='{escape_filter_path(font)}'"
        f":textfile='{escape_filter_path(handle.name)}'"
        f":fontsize={size}"
        f":fontcolor={config.HOOK_TEXT_COLOR}"
        f":borderw={border}:bordercolor=black"
        f":line_spacing=12"
        # x centres the text BLOCK; without text_align the lines inside
        # it stay left aligned, so two lines look ragged.
        f":text_align=C"
        f":x=(w-text_w)/2"
        # y comes from layout.plan(), so blur_band centres it in the
        # empty top zone and facecam_top puts it over the gameplay.
        f":y={y_expr}"
        f":enable='{enable}'"
    )
    return text_filter, handle.name


def text_y_expr(plan):
    """
    drawtext y expression centring a text block in the layout's text zone.
    text_h is drawtext's own height variable, resolved at render time.
    """
    ty, th = plan["text"]
    return f"{ty}+({th}-text_h)/2"


def build_hook_drawtext(title, plan):
    """The opening hook: big, and only for the first few seconds."""
    return _drawtext(
        title, text_y_expr(plan),
        size=config.HOOK_TEXT_SIZE,
        wrap=config.HOOK_TEXT_WRAP,
        max_lines=config.HOOK_TEXT_MAX_LINES,
        border=config.HOOK_TEXT_BORDER,
        enable=f"between(t,0,{config.HOOK_TEXT_DURATION})",
        what="hook text",
    )


def build_caption_drawtext(text, plan, start, end):
    """A per-clip caption: smaller, and up for that clip's whole span."""
    return _drawtext(
        text, text_y_expr(plan),
        size=config.CAPTION_SIZE,
        wrap=config.CAPTION_WRAP,
        max_lines=config.CAPTION_MAX_LINES,
        border=config.CAPTION_BORDER,
        enable=f"between(t,{start:.3f},{end:.3f})",
        what=f"caption {text!r}",
    )


def final_export(video_path, out_path, title=None, series=None,
                 plan=None, captions=None):
    """
    Render the delivery file with the hook and any per-clip captions.

    captions is a list of (start, end, text) on the finished timeline -
    core.join.clip_timeline() computes those, because the transition
    overlap means they are not just cumulative durations.

    Returns (out_path, note) describing which hook path was taken.

    -movflags +faststart moves the moov atom to the front of the file.
    MP4 puts it at the end by default, which breaks streaming playback
    and can make uploads fail to preview properly.
    """
    plan = plan or layout.plan()

    png = find_hook_overlay(title, series)
    has_hook = png is not None or bool(title and config.HOOK_TEXT_ENABLED)

    vfilters, tempfiles = [], []

    if config.CAPTION_ENABLED:
        for start, end, text in (captions or []):
            if not text:
                continue
            # The hook owns the top zone for its first seconds, so hold
            # the opening caption back rather than stacking them.
            if has_hook:
                start = max(start, config.HOOK_TEXT_DURATION)
            if end - start < 0.5:
                continue
            f, tmp = build_caption_drawtext(text, plan, start, end)
            if f:
                vfilters.append(f)
                tempfiles.append(tmp)

    note = "none"
    if png is not None:
        note = f"PNG overlay {png.name}"
    elif title and config.HOOK_TEXT_ENABLED:
        f, tmp = build_hook_drawtext(title, plan)
        if f:
            vfilters.append(f)
            tempfiles.append(tmp)
            note = "drawtext"

    args = ["-i", str(video_path)]

    if png is not None:
        args += ["-i", str(png)]
        # h is the overlay's own height, so the graphic centres in the
        # top zone without having to probe the PNG first.
        ty, th = plan["text"]
        chain = (f"[0:v][1:v]overlay=(W-w)/2:{ty}+({th}-h)/2:"
                 f"enable='between(t,0,{config.HOOK_TEXT_DURATION})'")
        chain += f",{','.join(vfilters)}[v]" if vfilters else "[v]"
        args += ["-filter_complex", chain, "-map", "[v]", "-map", "0:a?"]
    elif vfilters:
        args += ["-vf", ",".join(vfilters)]

    args += [
        # Codec-aware: -crf is meaningless to nvenc. See
        # ffmpeg_utils.video_quality_args().
        *video_quality_args(config.VIDEO_CODEC, config.PRESET,
                            config.QUALITY, config.MAXRATE, config.BUFSIZE),
        "-pix_fmt", "yuv420p",
        "-c:a", config.AUDIO_CODEC,
        "-b:a", config.AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(out_path),
    ]

    try:
        run_ffmpeg(args, description="final export")
    finally:
        for tmp in tempfiles:
            Path(tmp).unlink(missing_ok=True)

    return out_path, note


def extract_thumbnail(video_path, out_path, width=1080, height=1920):
    """
    Grab a thumbnail frame.

    The 'thumbnail' filter analyses a batch of frames and picks the most
    representative one, which beats grabbing a fixed timestamp that might
    land on a blur or a fade.
    """
    args = [
        "-i", str(video_path),
        "-vf", f"thumbnail,scale={width}:{height}",
        "-frames:v", "1",
        # -update 1 tells the image2 muxer this is a single file, not a
        # numbered sequence. Without it ffmpeg warns on every run and the
        # behaviour is only correct by accident.
        "-update", "1",
        str(out_path),
    ]
    run_ffmpeg(args, description="extracting thumbnail")
    return out_path
