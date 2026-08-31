"""
Background music.

IMPORTANT - licensing rule:
Only tracks from assets/music/ are ever used. Put ONLY royalty-free
tracks there, downloaded from YouTube Audio Library or Pixabay Music.
Never point this at your general music folder. Copyrighted music is
what triggers Content ID claims and kills monetization.

Keep the download page or licence screenshot for every track, so you
can dispute a false claim if one ever appears.
"""

import random
from pathlib import Path

import config
from .ffmpeg_utils import run_ffmpeg, probe

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def pick_music(mood):
    """Pick a random track from the mood folder. Returns None if empty."""
    folder = config.MUSIC_DIR / mood
    if not folder.exists():
        return None

    tracks = [p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS]
    return random.choice(tracks) if tracks else None


def add_music(video_path, out_path, preset, duck=True):
    """
    Mix background music under the gameplay audio.

    duck=True lowers the music automatically when game audio is loud,
    so gunfire and dialogue stay clear. This sounds much better than a
    flat volume, which either drowns the game or is inaudible.
    """
    mood = preset.get("music_mood", "tense")
    volume = float(preset.get("music_volume", 0.15))

    track = pick_music(mood)
    if track is None:
        # No music available - pass the video through unchanged rather
        # than failing the whole render.
        run_ffmpeg(
            ["-i", str(video_path), "-c", "copy", str(out_path)],
            description="passthrough (no music found)",
        )
        return out_path, None

    duration = probe(video_path)["duration"]
    has_game_audio = probe(video_path)["has_audio"]

    if not has_game_audio:
        # Music only
        filter_complex = f"[1:a]volume={volume},atrim=0:{duration:.3f}[aout]"
    elif duck:
        filter_complex = (
            f"[1:a]volume={volume}[music];"
            f"[music][0:a]sidechaincompress="
            f"threshold=0.05:ratio=8:attack=5:release=300[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]volume={volume}[music];"
            f"[0:a][music]amix=inputs=2:duration=first[aout]"
        )

    args = [
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(track),   # loop music if short
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", config.AUDIO_CODEC,
        "-b:a", config.AUDIO_BITRATE,
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    run_ffmpeg(args, description="mixing background music")
    return out_path, track.name
