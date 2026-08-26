"""Regressions for the FR14 S3 endpoint derivation helpers.

The first version of `derive_s3_endpoint.py` reported "0 VIP(s)" for all 15
of the default tenant's candidate pools on var203 while the same run printed
"386 VIPs across 33 pools". It indexed VIPs under whichever single pool field
it found first - on that build the pool NAME - and then looked them up by the
integer ids resolved from the tenant. Every lookup missed, and the run
concluded no endpoint could be derived when the data was present all along.

The other rule these pin is the one the whole FR turns on: ownership is not
capability, and a pool whose name contains "s3" is a hint, never evidence.
Capability comes only from a source that observed or configured S3 on that
address.

These import the helper module directly - no cluster, no network.
"""

from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "var203_validation")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import derive_s3_endpoint as d                              # noqa: E402


# ---------------------------------------------------------------------------
# The literal var203 defect: pool identified by NAME, looked up by ID
# ---------------------------------------------------------------------------
def test_vips_are_indexed_by_pool_name_and_id():
    """A build that names the pool must still resolve when the caller has the
    id, and vice versa - the var203 miss."""
    vips = [
        {"id": 1, "ip": "172.200.13.180", "vippool": "bart-s3"},
        {"id": 2, "ip": "172.200.13.181", "vippool_id": 42},
        {"id": 3, "ip": "172.200.20.10", "vippool": {"id": 2, "name": "main"}},
    ]
    index = d.index_vips_by_pool(vips)
    assert d.pool_vips(index, 42, "bart-s3") == ["172.200.13.180", "172.200.13.181"], (
        "a pool named by one record and id'd by another must resolve to both")
    assert d.pool_vips(index, 2, "main") == ["172.200.20.10"]


def test_lookup_by_id_alone_still_finds_a_name_keyed_vip():
    """The exact failure: refs are ints, records carry names."""
    index = d.index_vips_by_pool([{"ip": "10.0.0.5", "vippool": "bart-s3"}])
    assert d.pool_vips(index, None, "bart-s3") == ["10.0.0.5"]
    # and the id-only lookup must not silently succeed against the wrong pool
    assert d.pool_vips(index, 999, "other") == []


def test_internal_and_malformed_addresses_are_excluded():
    index = d.index_vips_by_pool([
        {"ip": "192.168.5.5", "vippool": "p"},        # internal, never a VIP
        {"ip": "not-an-ip", "vippool": "p"},
        {"ip": "", "vippool": "p"},
        {"ip": "10.1.2.3", "vippool": "p"},
    ])
    assert d.pool_vips(index, None, "p") == ["10.1.2.3"]


def test_a_vip_with_no_pool_field_is_not_invented_into_one():
    assert d.index_vips_by_pool([{"id": 700, "ip": "10.1.0.1"}]) == {}


# ---------------------------------------------------------------------------
# Capability: only an observation or an S3 configuration counts
# ---------------------------------------------------------------------------
VIP_VIEW = """# HELP vast_vip_view_iops VMS vip-view iops
# TYPE vast_vip_view_iops gauge
vast_vip_view_iops{cluster="c",path="/bgolliher/elbencho-crwd",protocol="S3",tenant="default",vip="172.200.13.180",vippool="bart-s3"} 41.0
vast_vip_view_iops{cluster="c",path="/kmacs/smb/opstat",protocol="SMB2",tenant="default",vip="172.200.203.6",vippool="main"} 900.0
vast_vip_view_iops{cluster="c",path="/view/1",protocol="NFS4",tenant="default",vip="172.200.20.10",vippool="main"} 500.0
vast_vip_view_bw{cluster="c",path="/bgolliher/elbencho-crwd",protocol="S3",tenant="default",vip="172.200.13.180",vippool="bart-s3"} 9.0
"""


def test_only_s3_labelled_vips_are_reported_as_s3_capable():
    """A VIP busy with SMB2 or NFS4 is not S3-capable, however busy it is.

    The SMB endpoint here carries 900 - far more than the S3 one - so an
    implementation that ranked by traffic rather than filtering by protocol
    would pick exactly the wrong address.
    """
    found = d.s3_vips_from_vip_view(VIP_VIEW)
    assert set(found) == {"172.200.13.180"}, found
    assert "172.200.203.6" not in found, (
        "the SMB VIP was promoted to an S3 endpoint")
    assert "172.200.20.10" not in found


def test_s3_vip_carries_its_correlating_labels():
    info = d.s3_vips_from_vip_view(VIP_VIEW)["172.200.13.180"]
    assert "/bgolliher/elbencho-crwd" in info["paths"]
    assert "default" in info["tenants"]
    assert "bart-s3" in info["pools"]
    assert info["value"] == pytest.approx(50.0)      # 41 iops + 9 bw


def test_empty_or_absent_vip_view_yields_nothing():
    assert d.s3_vips_from_vip_view("") == {}
    assert d.s3_vips_from_vip_view(None) == {}
    assert d.s3_vips_from_vip_view("# HELP only\n") == {}


def test_true_ip_config_addresses_are_extracted_from_any_shape():
    payload = {"enabled": True,
               "s3_true_ips": ["172.200.13.180", "172.200.13.181"],
               "nested": [{"ip": "10.9.9.9"}], "not_an_ip": "hello"}
    assert d.s3_ips_from_true_ip_config(payload) == {
        "172.200.13.180", "172.200.13.181", "10.9.9.9"}
    assert d.s3_ips_from_true_ip_config({}) == set()
    assert d.s3_ips_from_true_ip_config(None) == set()


# ---------------------------------------------------------------------------
# Pool references: ids and names, wherever the build puts them
# ---------------------------------------------------------------------------
def test_pool_refs_collects_ids_and_names_from_nested_shapes():
    tenant = {"vippools": [{"id": 42, "name": "bart-s3"}, {"id": 2, "name": "main"}],
              "vippool_names": ["bart-s3", "main"]}
    refs = d.pool_refs(tenant)
    assert {"42", "2", "bart-s3", "main"} <= refs, refs


def test_pool_refs_ignores_unrelated_fields():
    assert d.pool_refs({"qos_policy_id": 7, "name": "view"}) == set()


# ---------------------------------------------------------------------------
# Correlation: ownership and capability must never be conflated
# ---------------------------------------------------------------------------
POOLS = {"42": {"id": 42, "name": "bart-s3"}, "bart-s3": {"id": 42, "name": "bart-s3"},
         "2": {"id": 2, "name": "main"}, "main": {"id": 2, "name": "main"}}
INDEX = {"bart-s3": ["172.200.13.180"], "42": ["172.200.13.180"],
         "main": ["172.200.203.6"], "2": ["172.200.203.6"]}


def test_a_pool_named_s3_is_a_hint_and_never_capability():
    """THE rule: 'bart-s3' looks like an S3 pool and is owned by the cluster.
    Neither fact makes it S3-capable, and promoting the name would hand back
    an unproven endpoint that looks proven."""
    cands = d.correlate_candidates({"42"}, POOLS, INDEX, s3_capable={}, view_id=222)
    row = [c for c in cands if c["ip"] == "172.200.13.180"][0]
    assert row["pool_name_hint"] is True, "the hint should still be recorded"
    assert row["s3_evidence"] == [], "a pool NAME must never become evidence"
    assert row["owned_by_cluster"] is True
    assert d.proven_candidates(cands) == [], (
        "ownership plus a suggestive pool name must not count as proven")


def test_affirmative_evidence_promotes_a_candidate():
    cands = d.correlate_candidates(
        {"42"}, POOLS, INDEX,
        s3_capable={"172.200.13.180": {"vip_view protocol=S3"}}, view_id=222)
    proven = d.proven_candidates(cands)
    assert [c["ip"] for c in proven] == ["172.200.13.180"]
    assert proven[0]["s3_evidence"] == ["vip_view protocol=S3"]


def test_owned_vip_without_evidence_is_reported_but_not_proven():
    """172.200.203.6 is a known-owned var203 VIP that serves SMB. It must
    appear as a candidate and must never be proven S3-capable."""
    cands = d.correlate_candidates({"2"}, POOLS, INDEX, s3_capable={}, view_id=222)
    assert [c["ip"] for c in cands] == ["172.200.203.6"]
    assert d.proven_candidates(cands) == []


def test_s3_evidence_outside_the_views_pools_is_still_surfaced():
    """An address proven to serve S3 but not in this view's pools is worth
    reporting - it proves the cluster serves S3 somewhere - but it is clearly
    marked as not tied to the view."""
    cands = d.correlate_candidates(
        {"2"}, POOLS, INDEX,
        s3_capable={"10.9.9.9": {"s3_true_ip_config"}}, view_id=222)
    extra = [c for c in cands if c["ip"] == "10.9.9.9"][0]
    assert extra["owned_by_cluster"] is None
    assert "S3 evidence only" in extra["chain"]
    assert extra["s3_evidence"] == ["s3_true_ip_config"]


def test_no_refs_and_no_evidence_yields_nothing():
    assert d.correlate_candidates(set(), POOLS, INDEX, {}) == []
    assert d.proven_candidates([]) == []
