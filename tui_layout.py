#!/usr/bin/env python3
"""Terminal table layout helpers for opstat TUI rendering."""

import re
import unicodedata

_ANSI_RE = re.compile(r"\033\[[^m]*m")

# ---------------------------------------------------------------------------
# ANSI colors (shared by every protocol engine)
# ---------------------------------------------------------------------------
_RST = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_BRED = "\033[1;31m"
_BGREEN = "\033[1;32m"
_BYELLOW = "\033[1;33m"
_BBLUE = "\033[1;34m"
_BMAGENTA = "\033[1;35m"
_BCYAN = "\033[1;36m"
_BWHITE = "\033[1;37m"

COLOR_ENABLED = False


def set_color(enabled):
    """Enable/disable ANSI colorization for :func:`c`."""
    global COLOR_ENABLED
    COLOR_ENABLED = bool(enabled)


def c(text, code):
    """Wrap *text* in ANSI *code* when color is enabled, else return it plain."""
    return f"{code}{text}{_RST}" if COLOR_ENABLED else text


# ---------------------------------------------------------------------------
# Glyph system: UTF-8 box/indicator characters with an ASCII fallback, shared
# by every protocol engine so the drawing set lives in exactly one place.
# ---------------------------------------------------------------------------
_GLYPHS_UTF8 = {
    "H": "─", "V": "│",
    "TL": "┌", "TR": "┐", "BL": "└", "BR": "┘", "LT": "├", "RT": "┤",
    "BLK": "█", "SHD": "░",
    "ARR_UP": "▲", "ARR_DN": "▼", "ARR_EQ": "►", "DOT": "●", "MUS": "µs",
}
_GLYPHS_ASCII = {
    "H": "-", "V": "|",
    "TL": "+", "TR": "+", "BL": "+", "BR": "+", "LT": "+", "RT": "+",
    "BLK": "#", "SHD": ".",
    "ARR_UP": "+", "ARR_DN": "-", "ARR_EQ": "~", "DOT": "o", "MUS": "us",
}

# Latency-unit glyph used by :func:`format_latency_us`; updated by set_unicode().
_MUS = _GLYPHS_ASCII["MUS"]


def glyph_set(utf8):
    """Return a fresh copy of the box/indicator glyph map for the given mode."""
    return dict(_GLYPHS_UTF8 if utf8 else _GLYPHS_ASCII)


def set_unicode(enabled):
    """Select the latency-unit glyph used by the shared formatters."""
    global _MUS
    _MUS = _GLYPHS_UTF8["MUS"] if enabled else _GLYPHS_ASCII["MUS"]


# ---------------------------------------------------------------------------
# Numeric / display formatters (shared by every protocol engine)
# ---------------------------------------------------------------------------
def as_float(value):
    """Coerce *value* to float, returning None for null or unparseable input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def raw_bw_to_mb_sec(value):
    """Convert a raw bytes/sec counter to MB/s (decimal, 1e6)."""
    bw = as_float(value)
    return bw / 1_000_000.0 if bw is not None else None


def raw_bw_to_gb_sec(value):
    """Convert a raw bytes/sec counter to GB/s (decimal, 1e9)."""
    bw = as_float(value)
    return bw / 1_000_000_000.0 if bw is not None else None


def format_throughput_mbs(mbs):
    """Return (display, mbs) auto-scaled across KB/s, MB/s, GB/s."""
    mbs = as_float(mbs)
    if mbs is None or mbs <= 0:
        return "-", None
    if mbs >= 1024:
        return f"{mbs / 1024:.2f} GB/s", mbs
    if mbs >= 1:
        return f"{mbs:.2f} MB/s", mbs
    return f"{mbs * 1024:.2f} KB/s", mbs


def format_latency_us(us, active=True):
    """Return (display, us) auto-scaled between µs and ms; '-' when inactive/empty."""
    if not active:
        return "-", None
    us = as_float(us)
    if us is None or us <= 0:
        return "-", None
    if us >= 1000:
        return f"{us / 1000:.2f} ms", us
    return f"{us:.0f} {_MUS}", us


def format_iops(ops):
    """Return an ops/sec display string with precision that scales with magnitude."""
    ops = as_float(ops)
    if ops is None or ops <= 0:
        return "-"
    if ops >= 100_000:
        return f"{ops:,.0f}"
    if ops >= 100:
        return f"{ops:,.1f}"
    return f"{ops:,.2f}"


def format_os_release(version):
    """Render the VAST OS release header label, or '' when the version is unknown.

    VMS reports the full build (e.g. ``5.4.3.1.14178074658457882785``); only the
    first four dotted components are meaningful for display, so trim the rest.
    """
    if not version:
        return ""
    short = ".".join(str(version).split(".")[:4])
    return f"vast-os-release-{short}"


def format_block_size(value):
    """Return (display, bytes) auto-scaled across B, KB, MB for average I/O size."""
    value = as_float(value)
    if value is None or value <= 0:
        return "-", None
    if value >= 1024 ** 2:
        return f"{value / (1024 ** 2):.2f} MB", value
    if value >= 1024:
        return f"{value / 1024:.2f} KB", value
    return f"{value:.0f} B", value


def strip_ansi(text):
    """Remove ANSI SGR escape sequences from *text*."""
    return _ANSI_RE.sub("", text or "")


def char_display_width(ch):
    """Return terminal column width for a single Unicode character."""
    o = ord(ch)
    if o < 32 or o == 0x7F:
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me"):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    return 1


def display_width(text):
    """Visual column width of *text*, ignoring ANSI escapes."""
    plain = strip_ansi(text)
    return sum(char_display_width(ch) for ch in plain)


def truncate_display(text, max_width, ellipsis="…"):
    """Truncate *text* to *max_width* display columns, appending *ellipsis* if needed."""
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    ell_w = display_width(ellipsis)
    if ell_w >= max_width:
        return ellipsis[:max_width]
    target = max_width - ell_w
    out = []
    width = 0
    for ch in text:
        cw = char_display_width(ch)
        if width + cw > target:
            break
        out.append(ch)
        width += cw
    return "".join(out) + ellipsis


def pad_display(text, width, align="<"):
    """Pad or truncate *text* to exactly *width* terminal columns."""
    text = "" if text is None else str(text)
    if display_width(text) > width:
        text = truncate_display(text, width)
    pad = max(0, width - display_width(text))
    if align == ">":
        return " " * pad + text
    if align == "^":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


def join_columns(cells, sep=" "):
    """Join pre-sized table cells with a fixed separator."""
    return sep.join(cells)


def format_fixed_number(value, width, precision=2, empty="-"):
    """Right-align a numeric value (or placeholder) within *width* columns."""
    if value is None:
        return pad_display(empty, width, ">")
    try:
        text = f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        text = str(value)
    return pad_display(text, width, ">")


def format_scaled_metric(text, width, empty="-"):
    """Right-align a pre-formatted value+unit string within *width* columns."""
    if not text or text == empty:
        return pad_display(empty, width, ">")
    return pad_display(text, width, ">")
