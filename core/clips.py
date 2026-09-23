"""
Reads your marked timestamps file.

Format (one clip per line):
    START  END  [note]  ["caption"]

Everything after the two timestamps is YOUR NOTE, in plain words. It is
not parsed, matched or truncated - it is handed to the metadata model as
the facts of what happened, and the model turns it into a caption. Write
as much or as little as you like:

    # title: THIS AMBUSH NEARLY ENDED ME
    4:32   4:48   pinned behind the truck, two of them flanking
    12:10  12:25  chase
    19:03  19:20

The last line has no note at all, and that is a complete clip line: the
model writes that caption from the frames alone. A note only exists to
tell it what the frames cannot show - that you were low on health, that
the chopper had been circling for a minute - because it was not there
and you were.

A trailing "quoted string" is an explicit caption instead of a note. Use
it when you want those exact words on screen; CAPTION_MODE decides
whether the model may rewrite them. A note is always rewritten, which is
the point of writing one in rough.

Captions show in the top zone for that clip's whole time on screen, so
the frame is not empty once the opening hook has gone.

Blank lines and lines starting with # are ignored, except directives:

    # title:   <hook text burned across the top of the video>
    # game:    <which game, picks assets/logos/<slug>.png>
    # mission: <in-game mission, stash or location name - the thing
    #           people actually search for>
    # context: <facts only you know: region, what happened.
    #           The metadata AI uses these instead of guessing.>

The title lives here, next to the timestamps, because this file is the
one place you describe THIS recording. A title has to match what is
actually on screen, and nothing else in the pipeline knows that - the
series preset only knows how the video should look.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_utils import timestamp_to_seconds

# Where a caption came from, for the log in clips_used.txt. Worth
# recording because the four are not equally trustworthy: "ai" was
# written from pixels alone and is the one to re-read before upload.
CAPTION_SOURCES = {
    "yours":     "you wrote it, kept as typed",
    "polished":  "you wrote it, the model rewrote the phrasing",
    "note":      "written from your note",
    "ai":        "written from the frames, you wrote nothing",
}


@dataclass
class Clip:
    index: int
    start: float
    end: float
    label: str              # first word of the note; only REACTION_MAP reads it
    note: str = None        # your own words, handed to the metadata model
    caption: str = None     # the on-screen line, yours or written from the note
    caption_source: str = None   # who wrote it: see CAPTION_SOURCES
    score: float = 0.0      # filled in by hook detection
    path: Path = None       # filled in after cutting

    @property
    def duration(self):
        return self.end - self.start


def parse_directive(path, name):
    """
    Read a "# <name>: value" directive out of a clips file, or None.

    Kept separate from parse_clips_file so the timestamp parsing keeps
    its existing signature and callers opt in. parse_clips_file skips
    every "#" line wholesale, so directives are invisible to it and
    adding new ones breaks nothing.

    The match on the name is case insensitive but the VALUE keeps its
    original case, because a title is burned on screen exactly as
    written.
    """
    path = Path(path)
    if not path.exists():
        return None

    prefix = f"{name.lower()}:"
    lines = path.read_text().splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line.startswith("#"):
            continue
        body = line.lstrip("#").strip()
        if not body.lower().startswith(prefix):
            continue

        value = body[len(prefix):].strip()
        if not value:
            return None

        # Continuation lines: "#" followed by two or more spaces. A long
        # context does not fit on one line, and an ordinary comment uses
        # a single space, so the deeper indent keeps the two apart
        # without swallowing the surrounding notes.
        for follow in lines[i + 1:]:
            if not re.match(r"^#[ \t]{2,}\S", follow):
                break
            value += " " + follow.lstrip("#").strip()
        return value

    return None


def parse_title(path):
    """The "# title:" directive - the hook burned across the top."""
    return parse_directive(path, "title")


def parse_context(path):
    """
    The "# context:" directive - facts only the owner knows.

    Region, mission, what actually happened, which part of a series.
    The metadata model can read pixels but cannot know that a dark
    forest road is Henbane River, so anything it is not told it would
    have to guess - and a confidently wrong location in the description
    is the same bait problem as a title that misdescribes the clip.
    """
    return parse_directive(path, "context")


def parse_mission(path):
    """
    The "# mission:" directive - the in-game mission, stash or location.

    "Vespiary Prepper Stash", "Rye & Sons Aviation". This is the highest
    value search term a gaming video has: people type the mission name
    into YouTube, not "gameplay". Nothing in the frames spells it out,
    so it has to come from you.
    """
    return parse_directive(path, "mission")


def parse_game(path):
    """
    The "# game:" directive - which game this recording is from.

    Picks the logo out of assets/logos/ and tells the metadata model
    what it is looking at instead of leaving it to guess from frames.
    """
    return parse_directive(path, "game")


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
                f"Expected: START END [note] [\"caption\"]"
            )

        start = timestamp_to_seconds(parts[0])
        end = timestamp_to_seconds(parts[1])

        # EVERYTHING after the timestamps, not parts[2]. Taking one token
        # silently threw away the rest of the line: "fire to helicoptor"
        # became "fire", and since the note is what the metadata model is
        # told about the clip, the detail you typed never reached the one
        # thing that would have used it.
        note = " ".join(parts[2:]) or None
        # Still a single word, because REACTION_MAP is a dict lookup. It
        # is the only thing that reads this; everything else wants note.
        label = parts[2] if len(parts) > 2 else "clip"

        if end <= start:
            raise ValueError(
                f"Line {line_no}: end time ({parts[1]}) must be after "
                f"start time ({parts[0]})"
            )

        clips.append(Clip(index=len(clips) + 1, start=start, end=end,
                          label=label, note=note, caption=caption,
                          caption_source="yours" if caption else None))

    if not clips:
        raise ValueError(f"No clips found in {path}. Is the file empty?")

    return clips
