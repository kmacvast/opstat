#!/usr/bin/env python3
"""Shared VMS drill-down machinery: candidate ranking, batch monitors, throttle.

Every protocol engine offers the same drill-down shape - pick the most active
cNodes / views / tenants, keep a monitor on them, refresh alongside the
dashboard - and every engine had grown its own copy. The copies diverged in
ways that mattered on a real cluster:

* ranking by creating, querying and deleting one temporary monitor per 32
  objects, serially, which cost 42 requests and ~47 s to rank 429 views;
* or no ranking at all, so the drill showed whichever objects ``/views/``
  happened to list first - typically eight idle ones;
* one monitor per object, so a refresh tick issued one query per row.

This module holds the version that was measured and fixed. Engines supply
their own metric families, row builders and activity scoring; the ordering,
batching, capability discovery and caching live here.
"""

import time
import urllib.parse
from datetime import datetime

import tui_layout
import vast_common
from tui_layout import as_float, raw_bw_to_gb_sec

# ---------------------------------------------------------------------------
# Object-scope metric families
# ---------------------------------------------------------------------------
# NfsMetrics/ProtoMetrics are cluster- and cNode-scoped; a monitor asking for
# them with object_type=view or =tenant is rejected by current VMS builds.
# View and tenant scopes have their own families, and they differ in kind:
# ViewMetrics publishes instantaneous rates, TenantMetrics publishes
# cumulative counters that must be differentiated over the sample window.
VIEW_READ_IOPS = "ViewMetrics,read_iops__rate"
VIEW_WRITE_IOPS = "ViewMetrics,write_iops__rate"
VIEW_READ_MD = "ViewMetrics,read_md_iops__rate"
VIEW_WRITE_MD = "ViewMetrics,write_md_iops__rate"
VIEW_READ_LAT = "ViewMetrics,read_latency__avg"
VIEW_WRITE_LAT = "ViewMetrics,write_latency__avg"
VIEW_READ_BW = "ViewMetrics,read_bw__rate"
VIEW_WRITE_BW = "ViewMetrics,write_bw__rate"

TENANT_READ_IOPS = "TenantMetrics,read_iops__sum"
TENANT_WRITE_IOPS = "TenantMetrics,write_iops__sum"
TENANT_READ_MD = "TenantMetrics,read_md_iops__sum"
TENANT_WRITE_MD = "TenantMetrics,write_md_iops__sum"
TENANT_READ_BW = "TenantMetrics,read_bw__sum"
TENANT_WRITE_BW = "TenantMetrics,write_bw__sum"
TENANT_READ_LAT = "TenantMetrics,read_latency__sum"
TENANT_WRITE_LAT = "TenantMetrics,write_latency__sum"
TENANT_READ_CNT = "TenantMetrics,read_iops__num_samples"
TENANT_WRITE_CNT = "TenantMetrics,write_iops__num_samples"
TENANT_READ_MD_CNT = "TenantMetrics,read_md_iops__num_samples"
TENANT_WRITE_MD_CNT = "TenantMetrics,write_md_iops__num_samples"

# View monitors need seconds resolution without aggregation; tenant monitors
# keep the default avg aggregation over the frame.
VIEW_NO_AGGREGATION = True
TENANT_NO_AGGREGATION = False


def view_display_props():
    return [
        VIEW_READ_IOPS, VIEW_WRITE_IOPS, VIEW_READ_MD, VIEW_WRITE_MD,
        VIEW_READ_LAT, VIEW_WRITE_LAT, VIEW_READ_BW, VIEW_WRITE_BW,
    ]


def view_rank_props():
    """Minimal props for ranking view candidates by activity."""
    return [VIEW_READ_IOPS, VIEW_WRITE_IOPS, VIEW_READ_MD, VIEW_WRITE_MD]


def tenant_display_props():
    return [
        TENANT_READ_IOPS, TENANT_WRITE_IOPS, TENANT_READ_MD, TENANT_WRITE_MD,
        TENANT_READ_BW, TENANT_WRITE_BW, TENANT_READ_LAT, TENANT_WRITE_LAT,
        TENANT_READ_CNT, TENANT_WRITE_CNT, TENANT_READ_MD_CNT,
        TENANT_WRITE_MD_CNT,
    ]


def tenant_rank_props():
    return [TENANT_READ_IOPS, TENANT_WRITE_IOPS, TENANT_READ_MD, TENANT_WRITE_MD]

# ---------------------------------------------------------------------------
# Monitor result helpers (identical in every engine)
# ---------------------------------------------------------------------------


def result_parts(result):
    """Return (prop_list, data, prop_idx) from a monitor query result dict."""
    if not isinstance(result, dict):
        return [], [], {}
    prop_list = result.get("prop_list", []) or []
    data = result.get("data", []) or []
    return prop_list, data, {name: idx for idx, name in enumerate(prop_list)}


def normalize_object_id(value):
    """Coerce a VMS object_id for reliable batch-monitor slicing."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def slice_result_for_object(result, object_id):
    """Return a monitor query payload containing only one object_id's samples.

    A batch monitor's response carries every object's rows interleaved under
    an ``object_id`` column. Results without that column are passed through
    unchanged, which is what a single-object monitor returns.
    """
    if not isinstance(result, dict):
        return result
    prop_list, data, prop_idx = result_parts(result)
    oid_idx = prop_idx.get("object_id")
    if oid_idx is None:
        return result
    want = normalize_object_id(object_id)
    return {
        "prop_list": prop_list,
        "data": [
            row for row in data
            if len(row) > oid_idx and normalize_object_id(row[oid_idx]) == want
        ],
    }


def topn_titles(payload, object_type):
    """Flatten a /monitors/topn/ payload into an ordered list of object titles."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    block = data.get(object_type)
    if isinstance(block, dict):
        buckets = [rows for rows in block.values() if isinstance(rows, list)]
    elif isinstance(block, list):
        buckets = [block]
    else:
        return []
    for rows in buckets:
        titles = [row["title"] for row in rows
                  if isinstance(row, dict) and row.get("title")]
        if titles:
            return titles      # one metric bucket establishes the order
    return []


# ---------------------------------------------------------------------------
# View / tenant row construction
# ---------------------------------------------------------------------------
def parse_sample_ts(sample):
    if not sample or sample == "-":
        return None
    try:
        return datetime.fromisoformat(str(sample).replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_complete_values(result):
    """(values, prop_idx, sample) from the newest usable row of *result*."""
    _prop_list, data, prop_idx = result_parts(result)
    values, sample = vast_common.latest_complete_values(data, prop_idx)
    return values, prop_idx, sample


def delta_rate_from_samples(result, sum_fqn):
    """Average rate derived from cumulative __sum samples in a monitor query."""
    _prop_list, data, prop_idx = result_parts(result)
    idx = prop_idx.get(sum_fqn)
    if idx is None:
        return None
    newest, oldest = vast_common.bounding_samples(data, idx)
    if newest is None:
        return None
    t_new, t_old = parse_sample_ts(newest[0]), parse_sample_ts(oldest[0])
    if not t_new or not t_old:
        return None
    dt = abs((t_new - t_old).total_seconds())
    if dt <= 0:
        return None
    return max(as_float(newest[idx]) - as_float(oldest[idx]), 0.0) / dt


def avg_from_sum_count_deltas(result, sum_fqn, count_fqn):
    """Mean value per operation from paired cumulative sum/count counters."""
    _prop_list, data, prop_idx = result_parts(result)
    idx_s, idx_c = prop_idx.get(sum_fqn), prop_idx.get(count_fqn)
    if idx_s is None or idx_c is None:
        return None
    newest, oldest = vast_common.bounding_samples(data, idx_s, idx_c)
    if newest is None:
        return None
    cnt_delta = as_float(newest[idx_c]) - as_float(oldest[idx_c])
    if cnt_delta <= 0:
        return None
    return (as_float(newest[idx_s]) - as_float(oldest[idx_s])) / cnt_delta


def weighted_us(pairs):
    """Op-weighted mean of (weight, microseconds) pairs."""
    valid = [(w, v) for w, v in pairs if (w or 0) > 0 and v is not None]
    weight = sum(w for w, _v in valid)
    if weight <= 0:
        return None
    return sum(w * v for w, v in valid) / weight


def top_op(op_pairs):
    """Return (busiest label, its share of the active total) or ('-', None)."""
    active = [(label, ops) for label, ops in op_pairs if (ops or 0) > 0]
    if not active:
        return "-", None
    label, ops = max(active, key=lambda item: item[1])
    total = sum(o for _l, o in active)
    return label, (ops / total * 100.0) if total > 0 else None


def build_view_row(result, obj_name):
    """One VIEW drill row from a ViewMetrics monitor slice (instantaneous rates)."""
    values, _prop_idx, _sample = latest_complete_values(result)
    read_ops = as_float(values.get(VIEW_READ_IOPS)) or 0.0
    write_ops = as_float(values.get(VIEW_WRITE_IOPS)) or 0.0
    read_md = as_float(values.get(VIEW_READ_MD)) or 0.0
    write_md = as_float(values.get(VIEW_WRITE_MD)) or 0.0
    total_ops = read_ops + write_ops + read_md + write_md
    latency = weighted_us([
        (read_ops, as_float(values.get(VIEW_READ_LAT))),
        (write_ops, as_float(values.get(VIEW_WRITE_LAT))),
    ])
    bw = ((raw_bw_to_gb_sec(values.get(VIEW_READ_BW)) or 0.0)
          + (raw_bw_to_gb_sec(values.get(VIEW_WRITE_BW)) or 0.0))
    label, pct = top_op([
        ("READ", read_ops), ("WRITE", write_ops),
        ("RD MD", read_md), ("WR MD", write_md),
    ])
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": bw if bw > 0 else None,
        "top_rpc": label,
        "top_rpc_pct": pct,
    }


def build_tenant_row(result, obj_name):
    """One TENANT drill row from cumulative TenantMetrics counters."""
    read_ops = delta_rate_from_samples(result, TENANT_READ_IOPS) or 0.0
    write_ops = delta_rate_from_samples(result, TENANT_WRITE_IOPS) or 0.0
    read_md = delta_rate_from_samples(result, TENANT_READ_MD) or 0.0
    write_md = delta_rate_from_samples(result, TENANT_WRITE_MD) or 0.0
    total_ops = read_ops + write_ops + read_md + write_md
    latency = weighted_us([
        (read_ops, avg_from_sum_count_deltas(
            result, TENANT_READ_LAT, TENANT_READ_CNT)),
        (write_ops, avg_from_sum_count_deltas(
            result, TENANT_WRITE_LAT, TENANT_WRITE_CNT)),
    ])
    bw = ((raw_bw_to_gb_sec(delta_rate_from_samples(result, TENANT_READ_BW)) or 0.0)
          + (raw_bw_to_gb_sec(delta_rate_from_samples(result, TENANT_WRITE_BW)) or 0.0))
    label, pct = top_op([
        ("READ", read_ops), ("WRITE", write_ops),
        ("RD MD", read_md), ("WR MD", write_md),
    ])
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": bw if bw > 0 else None,
        "top_rpc": label,
        "top_rpc_pct": pct,
    }


def coverage_fraction(shown_ops, cluster_ops):
    """Share of cluster activity the drill rows account for, or None.

    Returns None when the comparison is not meaningful: no cluster activity,
    or a share so far above 100% that the two numbers are plainly not on the
    same footing (tenant rates come from differentiating cumulative counters
    over the sample window, cluster rates are instantaneous, and the windows
    need not line up). Callers should say the scopes are not comparable
    rather than print a misleading percentage.
    """
    if not cluster_ops or cluster_ops <= 0 or shown_ops is None:
        return None
    fraction = shown_ops / cluster_ops
    return None if fraction > 1.5 else fraction


class DrillSession:
    """Per-run drill state: learned cluster capabilities and cached rankings.

    ``create_monitor_fn(name_suffix, prop_list, object_type, object_ids)`` and
    ``delete_monitor_fn(monitor_id)`` are the engine's own helpers, so API-call
    logging and monitor-teardown registration keep working unchanged.
    """

    def __init__(self, *, request_fn, create_monitor_fn, delete_monitor_fn,
                 max_objects=8, min_batch=32, topn_limit=32,
                 cache_ttl=300.0, min_query_interval=15.0):
        self._request = request_fn
        self._create_monitor = create_monitor_fn
        self._delete_monitor = delete_monitor_fn
        self.max_objects = max_objects
        self.min_batch = min_batch
        self.topn_limit = topn_limit
        self.cache_ttl = cache_ttl
        self.min_query_interval = min_query_interval
        self.reset()

    def reset(self):
        """Clear learned capabilities and cached rankings (new run, or tests)."""
        self._rank_cache = {}
        self._rank_chunk_size = None
        self._batch_unsupported = set()
        self._last_query_at = 0.0

    # -- poll throttling ---------------------------------------------------
    def should_query(self, force=False, have_data=True):
        """True when the drill monitors are due for a re-query.

        Object-scoped metric families publish roughly once a minute, so a 5 s
        dashboard tick re-fetched byte-identical payloads. Returning False
        skips those without making the panel any staler.
        """
        now = time.monotonic()
        if force or not have_data:
            self._last_query_at = now
            return True
        if now - self._last_query_at < self.min_query_interval:
            return False
        self._last_query_at = now
        return True

    def note_queried(self):
        self._last_query_at = time.monotonic()

    # -- ranking -----------------------------------------------------------
    def rank(self, mode, objects, *, object_type, rank_props, score_fn,
             time_frame, name_of, no_aggregation=False, use_topn=True):
        """Return the most active ``max_objects`` candidates as {id, name} dicts.

        ``score_fn(sliced_result)`` returns one object's activity; ``name_of``
        maps a raw VMS object dict to its display name.
        """
        if not objects:
            return []
        cached = self._cached(mode, objects)
        if cached is not None:
            return cached
        ranked = None
        if use_topn:
            ranked = self._rank_via_topn(
                objects, object_type, rank_props, time_frame, name_of,
            )
        if ranked is None:
            ranked = self._rank_via_monitors(
                mode, objects, object_type, rank_props, score_fn, time_frame,
                name_of, no_aggregation,
            )
        self._store(mode, objects, ranked)
        return ranked

    def _rank_via_topn(self, objects, object_type, rank_props, time_frame, name_of):
        """One server-side GET /monitors/topn/, or None when it is not usable.

        Creates no monitors. Best-effort: absent on some builds, and it
        identifies objects by title rather than id, so the result is accepted
        only when enough titles map back to real objects. When every candidate
        already fits in one rank monitor that scan is both cheap and exact, so
        skip the extra round trip entirely.
        """
        if len(objects) <= self.min_batch or not rank_props:
            return None
        path = (
            "/monitors/topn/?object_type=" + urllib.parse.quote(object_type, safe="")
            + "&prop_list=" + urllib.parse.quote(rank_props[0], safe=",")
            + "&time_frame=" + urllib.parse.quote(str(time_frame), safe="")
            + "&limit=%d" % self.topn_limit
        )
        try:
            payload = self._request("GET", path)
        except RuntimeError:
            return None
        titles = topn_titles(payload, object_type)
        if not titles:
            return None
        by_name = {}
        for obj in objects:
            by_name.setdefault(name_of(obj).lower(), obj)
        ranked, seen = [], set()
        for title in titles:
            obj = by_name.get(str(title).lower())
            if obj is None or obj["id"] in seen:
                continue
            seen.add(obj["id"])
            ranked.append({"id": obj["id"], "name": name_of(obj)})
            if len(ranked) >= self.max_objects:
                break
        # A short list means topn and the object endpoint disagree about
        # naming, or the cluster is near-idle; the monitor scan is
        # authoritative either way.
        if len(ranked) < min(self.max_objects, len(objects)):
            return None
        return ranked

    def _rank_chunk_sizes(self, total):
        """Batch sizes to try, largest first: everything, then stepping down.

        A cluster that refuses an oversized ``object_ids`` list fails the
        create fast, so this discovers the real cap in a few extra POSTs and
        the working size is reused for the rest of the run.
        """
        if self._rank_chunk_size:
            return [min(self._rank_chunk_size, total)]
        ordered = []
        for size in (total, 256, 128, 64, self.min_batch):
            size = min(size, total)
            if size >= 1 and size not in ordered:
                ordered.append(size)
        return sorted(ordered, reverse=True)

    def _rank_scan(self, mode, objects, object_type, rank_props, score_fn,
                   time_frame, id_to_name, no_aggregation, chunk_size):
        """Rank every object using monitors of *chunk_size* ids each.

        Re-raises if the very first create is rejected so the caller can retry
        with a smaller batch instead of reporting the cluster as idle.
        """
        ranked = []
        for start in range(0, len(objects), chunk_size):
            chunk = objects[start:start + chunk_size]
            object_ids = [obj["id"] for obj in chunk]
            monitor_id = None
            try:
                monitor_id = self._create_monitor(
                    "rank_%s_%d" % (mode, start), rank_props, object_type,
                    object_ids, no_aggregation=no_aggregation,
                )
                result = self._request("GET", "/monitors/%s/query/" % monitor_id)
                for obj_id in object_ids:
                    score = score_fn(slice_result_for_object(result, obj_id))
                    ranked.append({"id": obj_id, "name": id_to_name[obj_id],
                                   "total_ops": score or 0.0})
            except RuntimeError:
                if start == 0:
                    raise
                # A later chunk failing is not worth losing the whole drill
                # for; treat those objects as idle.
                for obj_id in object_ids:
                    ranked.append({"id": obj_id, "name": id_to_name[obj_id],
                                   "total_ops": 0.0})
            finally:
                if monitor_id is not None:
                    self._delete_monitor(monitor_id)
        return ranked

    def _rank_via_monitors(self, mode, objects, object_type, rank_props,
                           score_fn, time_frame, name_of, no_aggregation):
        id_to_name = {obj["id"]: name_of(obj) for obj in objects}
        ranked = None
        for chunk_size in self._rank_chunk_sizes(len(objects)):
            try:
                ranked = self._rank_scan(
                    mode, objects, object_type, rank_props, score_fn,
                    time_frame, id_to_name, no_aggregation, chunk_size,
                )
            except RuntimeError:
                continue
            self._rank_chunk_size = chunk_size
            break
        if ranked is None:
            # Every batch size was refused: keep the objects so the drill can
            # still open, ordered by name so the list is at least stable.
            ranked = [{"id": obj["id"], "name": id_to_name[obj["id"]],
                       "total_ops": 0.0} for obj in objects]
        ranked.sort(key=lambda item: (-item["total_ops"], str(item["name"]).lower()))
        return [{"id": item["id"], "name": item["name"]}
                for item in ranked[:self.max_objects]]

    def _signature(self, objects):
        return len(objects), tuple(sorted(str(obj["id"]) for obj in objects))[:64]

    def _cached(self, mode, objects):
        entry = self._rank_cache.get(mode)
        if not entry:
            return None
        stamped, signature, ranked = entry
        if signature != self._signature(objects):
            return None
        if time.monotonic() - stamped > self.cache_ttl:
            return None
        return list(ranked)

    def _store(self, mode, objects, ranked):
        self._rank_cache[mode] = (
            time.monotonic(), self._signature(objects), list(ranked),
        )

    # -- monitor creation --------------------------------------------------
    def create_monitors(self, mode, drill_objects, *, object_type, props,
                        no_aggregation=False, validate_batch=False):
        """Create drill monitors, batched into one where the cluster allows it.

        Returns ``(monitors, error)`` where monitors is a list of
        ``(monitor_id, object_name_or_None)``; a single entry with a None name
        means one batch monitor covers every object. ``validate_batch`` spends
        one extra query confirming the response really can be split per
        object before committing to the batch layout - worth it for scopes
        where per-object rows are inferred rather than documented.
        """
        object_ids = [obj["id"] for obj in drill_objects]
        if not object_ids:
            return [], "no objects selected"

        if mode not in self._batch_unsupported:
            monitor_id, error = self._create_batch(
                mode, object_ids, object_type, props, no_aggregation, validate_batch,
            )
            if monitor_id is not None:
                return [(monitor_id, None)], None
            batch_error = error
        else:
            batch_error = None

        monitors, last_error = [], batch_error
        for obj in drill_objects:
            try:
                monitors.append((
                    self._create_monitor(
                        "%s_%s" % (mode, obj["id"]), props, object_type,
                        [obj["id"]], no_aggregation=no_aggregation,
                    ),
                    obj["name"],
                ))
            except RuntimeError as exc:
                last_error = str(exc)
        return monitors, (None if monitors else last_error)

    def _create_batch(self, mode, object_ids, object_type, props,
                      no_aggregation, validate):
        try:
            monitor_id = self._create_monitor(
                "%s_batch" % mode, props, object_type, object_ids,
                no_aggregation=no_aggregation,
            )
        except RuntimeError as exc:
            self._batch_unsupported.add(mode)
            return None, str(exc)

        if not validate or len(object_ids) < 2:
            return monitor_id, None
        try:
            result = self._request("GET", "/monitors/%s/query/" % monitor_id)
        except RuntimeError as exc:
            self._delete_monitor(monitor_id)
            self._batch_unsupported.add(mode)
            return None, str(exc)
        _props, _data, prop_idx = result_parts(result)
        splits = {
            len(slice_result_for_object(result, oid).get("data") or [])
            for oid in object_ids
        }
        if "object_id" in prop_idx and splits != {0}:
            return monitor_id, None
        self._delete_monitor(monitor_id)
        self._batch_unsupported.add(mode)
        return None, "%s batch monitor is not splittable per object" % mode

    def batch_active(self, monitors):
        """True when *monitors* is the single-batch layout."""
        return len(monitors) == 1 and monitors[0][1] is None


# ---------------------------------------------------------------------------
# Canonical navigation contract (FR-A)
# ---------------------------------------------------------------------------
# Same conceptual action -> same key, same label, same relative order in every
# engine's footer. Protocol-specific controls are appended AFTER the common
# set. Two bindings are load-bearing prohibitions: VIP is [i] (never [v], which
# means the View drill), and exit-drill is [x] (never [p], an old NVMe binding
# that survived into help text after the key itself changed).
#
# An engine advertises only the subset it actually supports - the contract
# standardizes what exists, it does not invent controls.
CANONICAL_CONTROLS = (
    ("q", "Quit"),
    ("o", "Ops"),
    ("l", "Lat"),
    ("n", "Name"),
    ("c", "cNode"),
    ("v", "View"),
    ("t", "Tenant"),
    ("i", "VIP"),
    ("x", "Exit drill"),
    ("space", "Refresh"),
)

_CANONICAL_ORDER = {key: idx for idx, (key, _label) in enumerate(CANONICAL_CONTROLS)}
_CANONICAL_LABELS = dict(CANONICAL_CONTROLS)


def nav_controls(common, extra=()):
    """Build an engine's footer control list from the canonical contract.

    ``common`` names the canonical keys the engine supports; they come back in
    canonical order with canonical labels regardless of the order given.
    ``extra`` is the engine's protocol-specific ``(key, label)`` tuples,
    appended after the common set. A non-canonical key in ``common`` is a
    programming error and raises immediately rather than rendering a footer
    that silently diverges from the contract.
    """
    unknown = [key for key in common if key not in _CANONICAL_ORDER]
    if unknown:
        raise ValueError("non-canonical nav keys: %s" % ", ".join(unknown))
    ordered = sorted(set(common), key=_CANONICAL_ORDER.get)
    return tuple((key, _CANONICAL_LABELS[key]) for key in ordered) + tuple(extra)


def nav_legend(controls):
    """Render ``[key] Label | [key] Label ...`` footer text from control tuples.

    One shared renderer so the look (brackets, pipes, spacing, dim/bright
    split) cannot drift between engines. Engines wrap the returned string in
    their own frame furniture (``box_row`` or a plain line).
    """
    parts = []
    for key, label in controls:
        if parts:
            parts.append(tui_layout.c("|", tui_layout._DIM))
        parts.append(
            tui_layout.c("[%s]" % key, tui_layout._BWHITE)
            + tui_layout.c(" %s " % label, tui_layout._DIM)
        )
    return "".join(parts).rstrip()


# ---------------------------------------------------------------------------
# Drill-entry loading status
# ---------------------------------------------------------------------------
# Entering a drill can block for seconds against a real VMS: ranking hundreds
# of views, creating monitors, or scraping a 276 KB exporter endpoint. The
# message has to reach the terminal *before* that work starts, or the TUI
# simply looks hung.
LOADING_MESSAGES = {
    "startup": "Gathering initial metrics, please stand by...",
    "cnode": "Loading the cNODE drill-down, please stand by...",
    "view": "Loading the VIEW drill-down, please stand by...",
    "views": "Loading the NFSv4 views, please stand by...",
    "tenant": "Loading the TENANT drill-down, please stand by...",
    "vip": "Loading the VIP drill-down, please stand by...",
    "host": "Loading the HOST drill-down, please stand by...",
    "native": "Loading the NFSv4 telemetry view, please stand by...",
    "hosts": "Loading the NFSv4 hosts view, please stand by...",
}


def loading_message(mode):
    """The stand-by message for a drill mode."""
    return LOADING_MESSAGES.get(
        mode, f"Loading the {str(mode).upper()} drill-down, please stand by...")


def with_loading_status(show_status, render, mode, work):
    """Paint a loading frame, then run blocking drill initialisation.

    ``show_status(text_or_None)`` sets the engine's status global and
    ``render()`` flushes one frame. Both run before *work* so the user sees
    progress rather than a frozen screen; the status is always cleared, even
    if the work raises.
    """
    show_status(loading_message(mode))
    try:
        render()
        return work()
    finally:
        show_status(None)


def with_startup_status(show_status, render, steps):
    """Run each blocking startup step behind its own status frame.

    ``steps`` is a sequence of ``(message, work)`` pairs run in order. For each
    step the status is set, one frame is rendered, *then* the blocking work
    runs — so the terminal shows what the process is waiting on before it
    blocks, and the message visibly changes as startup progresses (the engines
    are single-threaded, so this frame-per-phase is the progress signal; there
    is no spinner).

    ``message`` may be a string or a zero-argument callable returning one, so a
    later step can name the cluster once an earlier step has resolved it (the
    cluster name is unknown until the first call returns). The status is cleared
    in a ``finally`` so it never survives startup, including when a step raises.
    """
    try:
        for message, work in steps:
            show_status(message() if callable(message) else message)
            render()
            work()
    finally:
        show_status(None)
