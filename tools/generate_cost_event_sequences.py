#!/usr/bin/env python3
"""Build one SVG with local and partitioned event sequences from the latest syslog.

Run ``python3 tools/generate_cost_event_sequences.py`` from any directory.
The output stays in tools/. No third-party package is needed.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIRECTORY = ROOT / "logs"
OUTPUT = Path(__file__).with_name("cost_event_sequences.svg")
WIDTH, HEIGHT = 1600, 1950
INK, PAPER, WHITE, MUTED = "#293444", "#f4f4f4", "#ffffff", "#667283"
MAIN, WORKER_1, WORKER_2, TEAL = "#e8247f", "#5956c9", "#3db9bd", "#138572"
LINE = "#d4dce5"
SYSLOG = re.compile(r"^<\d+>1 (?P<timestamp>\S+) .*?\ufeff?(?P<event>\[(?:main|worker \d+)\].*)$")


@dataclass(frozen=True)
class Entry:
    time: datetime
    event: str
    line: int


def parse_log(path: Path) -> list[Entry]:
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        match = SYSLOG.match(line)
        if not match:
            raise ValueError(f"Unrecognized syslog entry at {path}:{number}")
        entries.append(Entry(datetime.fromisoformat(match["timestamp"].replace("Z", "+00:00")),
                             match["event"], number))
    return entries


def event(entries: list[Entry], fragment: str, *, source: str | None = None,
          occurrence: int | None = None) -> Entry:
    matches = [entry for entry in entries if fragment in entry.event
               and (source is None or entry.event.startswith(f"[{source}] "))]
    if not matches:
        raise ValueError(f"Missing syslog event: {fragment}")
    if occurrence is None and len(matches) != 1:
        raise ValueError(f"Ambiguous syslog event: {fragment} ({len(matches)} matches)")
    return matches[occurrence or 0]


def duration(start: Entry, end: Entry) -> str:
    value = (end.time - start.time).total_seconds()
    return f"{value:.6f} s" if value >= 1 else f"{value * 1000:.3f} ms"


def worker_duration(entries: list[Entry], worker: int, request_id: int) -> tuple[Entry, str]:
    entry = event(entries, "function_execution=OK; elapsed=", source=f"worker {worker}")
    if f"by id: {request_id} " not in entry.event:
        raise ValueError(f"Unexpected execution ID on syslog line {entry.line}")
    match = re.search(r"elapsed=([0-9.]+)s", entry.event)
    if not match:
        raise ValueError(f"Missing execution duration on syslog line {entry.line}")
    return entry, f"{float(match[1]):.6f} s"


def text(x: float, y: float, value: str, *, size: int = 16, color: str = INK,
         weight: int = 400, anchor: str = "start") -> str:
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
            'font-family="Inter, Avenir, Helvetica, Arial, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}">{escape(value)}</text>')


def card(x: int, y: int, width: int, height: int, *, fill: str = WHITE,
         stroke: str = LINE, radius: int = 18) -> str:
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')


def panel(y: int, height: int, title: str, headline: str, boundary: str,
          tint: str, *, line_bottom: int) -> list[str]:
    return [
        card(40, y, 1520, height, fill=WHITE),
        f'<rect x="40" y="{y}" width="1520" height="126" rx="18" fill="{tint}"/>',
        text(70, y + 48, title, size=29, color=WHITE, weight=700),
        text(70, y + 86, boundary, size=16, color=WHITE),
        text(1530, y + 59, headline, size=37, color=WHITE, weight=700, anchor="end"),
        text(1530, y + 92, "logged event boundary", size=14, color=WHITE, anchor="end"),
        f'<line x1="70" y1="{line_bottom}" x2="1530" y2="{line_bottom}" stroke="{LINE}"/>',
    ]


def lane(x: int, top: int, bottom: int, name: str, color: str) -> list[str]:
    return [
        f'<line x1="{x}" y1="{top+38}" x2="{x}" y2="{bottom}" '
        f'stroke="{color}" stroke-width="3" stroke-dasharray="7 7" opacity="0.58"/>',
        f'<rect x="{x-83}" y="{top}" width="166" height="48" rx="12" fill="{color}"/>',
        text(x, top + 31, name, size=17, color=WHITE, weight=700, anchor="middle"),
    ]


def arrow(x1: int, x2: int, y: int, title: str, *, detail: str = "",
          color: str = INK, dashed: bool = False) -> list[str]:
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    mid = (x1 + x2) / 2
    out = [
        f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{color}" '
        f'stroke-width="2.5"{dash} marker-end="url(#arrowhead)"/>',
        f'<rect x="{mid-206}" y="{y-44}" width="412" height="35" rx="17" fill="{INK}"/>',
        text(mid, y - 21, title, size=15, color=WHITE, weight=600, anchor="middle"),
    ]
    if detail:
        out.append(text(mid, y + 23, detail, size=14, color=MUTED, anchor="middle"))
    return out


def note(x: int, y: int, title: str, detail: str, color: str,
         *, width: int = 390) -> list[str]:
    return [
        card(x, y, width, 68, fill="#f8fafc", stroke=color, radius=12),
        text(x + 16, y + 27, title, size=16, weight=700),
        text(x + 16, y + 51, detail, size=14, color=MUTED),
    ]


def build(entries: list[Entry], source_name: str) -> str:
    main_start = event(entries, "process started", source="main")
    worker_start = event(entries, "process started", source="worker 1")
    registered = event(entries, "registered with main process", source="worker 2")
    local_end = event(entries, "message sent source=main dest=worker 3 request_type=ackLogs", occurrence=0)
    local_ack = event(entries, "message received source=worker 3 dest=main request_type=OK operation=flush", occurrence=0)
    create1 = event(entries, "message sent source=main dest=worker 1 request_type=createFunction")
    create1_ack = event(entries, "message received source=worker 1 dest=main request_type=OK operation=noop request_id=0", occurrence=0)
    create2 = event(entries, "message sent source=main dest=worker 2 request_type=createFunction")
    create2_ack = event(entries, "message received source=worker 2 dest=main request_type=OK operation=noop request_id=1", occurrence=0)
    result1 = event(entries, "message sent source=main dest=worker 1 request_type=getFunctionResult")
    result1_reply = event(entries, "message received source=worker 1 dest=main request_type=OK operation=noop request_id=0", occurrence=-1)
    result2 = event(entries, "message sent source=main dest=worker 2 request_type=getFunctionResult")
    result2_reply = event(entries, "message received source=worker 2 dest=main request_type=OK operation=noop request_id=1", occurrence=-1)
    collect = event(entries, "message sent source=main dest=worker 1 request_type=getLogs")
    _, eval1 = worker_duration(entries, 1, 0)
    _, eval2 = worker_duration(entries, 2, 1)

    elements = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>',
        f'<rect width="{WIDTH}" height="145" fill="{INK}"/>',
        text(50, 65, "Matrix evaluation cost", size=35, color=WHITE, weight=700),
        text(50, 106, "Local and partitioned event sequences from one syslog run", size=18, color="#bdc7d4"),
        text(1550, 65, "EVENT SEQUENCES", size=14, color="#55c7ca", weight=700, anchor="end"),
    ]

    # The first panel is a local computation. Its only worker messages are
    # registration and an acknowledgment after evaluation has already ended.
    elements += panel(170, 640, "NOT PARTITIONED · MAIN PROCESS",
                      duration(registered, local_end),
                      "Final registration → first event after local evaluation · enclosing window",
                      MAIN, line_bottom=779)
    elements += lane(390, 319, 760, "Main", MAIN)
    elements += lane(1200, 319, 760, "Workers 1–3", WORKER_1)
    elements += [
        f'<circle cx="390" cy="394" r="8" fill="{MAIN}"/>',
        text(410, 397, f"Process starts · {main_start.time.strftime('%H:%M:%S.%f')} UTC",
             size=15, color=MUTED),
    ]
    elements += arrow(1200, 390, 466, "Workers start, connect and register",
                      detail=f"{duration(worker_start, registered)} · first worker start → last registration")
    elements += [
        f'<path d="M 390 528 H 650 V 584 H 390" fill="none" stroke="{TEAL}" '
        'stroke-width="3" stroke-dasharray="8 6" marker-end="url(#arrowhead)"/>',
    ]
    elements += note(685, 530, "Create and evaluate full 15 × 15 matrix",
                     f"≤ {duration(registered, local_end)} · exact eval markers absent", TEAL, width=550)
    elements += arrow(390, 1200, 662, "ackLogs · worker 3", color=MAIN)
    elements += arrow(1200, 390, 723, "OK · flush",
                      detail=f"{duration(local_end, local_ack)} request → reply", color=WORKER_1, dashed=True)
    elements.append(text(70, 796, "Sequence spacing shows event order, not elapsed-time scale.",
                         size=13, color=MUTED))

    # The second panel shows each message pair and both worker-side timers.
    elements += panel(838, 1030, "PARTITIONED · TWO WORKERS",
                      duration(create1, result2_reply),
                      "First createFunction send → final result reply · logged wall-clock span",
                      WORKER_1, line_bottom=1832)
    elements += lane(250, 982, 1800, "Main", MAIN)
    elements += lane(800, 982, 1800, "Worker 1", WORKER_1)
    elements += lane(1350, 982, 1800, "Worker 2", WORKER_2)
    elements += [
        f'<rect x="787" y="1075" width="26" height="318" rx="8" fill="{WORKER_1}" opacity="0.19"/>',
        f'<rect x="1337" y="1195" width="26" height="418" rx="8" fill="{WORKER_2}" opacity="0.22"/>',
    ]
    elements += note(895, 996, "Create object and split columns",
                     f"≤ {duration(local_ack, create1)} · enclosing window", MAIN, width=370)
    elements += arrow(250, 800, 1095, "createFunction · left half · #0", color=MAIN)
    elements += arrow(800, 250, 1150, "OK create · #0",
                      detail=f"{duration(create1, create1_ack)} request → reply", color=WORKER_1, dashed=True)
    elements += arrow(250, 1350, 1215, "createFunction · right half · #1", color=MAIN)
    elements += arrow(1350, 250, 1270, "OK create · #1",
                      detail=f"{duration(create2, create2_ack)} request → reply", color=WORKER_2, dashed=True)
    elements += arrow(250, 800, 1340, "getFunctionResult · #0", color=MAIN)
    elements += note(850, 1380, "Worker 1 evaluation finishes", eval1 + " worker timer", WORKER_1, width=425)
    elements += arrow(800, 250, 1480, "OK result · #0",
                      detail=f"{duration(result1, result1_reply)} request → reply, includes waiting",
                      color=WORKER_1, dashed=True)
    elements += arrow(250, 1350, 1545, "getFunctionResult · #1", color=MAIN)
    elements += note(850, 1600, "Worker 2 evaluation finishes", eval2 + " worker timer", WORKER_2, width=425)
    elements += arrow(1350, 250, 1710, "OK result · #1",
                      detail=f"{duration(result2, result2_reply)} request → reply, includes waiting",
                      color=WORKER_2, dashed=True)
    elements += note(303, 1760, "Join halves and check result",
                     f"≤ {duration(result2_reply, collect)} · enclosing window", TEAL, width=480)

    elements += [
        text(52, 1900, "Logged send/reply intervals include scheduling, serialization, handling and transport; they are not isolated network latency.",
             size=14, color=MUTED),
        text(52, 1926, "The local and partitioned headlines use different boundaries. Exact local eval and partition/orchestration timers are absent from syslog.",
             size=14, color=MUTED),
        text(1550, 1926, f"Source: {source_name} · {main_start.time.date()} · UTC",
             size=13, color=MUTED, anchor="end"),
    ]
    body = "\n  ".join(elements)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img"
     aria-labelledby="diagram-title diagram-description">
  <title id="diagram-title">Local and partitioned matrix evaluation event sequences</title>
  <desc id="diagram-description">Two sequence diagrams in one image show the logged local evaluation window and the partitioned worker request, execution and reply flow with durations.</desc>
  <defs>
    <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="{INK}"/>
    </marker>
  </defs>
  {body}
</svg>
'''


def latest_log() -> Path:
    timestamped = list(LOG_DIRECTORY.glob("syslog-*.log"))
    if timestamped:
        return max(timestamped, key=lambda path: path.name)
    legacy = LOG_DIRECTORY / "syslog.log"
    if legacy.is_file():
        return legacy
    raise FileNotFoundError(f"No syslog files found in {LOG_DIRECTORY}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="Syslog to read (default: latest run)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    log_path = args.log or latest_log()
    args.output.write_text(build(parse_log(log_path), log_path.name), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
