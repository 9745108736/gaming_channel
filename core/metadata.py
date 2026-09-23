"""
AI metadata generation.

One call to a vision model, from frames of the cut clips, produces:
  - a caption for each clip
  - a short thumbnail label
  - which frame is the most striking, for the thumbnail
  - the title, description and hashtags for seo.txt

Captions written in clips.txt always win over the model's. Rule 11 in
CLAUDE.md used to forbid generated captions outright, on the grounds
that nothing in the code could see the footage. A vision model reading
actual frames changes that premise - but only for text the owner can
still override, and a caption you wrote is never replaced.

With no API key it degrades instead of failing: the frames and a
ready-to-paste prompt land in the output folder.

No pip dependency - the API is called over plain HTTPS with urllib.
"""

import base64
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

import config
from .ffmpeg_utils import run_ffmpeg, probe

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
              "models/{model}:generateContent")


def api_key():
    """The Gemini key from the environment, or None."""
    return os.environ.get("GEMINI_API_KEY") or None


def sample_clip_frames(clips, out_dir, per_clip=None):
    """
    Sample frames from each cut clip.

    Returns [(clip_index, position, path)], where position is the
    fraction through that clip - enough to re-extract the same moment
    from the raw recording later at full quality.
    """
    per_clip = per_clip or config.METADATA_FRAMES_PER_CLIP
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for ci, clip in enumerate(clips):
        duration = probe(clip.path)["duration"]
        for j in range(per_clip):
            pos = (j + 1) / (per_clip + 1)
            out = out_dir / f"clip{ci + 1:02d}_{j + 1}.jpg"
            run_ffmpeg(
                ["-ss", f"{duration * pos:.3f}", "-i", str(clip.path),
                 "-frames:v", "1", "-q:v", "3", "-update", "1", str(out)],
                description=f"sampling frame for clip {ci + 1}",
            )
            frames.append((ci, pos, out))
    return frames


def build_prompt(frames, clips, title, series, game=None, context=None,
                 mission=None):
    """The instruction text. Frames are referenced by number."""
    lines = [
        "You are writing text for a YouTube Short made from a gamer's own",
        "gameplay recording.",
        "",
        "You will be shown numbered frames. Write only what the frames",
        "actually show. Do not invent kills, vehicles or events that are",
        "not visible. If the frames do not support a claim, leave it out.",
        "",
        "Channel: Malabari Gamer - first person action gameplay shorts.",
        f"Game: {game}." if game else "The game is not stated - identify it "
                                      "from the frames if you can.",
        f"Series preset: {series} (controls the look only, says nothing",
        "about the content).",
        f"Owner's working title: {title or '(none given)'}",
    ]
    if mission:
        lines += [
            "",
            f"Mission / location: {mission}",
            "This is the SEARCH TERM. People type a mission name into YouTube,",
            "not 'gameplay'. It must appear in the TITLE and get its own",
            "hashtag. Never alter or shorten it.",
        ]
    if context:
        lines += [
            "",
            "Facts the owner gave you about this recording. These are true -",
            "use them, especially the location and what happened:",
            f"  {context}",
        ]
    lines += [
        "",
        "NEVER name a specific in-game region, mission or landmark unless the",
        "owner stated it above or it is legible on screen. Guessing wrong is",
        "worse than staying general: describe the setting plainly instead",
        "(a night forest road, a mountain bridge) when you do not know.",
        "",
        "The frames belong to these clips:",
    ]
    for ci, clip in enumerate(clips):
        nums = [str(i + 1) for i, (c, _, _) in enumerate(frames) if c == ci]
        # The whole note, not the one-word label. The note is the only
        # thing in the run that knows what the frames cannot show.
        hint = clip.caption or clip.note
        own = (f'  owner note: "{hint}"' if hint
               else "  (no note - write this one from the frames alone)")
        lines.append(f"  Clip {ci + 1}: frames {', '.join(nums)}{own}")

    lines += [
        "",
        "Return EXACTLY this format and nothing else. One CAPTION line per",
        f"clip, numbered 1 to {len(clips)}:",
        "",
    ]
    if any(c.caption or c.note for c in clips):
        lines += [
            "Where a clip shows an 'owner note', treat it as the FACTS of what",
            "happened - the owner was there and you were not. The note is",
            "rough: it may be shorthand, misspelled or ungrammatical. Keep",
            "every fact in it, fix the wording, and use the frames to sharpen",
            "it into six words or fewer. Never contradict the note, and never",
            "add events it does not mention and the frames do not show.",
            "",
            "Where a clip shows no note, write that caption from its frames",
            "alone, and stay with what is plainly visible.",
            "",
        ]

    for ci in range(len(clips)):
        lines.append(
            f"CAPTION {ci + 1}: <max 6 words. Make the viewer want to keep "
            f"watching clip {ci + 1} - tension, stakes or a question, not a "
            f"flat description of what is on screen. Ground it in the frames: "
            f"never invent danger, kills or near misses that are not visible.>"
        )
    lines += [
        "THUMBNAIL: <3 to 5 words, the single most dramatic thing on offer>",
        "BEST_FRAME: <the number of the most visually striking frame>",
        "TITLE: <one line under 80 characters. If a mission or location was "
        "given above it MUST appear word for word - that is what people "
        "search. A shape that works: '<Game> <Mission Name> - <what "
        "happens>'. Specific, and no clickbait the footage does not "
        "deliver.>",
        "DESCRIPTION: <3 to 4 lines. First line: what specifically happens. "
        "Second: where it takes place - the region or landmark if the owner "
        "gave it or it is on screen, otherwise the setting in plain words. "
        "Third: why it is worth watching to the end. Concrete, not generic - "
        "a line that could describe any clip of this game is wasted.>",
        "HASHTAGS: <8 to 12 space separated tags. Lead with the ones people "
        "actually search: the game, the mission or location name, the region, "
        "and intent words that fit what the frames show - walkthrough, guide, "
        "location, stash, secret. Generic tags like #gaming or #gamerlife are "
        "filler: at most two of those, at the end.>",
    ]
    return "\n".join(lines)


def call_gemini(prompt, frames, key, model=None, timeout=None):
    """
    Send the prompt and numbered frames to Gemini. Returns the reply text.

    Raises RuntimeError carrying the API's own message on failure - the
    error body says what is actually wrong (bad key, retired model,
    quota) and swallowing it would leave you guessing.
    """
    model = model or config.METADATA_MODEL
    timeout = timeout or config.METADATA_TIMEOUT

    parts = [{"text": prompt}]
    for i, (_, _, path) in enumerate(frames, start=1):
        parts.append({"text": f"Frame {i}:"})
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        })

    body = json.dumps({"contents": [{"parts": parts}]}).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_URL.format(model=model) + f"?key={key}",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Gemini returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response: {json.dumps(data)[:400]}")


# Failures that recover on their own. A stalled socket reports no HTTP
# status at all ("read operation timed out", "Server disconnected"), so
# matching only on status codes lets those fall straight through to a
# crash instead of retrying like a 503 does.
_TRANSIENT = (
    "http 429", "http 500", "http 502", "http 503", "http 504",
    "quota", "timed out", "timeout", "deadline_exceeded", "unavailable",
    "disconnected", "connection", "overloaded", "high demand",
    "request failed", "temporarily",
)

# A retired model name never recovers, so the chain moves on immediately
# rather than spending the whole retry budget proving it.
_RETIRED = ("http 404", "not_found", "no longer available")


def _matches(text, needles):
    low = text.lower()
    return any(n in low for n in needles)


def call_with_fallback(prompt, frames, key, models=None, attempts=None):
    """
    Try each model in turn, retrying transient failures on each.

    Three separate failure modes, handled differently because the right
    response to each is different:

      retired (404)   move to the next model at once - retrying a name
                      Google has withdrawn can never succeed.
      transient       retry the SAME model with growing backoff, since
                      overload and timeouts do clear, then move on.
      anything else   raise. A bad key or a malformed request will fail
                      identically on every model in the chain.

    The -lite models sit at the end of the chain but do the real work
    when it matters: they are on a separate quota bucket from the flash
    tier, so they answer while flash is returning "high demand" 503s.
    """
    models = models or [config.METADATA_MODEL, *config.METADATA_FALLBACK_MODELS]
    attempts = attempts or config.METADATA_RETRIES
    last = None

    for index, model in enumerate(models):
        for n in range(1, attempts + 1):
            try:
                reply = call_gemini(prompt, frames, key, model=model)
                if index:
                    print(f"  ! metadata AI answered on fallback model "
                          f"{model}", flush=True)
                return reply
            except RuntimeError as exc:
                last = exc
                text = str(exc)

                if _matches(text, _RETIRED):
                    print(f"  ! {model} is retired, skipping it", flush=True)
                    break
                if not _matches(text, _TRANSIENT):
                    raise
                if n == attempts:
                    break

                # Longer than a plain doubling: an overloaded model needs
                # real seconds to come back, and the jitter stops repeated
                # renders from retrying in lockstep.
                wait = 2 ** n + 8 + random.uniform(0, 1.5)
                print(f"  ! {model} busy (attempt {n}/{attempts}), retrying "
                      f"in {wait:.0f}s: {text[:90]}", flush=True)
                time.sleep(wait)

        if index + 1 < len(models):
            print(f"  ! falling back to {models[index + 1]}", flush=True)

    raise last


def parse_response(text, clip_count):
    """
    Pull the fields out of the reply.

    Returns None when the title is missing: a reply that ignored the
    format is more likely an apology or a refusal than usable text, and
    writing that over the owner's own words would be worse than nothing.
    """
    out = {"captions": {}, "thumbnail": None, "best_frame": None,
           "title": None, "description": [], "hashtags": ""}
    section = None

    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        upper = line.upper()

        m = re.match(r"CAPTION\s+(\d+)\s*:\s*(.+)", line, re.IGNORECASE)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < clip_count:
                out["captions"][idx] = m.group(2).strip().strip('"')
            section = None
            continue

        if upper.startswith("THUMBNAIL:"):
            out["thumbnail"] = line[10:].strip().strip('"')
            section = None
        elif upper.startswith("BEST_FRAME:"):
            digits = re.search(r"\d+", line[11:])
            if digits:
                out["best_frame"] = int(digits.group()) - 1
            section = None
        elif upper.startswith("TITLE:"):
            out["title"] = line[6:].strip()
            section = None
        elif upper.startswith("DESCRIPTION:"):
            rest = line[12:].strip()
            if rest:
                out["description"].append(rest)
            section = "description"
        elif upper.startswith("HASHTAGS:"):
            out["hashtags"] = line[9:].strip()
            section = "hashtags"
        elif not line:
            continue
        elif section == "description":
            out["description"].append(line)
        elif section == "hashtags":
            out["hashtags"] = (out["hashtags"] + " " + line).strip()

    return out if out["title"] else None


def analyze(clips, out_dir, title, series, game=None, context=None,
            mission=None):
    """
    One pass over the cut clips. Returns the parsed result, or None.

    Falls back to writing seo_prompt.txt beside the frames so the step
    still leaves something to paste into an AI by hand.
    """
    if not config.METADATA_AI_ENABLED:
        return None

    frames = sample_clip_frames(clips, out_dir / "frames")
    prompt = build_prompt(frames, clips, title, series, game, context,
                          mission)

    key = api_key()
    if not key:
        (out_dir / "seo_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        print("  ! no GEMINI_API_KEY - wrote seo_prompt.txt and frames/ "
              "instead. Paste them into any AI and copy the answer back.",
              flush=True)
        return None

    try:
        reply = call_with_fallback(prompt, frames, key)
    except RuntimeError as exc:
        (out_dir / "seo_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        print(f"  ! metadata AI failed, keeping your own text: {exc}", flush=True)
        return None

    parsed = parse_response(reply, len(clips))
    if parsed is None:
        (out_dir / "seo_reply.txt").write_text(reply + "\n", encoding="utf-8")
        print("  ! metadata AI reply did not match the expected format, "
              "keeping your own text. Raw reply saved to seo_reply.txt.",
              flush=True)
        return None

    # Map the chosen frame back to (clip index, position in that clip) so
    # the thumbnail can be re-cut from the raw recording at full quality.
    if parsed["best_frame"] is not None and 0 <= parsed["best_frame"] < len(frames):
        ci, pos, _ = frames[parsed["best_frame"]]
        parsed["best"] = (ci, pos)
    else:
        parsed["best"] = None
    return parsed
