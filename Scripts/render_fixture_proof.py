from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

import pandas as pd

from corr.benchmarks import compute_errors
from corr.clk_adapters import attach_issue_time, infer_issue_time, parse_rinex_clock

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "images" / "fixture-benchmark.svg"


def load_fixture_metrics() -> pd.DataFrame:
    """Run the benchmark calculation from the committed clock fixtures."""
    final = pd.read_parquet(ROOT / "tests" / "data" / "final_fixture.parquet")

    rapid_path = ROOT / "tests" / "data" / "clk" / "rapid_fixture_20250102.clk"
    rapid = parse_rinex_clock(rapid_path, "rapid")

    ultra_path = ROOT / "tests" / "data" / "clk" / "igu23475_18.clk"
    ultra = parse_rinex_clock(ultra_path, "ultra")
    ultra = attach_issue_time(ultra, infer_issue_time(ultra_path, "ultra"))

    metrics = pd.concat(
        [
            compute_errors(final, rapid, "rapid"),
            compute_errors(final, ultra, "ultra"),
        ],
        ignore_index=True,
    )
    if metrics.empty:
        raise RuntimeError("Bundled fixtures produced no overlapping benchmark rows.")

    expected_sources = {"rapid", "ultra"}
    expected_prns = {"G01", "G02", "G03", "G04", "G05"}
    if set(metrics["source"]) != expected_sources:
        raise RuntimeError("Fixture benchmark did not produce rapid and ultra rows.")
    if set(metrics["prn"]) != expected_prns:
        raise RuntimeError("Fixture benchmark PRN coverage changed unexpectedly.")
    return metrics.sort_values(["prn", "source"]).reset_index(drop=True)


def render_svg(metrics: pd.DataFrame) -> str:
    width = 1200
    height = 680
    plot_left = 190
    plot_right = 1110
    plot_top = 190
    plot_bottom = 550
    plot_width = plot_right - plot_left
    max_value = 700.0

    colors = {
        "ink": "#17202a",
        "muted": "#5f6b76",
        "grid": "#d9e0e6",
        "rapid": "#157a7a",
        "ultra": "#c58b18",
        "background": "#ffffff",
        "note": "#f4f7f9",
    }
    rows = metrics.pivot(index="prn", columns="source", values="rmse_ns")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="title desc">'
        ),
        '<title id="title">Rapid and ultra-rapid clock RMSE against final fixture</title>',
        (
            '<desc id="desc">Grouped horizontal bars compare benchmark RMSE in '
            "nanoseconds for five bundled GPS fixture satellites.</desc>"
        ),
        f'<rect width="{width}" height="{height}" fill="{colors["background"]}"/>',
        (
            f'<text x="64" y="68" fill="{colors["ink"]}" font-family="Arial, sans-serif" '
            'font-size="30" font-weight="700">Fixture benchmark: clock-product error</text>'
        ),
        (
            f'<text x="64" y="104" fill="{colors["muted"]}" '
            'font-family="Arial, sans-serif" font-size="18">'
            "GPS PRNs G01–G05 · 2025-01-02 · root mean square error (nanoseconds)</text>"
        ),
        (
            f'<rect x="64" y="128" width="18" height="18" rx="3" '
            f'fill="{colors["rapid"]}"/><text x="92" y="143" fill="{colors["ink"]}" '
            'font-family="Arial, sans-serif" font-size="16">Rapid</text>'
        ),
        (
            f'<rect x="180" y="128" width="18" height="18" rx="3" '
            f'fill="{colors["ultra"]}"/><text x="208" y="143" fill="{colors["ink"]}" '
            'font-family="Arial, sans-serif" font-size="16">Ultra-rapid</text>'
        ),
    ]

    for tick in range(0, 701, 100):
        x = plot_left + (tick / max_value) * plot_width
        parts.extend(
            [
                (
                    f'<line x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" '
                    f'y2="{plot_bottom}" stroke="{colors["grid"]}" stroke-width="1"/>'
                ),
                (
                    f'<text x="{x:.1f}" y="{plot_bottom + 30}" '
                    f'fill="{colors["muted"]}" text-anchor="middle" '
                    f'font-family="Arial, sans-serif" font-size="14">{tick}</text>'
                ),
            ]
        )

    group_height = 68
    bar_height = 22
    for index, (prn, row) in enumerate(rows.iterrows()):
        group_y = plot_top + index * group_height
        label_y = group_y + 29
        parts.append(
            f'<text x="{plot_left - 24}" y="{label_y}" fill="{colors["ink"]}" '
            f'text-anchor="end" font-family="Arial, sans-serif" font-size="18" '
            f'font-weight="700">{escape(str(prn))}</text>'
        )
        for offset, source in ((0, "rapid"), (28, "ultra")):
            value = float(row[source])
            bar_width = (value / max_value) * plot_width
            y = group_y + offset
            parts.extend(
                [
                    (
                        f'<rect x="{plot_left}" y="{y}" width="{bar_width:.1f}" '
                        f'height="{bar_height}" rx="4" fill="{colors[source]}"/>'
                    ),
                    (
                        f'<text x="{plot_left + bar_width + 10:.1f}" y="{y + 17}" '
                        f'fill="{colors["ink"]}" font-family="Arial, sans-serif" '
                        f'font-size="15" font-weight="700">{value:.1f}</text>'
                    ),
                ]
            )

    parts.extend(
        [
            (
                f'<text x="{(plot_left + plot_right) / 2:.1f}" y="{plot_bottom + 64}" '
                f'fill="{colors["muted"]}" text-anchor="middle" '
                'font-family="Arial, sans-serif" font-size="16">RMSE (ns)</text>'
            ),
            (f'<rect x="64" y="620" width="1072" height="40" rx="8" fill="{colors["note"]}"/>'),
            (
                f'<text x="84" y="646" fill="{colors["muted"]}" '
                'font-family="Arial, sans-serif" font-size="15">'
                "Deterministic CI fixture—not an operational accuracy claim. Regenerate: "
                '<tspan font-family="monospace">python Scripts/render_fixture_proof.py</tspan>'
                "</text>"
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the recruiter-facing benchmark chart from bundled fixtures."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed SVG differs from the reproducible rendering.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rendered = render_svg(load_fixture_metrics())
    output = args.output.resolve()

    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"{output} is stale; run python Scripts/render_fixture_proof.py")
        print(f"fixture visualization is current: {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote reproducible fixture visualization: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
