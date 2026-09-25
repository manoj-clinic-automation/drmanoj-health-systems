#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_ram.py -- memory per family member, measured, not estimated.

FAMILY_EDITION_V1. Starts one SCRATCH member (famtest.Rig: scratch folders,
loopback ports, never a real member), loads its pages the way a phone would,
then reads the resident memory of its three processes from /proc, and states
how many members this server can hold with the memory that is free now,
keeping a stated reserve. Linux only.

    python3 -B measure_ram.py /root/gutlog/app.py
Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402

RESERVE_MB = 1536


def rss_kb(pid):
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def mem_available_mb():
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return 0


def main():
    rig = famtest.Rig(sys.argv[1], members=(("m1", "Member A", "joint"),))
    try:
        c = rig.member_client("m1")
        c.post("/m1/welcome", {"name": "Member A", "age": "60", "cond": "knee_oa"})
        for p in ("/m1/", "/m1/api/now", "/m1/api/joint/watch", "/m1/api/painmeds", "/m1/kitchen",
                  "/m1/api/kitchen/recipes", "/m1/rx/", "/m1/rx/astaken", "/m1/fit/", "/m1/fit/watch"):
            c.get(p, follow=True)
        per = {}
        for label, p, _log in rig.procs:
            per[label] = rss_kb(p.pid) // 1024
        member = sum(v for k, v in per.items() if k.startswith("m1-"))
        free = mem_available_mb()
        n = max(0, (free - RESERVE_MB) // member) if member else 0
        print("per process (MB): " + ", ".join("%s %d" % kv for kv in sorted(per.items())))
        print("one member (3 processes): %d MB" % member)
        print("available now: %d MB; reserve kept: %d MB" % (free, RESERVE_MB))
        print("capacity: about %d more members at this size" % n)
    finally:
        rig.stop()


if __name__ == "__main__":
    main()
