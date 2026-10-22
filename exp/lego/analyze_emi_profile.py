#!/usr/bin/env python3
"""Attribute EMI CUDA transfers to PETSc NVTX ranges in an Nsight SQLite export."""
import argparse
import collections
import heapq
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{args.sqlite.resolve()}?mode=ro", uri=True)
    begin, end = db.execute(
        "SELECT start,end FROM NVTX_EVENTS WHERE text='openCARP simulation loop'"
    ).fetchone()
    ranges = db.execute(
        "SELECT start,end,text,globalTid FROM NVTX_EVENTS "
        "WHERE text IS NOT NULL AND start>=? AND end<=? ORDER BY start",
        (begin, end),
    ).fetchall()
    # Match each copy's CPU API to the innermost enclosing named PETSc range.
    # Stream the range starts and keep a separate nesting heap per CPU thread.
    copies = db.execute(
        "SELECT a.start,a.end,a.globalTid,m.copyKind,m.bytes "
        "FROM CUPTI_ACTIVITY_KIND_MEMCPY m "
        "JOIN CUPTI_ACTIVITY_KIND_RUNTIME a ON a.correlationId=m.correlationId "
        "AND (a.globalTid >> 24)=(m.globalPid >> 24) "
        "WHERE m.start>=? AND m.end<=? ORDER BY a.start", (begin, end),
    ).fetchall()
    heaps = collections.defaultdict(list)
    cursor = 0
    transfers = collections.defaultdict(lambda: dict(calls=0, bytes=0))
    directions = {1: "H2D", 2: "D2H", 8: "D2D"}
    for start, stop, thread, kind, size in copies:
        while cursor < len(ranges) and ranges[cursor][0] <= start:
            left, right, name, tid = ranges[cursor]
            heapq.heappush(heaps[tid], (-left, right, name))
            cursor += 1
        heap = heaps[thread]
        while heap and heap[0][1] < stop:
            heapq.heappop(heap)
        name = heap[0][2] if heap else "outside named PETSc events"
        key = (directions.get(kind, str(kind)), size, name)
        transfers[key]["calls"] += 1
        transfers[key]["bytes"] += size
    solves = db.execute(
        "SELECT start,end FROM NVTX_EVENTS WHERE text='KSPSolve' "
        "AND start>=? AND end<=? ORDER BY start", (begin, end),
    ).fetchall()
    event_times = db.execute(
        "SELECT text,count(*),sum(end-start)*1e-9 FROM NVTX_EVENTS "
        "WHERE start>=? AND end<=? AND text IN "
        "('KSPSolve','VecTDot','VecNorm','VecShift','VecCUDACopyTo',"
        "'VecCUDACopyFrom','DCtxSync') GROUP BY text", (begin, end),
    ).fetchall()
    phases = []
    last_api = db.execute("SELECT max(end) FROM CUPTI_ACTIVITY_KIND_RUNTIME").fetchone()[0]
    for label, left, right in (("before_loop", 0, begin),
                               ("loop", begin, end),
                               ("after_loop", end, max(end, last_api))):
        # These are trace-relative boundaries, not exact CPU process lifetime.
        phase_copies = db.execute(
            "SELECT copyKind,count(*),sum(bytes) FROM CUPTI_ACTIVITY_KIND_MEMCPY "
            "WHERE start>=? AND end<=? GROUP BY copyKind", (left, right),
        ).fetchall()
        sync = db.execute(
            "SELECT count(*),coalesce(sum(a.end-a.start),0)*1e-9 "
            "FROM CUPTI_ACTIVITY_KIND_RUNTIME a JOIN StringIds s ON s.id=a.nameId "
            "WHERE a.start>=? AND a.end<=? AND s.value LIKE 'cudaDeviceSynchronize%'",
            (left, right),
        ).fetchone()
        phases.append(dict(
            phase=label, trace_seconds=(right-left)/1e9,
            copies=[dict(direction=directions.get(kind, str(kind)), calls=n, bytes=size)
                    for kind, n, size in phase_copies],
            device_sync_calls=sync[0], device_sync_seconds=sync[1],
        ))
    # Kernel launches and memcopies correlate with CPU APIs, not necessarily
    # their execution-time nesting. The loop-level analyzer reports GPU unions.
    result = dict(
        loop_seconds=(end - begin) / 1e9,
        solve_count=len(solves),
        solve_seconds=sum(right - left for left, right in solves) / 1e9,
        named_event_times=[dict(event=name, calls=n, seconds=seconds)
                           for name, n, seconds in event_times],
        phases=phases,
        transfer_attribution=[dict(direction=direction, size=size, event=event, **value)
                              for (direction, size, event), value in sorted(
                                  transfers.items(), key=lambda x: x[1]["calls"], reverse=True)],
        notes=["CPU event times overlap and must not be summed.",
               "Copy attribution uses the enclosing PETSc event at CPU API submission.",
               "Initialization of lazy GPU resources can occur inside the loop.",
               "Managed-memory migrations are not included in explicit-copy counts."],
    )
    db.close()
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
