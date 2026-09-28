#!/usr/bin/env python3
"""Generate the EXECUTE sequence diagram described by info.md.

The output is a standalone SVG and does not require third-party packages.
Run from any directory with:

    python3 tools/generate_sequence_diagram.py

Use ``--input`` or ``--output`` to override the repository defaults.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "info.md"
DEFAULT_OUTPUT = ROOT / "sequence_diagram.svg"


@dataclass(frozen=True)
class Participant:
    name: str
    x: int
    color: str


@dataclass(frozen=True)
class Message:
    source: str
    target: str
    label: str
    y: int
    is_return: bool = False


WIDTH = 1200
HEIGHT = 1080
HEADER_HEIGHT = 150
LIFELINE_TOP = 202
LIFELINE_BOTTOM = 1030

COLORS = {
    "ink": "#293444",
    "paper": "#f4f4f4",
    "white": "#ffffff",
    "muted": "#667283",
    "manager": "#e8247f",
    "engine": "#5956c9",
    "functions": "#3db9bd",
    "loop": "#4f9fd7",
}

PARTICIPANTS = (
    Participant("Manager", 180, COLORS["manager"]),
    Participant("Engine", 600, COLORS["engine"]),
    Participant("Functions", 1020, COLORS["functions"]),
)

# The order below is the architecture described in info.md: Manager owns
# partitioning/orchestration, Engines do all evaluation, and Functions contains
# the callable plus the information needed to partition and combine results.
MESSAGES = (
    Message("external", "Manager", "EXECUTE", 248),
    Message("Manager", "Functions", "Get partition plan", 328),
    Message("Functions", "Manager", "Return partitions", 408, True),
    Message("Manager", "Engine", "Dispatch Function + args", 500),
    Message("Engine", "Functions", "FunctionName.func(args)", 580),
    Message("Functions", "Engine", "Return partition result", 660, True),
    Message("Engine", "Manager", "Return evaluated result", 740, True),
    Message("Manager", "Functions", "Orchestrate results", 838),
    Message("Functions", "Manager", "Return singular result", 918, True),
    Message("Manager", "external", "Resolve Coroutine", 998, True),
)


def validate_source(text: str, source: Path) -> None:
    """Fail clearly if the design note no longer describes this diagram."""
    missing = [
        name
        for name in ("Manager", "Engine", "Functions")
        if re.search(rf"\b{re.escape(name)}\b", text) is None
    ]
    if re.search(r"\bexecute\b", text, flags=re.IGNORECASE) is None:
        missing.append("EXECUTE")
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{source} does not describe required item(s): {joined}")


def pill_width(label: str) -> int:
    return max(118, min(250, 26 + len(label) * 7))


def text_element(
    x: float,
    y: float,
    value: str,
    *,
    size: int,
    color: str,
    weight: int = 400,
    anchor: str = "middle",
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Inter, Avenir, Helvetica, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">'
        f"{escape(value)}</text>"
    )


def participant_svg(participant: Participant) -> str:
    box_width = 150
    box_height = 64
    box_x = participant.x - box_width / 2
    box_y = 112
    return "\n".join(
        (
            f'<line x1="{participant.x}" y1="{LIFELINE_TOP}" '
            f'x2="{participant.x}" y2="{LIFELINE_BOTTOM}" '
            f'stroke="{participant.color}" stroke-width="4" opacity="0.9"/>',
            f'<rect x="{box_x}" y="{box_y}" width="{box_width}" '
            f'height="{box_height}" rx="12" fill="{participant.color}"/>',
            text_element(
                participant.x,
                box_y + 40,
                participant.name,
                size=18,
                color=COLORS["white"],
                weight=600,
            ),
        )
    )


def activation_svg(name: str, y: int, height: int) -> str:
    participant = next(item for item in PARTICIPANTS if item.name == name)
    return (
        f'<rect x="{participant.x - 15}" y="{y}" width="30" height="{height}" '
        f'fill="{participant.color}"/>'
    )


def message_svg(message: Message, positions: dict[str, int]) -> str:
    x1 = positions[message.source]
    x2 = positions[message.target]
    marker = "arrow-return" if message.is_return else "arrow-call"
    label_width = pill_width(message.label)
    center_x = (x1 + x2) / 2
    label_x = center_x - label_width / 2
    label_y = message.y - 15
    line_color = COLORS["muted"] if message.is_return else COLORS["ink"]
    return "\n".join(
        (
            f'<line x1="{x1}" y1="{message.y}" x2="{x2}" y2="{message.y}" '
            f'stroke="{line_color}" stroke-width="2.2" stroke-dasharray="2 7" '
            f'stroke-linecap="round" marker-end="url(#{marker})"/>',
            f'<rect x="{label_x}" y="{label_y}" width="{label_width}" '
            f'height="30" rx="15" fill="{COLORS["ink"]}"/>',
            text_element(
                center_x,
                message.y + 5,
                message.label,
                size=13,
                color=COLORS["white"],
                weight=500,
            ),
        )
    )


def build_svg(source_name: str) -> str:
    positions = {participant.name: participant.x for participant in PARTICIPANTS}
    positions["external"] = 38

    participants = "\n".join(participant_svg(item) for item in PARTICIPANTS)
    activations = "\n".join(
        (
            activation_svg("Manager", 234, 780),
            activation_svg("Functions", 312, 112),
            activation_svg("Engine", 484, 274),
            activation_svg("Functions", 564, 112),
            activation_svg("Functions", 822, 112),
        )
    )
    messages = "\n".join(message_svg(item, positions) for item in MESSAGES)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img"
     aria-labelledby="diagram-title diagram-description">
  <title id="diagram-title">Resolution Engine EXECUTE sequence</title>
  <desc id="diagram-description">Manager partitions work with Functions, Engine evaluates each partition, and Manager orchestrates the results.</desc>
  <defs>
    <marker id="arrow-call" markerWidth="8" markerHeight="8" refX="7" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="{COLORS["ink"]}"/>
    </marker>
    <marker id="arrow-return" markerWidth="8" markerHeight="8" refX="7" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="{COLORS["muted"]}"/>
    </marker>
  </defs>

  <rect width="{WIDTH}" height="{HEIGHT}" fill="{COLORS["paper"]}"/>
  <rect width="{WIDTH}" height="{HEADER_HEIGHT}" fill="{COLORS["ink"]}"/>
  {text_element(52, 67, "Resolution Engine", size=34, color=COLORS["white"], weight=600, anchor="start")}
  {text_element(52, 101, "EXECUTE sequence", size=17, color="#bdc7d4", weight=400, anchor="start")}
  {text_element(1148, 65, "MANAGER  •  ENGINE  •  FUNCTIONS", size=13, color="#55c7ca", weight=600, anchor="end")}

  {participants}

  <rect x="363" y="449" width="474" height="330" rx="18"
        fill="none" stroke="{COLORS["loop"]}" stroke-width="1.5" stroke-dasharray="6 7" opacity="0.65"/>
  <rect x="382" y="437" width="178" height="26" rx="13" fill="{COLORS["loop"]}"/>
  {text_element(471, 455, "FOR EACH PARTITION", size=12, color=COLORS["white"], weight=700)}

  {activations}
  {messages}

  {text_element(1150, 1050, f"Generated from {source_name}, made by ChatGPT; Author Reviewed", size=12, color="#87909c", anchor="end")}
</svg>
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Manager/Engine/Functions EXECUTE sequence diagram."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"architecture note (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"SVG destination (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.input.resolve()
    destination = args.output.resolve()

    text = source.read_text(encoding="utf-8")
    validate_source(text, source)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_svg(source.name), encoding="utf-8")
    print(f"Wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
