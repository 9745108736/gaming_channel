"""
AI metadata generation.

Drafts seo.txt - the title, description and hashtags you paste into
YouTube - by showing a vision model a few frames of the finished video
together with the title and captions you already wrote.

This never touches the burned-in text on the video. A title drawn on
screen has to be right the first time and costs retention when it is
not; seo.txt is a draft you read before uploading, so a model writing
it is a different kind of risk. See rule 11 in CLAUDE.md.

With no API key it degrades instead of failing: the frames and a
ready-to-paste prompt are written into the output folder, so the step
still produces something useful.

No pip dependency - the API is called over plain HTTPS with urllib.
"""

import base64
import json
import os
import urllib.error
import urllib.request

import config
from .ffmpeg_utils import run_ffmpeg, probe

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
              "models/{model}:generateContent")


def api_key():
    """The Gemini key from the environment, or None."""
    return os.environ.get("GEMINI_API_KEY") or None


def extract_frames(video_path, out_dir, count=None):
    """
    Sample frames evenly across the video.

    Deliberately skips the first and last fifth: the hook title covers
    the top of the frame at both ends, and a frame of the title tells
    the model nothing about the gameplay.
    """
    count = count or config.METADATA_FRAMES
    duration = probe(video_path)["duration"]
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for i in range(count):
        # spread across the middle 60% of the runtime
        pos = 0.2 + (0.6 * (i + 0.5) / count)
        out = out_dir / f"frame_{i + 1:02d}.jpg"
        run_ffmpeg(
            ["-ss", f"{duration * pos:.3f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "3", "-update", "1", str(out)],
            description=f"sampling frame {i + 1} for metadata",
        )
        frames.append(out)
    return frames


def build_prompt(title, clips, series):
    """The instruction text sent with the frames."""
    captions = [c.caption for c in clips if getattr(c, "caption", None)]
    labels = sorted({c.label for c in clips})

    lines = [
        "You are writing upload metadata for a YouTube Short.",
        "",
        "The attached images are frames from a gameplay clip recorded by",
        "the channel owner. Look at them and write metadata that matches",
        "WHAT YOU ACTUALLY SEE in the frames. Do not invent events, kills",
        "or vehicles that are not visible. If the frames do not support a",
        "claim, leave it out.",
        "",
        "Channel: Malabari Gamer - first person shooter gameplay shorts.",
        f"Series preset used: {series} (this controls the look only, it",
        "says nothing about the content).",
        "",
        "What the owner wrote about this video:",
        f"  Working title: {title or '(none given)'}",
    ]
    if captions:
        lines.append("  On-screen captions, in order:")
        lines += [f"    - {c}" for c in captions]
    if labels:
        lines.append(f"  Moment labels: {', '.join(labels)}")

    lines += [
        "",
        "Return EXACTLY this format and nothing else:",
        "",
        "TITLE: <one line, under 80 characters, specific and punchy. No",
        "clickbait the footage does not deliver.>",
        "DESCRIPTION: <2 to 3 short lines describing what happens>",
        "HASHTAGS: <8 to 12 space separated tags, each starting with #>",
    ]
    return "\n".join(lines)


def call_gemini(prompt, frames, key, model=None, timeout=None):
    """
    Send the prompt and frames to Gemini. Returns the raw reply text.

    Raises RuntimeError with the API's own message on failure - the body
    of an error response says what is actually wrong (bad key, unknown
    model, quota) and swallowing it would leave you guessing.
    """
    model = model or config.METADATA_MODEL
    timeout = timeout or config.METADATA_TIMEOUT

    parts = [{"text": prompt}]
    for f in frames:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(f.read_bytes()).decode("ascii"),
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


def parse_response(text):
    """
    Pull TITLE / DESCRIPTION / HASHTAGS out of the reply.

    Returns None if the title is missing, because a reply that did not
    follow the format is more likely to be an apology or a refusal than
    usable metadata, and writing that into seo.txt would be worse than
    keeping the draft.
    """
    result = {"title": None, "description": [], "hashtags": ""}
    section = None

    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        upper = line.upper()
        if upper.startswith("TITLE:"):
            result["title"] = line[6:].strip()
            section = "title"
        elif upper.startswith("DESCRIPTION:"):
            rest = line[12:].strip()
            if rest:
                result["description"].append(rest)
            section = "description"
        elif upper.startswith("HASHTAGS:"):
            result["hashtags"] = line[9:].strip()
            section = "hashtags"
        elif not line:
            continue
        elif section == "description":
            result["description"].append(line)
        elif section == "hashtags":
            result["hashtags"] = (result["hashtags"] + " " + line).strip()

    return result if result["title"] else None


def generate(video_path, out_dir, title, clips, series):
    """
    Draft the metadata. Returns a dict, or None if it could not.

    Falls back to writing seo_prompt.txt next to the frames so the step
    still leaves you something to paste into an AI by hand.
    """
    if not config.METADATA_AI_ENABLED:
        return None

    frames_dir = out_dir / "frames"
    frames = extract_frames(video_path, frames_dir)
    prompt = build_prompt(title, clips, series)

    key = api_key()
    if not key:
        (out_dir / "seo_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        print("  ! no GEMINI_API_KEY - wrote seo_prompt.txt and frames/ "
              "instead. Paste them into any AI and copy the answer into "
              "seo.txt.", flush=True)
        return None

    try:
        reply = call_gemini(prompt, frames, key)
    except RuntimeError as exc:
        (out_dir / "seo_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        print(f"  ! metadata AI failed, keeping your draft: {exc}", flush=True)
        print("  ! wrote seo_prompt.txt and frames/ so you can do it by hand.",
              flush=True)
        return None

    parsed = parse_response(reply)
    if parsed is None:
        (out_dir / "seo_reply.txt").write_text(reply + "\n", encoding="utf-8")
        print("  ! metadata AI reply did not match the expected format, "
              "keeping your draft. Raw reply saved to seo_reply.txt.",
              flush=True)
    return parsed
