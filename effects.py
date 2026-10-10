from __future__ import annotations


def clamp_bass(value: int | float) -> int:
    return max(0, min(10, int(value)))


def ffmpeg_options(bass: int | float = 0) -> dict[str, str]:
    """Build safe reconnect and optional bass filter arguments for FFmpeg."""
    bass = clamp_bass(bass)
    options = "-vn -loglevel error"
    if bass:
        options += f" -af bass=g={bass}:f=100:w=0.5"
    return {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": options,
    }
