"""tui_layout formatter and display helper tests."""

import re as _re

import pytest

import tui_layout as tl


def test_as_float():
    assert tl.as_float(None) is None
    assert tl.as_float("bad") is None
    assert tl.as_float("1.5") == 1.5
    assert tl.as_float(2) == 2.0


def test_bandwidth_conversions():
    assert tl.raw_bw_to_mb_sec(None) is None
    assert tl.raw_bw_to_mb_sec(5_000_000) == 5.0
    assert tl.raw_bw_to_gb_sec(2_000_000_000) == 2.0


def test_format_throughput_mbs():
    assert tl.format_throughput_mbs(None) == ("-", None)
    assert tl.format_throughput_mbs(0) == ("-", None)
    text, val = tl.format_throughput_mbs(0.5)
    assert "KB/s" in text and val == 0.5
    text, val = tl.format_throughput_mbs(12.5)
    assert "MB/s" in text and val == 12.5
    text, val = tl.format_throughput_mbs(2048)
    assert "GB/s" in text and val == 2048


def test_format_latency_us():
    tl.set_unicode(False)
    assert tl.format_latency_us(100, active=False) == ("-", None)
    assert tl.format_latency_us(0) == ("-", None)
    text, val = tl.format_latency_us(250)
    assert text.endswith("us") and val == 250
    text, val = tl.format_latency_us(2500)
    assert "ms" in text and val == 2500
    tl.set_unicode(True)
    text, _ = tl.format_latency_us(250)
    assert "µs" in text


def test_format_iops_and_block_size():
    assert tl.format_iops(None) == "-"
    assert tl.format_iops(12.345) == "12.35"
    assert tl.format_iops(150.6).startswith("150")
    assert tl.format_iops(200_000).replace(",", "") == "200000"
    assert tl.format_block_size(512)[0].endswith("B")
    assert "KB" in tl.format_block_size(4096)[0]
    assert "MB" in tl.format_block_size(2 * 1024 * 1024)[0]


def test_format_os_release():
    assert tl.format_os_release(None) == ""
    assert tl.format_os_release("") == ""
    assert tl.format_os_release("5.4.3.1.14178074658457882785") == "vast-os-release-5.4.3.1"


def test_color_wrapper():
    tl.set_color(False)
    assert tl.c("x", tl._BCYAN) == "x"
    tl.set_color(True)
    colored = tl.c("x", tl._BCYAN)
    assert colored.startswith(tl._BCYAN) and colored.endswith(tl._RST)


def test_pad_and_truncate_ignore_ansi():
    tl.set_color(True)
    colored = tl.c("hi", tl._BOLD)
    padded = tl.pad_display(colored, 6, "<")
    assert tl.display_width(padded) == 6
    truncated = tl.truncate_display(colored + " world", 4)
    # Exactly 4: '<= 4' was also satisfied by the old escape-eating
    # truncation, which under-filled the budget.
    assert tl.display_width(truncated) == 4


def test_glyph_set_modes():
    utf = tl.glyph_set(True)
    asc = tl.glyph_set(False)
    assert utf["H"] != asc["H"]
    assert asc["MUS"] == "us"


# ---------------------------------------------------------------------------
# truncate_display is ANSI-aware (FR7 narrow-terminal sweep).
#
# Callers truncate already-colored strings. Escape sequences occupy zero
# terminal columns, so counting their bytes against the budget silently threw
# away real content: an 80-column NFSv4.1 header rendered only 50 visible
# columns with color on - losing the cluster name - while the same header was
# complete with color off. A cut landing inside an escape also emitted a
# broken sequence.
# ---------------------------------------------------------------------------


def _colored(text, code):
    return f"{code}{text}{tl._RST}"


def test_escapes_do_not_consume_truncation_budget():
    plain = "hello world-of-text-that-is-long"
    styled = _colored("hello", tl._BCYAN) + plain[5:]
    assert tl.display_width(styled) == len(plain)
    cut = tl.truncate_display(styled, 12)
    assert tl.display_width(cut) == 12, (
        "escape bytes were counted as visible columns: %r" % cut)
    assert tl.strip_ansi(cut) == tl.truncate_display(plain, 12)


def test_truncation_never_cuts_an_escape_in_half():
    styled = _colored("abcdefghij", tl._BWHITE) * 4
    for width in range(1, 40):
        cut = tl.truncate_display(styled, width)
        assert not _re.search(r"\033(?!\[[0-9;]*m)", cut), (
            "broken escape at width %d: %r" % (width, cut))


def test_truncation_closes_styling_it_leaves_open():
    styled = tl._BCYAN + "abcdefghijklmnop"      # opened, never closed
    cut = tl.truncate_display(styled, 8)
    assert cut.endswith(tl._RST), (
        "colour would bleed past the truncation point: %r" % cut)


def test_text_that_fits_is_returned_unchanged():
    """Named for what it actually exercises: the early return, not the
    reset-suppression branch (which this input never reaches)."""
    styled = _colored("abcdefghijklmnop", tl._BCYAN)
    cut = tl.truncate_display(styled, 40)
    assert cut == styled, "text that already fits must be returned unchanged"


def test_no_redundant_reset_when_styling_was_already_closed():
    """The styled=False branch: styling closed before the cut must not earn
    a second reset. Previously untested in either direction."""
    styled = _colored("abcdefgh", tl._BCYAN) + "ijklmnopqrstuvwx"
    cut = tl.truncate_display(styled, 12)
    assert cut.count(tl._RST) == 1, (
        "expected exactly the original reset, got %r" % cut)


def test_colored_and_plain_truncate_to_the_same_visible_text():
    plain = "cluster selab-var-203  VMS var203.selab.vastdata.com:443  refresh 5s"
    styled = (_colored("cluster ", tl._DIM)
              + _colored("selab-var-203", tl._BWHITE)
              + "  VMS " + _colored("var203.selab.vastdata.com:443",
                                    tl._BWHITE)
              + _colored("  refresh 5s", tl._DIM))
    for width in (80, 60, 40, 24, 10):
        assert tl.strip_ansi(
            tl.truncate_display(styled, width)
        ) == tl.truncate_display(plain, width), (
            "colour changed what content survived at width %d" % width)


# A-4 / TUI-review finding 6: edge cases that were correct by inspection but
# unpinned - degenerate budgets, a lone ESC, wide characters, combining marks.
@pytest.mark.parametrize("max_width", [0, -5])
def test_non_positive_budget_returns_empty(max_width):
    """Reachable in production: box_row computes inner = max(0, width - 4)."""
    assert tl.truncate_display("abcdef", max_width) == ""


def test_budget_of_one_returns_just_the_ellipsis():
    cut = tl.truncate_display("abcdef", 1)
    assert tl.display_width(cut) <= 1


def test_a_lone_escape_is_preserved_at_zero_cost():
    """_ANSI_RE does not match a dangling ESC, so it falls through to the
    zero-width control path - truncation and display_width must agree."""
    text = "\033abcdefghijklmnop"
    cut = tl.truncate_display(text, 8)
    assert tl.display_width(cut) <= 8


@pytest.mark.parametrize("width", [1, 2, 3, 5, 8, 13])
def test_wide_characters_never_straddle_the_budget(width):
    """East Asian wide characters are 2 columns; one must never be split
    across the boundary, and the result must never exceed the budget."""
    text = "日本語テキストの長い行" * 3
    cut = tl.truncate_display(text, width)
    assert tl.display_width(cut) <= width, repr(cut)


@pytest.mark.parametrize("width", [2, 4, 6, 10])
def test_combining_marks_do_not_exceed_the_budget(width):
    text = "éàôü" * 4      # é à ô ü as base+mark
    cut = tl.truncate_display(text, width)
    assert tl.display_width(cut) <= width, repr(cut)


def test_rstrip_display_ignores_trailing_escapes():
    """str.rstrip leaves a space that sits before a trailing reset, and that
    stray column cost the NFSv4.1 footer the final letter of "[d] Delegation"
    at 80 columns with colour on."""
    plain, styled = "abc   ", "abc   " + tl._RST
    assert tl.rstrip_display(plain) == "abc"
    assert tl.rstrip_display(styled) == "abc" + tl._RST
    assert tl.display_width(tl.rstrip_display(styled)) == 3
    assert tl.rstrip_display("") == ""
    assert tl.rstrip_display("abc") == "abc"


def test_legend_width_is_identical_with_and_without_colour():
    """The regression itself: the same legend must occupy the same number of
    columns whether or not colour is enabled."""
    import vast_drill

    controls = vast_drill.nav_controls(("q", "o", "l", "c", "v", "t", "x"))
    try:
        tl.set_color(False)
        plain = vast_drill.nav_legend_lines(controls, 76)
        tl.set_color(True)
        coloured = vast_drill.nav_legend_lines(controls, 76)
    finally:
        tl.set_color(False)
    assert len(plain) == len(coloured)
    for p, q in zip(plain, coloured):
        assert tl.display_width(p) == tl.display_width(q), (
            f"colour changed the legend width: {p!r} vs {q!r}")
        assert tl.strip_ansi(q) == p
