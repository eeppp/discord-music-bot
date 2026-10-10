from __future__ import annotations


VALID_EFFECTS = {"normal", "bass", "treble", "nightcore", "8d"}
EFFECT_NAMES = {
    "normal": "عادي",
    "bass": "Bass Boost",
    "treble": "Treble",
    "nightcore": "Nightcore",
    "8d": "8D",
}


def clamp_bass(value: int | float) -> int:
    return max(0, min(10, int(value)))


def normalize_effect(value: str) -> str:
    value = value.strip().lower()
    aliases = {
        "عادي": "normal",
        "إيقاف": "normal",
        "off": "normal",
        "جهير": "bass",
        "باس": "bass",
        "تريبل": "treble",
        "نايتكور": "nightcore",
        "٨دي": "8d",
        "8دي": "8d",
    }
    effect = aliases.get(value, value)
    return effect if effect in VALID_EFFECTS else "normal"


def ffmpeg_options(bass: int | float = 0, effect: str = "normal") -> dict[str, str]:
    """Build reconnect and audio filter arguments for FFmpeg."""
    bass = clamp_bass(bass)
    effect = normalize_effect(effect)
    filters: list[str] = []

    if bass or effect == "bass":
        gain = bass if bass else 6
        filters.append(f"bass=g={gain}:f=100:w=0.5")
    if effect == "treble":
        filters.append("treble=g=5:f=3000")
    elif effect == "nightcore":
        # Atempo changes speed without assuming the source sample rate.
        filters.append("atempo=1.12")
    elif effect == "8d":
        filters.append("apulsator=hz=0.125")

    options = "-vn -loglevel error"
    if filters:
        options += " -af " + ",".join(filters)
    return {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": options,
    }
