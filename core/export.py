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


def chars_per_line(size, usable=None):
    """
    How many characters of the hook font fit across the frame.

    A fixed character count cannot know how wide the glyphs actually
    are. Measured against Impact at 78px, the old hardcoded 18 filled
    only 59% of the frame width, so long titles lost their tail while
    40% of the line sat empty. FONT_WIDTH_RATIO is the average glyph
    width as a fraction of font size, so this scales with the size.
    """
    usable = usable or (config.WIDTH - 2 * config.TEXT_SIDE_MARGIN)
    return max(6, int(usable / (config.FONT_WIDTH_RATIO * size)))


def fit_text(text, size, max_lines, zone_h, min_size=None):
    """
    Wrap text so that EVERY word survives, shrinking the font to make it.

    Returns (lines, fontsize).

    The old behaviour dropped whatever did not fit and warned about it,
    which still shipped a hook ending mid-sentence. Shrinking instead
    means a long title just gets smaller, which is recoverable; losing
    "SURVIVE PART 2" off the end is not.

    Stops shrinking at min_size - past that the text is too small to
    read on a phone, and letting it run away would trade one silent
    failure for another.
    """
    min_size = min_size or config.TEXT_MIN_SIZE
    words = str(text).split()
    if not words:
        return [], size

    while True:
        lines = _greedy_fill(words, chars_per_line(size))
        # 1.18 leaves room for line spacing and descenders.
        block_h = len(lines) * size * 1.18
        if (len(lines) <= max_lines and block_h <= zone_h) or size <= min_size:
            return lines, size
        size = max(min_size, int(size * 0.9))


def escape_filter_path(path):
    """
    Windows paths inside a filtergraph need the drive colon escaped, or
    the parser reads "C:" as the end of the option value and drawtext
    fails to load the file.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def _drawtext(text, y_expr, size, wrap, max_lines, border, enable, what,
              zone_h=None):
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

    original_size = size
    zone_h = zone_h or config.HEIGHT

    # Fit by measured width, shrinking the font rather than dropping the
    # tail. Losing the end of a title ships a hook that stops mid
    # sentence; smaller text is merely smaller.
    lines, size = fit_text(str(text).upper(), size, max_lines, zone_h)
    if not lines:
        return None, None

    if size < original_size:
        print(f"  ! {what} shrunk to {size}px to fit "
              f"({len(lines)} lines, nothing dropped)", flush=True)

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


def hook_windows(duration=None):
    """
    When the title is on screen: (start, end) pairs.

    Opening hook, plus a reprise over the closing seconds so a viewer who
    joined mid-video still learns what this is. Returns just the opener
    when the video is too short to carry both without them colliding.
    """
    windows = [(0.0, float(config.HOOK_TEXT_DURATION))]
    outro = float(config.HOOK_OUTRO_SECONDS or 0)
    if duration and outro > 0:
        start = duration - outro
        # Needs to clear the opener with real gameplay in between,
        # otherwise the title just blinks off and straight back on.
        if start > config.HOOK_TEXT_DURATION + 1.0:
            windows.append((start, duration))
    return windows


def hook_enable(duration=None):
    """
    ffmpeg enable expression for those windows.

    between() yields 1 or 0 and the windows never overlap, so summing
    them acts as OR.
    """
    return "+".join(
        f"between(t,{a:.3f},{b:.3f})" for a, b in hook_windows(duration)
    )


def build_hook_drawtext(title, plan, duration=None):
    """The hook: big, at the start and again over the closing seconds."""
    return _drawtext(
        title, text_y_expr(plan),
        size=config.HOOK_TEXT_SIZE,
        wrap=config.HOOK_TEXT_WRAP,
        max_lines=config.HOOK_TEXT_MAX_LINES,
        border=config.HOOK_TEXT_BORDER,
        enable=hook_enable(duration),
        what="hook text",
        zone_h=plan["text"][1],
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
        zone_h=plan["text"][1],
    )


def find_game_logo(game):
    """
    assets/logos/<game-slug>.png for the game named in the clips file.

    "Far Cry 5" looks for far_cry_5.png. Returns None when there is no
    game or no matching file - a missing logo is skipped with a log
    line, the same way a missing reaction clip or music track is.
    """
    if not game or not config.GAME_LOGO_ENABLED:
        return None

    slug = slugify(game)
    for ext in (".png", ".webp"):
        path = config.LOGO_DIR / f"{slug}{ext}"
        if path.exists():
            return path
    return None


def final_export(video_path, out_path, title=None, series=None,
                 plan=None, captions=None, game=None):
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

    # The title reprises over the closing seconds, so the export needs to
    # know how long the video actually is.
    duration = probe(video_path)["duration"]
    windows = hook_windows(duration) if has_hook else []
    outro_start = windows[1][0] if len(windows) > 1 else None

    vfilters, tempfiles = [], []

    if config.CAPTION_ENABLED:
        for start, end, text in (captions or []):
            if not text:
                continue
            # The hook owns the top zone at both ends, so hold the opening
            # caption back and cut the closing one short rather than
            # stacking two lots of text in a zone that fits one.
            if has_hook:
                start = max(start, config.HOOK_TEXT_DURATION)
                if outro_start is not None:
                    end = min(end, outro_start)
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
        f, tmp = build_hook_drawtext(title, plan, duration)
        if f:
            vfilters.append(f)
            tempfiles.append(tmp)
            note = "drawtext"
    if note != "none" and outro_start is not None:
        note += f" (+reprise at {outro_start:.1f}s)"

    logo = find_game_logo(game)
    if logo is not None:
        note += f" +logo {logo.name}"

    args = ["-i", str(video_path)]

    # Input indexes are positional, so they are counted rather than
    # written literally: the logo is [1:v] on its own but [2:v] when a
    # hook PNG is also present.
    segments = []
    stream = "[0:v]"
    next_input = 1

    if png is not None:
        args += ["-i", str(png)]
        # h is the overlay's own height, so the graphic centres in the
        # top zone without having to probe the PNG first.
        ty, th = plan["text"]
        segments.append(f"{stream}[{next_input}:v]overlay=(W-w)/2:"
                        f"{ty}+({th}-h)/2:enable='{hook_enable(duration)}'"
                        f"[hooked]")
        stream = "[hooked]"
        next_input += 1

    if logo is not None:
        args += ["-i", str(logo)]
        width = int(config.WIDTH * config.LOGO_VIDEO_WIDTH) // 2 * 2
        # Scaled to a fixed width so every game's logo lands the same
        # size whatever the source file happens to be. -2 keeps the
        # height even, which yuv420p requires.
        segments.append(f"[{next_input}:v]scale={width}:-2[logo]")
        # Only the opening window, not hook_enable's closing reprise:
        # this is a title card, and by the end the viewer knows the game.
        opening = hook_windows(duration)[0]
        segments.append(
            f"{stream}[logo]overlay=(W-w)/2:{plan['logo_y']}:"
            f"enable='between(t,{opening[0]:.3f},{opening[1]:.3f})'[logoed]"
        )
        stream = "[logoed]"
        next_input += 1

    if segments:
        if vfilters:
            segments.append(f"{stream}{','.join(vfilters)}[v]")
        else:
            # Nothing follows the overlays, so relabel the last one's
            # output to [v] instead of appending an empty filter.
            segments[-1] = segments[-1][:-len(stream)] + "[v]"
        # -map 0:a? is required here. The -vf branch below relies on
        # ffmpeg's default stream selection to carry the audio; once we
        # switch to -filter_complex it stops doing that, and the audio
        # would be dropped without a word.
        args += ["-filter_complex", ";".join(segments),
                 "-map", "[v]", "-map", "0:a?"]
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


def render_thumbnail(source, timestamp, label, preset, out_path, game=None):
    """
    Build the thumbnail from the RAW recording at one chosen moment.

    Not from the finished video. That frame carries blur bands, the
    reaction cam and whatever caption happened to be up, all of which
    eat pixels at channel-grid size. Cropping the raw 16:9 full bleed to
    9:16 does lose the sides, but a thumbnail is one moment rather than
    the whole scene, so filling the frame beats showing everything.

    The label is drawn much larger than the video's own hook text: in a
    grid the hook is far too small to read.
    """
    W, H = config.WIDTH, config.HEIGHT

    parts = []
    if preset.get("eq"):
        # Same grade as the video, or the thumbnail misrepresents the look.
        parts.append(f"eq={preset['eq']}")
    parts.append(f"scale={W}:{H}:force_original_aspect_ratio=increase")
    parts.append(f"crop={W}:{H}")

    tmp = None
    font = Path(config.HOOK_TEXT_FONT)
    if label and font.exists():
        band = int(H * 0.34)
        # Same fitting as the video text: shrink rather than drop words.
        lines, label_size = fit_text(str(label).upper(),
                                     config.THUMBNAIL_LABEL_SIZE,
                                     config.THUMBNAIL_LABEL_MAX_LINES,
                                     band - 40)
        if config.THUMBNAIL_SCRIM:
            # Darken behind the text so it stays readable over a bright sky.
            parts.append(f"drawbox=0:0:{W}:{band}:"
                         f"color=black@{config.THUMBNAIL_SCRIM}:t=fill")

        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8", newline="")
        handle.write("\n".join(lines))
        handle.close()
        tmp = handle.name
        parts.append(
            f"drawtext=fontfile='{escape_filter_path(font)}'"
            f":textfile='{escape_filter_path(tmp)}'"
            f":fontsize={label_size}"
            f":fontcolor=white"
            f":borderw={config.THUMBNAIL_LABEL_BORDER}:bordercolor=black"
            f":line_spacing=14:text_align=C"
            f":x=(w-text_w)/2:y=({band}-text_h)/2"
        )

    args = ["-ss", f"{timestamp:.3f}", "-i", str(source)]

    logo = find_game_logo(game)
    if logo is not None:
        args += ["-i", str(logo)]

    # -frames:v goes after EVERY -i. Options before an -i attach to that
    # input, so with the logo added this would be read as "one frame of
    # the logo" and ffmpeg rejects it outright. Same rule as -t in
    # process.process_clip().
    args += ["-frames:v", "1"]

    if logo is None:
        args += ["-vf", ",".join(parts)]
    else:
        # A second input means -filter_complex; -vf takes only one.
        width = int(W * config.LOGO_THUMBNAIL_WIDTH) // 2 * 2
        args += [
            "-filter_complex",
            f"[0:v]{','.join(parts)}[base];"
            f"[1:v]scale={width}:-2[logo];"
            # Bottom centre: the label scrim owns the top third, and the
            # action sits in the middle, so the foot of the frame is the
            # only place a logo does not cover something that matters.
            f"[base][logo]overlay=(W-w)/2:H-h-{config.LOGO_THUMBNAIL_MARGIN}[v]",
            "-map", "[v]",
        ]

    args += ["-update", "1", str(out_path)]

    try:
        run_ffmpeg(args, description="rendering thumbnail")
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    return out_path


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
