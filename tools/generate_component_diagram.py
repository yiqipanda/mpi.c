#!/usr/bin/env python3
"""Generate the component/domain diagram described by info.md.

The generated SVG separates the main computer from the worker-computer domain,
shows the process and classes hosted by each, and labels the execution protocol.
No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import re
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "info.md"
DEFAULT_OUTPUT = ROOT / "component_diagram.svg"

WIDTH = 1500
HEIGHT = 1120

INK = "#293444"
PAPER = "#f4f4f4"
WHITE = "#ffffff"
MUTED = "#687486"
LINE = "#9ca9b8"
MANAGER = "#e8247f"
ENGINE = "#5956c9"
FUNCTIONS = "#3db9bd"
PROCESS = "#4f9fd7"


def validate_source(text: str, source: Path) -> None:
    required = ("main computer", "process", "Manager", "Engine", "Functions", "execute")
    missing = [
        item
        for item in required
        if re.search(rf"\b{re.escape(item)}\b", text, flags=re.IGNORECASE) is None
    ]
    if missing:
        raise ValueError(
            f"{source} does not describe required item(s): {', '.join(missing)}"
        )


def text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 15,
    color: str = INK,
    weight: int = 400,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Inter, Avenir, Helvetica, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">'
        f"{escape(value)}</text>"
    )


def domain(x: int, y: int, width: int, height: int, title: str, subtitle: str) -> str:
    return "\n".join(
        (
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="22" '
            f'fill="{WHITE}" stroke="{LINE}" stroke-width="2"/>',
            f'<path d="M {x + 22} {y} H {x + width - 22} Q {x + width} {y} '
            f'{x + width} {y + 22} V {y + 74} H {x} V {y + 22} '
            f'Q {x} {y} {x + 22} {y} Z" fill="{INK}"/>',
            text(x + 28, y + 34, title, size=18, color=WHITE, weight=700),
            text(x + 28, y + 58, subtitle, size=13, color="#c4ceda"),
        )
    )


def component(
    x: int,
    y: int,
    width: int,
    height: int,
    name: str,
    kind: str,
    color: str,
    bullets: tuple[str, ...],
) -> str:
    rows = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="14" '
        f'fill="#fbfbfc" stroke="{color}" stroke-width="2"/>',
        f'<rect x="{x}" y="{y}" width="12" height="{height}" rx="6" fill="{color}"/>',
        text(x + 30, y + 31, name, size=18, color=INK, weight=700),
        text(x + width - 22, y + 30, kind.upper(), size=11, color=color, weight=700, anchor="end"),
    ]
    bullet_y = y + 61
    for item in bullets:
        rows.append(f'<circle cx="{x + 32}" cy="{bullet_y - 5}" r="3" fill="{color}"/>')
        rows.append(text(x + 46, bullet_y, item, size=14, color=MUTED))
        bullet_y += 27
    return "\n".join(rows)


def process_container(
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    subtitle: str,
) -> str:
    return "\n".join(
        (
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="17" '
            f'fill="#f7fbfd" stroke="{PROCESS}" stroke-width="2" stroke-dasharray="7 7"/>',
            f'<rect x="{x + 18}" y="{y - 14}" width="178" height="28" rx="14" fill="{PROCESS}"/>',
            text(x + 107, y + 5, title.upper(), size=12, color=WHITE, weight=700, anchor="middle"),
            text(x + width - 20, y + 33, subtitle, size=12, color=MUTED, anchor="end"),
        )
    )


def runtime_process(x: int, y: int, title: str, subtitle: str) -> str:
    """Render the process shared, unchanged, by every computer."""
    engine_x = x + 28
    functions_x = x + 318
    card_y = y + 48
    return "\n".join(
        (
            process_container(x, y, 596, 270, title, subtitle),
            component(
                engine_x,
                card_y,
                250,
                174,
                "Engine",
                "class",
                ENGINE,
                (
                    "Waits for evaluation orders",
                    "Runs registered func(args)",
                    "Returns partition result",
                ),
            ),
            component(
                functions_x,
                card_y,
                250,
                174,
                "Functions",
                "class",
                FUNCTIONS,
                (
                    "Registered functions",
                    "Partition metadata",
                    "Orchestration rules",
                ),
            ),
            arrow(
                f"M {engine_x + 250} {card_y + 86} H {functions_x}",
                "func(args)",
                x + 298,
                card_y + 86,
                label_width=112,
            ),
        )
    )


def arrow(
    path: str,
    label: str,
    label_x: int,
    label_y: int,
    *,
    return_flow: bool = False,
    label_width: int = 230,
) -> str:
    stroke = MUTED if return_flow else INK
    marker = "return-arrow" if return_flow else "call-arrow"
    dash = ' stroke-dasharray="4 7"' if return_flow else ""
    return "\n".join(
        (
            f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="2.2"{dash} '
            f'marker-end="url(#{marker})"/>',
            f'<rect x="{label_x - label_width / 2}" y="{label_y - 15}" '
            f'width="{label_width}" height="30" rx="15" fill="{INK}"/>',
            text(label_x, label_y + 5, label, size=12, color=WHITE, weight=600, anchor="middle"),
        )
    )


def build_svg(source_name: str) -> str:
    elements = [
        domain(40, 184, 680, 866, "MAIN COMPUTER", "Control plane • owns the main program"),
        domain(780, 184, 680, 866, "OTHER COMPUTERS", "Evaluation plane • one or more worker computers"),

        component(
            82,
            284,
            596,
            126,
            "Main Program",
            "application",
            PROCESS,
            (
                "Calls Manager.execute(Function_name, arguments)",
                "Receives a Coroutine and later reads its value",
            ),
        ),
        component(
            82,
            464,
            596,
            198,
            "Manager",
            "class / API",
            MANAGER,
            (
                "Partitions arguments using Functions metadata",
                "Assigns partitions to available Engines",
                "Collects results and orchestrates one result",
                "Never evaluates a partition itself",
            ),
        ),
        runtime_process(82, 732, "Runtime process", "same structure on every computer"),

        runtime_process(822, 330, "Runtime process", "identical copy on each other computer • 1..N"),
        component(
            862,
            684,
            516,
            184,
            "Worker Domain",
            "deployment",
            PROCESS,
            (
                "Every computer hosts the identical runtime process",
                "Available Engines receive partitions from Manager",
                "Results travel back to Manager for orchestration",
                "Adding computers adds evaluation capacity",
            ),
        ),

        # Application/API interaction.
        arrow("M 380 410 V 464", "EXECUTE → Coroutine", 380, 437, label_width=190),

        # The main computer also owns a process, because info.md says all computers do.
        arrow("M 238 662 V 780", "available Engine", 238, 690, label_width=160),
        arrow("M 520 662 V 780", "partition + orchestrate", 520, 690, label_width=190),

        # Cross-domain dispatch and collection.
        arrow("M 678 510 C 744 510, 790 426, 850 426", "evaluation order", 770, 444, label_width=180),
        arrow(
            "M 850 522 C 790 522, 744 604, 678 604",
            "partition result",
            770,
            568,
            return_flow=True,
            label_width=170,
        ),
    ]

    body = "\n".join(elements)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img"
     aria-labelledby="diagram-title diagram-description">
  <title id="diagram-title">Resolution Engine component diagram</title>
  <desc id="diagram-description">Main-computer control components and other-computer worker processes, including their responsibilities and interactions.</desc>
  <defs>
    <marker id="call-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="{INK}"/>
    </marker>
    <marker id="return-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="{MUTED}"/>
    </marker>
  </defs>

  <rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>
  <rect width="{WIDTH}" height="150" fill="{INK}"/>
  {text(52, 66, "Resolution Engine", size=34, color=WHITE, weight=600)}
  {text(52, 102, "Component and deployment domains", size=17, color="#bdc7d4")}
  {text(1448, 66, "CONTROL PLANE  ↔  EVALUATION PLANE", size=13, color="#55c7ca", weight=700, anchor="end")}

  {body}

  {text(1448, 1091, f"Generated from {source_name} by ChatGPT; Author Reviewed", size=12, color="#87909c", anchor="end")}
</svg>
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the main/other-computers component diagram."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.input.resolve()
    destination = args.output.resolve()

    source_text = source.read_text(encoding="utf-8")
    validate_source(source_text, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_svg(source.name), encoding="utf-8")
    print(f"Wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
