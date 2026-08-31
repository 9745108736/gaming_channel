"""
Thin wrapper around ffmpeg / ffprobe.
Every ffmpeg call in this project goes through run_ffmpeg() so errors
are reported in one consistent place.
"""

import json
import subprocess
from pathlib import Path


class FFmpegError(Exception):
    pass


def run_ffmpeg(args, description=""):
    """Run an ffmpeg command. args is a list, without the leading 'ffmpeg'."""
    # -loglevel warning, not error. ffmpeg reports genuinely broken
    # settings as warnings and still exits 0: an option that never
    # reached any stream, a filter silently falling back. At "error"
    # the run looks clean while producing wrong output - that is how
    # -crf sat ignored by nvenc, capping every render at ~2 Mbps.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed{' during ' + description if description else ''}\n"
            f"Command: {' '.join(cmd)}\n"
            f"Error: {result.stderr.strip()}"
        )

    report_warnings(result.stderr, description)
    return result


def probe(path):
    """
    Return video properties as a dict.
    Always probe before joining - mismatched files are the #1 cause of
    broken output that plays in VLC but breaks on mobile.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
        "-show_entries", "format=duration,size",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    info = {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "size": int(data.get("format", {}).get("size", 0)),
        "has_video": False,
        "has_audio": False,
        "width": None,
        "height": None,
        "fps": None,
        "channels": None,
        "sample_rate": None,
    }

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            info["has_video"] = True
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
            rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = rate.split("/")
                info["fps"] = round(int(num) / int(den), 3) if int(den) else None
            except (ValueError, ZeroDivisionError):
                info["fps"] = None
        elif stream.get("codec_type") == "audio":
            info["has_audio"] = True
            info["channels"] = stream.get("channels")
            info["sample_rate"] = stream.get("sample_rate")

    return info


def timestamp_to_seconds(ts):
    """Accept '4:32', '04:32', '1:04:32' or '272' and return float seconds."""
    ts = str(ts).strip()
    if ":" not in ts:
        return float(ts)

    parts = [float(p) for p in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Bad timestamp: {ts}")


# Warnings ffmpeg emits on healthy runs. Nothing actionable, and muting
# the whole warning level to hide them is what let the -crf problem stay
# invisible in the first place.
_WARNING_NOISE = (
    "Setting vsync/fps_mode",
    "deprecated pixel format used",
    "VBV buffer size not set",
    # mp4 metadata atom the concat demuxer retries and recovers from
    # on every join. Cosmetic, and it fires on every healthy run.
    "UDTA parsing failed retrying raw",
)


def report_warnings(stderr, description="", limit=6):
    """Print ffmpeg warnings from a run that otherwise succeeded."""
    seen = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        if any(noise in line for noise in _WARNING_NOISE):
            continue
        seen.append(line)

    if not seen:
        return

    where = f" during {description}" if description else ""
    for line in seen[:limit]:
        print(f"  ! ffmpeg warning{where}: {line}", flush=True)
    if len(seen) > limit:
        print(f"  ! ...and {len(seen) - limit} more ffmpeg warnings", flush=True)


# Encoders that take -crf. Anything not listed needs its own quality flag,
# and ffmpeg only WARNS when an option never reaches a stream, so the
# wrong flag produces default-bitrate video with no error at all.
_CRF_ENCODERS = {
    "libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1",
}

# NVENC spells constant quality "-cq", and ignores it unless rate control
# is vbr AND the average bitrate is freed with -b:v 0.
_CQ_ENCODERS = {
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
}


def video_quality_args(codec, preset, quality, maxrate=None, bufsize=None):
    """
    Translate one quality number into the flags the codec actually reads.

    This exists because -crf on h264_nvenc does nothing. ffmpeg prints
    "Codec AVOption crf ... has not been used for any stream" as a
    warning, exits 0, and encodes at roughly 2 Mbps no matter what number
    you set. Every render went out at that default.
    """
    args = ["-c:v", codec, "-preset", preset]

    if codec in _CQ_ENCODERS:
        # All three are required: without -rc vbr the -cq value is
        # ignored, and without -b:v 0 the encoder targets its default
        # average bitrate instead of the quality level.
        args += ["-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
        if maxrate:
            args += ["-maxrate", maxrate]
        if bufsize:
            args += ["-bufsize", bufsize]
        # nvenc defaults to Main; High costs nothing at the same bitrate.
        args += ["-profile:v", "high"]

    elif codec in _CRF_ENCODERS:
        args += ["-crf", str(quality)]
        if maxrate:
            args += ["-maxrate", maxrate]
        if bufsize:
            args += ["-bufsize", bufsize]

    else:
        known = ", ".join(sorted(_CRF_ENCODERS | _CQ_ENCODERS))
        raise FFmpegError(
            f"No quality mapping for video codec '{codec}'.\n"
            f"Known codecs: {known}\n"
            f"Add it to _CRF_ENCODERS or _CQ_ENCODERS in ffmpeg_utils.py. "
            f"Guessing here means silently shipping default-bitrate video, "
            f"which is the bug this function exists to prevent."
        )

    return args
