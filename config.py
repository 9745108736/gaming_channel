"""
All settings live here. Change values in this file, not in the code.
"""

from pathlib import Path

# ---------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------
ROOT = Path(__file__).parent

RAW_DIR = ROOT / "raw"           # your gameplay recordings go here
OUTPUT_DIR = ROOT / "output"     # finished videos come out here
WORK_DIR = ROOT / ".work"        # temporary files, auto-deleted
ASSETS = ROOT / "assets"

MUSIC_DIR = ASSETS / "music"        # music/tense, music/hype, music/chill
OVERLAY_DIR = ASSETS / "overlays"   # your PNG text templates
REACTION_DIR = ASSETS / "reactions" # surprise.mp4, laugh.mp4, etc.
LUT_DIR = ASSETS / "luts"           # .cube color grade files


# ---------------------------------------------------------------
# VIDEO SPEC
# Everything gets normalized to this before joining.
# Do not change mid-project or old clips won't join with new ones.
# ---------------------------------------------------------------
WIDTH = 1080
HEIGHT = 1920        # 9:16 vertical for Shorts / Reels
FPS = 30
SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2   # forced stereo, prevents sync drift


# ---------------------------------------------------------------
# FRAME LAYOUT
# How the 9:16 frame is divided in "blur" mode:
#
#   +----------------+  <- top zone: hook text
#   |                |
#   +----------------+
#   |   GAMEPLAY     |  <- GAMEPLAY_HEIGHT of the frame
#   +----------------+
#   |                |  <- bottom zone: reaction cam
#   +----------------+
#
# A plain full-width 16:9 fit is only 0.32 of the height, which leaves
# two thirds of the screen as blurred filler and reads as reposted
# landscape video. Punching in past full width trades horizontal FOV
# (the minimap and ammo corners get clipped) for a band that carries
# the frame. Per-series override with "gameplay_height".
# ---------------------------------------------------------------
GAMEPLAY_HEIGHT = 0.46

# Shorts and Reels draw their OWN interface over the bottom of the frame:
# your handle, the caption, the sound name, and the action buttons up the
# right side. That strip is never visible to the viewer, so nothing may be
# placed in it. The band is centred in (HEIGHT - SAFE_BOTTOM), not in
# HEIGHT - centring on the full frame put 63% of the reaction cam behind
# YouTube's own UI.
SAFE_BOTTOM = 280

# How the space left over after the gameplay band is split between the
# text zone above it and the reaction zone below. Not 50/50: two lines of
# hook text need ~190px, a face needs as much as it can get, so the
# bottom gets the larger share.
TEXT_ZONE_SHARE = 0.34


# ---------------------------------------------------------------
# LAYOUT MODES
# Set per series with a "layout" key - a series preset controls how a
# video looks, and different footage wants different framing.
#
#   blur_band     gameplay band centred, blurred filler above and below,
#                 reaction cam as a bubble in the bottom zone.
#                 Keeps ~75% of the source width. Use it when the scene
#                 matters: driving, landscape, vehicle tests.
#
#   facecam_top   full width reaction strip across the top, gameplay
#                 filling everything below it, text over the top of the
#                 gameplay. No blur, face always on screen. Keeps only
#                 ~40% of the source width, so use it where the action
#                 is centred: gunfights, stealth, close quarters.
#
# facecam_top needs a LANDSCAPE reaction recording. A 2.81:1 strip takes
# 63% of a 16:9 source but only 20% of a 720x1280 portrait one, which
# crops the face down to an eyes-only band.
# ---------------------------------------------------------------
LAYOUT_BLUR_BAND = "blur_band"
LAYOUT_FACECAM_TOP = "facecam_top"
DEFAULT_LAYOUT = LAYOUT_BLUR_BAND

FACECAM_HEIGHT = 0.20      # fraction of frame height for the top strip
FACECAM_TEXT_BAND = 300    # px under the strip where hook and captions sit


# ---------------------------------------------------------------
# ENCODING
# -------------------------
# --------------------------------------
# Use "libx264" for CPU. If you have an NVIDIA GPU, "h264_nvenc" is
# much faster with a small quality cost.
VIDEO_CODEC = "h264_nvenc"
PRESET = "medium"    # ultrafast > superfast > fast > medium > slow

# Quality target. Lower = better quality, bigger file. 18-23 is sane.
# Deliberately NOT named CRF: -crf is a libx264/libx265 private option
# that nvenc does not have. Passing -crf to nvenc produces only a WARNING,
# and ffmpeg does not fail on warnings, so the encode silently runs at
# nvenc's ~2 Mbps default and the quality dial does nothing.
# ffmpeg_utils.video_quality_args() translates this into whatever flags
# the configured codec actually reads.
QUALITY = 20

# VBR ceiling. Constant quality alone lets nvenc spend its budget on the
# cheap blurred background; the cap gives high-motion gunfights room
# without letting file size run away.
MAXRATE = "12M"
BUFSIZE = "24M"

AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"


# ---------------------------------------------------------------
# SERIES PRESETS
# Each series gets its own look. Add new ones as you create series.
# ---------------------------------------------------------------
# "hook_text" is a per-series fallback hook, and it stays None on purpose.
# A series preset controls how a video LOOKS - grade, music, transitions -
# and says nothing about what happens in the footage. Running
# --series vehicle_test on a gunfight clip for its grade is legitimate;
# it does not make the clip a vehicle test. A standing hook line here
# would assert content the pipeline cannot verify, and a title that
# misdescribes the clip costs more retention than having no title at all.
# Set this only for a series where every video genuinely is that thing,
# and even then a per-video title beats it.
SERIES = {
    "default": {
        "lut": None,                  # e.g. "cinematic.cube" or None
        "eq": "contrast=1.12:saturation=1.15:brightness=0.01",
        "music_mood": "tense",
        "music_volume": 0.15,         # 0.0 - 1.0
        "transition": "fade",
        "transition_duration": 0.4,
        "vertical_mode": "blur",      # "blur" or "crop"
        "layout": LAYOUT_BLUR_BAND,
        "gameplay_height": None,      # None = use config.GAMEPLAY_HEIGHT
        "hook_text": None,
        "hashtags": [],               # e.g. ["#farcry5", "#ubisoft"]
    },
    "vehicle_test": {
        "lut": None,
        "eq": "contrast=1.15:saturation=1.25:brightness=0.02",
        "music_mood": "hype",
        "music_volume": 0.18,
        "transition": "fade",
        "transition_duration": 0.3,
        "vertical_mode": "blur",
        "layout": LAYOUT_BLUR_BAND,
        "gameplay_height": None,      # None = use config.GAMEPLAY_HEIGHT
        "hook_text": None,
        "hashtags": [],               # e.g. ["#farcry5", "#ubisoft"]
    },
    "stealth": {
        "lut": None,
        "eq": "contrast=1.2:saturation=0.9:brightness=-0.02",
        "music_mood": "tense",
        "music_volume": 0.12,
        "transition": "fadeblack",
        "transition_duration": 0.5,
        "vertical_mode": "blur",
        "layout": LAYOUT_FACECAM_TOP,
        "gameplay_height": None,      # None = use config.GAMEPLAY_HEIGHT
        "hook_text": None,
        "hashtags": [],               # e.g. ["#farcry5", "#ubisoft"]
    },
}


# ---------------------------------------------------------------
# REACTION MAPPING
# The label you write in clips.txt picks the reaction clip.
# ---------------------------------------------------------------
REACTION_MAP = {
    "gunfight":   "hype",
    "explosion":  "surprise",
    "fail":       "disappointment",
    "close_call": "wince",
    "funny":      "laugh",
    "chase":      "hype",
    "kill":       "hype",
}
DEFAULT_REACTION = "idle"     # used for every clip while REACTION_MATCH_LABELS is False


# ---------------------------------------------------------------
# HOOK DETECTION
# Scores clips so the most exciting one goes first.
# ---------------------------------------------------------------
HOOK_DETECTION = True
HOOK_MOTION_WEIGHT = 0.6   # how much visual movement matters
HOOK_AUDIO_WEIGHT = 0.4    # how much loudness matters


# ---------------------------------------------------------------
# HOOK TEXT
# Drawn in the top zone, applied at final export because that is the
# first point where clip order is known (hook detection reorders).
# Resolution order:
#   1. assets/overlays/<title-slug>.png   per-video graphic
#   2. assets/overlays/<series>.png       series brand plate
#   3. drawtext                           plain text fallback
# ---------------------------------------------------------------
HOOK_TEXT_ENABLED = True
HOOK_TEXT_DURATION = 3.0        # seconds on screen from the start
HOOK_TEXT_FONT = "C:/Windows/Fonts/impact.ttf"
HOOK_TEXT_SIZE = 78
HOOK_TEXT_COLOR = "white"
HOOK_TEXT_BORDER = 6            # black outline, keeps it legible on any frame
HOOK_TEXT_WRAP = 18             # characters per line before wrapping
HOOK_TEXT_MAX_LINES = 2


# ---------------------------------------------------------------
# REACTION CAM
# Composited into the bottom zone of each clip, picked by the clip label
# through REACTION_MAP. Skipped with a log line if the file is missing,
# so the pipeline keeps working until the footage exists.
# ---------------------------------------------------------------
REACTION_ENABLED = True

# One continuous recording per costume, holding every reaction in
# sequence. Files are picked up as assets/reactions/reactions_*.<ext> -
# the prefix keeps them apart from anything else in the folder. One
# variant is chosen at RANDOM PER VIDEO, never per clip: a costume that
# changes between clips of the same short reads as broken editing, not
# as variety.
REACTION_VARIANT_PREFIX = "reactions_"

# Where each reaction sits inside a variant file, as (start, duration)
# in seconds. REACTION_MAP turns a clip label into one of these names.
# Measured off the footage - regenerate these if you re-record, and
# check them, because a wrong boundary shows the wrong emotion.
REACTION_SEGMENTS = {
    "smile":          (10.7, 1.5),
    "hype":           (12.5, 1.3),
    "surprise":       (13.9, 1.0),
    "laugh":          (15.3, 1.3),
    # This take has no distinct wince, so it borrows the closing
    # facepalm. Give it its own range once one is recorded.
    "wince":          (18.9, 1.1),
    "disappointment": (18.9, 1.1),
    # The whole usable span of the variant, for always-on use. Long
    # enough to cover a clip without looping back on itself, so the cam
    # reads as a person watching rather than a repeating GIF.
    "idle":           (10.7, 9.3),
}

# How long the cam stays on screen, in seconds. None = the whole clip,
# never disappearing, which is what you want when the face is part of
# the format rather than a punchline at the cut.
REACTION_DISPLAY_SECONDS = None

# False = ignore the clip label and use DEFAULT_REACTION for every clip.
# Matching an emotion to a moment only works if the recording actually
# holds distinct emotions; with one generic take, a single calm segment
# that suits anything beats a mismatched "hype" over a death.
REACTION_MATCH_LABELS = False

REACTION_HEIGHT = 460           # px tall; must fit the visible bottom
                                # zone, see SAFE_BOTTOM and TEXT_ZONE_SHARE
# 1.78 is the native aspect of a 16:9 webcam, so the crop keeps the WHOLE
# source frame - no forehead or chin lost. Narrower values crop the sides,
# wider ones start slicing the face top and bottom.
REACTION_ASPECT = 1.78

# Zoom into the middle of the webcam frame before scaling: 1.0 is the
# whole frame, 1.25 keeps the middle 80% and trims chair and wall off the
# sides. It crops the source, not the on-screen box, so the bubble stays
# the same size and the face just gets bigger inside it.
REACTION_ZOOM = 1.25
REACTION_FACE_BIAS = 0.30       # crop window position, 0 = top, 1 = bottom
REACTION_BORDER = 4
REACTION_BORDER_COLOR = "white"
REACTION_MUTE = True            # keep the gameplay + music mix clean


# ---------------------------------------------------------------
# PER-CLIP CAPTIONS
# A clip line in clips.txt may end with a quoted caption. It sits in the
# top zone for that clip's whole time on screen, which is what stops the
# frame going empty once the 3 second opening hook has gone.
# Smaller than the hook: the hook is the promise, a caption is narration.
# ---------------------------------------------------------------
CAPTION_ENABLED = True
CAPTION_SIZE = 52
CAPTION_COLOR = "white"
CAPTION_BORDER = 5
CAPTION_WRAP = 24
CAPTION_MAX_LINES = 2


# ---------------------------------------------------------------
# SEO / METADATA
# seo.txt is assembled from what YOU already wrote in clips.txt - the
# title and the per-clip captions. Nothing in it is generated: the
# pipeline cannot see the footage, so it has nothing of its own to say
# about it, and a description that misdescribes the video costs more
# than a blank one. Item 4 on the roadmap (a vision model reading
# frames) is what fills the remaining gaps.
#
# Tags that are true of every video you publish. Per-series tags go in
# the series preset as "hashtags".
# ---------------------------------------------------------------
HASHTAGS_COMMON = ["#shorts", "#gaming", "#gameplay"]
