"""Shared theme tokens for the web UI, generated from theme_tokens.json.

Keeps the dark/light palette and brand colors in one checked-in config file
instead of duplicated literals across publisher.css and theme.css.
"""
import json
from pathlib import Path

TOKENS_PATH = Path(__file__).parent / "theme_tokens.json"


def load_tokens():
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


def _declarations(mapping):
    return "".join(f"--{name}:{value};" for name, value in mapping.items())


def root_tokens_css():
    """:root block consumed by publisher.css: dark palette + brand/status colors."""
    tokens = load_tokens()
    merged = {**tokens["dark"], "serif": tokens["shared"]["serif"], **{
        key: value for key, value in tokens["shared"].items() if key != "serif"
    }}
    return ":root{" + _declarations(merged) + "}"


def light_theme_css():
    """:root[data-theme=light] override block consumed by theme.css."""
    tokens = load_tokens()
    return ':root[data-theme="light"]{color-scheme:light;' + _declarations(tokens["light"]) + "}"
