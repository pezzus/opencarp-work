#!/usr/bin/env python3
"""Summarize CUDA activity strictly inside the openCARP simulation-loop range."""
import argparse
import collections
import json
from pathlib import Path
import sqlite3


def union_ns(intervals):
    end = None
    total = 0
    for left, right in sorted(intervals):
        if end is None or left >= end:
            total += right - left
            end = right
        elif right > end:
            total += right - end
            end = right
    return total


def summarize(path, range_name):
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    ranges = connection.execute(
        "SELECT e.start,e.end FROM NVTX_EVENTS e LEFT JOIN StringIds s ON e.textId=s.id "
        "WHERE COALESCE(e.text,s.value)=? AND e.end>e.start "
        "ORDER BY e.end-e.start DESC", (range_name,)).fetchall()
    if len(ranges) != 1:
        raise ValueError(f"Expected one completed loop range, found {len(ranges)}")
    begin, end = ranges[0]
    duration = end - begin

    def events(table, extra=""):
        if table not in tables:
            return []
        return connection.execute(
            f"SELECT start,end{extra} FROM {table} "
            "WHERE end>? AND start<?", (begin, end)).fetchall()

    def clipped(records):
        return [(max(begin, row[0]), min(end, row[1])) for row in records]

    kernels = events("CUPTI_ACTIVITY_KIND_KERNEL", ",demangledName")
    copies = events("CUPTI_ACTIVITY_KIND_MEMCPY", ",bytes,copyKind")
    memsets = events("CUPTI_ACTIVITY_KIND_MEMSET")
    kernel_intervals = clipped(kernels)
    copy_intervals = clipped(copies)
    all_intervals = kernel_intervals + copy_intervals + clipped(memsets)
    names = dict(connection.execute("SELECT id,value FROM StringIds"))
    kernel_totals = collections.defaultdict(lambda: [0, 0])
    for left, right, name in kernels:
        entry = kernel_totals[names.get(name, str(name))]
        entry[0] += 1
        entry[1] += min(end, right) - max(begin, left)
    copy_totals = collections.defaultdict(lambda: {"count": 0, "bytes": 0, "sizes": collections.Counter()})
    directions = {1: "H2D", 2: "D2H", 8: "D2D"}
    for left, right, size, kind in copies:
        if left < begin or right > end:
            continue  # Do not invent a partial byte count at a range boundary.
        entry = copy_totals[directions.get(kind, str(kind))]
        entry["count"] += 1
        entry["bytes"] += size
        entry["sizes"][size] += 1
    for entry in copy_totals.values():
        entry["sizes"] = sorted(
            (dict(bytes=size, count=count) for size, count in entry["sizes"].items()),
            key=lambda x: x["bytes"] * x["count"], reverse=True)[:8]
    api_totals = collections.defaultdict(lambda: [0, 0])
    for table in ("CUPTI_ACTIVITY_KIND_RUNTIME", "CUPTI_ACTIVITY_KIND_DRIVER"):
        for left, right, name in events(table, ",nameId"):
            entry = api_totals[names.get(name, str(name))]
            entry[0] += 1
            entry[1] += min(end, right) - max(begin, left)

    def ranked(totals, limit):
        return [dict(name=name, calls=count, seconds=ns / 1e9)
                for name, (count, ns) in sorted(
                    totals.items(), key=lambda item: item[1][1], reverse=True)[:limit]]

    result = dict(
        range=range_name, loop_seconds=duration / 1e9,
        kernel_count=len(kernels),
        kernel_active_seconds=union_ns(kernel_intervals) / 1e9,
        copy_active_seconds=union_ns(copy_intervals) / 1e9,
        traced_gpu_active_seconds=union_ns(all_intervals) / 1e9,
        kernel_active_percent=100 * union_ns(kernel_intervals) / duration,
        traced_gpu_active_percent=100 * union_ns(all_intervals) / duration,
        transfers=dict(copy_totals),
        top_kernels=ranked(kernel_totals, 8),
        top_cuda_apis=ranked(api_totals, 10),
        note="Activity coverage is not SM utilization, achieved FLOP/s, or percent "
             "of peak memory bandwidth. API and device times overlap. Untraced "
             "work such as managed-memory migrations can affect the gaps.")
    connection.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite", type=Path)
    parser.add_argument("--range", default="openCARP simulation loop")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(summarize(args.sqlite, args.range), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")


if __name__ == "__main__":
    main()
