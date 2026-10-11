#!/usr/bin/env python3
"""Draw the local/worker Function-object flow as a standalone SVG.

Run from any directory with ``python3 tools/generate_function_object_flow.py``.
The default output stays in tools/, alongside this generator.
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path


OUTPUT = Path(__file__).with_name("function_object_flow.svg")
WIDTH = 1500
HEIGHT = 1390

INK = "#293444"
MUTED = "#607184"
PAPER = "#f4f4f4"
WHITE = "#ffffff"
MAIN = "#e8247f"
WORKER = "#5956c9"
FUNCTION = "#3db9bd"
RESULT = "#138572"


def label(
    x: int, y: int, value: str, *, size: int = 17, color: str = INK,
    weight: int = 400, anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Inter, Avenir, Helvetica, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">'
        f"{escape(value)}</text>"
    )


def card(
    x: int, y: int, width: int, height: int, *, title: str,
    detail: str, state: str, accent: str,
) -> str:
    return "\n".join((
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="15" fill="{WHITE}" stroke="{accent}" stroke-width="2.5"/>',
        f'<rect x="{x}" y="{y}" width="10" height="{height}" '
        f'rx="5" fill="{accent}"/>',
        label(x + 26, y + 34, title, size=21, weight=700),
        label(x + 26, y + 62, detail, size=15, color=MUTED),
        label(x + 26, y + 91, state, size=17, color=accent, weight=700),
    ))


def arrow(path: str, *, dashed: bool = False, color: str = INK) -> str:
    dash = ' stroke-dasharray="8 7"' if dashed else ""
    return (
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.8"'
        f'{dash} stroke-linecap="round" stroke-linejoin="round" '
        'marker-end="url(#arrowhead)"/>'
    )


def tag(x: int, y: int, width: int, value: str) -> str:
    return "\n".join((
        f'<rect x="{x - width // 2}" y="{y - 17}" width="{width}" '
        f'height="34" rx="17" fill="{INK}"/>',
        label(x, y + 5, value, size=14, color=WHITE, weight=600, anchor="middle"),
    ))


def build_svg() -> str:
    parts = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>',
        f'<rect width="{WIDTH}" height="150" fill="{INK}"/>',
        label(50, 65, "Function object flow", size=34, color=WHITE, weight=700),
        label(50, 105, "One part runs locally; one part runs on a worker", size=18,
              color="#c4ceda"),
        label(1450, 66, "CONCEPTUAL FLOW", size=14, color="#55c7ca",
              weight=700, anchor="end"),
        f'<rect x="40" y="176" width="680" height="1150" rx="20" '
        f'fill="#fff9fc" stroke="{MAIN}" stroke-width="2"/>',
        f'<rect x="780" y="176" width="680" height="1150" rx="20" '
        f'fill="#f9f8ff" stroke="{WORKER}" stroke-width="2"/>',
        f'<rect x="40" y="176" width="680" height="58" rx="20" fill="{MAIN}"/>',
        f'<rect x="780" y="176" width="680" height="58" rx="20" fill="{WORKER}"/>',
        label(68, 214, "MAIN PROCESS", size=20, color=WHITE, weight=700),
        label(808, 214, "WORKER PROCESS", size=20, color=WHITE, weight=700),
        label(1427, 214, "separate object identity", size=14, color=WHITE,
              anchor="end"),

        # Draw paths first so cards and captions stay legible above them.
        arrow("M 380 342 V 366 H 230 V 405"),
        arrow("M 380 366 H 530 V 405"),
        arrow("M 230 517 V 630"),
        arrow("M 670 461 H 855", dashed=True, color=WORKER),
        arrow("M 1120 517 V 630"),
        arrow("M 1120 742 V 916 H 670", dashed=True, color=WORKER),
        arrow("M 230 742 V 1050 H 380 V 1090"),
        arrow("M 530 972 V 1050 H 380 V 1090"),
        arrow("M 380 1202 V 1238"),

        card(235, 242, 290, 100, title="Function A", detail="original input",
             state="result = None", accent=MAIN),
        card(90, 405, 280, 112, title="Part 1", detail="main process original",
             state="result = None", accent=MAIN),
        card(390, 405, 280, 112, title="Part 2", detail="main process original",
             state="result = None", accent=MAIN),
        card(90, 630, 280, 112, title="Part 1", detail="evaluated locally",
             state="result = value", accent=RESULT),
        card(855, 405, 530, 112, title="Part 2 copy", detail="deserialized; stored by request ID",
             state="result = None", accent=WORKER),
        card(855, 630, 530, 112, title="Part 2 copy", detail="eval() mutates this worker object",
             state="result = value", accent=RESULT),
        card(390, 860, 280, 112, title="Returned Part 2", detail="new main process copy",
             state="result = value", accent=RESULT),
        card(210, 1090, 340, 112, title="Function A", detail="orchestrate(part results)",
             state="result = final value", accent=RESULT),
        f'<rect x="210" y="1238" width="340" height="68" rx="15" '
        f'fill="{RESULT}"/>',
        label(380, 1280, "Final result ready to use", size=19,
              color=WHITE, weight=700, anchor="middle"),

        tag(380, 367, 118, "partition()"),
        tag(230, 573, 126, "eval() local"),
        tag(762, 422, 166, "serialize + send"),
        tag(1120, 573, 116, "eval()"),
        tag(762, 878, 178, "serialize + return"),
        tag(380, 1046, 146, "orchestrate()"),
        label(405, 565, "Main Part 2 stays unevaluated.",
              size=15, color=MUTED),
        label(405, 589, "Returned Part 2 is another copy.",
              size=15, color=MUTED),
        label(855, 338, "Evaluation status is tracked separately from the Function object.",
              size=15, color=MUTED),
        label(1450, 1363,
              "Conceptual split • current demo dispatches all jobs to workers",
              size=14, color=MUTED, anchor="end"),
    ]
    body = "\n  ".join(parts)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img"
     aria-labelledby="diagram-title diagram-description">
  <title id="diagram-title">Function object mutation across main and worker processes</title>
  <desc id="diagram-description">Function A is partitioned. Part 1 is evaluated in the main process, while a serialized copy of Part 2 is evaluated in a worker. The evaluated worker copy returns as a new main process object. Function A combines both results.</desc>
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="8" refY="5"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{INK}"/>
    </marker>
  </defs>
  {body}
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.write_text(build_svg(), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
