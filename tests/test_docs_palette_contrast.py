"""Guard the docs palette: every key text pairing must meet WCAG AA.

The YanShi docs theme (``docs/stylesheets/extra.css``) defines an OKLCH palette.
This test parses those tokens straight from the stylesheet, converts
OKLCH -> OKLab -> linear sRGB -> WCAG relative luminance, and asserts that the
foreground/background pairings actually used by the site clear the AA contrast
bar (4.5:1 for body text, 3:1 for large text). It fails loudly if a future
palette edit regresses legibility.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[1] / "docs" / "stylesheets" / "extra.css"

_TOKEN_RE = re.compile(
    r"--color-(?P<name>[a-z0-9-]+):\s*oklch\(\s*"
    r"(?P<l>[0-9.]+)\s+(?P<c>[0-9.]+)\s+(?P<h>[0-9.]+)\s*\)"
)


def _load_tokens() -> dict[str, tuple[float, float, float]]:
    text = CSS_PATH.read_text(encoding="utf-8")
    tokens = {
        m.group("name"): (float(m.group("l")), float(m.group("c")), float(m.group("h")))
        for m in _TOKEN_RE.finditer(text)
    }
    if not tokens:
        raise AssertionError(f"no --color-* oklch tokens found in {CSS_PATH}")
    return tokens


def _oklch_to_linear_srgb(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    h = math.radians(h_deg)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    cl, cm, cs = l_**3, m_**3, s_**3
    r = 4.0767416621 * cl - 3.3077115913 * cm + 0.2309699292 * cs
    g = -1.2684380046 * cl + 2.6097574011 * cm - 0.3413193965 * cs
    bb = -0.0041960863 * cl - 0.7034186147 * cm + 1.7076147010 * cs
    return r, g, bb


def _luminance(color: tuple[float, float, float]) -> float:
    r, g, b = (max(0.0, min(1.0, v)) for v in _oklch_to_linear_srgb(*color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(fg: tuple, bg: tuple, pct: float) -> tuple[float, float, float]:
    """Approximate CSS color-mix(in oklch, fg pct%, bg)."""
    t = pct / 100.0
    return tuple(fg[i] * t + bg[i] * (1 - t) for i in range(3))  # type: ignore[return-value]


def _contrast(fg: tuple, bg: tuple) -> float:
    l1, l2 = _luminance(fg), _luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _pairs():
    t = _load_tokens()
    black = (0.0, 0.0, 0.0)
    code_bg = _mix(t["surface"], black, 86)  # --md-code-bg-color
    code_fg = _mix(t["thread"], t["ink"], 80)  # .md-typeset code color
    return [
        # (label, fg, bg, min_ratio)
        ("body ink on bg", t["ink"], t["bg"], 4.5),
        ("body ink on surface", t["ink"], t["surface"], 4.5),
        ("muted on bg", t["muted"], t["bg"], 4.5),
        ("muted on surface", t["muted"], t["surface"], 4.5),
        ("muted on surface-2", t["muted"], t["surface-2"], 4.5),
        ("link/thread on bg", t["thread"], t["bg"], 4.5),
        ("link/thread on surface", t["thread"], t["surface"], 4.5),
        ("thread-strong on bg", t["thread-strong"], t["bg"], 4.5),
        ("inline code on code bg", code_fg, code_bg, 4.5),
        ("danger on bg", t["danger"], t["bg"], 4.5),
        ("danger on surface", t["danger"], t["surface"], 4.5),
        ("h1 ink on bg (large)", t["ink"], t["bg"], 3.0),
    ]


@pytest.mark.parametrize(
    "label,fg,bg,minimum",
    _pairs(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_palette_pairing_meets_wcag_aa(label, fg, bg, minimum):
    ratio = _contrast(fg, bg)
    assert ratio >= minimum, f"{label}: contrast {ratio:.2f} < required {minimum}"


def test_all_expected_tokens_present():
    t = _load_tokens()
    required = {"bg", "surface", "surface-2", "ink", "muted", "thread", "thread-strong", "danger"}
    missing = required - t.keys()
    assert not missing, f"missing palette tokens in extra.css: {sorted(missing)}"
