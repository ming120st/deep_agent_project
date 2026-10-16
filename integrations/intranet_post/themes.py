"""Design tokens. All colours / sizes live here so a new look = a new Theme entry.

The renderer never hard-codes a colour; it only asks the theme. To add a theme,
add an entry to ``THEMES`` (and nothing else).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tone:
    """A semantic colour set used by callouts and table cells (info/good/warn/danger/neutral)."""

    bg: str
    border: str
    text: str
    accent: str


@dataclass(frozen=True)
class Theme:
    name: str
    font_family: str
    base_size: int        # body text, px
    content_width: int    # max width of the post body, px
    text: str
    muted: str
    line: str             # hairlines / table borders
    surface: str          # card / cell background
    zebra: str            # striped table rows
    primary: str
    primary_dark: str
    primary_soft: str
    primary_border: str
    on_primary: str       # text colour on a primary background
    neutral_head_bg: str  # table header for header_tone="neutral"
    radius: int
    tones: dict[str, Tone]


DEFAULT = Theme(
    name="default",
    font_family="'Malgun Gothic','Apple SD Gothic Neo',sans-serif",
    base_size=14,
    content_width=860,
    text="#222222",
    muted="#6b7280",
    line="#d9dee5",
    surface="#ffffff",
    zebra="#f6f8fb",
    primary="#2b5fab",
    primary_dark="#1d457f",
    primary_soft="#eaf1fb",
    primary_border="#bcd1ee",
    on_primary="#ffffff",
    neutral_head_bg="#eef1f5",
    radius=6,
    tones={
        "info": Tone(bg="#e8f1fb", border="#b8d3f0", text="#174a7e", accent="#2b5fab"),
        "good": Tone(bg="#e6f4ea", border="#b7dfc1", text="#1e6b34", accent="#2e9e4f"),
        "warn": Tone(bg="#fff4e0", border="#f5d49a", text="#8a5a00", accent="#e6a118"),
        "danger": Tone(bg="#fdecea", border="#f5c2bd", text="#a12622", accent="#d64545"),
        "neutral": Tone(bg="#f3f4f6", border="#d9dee5", text="#374151", accent="#9ca3af"),
    },
)

THEMES: dict[str, Theme] = {DEFAULT.name: DEFAULT}

TONE_NAMES = ("info", "good", "warn", "danger", "neutral")


def get_theme(name: str) -> Theme:
    try:
        return THEMES[name]
    except KeyError as exc:
        raise ValueError(f"unknown theme: {name!r} (available: {', '.join(THEMES)})") from exc
