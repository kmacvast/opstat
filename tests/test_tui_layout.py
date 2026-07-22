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
