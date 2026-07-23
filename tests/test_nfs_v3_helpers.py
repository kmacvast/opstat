"""nfs_v3 helper tests."""

import nfs_v3


def test_slice_result_matches_string_and_int_object_ids():
    # VMS may return object_id as int or str depending on version; slicing
    # must match either representation (regression: nfs_v3 drill rows came
    # back empty when ids arrived as strings).
    result = {
        "prop_list": ["timestamp", "object_id", "iops"],
        "data": [
            ["t1", "7", 100],
            ["t1", 8, 200],
            ["t2", 7, 300],
        ],
    }
    sliced = nfs_v3._slice_result_for_object(result, 7)
    assert [row[2] for row in sliced["data"]] == [100, 300]

    sliced = nfs_v3._slice_result_for_object(result, "7")
    assert [row[2] for row in sliced["data"]] == [100, 300]


def test_slice_result_passthrough_without_object_id_column():
    result = {"prop_list": ["timestamp", "iops"], "data": [["t1", 1]]}
    assert nfs_v3._slice_result_for_object(result, 7) is result
    assert nfs_v3._slice_result_for_object(None, 7) is None


def test_normalize_object_id():
    assert nfs_v3._normalize_object_id("7") == 7
    assert nfs_v3._normalize_object_id(7) == 7
    assert nfs_v3._normalize_object_id("bucket-a") == "bucket-a"
    assert nfs_v3._normalize_object_id(None) is None
