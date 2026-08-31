"""
Reads your marked timestamps file.

Format (one clip per line):
    START  END  LABEL

Example:
    # title: THIS AMBUSH NEARLY ENDED ME
    4:32   4:48   gunfight    "THEY HAD ME PINNED"
    12:10  12:25  chase       "NO WAY OUT"
    19:03  19:20  explosion

A clip may end with a quoted caption. It is shown in the top zone for
that clip's whole time on screen, so the frame is not empty once the
opening hook has gone. Captions are optional, per clip.

Blank lines and lines starting with # are ignored, except "# title:",
which sets the hook text burned across the top of the video.

The title lives here, next to the timestamps, because this file is the
one place you describe THIS recording. A title has to match what is
actually on screen, and nothing else in the pipeline knows that - the
series preset only knows how the video should look.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_utils import timestamp_to_seconds


@dataclass
class Clip:
    index: int
    start: float
    end: float
    label: str
    caption: str = None     # optional on-screen line for this clip
    score: float = 0.0      # filled in by hook detection
    path: Path = None       # filled in after cutting

    @property
    def duration(self):
        return self.end - self.start


def parse_title(path):
    """
    Read the "# title:" directive out of a clips file, or None.

    Kept separate from parse_clips_file so the timestamp parsing keeps
    its existing signature and callers opt in to the title.
    """
    path = Path(path)
    if not path.exists():
        return None

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        body = line.lstrip("#").strip()
        if body.lower().startswith("title:"):
            title = body[len("title:"):].strip()
            if title:
                return title
    return None


def parse_clips_file(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Clips file not found: {path}\n"
            f"Create it with lines like:  4:32  4:48  gunfight"
        )

    clips = []
    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Optional caption in double quotes at the end of the line. Pulled
        # off before splitting so spaces inside it do not become columns.
        caption = None
        quoted = re.search(r'"([^"]*)"\s*$', line)
        if quoted:
            caption = quoted.group(1).strip() or None
            line = line[:quoted.start()].strip()

        parts = line.split()
        if len(parts) < 2:
            raise ValueError(
                f"Line {line_no} in {path.name} is malformed: '{raw}'\n"
                f"Expected: START END [LABEL]"
            )

        start = timestamp_to_seconds(parts[0])
        end = timestamp_to_seconds(parts[1])
        label = parts[2] if len(parts) > 2 else "clip"

        if end <= start:
            raise ValueError(
                f"Line {line_no}: end time ({parts[1]}) must be after "
                f"start time ({parts[0]})"
            )

        clips.append(Clip(index=len(clips) + 1, start=start, end=end,
                          label=label, caption=caption))

    if not clips:
        raise ValueError(f"No clips found in {path}. Is the file empty?")

    return clips
