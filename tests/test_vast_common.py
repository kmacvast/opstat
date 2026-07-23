"""vast_common helper and monitor registry tests."""

from unittest.mock import Mock

import pytest

import vast_common


def test_normalize_list_response():
    assert vast_common.normalize_list_response([1, 2]) == [1, 2]
    assert vast_common.normalize_list_response({"results": [1]}) == [1]
    assert vast_common.normalize_list_response({"data": [2]}) == [2]
    assert vast_common.normalize_list_response({"objects": [3]}) == [3]
    assert vast_common.normalize_list_response({"results": [1], "data": [2]}) == [1]
    assert vast_common.normalize_list_response({"other": []}) == []
    assert vast_common.normalize_list_response(None) == []


def test_resolve_object_name():
    obj = {"path": "/", "title": "root", "id": 9}
    assert vast_common.resolve_object_name(obj, ("path", "title")) == "/ (default)"
    assert vast_common.resolve_object_name({"title": "share", "id": 1}, ("path", "title")) == "share"
    assert vast_common.resolve_object_name({"id": 42}, ("name",)) == "42"


def test_select_local_cluster():
    clusters = [
        {"id": 1, "name": "a", "local": False},
        {"id": 2, "name": "b", "is_local": True},
    ]
    assert vast_common.select_local_cluster(clusters)["id"] == 2
    assert vast_common.select_local_cluster([{"id": 7, "name": "only"}])["id"] == 7
    assert vast_common.select_local_cluster([]) is None


def test_os_release_from_cluster():
    assert vast_common.os_release_from_cluster({"sw_version": "5.4.3"}) == "5.4.3"
    assert vast_common.os_release_from_cluster({}) is None
    assert vast_common.os_release_from_cluster(None) is None


def test_monitor_registry_and_drain():
    vast_common.register_monitor(10)
    vast_common.register_monitor(20)
    deleted = []
    vast_common.drain_monitors(deleted.append)
    assert set(deleted) == {10, 20}
    vast_common.register_monitor(30)
    vast_common.forget_monitor(30)
    deleted.clear()
    vast_common.drain_monitors(deleted.append)
    assert deleted == []


def test_create_monitor_raw_with_granularity_retry():
    calls = []

    def request_fn(method, path, payload=None):
        calls.append(payload)
        if payload and payload.get("granularity") == "auto":
            raise RuntimeError("Invalid granularity: auto")
        return {"id": 99}

    mid = vast_common.create_monitor_raw(
        request_fn, "adhoc_test", ["ProtoMetrics,proto_name=S3Common,iops"],
        "cluster", [1], time_frame="10m",
    )
    assert mid == 99
    assert len(calls) == 2
    assert calls[0].get("granularity") == "auto"
    assert "granularity" not in calls[1]


def test_create_monitor_raw_no_aggregation_skips_granularity():
    request_fn = Mock(return_value={"id": 5})
    mid = vast_common.create_monitor_raw(
        request_fn, "view_mon", ["ViewMetrics,read_iops__rate"],
        "view", [3], time_frame="10m", no_aggregation=True,
    )
    assert mid == 5
    payload = request_fn.call_args.args[2]
    assert "granularity" not in payload
    assert "aggregation" not in payload


def test_create_monitor_raw_missing_id_raises():
    with pytest.raises(RuntimeError, match="did not return id"):
        vast_common.create_monitor_raw(
            lambda *_a, **_k: {}, "bad", ["x"], "cluster", [1], time_frame="10m",
        )


def test_delete_monitor_records_non_404():
    def boom(_method, _path, _payload=None):
        raise RuntimeError("HTTP 500: fail")

    vast_common.delete_monitor(boom, 77)
    fails = vast_common.failed_deletes()
    assert any(mid == 77 for mid, _detail in fails)


def test_strip_escape_sequences_drops_arrow_keys():
    # Arrow keys must not satisfy engine substring checks like `"c" in chars`.
    assert vast_common.strip_escape_sequences("\x1b[C") == ""   # right arrow
    assert vast_common.strip_escape_sequences("\x1b[B") == ""   # down arrow
    assert vast_common.strip_escape_sequences("\x1b[A\x1b[D") == ""
    assert "c" not in vast_common.strip_escape_sequences("\x1b[C").lower()


def test_strip_escape_sequences_keeps_plain_keys():
    assert vast_common.strip_escape_sequences("q") == "q"
    assert vast_common.strip_escape_sequences("\x03") == "\x03"  # Ctrl-C
    assert vast_common.strip_escape_sequences(" c") == " c"
    assert vast_common.strip_escape_sequences("\x1b[Cq\x1b[B") == "q"


def test_strip_escape_sequences_handles_extended_and_partial():
    assert vast_common.strip_escape_sequences("\x1b[1;5C") == ""  # Ctrl+right
    assert vast_common.strip_escape_sequences("\x1bOP") == ""     # F1 (SS3)
    assert vast_common.strip_escape_sequences("\x1b[15~") == ""   # F5
    assert vast_common.strip_escape_sequences("\x1bb") == ""      # Alt-b chord
    assert vast_common.strip_escape_sequences("\x1b[") == ""      # truncated CSI
    assert vast_common.strip_escape_sequences("\x1b") == ""       # lone ESC


def test_delete_monitor_ignores_404():
    def missing(_method, _path, _payload=None):
        raise RuntimeError("HTTP 404: gone")

    vast_common.delete_monitor(missing, 88)
    assert not any(mid == 88 for mid, _ in vast_common.failed_deletes())


def test_resolve_auth_token_wins_without_password_prompt(monkeypatch):
    monkeypatch.setenv("VAST_TOKEN", "tok123")
    monkeypatch.setenv("VAST_PASSWORD", "should-be-ignored")
    monkeypatch.setattr(
        "vast_common.getpass.getpass",
        lambda *_a, **_k: pytest.fail("token users must never be prompted"),
    )
    headers, auth, password = vast_common.resolve_auth("admin", "vms1", None, "opstat/test/1")
    assert headers["Authorization"] == "Bearer tok123"
    assert headers["User-Agent"] == "opstat/test/1"
    assert auth is None and password is None


def test_resolve_auth_basic_from_env_password(monkeypatch):
    monkeypatch.delenv("VAST_TOKEN", raising=False)
    monkeypatch.setenv("VAST_PASSWORD", "sekrit")
    headers, auth, password = vast_common.resolve_auth("admin", "vms1", None, "opstat/test/1")
    import base64
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"admin:sekrit").decode()
    assert password == "sekrit"
    assert auth is not None


def test_resolve_auth_cli_password_beats_env(monkeypatch):
    monkeypatch.delenv("VAST_TOKEN", raising=False)
    monkeypatch.setenv("VAST_PASSWORD", "env-pw")
    _headers, _auth, password = vast_common.resolve_auth("admin", "vms1", "cli-pw", "ua")
    assert password == "cli-pw"


def test_resolve_auth_prompts_as_last_resort(monkeypatch):
    monkeypatch.delenv("VAST_TOKEN", raising=False)
    monkeypatch.delenv("VAST_PASSWORD", raising=False)
    monkeypatch.setattr("vast_common.getpass.getpass", lambda *_a, **_k: "typed-pw")
    _headers, _auth, password = vast_common.resolve_auth("admin", "vms1", None, "ua")
    assert password == "typed-pw"


def test_guarded_poll_success_calls_fetch_then_render():
    calls = []
    ok = vast_common.guarded_poll(lambda: calls.append("fetch"), lambda: calls.append("render"))
    assert ok is True
    assert calls == ["fetch", "render"]


def test_guarded_poll_tolerates_transient_failure(capsys):
    renders = []

    def boom():
        raise RuntimeError("HTTP 502: VMS restarting")

    ok = vast_common.guarded_poll(boom, lambda: renders.append("render"))
    assert ok is False
    assert renders == ["render"]  # last good data redrawn
    out = capsys.readouterr().out
    assert "poll failed (1/" in out
    assert "HTTP 502" in out


def test_guarded_poll_success_resets_failure_count(capsys):
    def boom():
        raise RuntimeError("blip")

    vast_common.guarded_poll(boom, lambda: None)
    vast_common.guarded_poll(boom, lambda: None)
    assert "poll failed (2/" in capsys.readouterr().out
    # A successful tick restarts the count: the next failure reports 1 again.
    vast_common.guarded_poll(lambda: None, lambda: None)
    vast_common.guarded_poll(boom, lambda: None)
    assert "poll failed (1/" in capsys.readouterr().out


def test_guarded_poll_raises_after_max_consecutive_failures(capsys):
    def boom():
        raise RuntimeError("persistent outage")

    for _ in range(vast_common.MAX_CONSECUTIVE_POLL_FAILURES - 1):
        assert vast_common.guarded_poll(boom, lambda: None) is False
    with pytest.raises(RuntimeError, match="persistent outage"):
        vast_common.guarded_poll(boom, lambda: None)


def test_guarded_poll_propagates_keyboard_interrupt():
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        vast_common.guarded_poll(interrupted, lambda: None)


def test_guarded_poll_survives_render_failure_in_error_path(capsys):
    def boom():
        raise RuntimeError("fetch down")

    def bad_render():
        raise ValueError("render broken")

    assert vast_common.guarded_poll(boom, bad_render) is False
    assert "fetch down" in capsys.readouterr().out
