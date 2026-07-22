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


def test_delete_monitor_ignores_404():
    def missing(_method, _path, _payload=None):
        raise RuntimeError("HTTP 404: gone")

    vast_common.delete_monitor(missing, 88)
    assert not any(mid == 88 for mid, _ in vast_common.failed_deletes())
