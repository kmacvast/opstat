"""tui_layout formatter and display helper tests."""

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
    assert tl.display_width(truncated) <= 4


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
import re as _re


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


def test_already_reset_text_is_not_given_a_redundant_reset():
    styled = _colored("abcdefghijklmnop", tl._BCYAN)
    cut = tl.truncate_display(styled, 40)
    assert cut == styled, "text that already fits must be returned unchanged"


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
