"""Generate high-resolution standalone SVG figures for Stage 4 Task 50 Benchmark Report.

Creates:
1. eval_results/figures/hit_at_k_comparison.svg
2. eval_results/figures/mrr_ndcg_comparison.svg
3. eval_results/figures/precision_recall_comparison.svg
4. eval_results/figures/latency_breakdown.svg
5. eval_results/figures/accuracy_vs_latency_tradeoff.svg

Zero external library dependencies (pure Python SVG generator).
"""

from __future__ import annotations

import json
from pathlib import Path

BENCHMARK_PATH = Path("eval_results/stage4_task50_benchmark.json")
OUTPUT_DIR = Path("eval_results/figures")


def make_svg_header(width: int, height: int, title: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" style="background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <style>
    .title {{ font-size: 18px; font-weight: 700; fill: #f8fafc; }}
    .subtitle {{ font-size: 12px; fill: #94a3b8; }}
    .axis-label {{ font-size: 11px; fill: #94a3b8; font-weight: 500; }}
    .tick-label {{ font-size: 10px; fill: #64748b; }}
    .val-label {{ font-size: 10px; font-weight: 600; fill: #e2e8f0; }}
    .legend-text {{ font-size: 11px; fill: #cbd5e1; }}
    .grid-line {{ stroke: #334155; stroke-dasharray: 4,4; stroke-width: 1; }}
    .axis-line {{ stroke: #475569; stroke-width: 1.5; }}
  </style>
  <text x="30" y="32" class="title">{title}</text>
  <text x="30" y="50" class="subtitle">Curated 50-Query SRE Golden Benchmark (sre_copilot_v1, 252 docs, 5,533 points)</text>
"""


def generate_hit_at_k_chart(data: dict):
    width, height = 960, 480
    strategies = list(data["strategies"].keys())
    labels = [
        "Dense Only",
        "Sparse Only",
        "Hybrid Weighted",
        "Hybrid RRF",
        "Hybrid RRF + RR",
        "Hybrid Wtd + RR",
        "Metadata Filt",
        "HyDE + RRF",
    ]

    h1_vals = [data["strategies"][s]["overall_canonical"]["hit@1"] for s in strategies]
    h3_vals = [data["strategies"][s]["overall_canonical"]["hit@3"] for s in strategies]
    h5_vals = [data["strategies"][s]["overall_canonical"]["hit@5"] for s in strategies]

    svg = [make_svg_header(width, height, "Retrieval Accuracy Comparison: Hit@1, Hit@3, and Hit@5")]

    # Legend
    svg.append(
        """
  <g transform="translate(620, 25)">
    <rect x="0" y="0" width="14" height="14" rx="3" fill="#3b82f6"/>
    <text x="20" y="11" class="legend-text">Hit@1</text>
    <rect x="80" y="0" width="14" height="14" rx="3" fill="#8b5cf6"/>
    <text x="100" y="11" class="legend-text">Hit@3</text>
    <rect x="160" y="0" width="14" height="14" rx="3" fill="#10b981"/>
    <text x="180" y="11" class="legend-text">Hit@5</text>
  </g>
"""
    )

    chart_left = 60
    chart_right = 920
    chart_top = 80
    chart_bottom = 400
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    # Y-axis ticks (0.0 to 1.0)
    for y_val in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        y_pos = chart_bottom - (y_val * chart_h)
        svg.append(
            f'  <line x1="{chart_left}" y1="{y_pos}" x2="{chart_right}" y2="{y_pos}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{chart_left - 10}" y="{y_pos + 4}" text-anchor="end" class="tick-label">{y_val:.1f}</text>\n'
        )

    # Bars per strategy
    group_width = chart_w / len(strategies)
    bar_w = 20

    for i, strat in enumerate(strategies):
        gx = chart_left + (i * group_width)
        center_x = gx + (group_width / 2)

        # 3 bars
        b1_x = center_x - bar_w * 1.6
        b2_x = center_x - bar_w * 0.5
        b3_x = center_x + bar_w * 0.6

        h1 = h1_vals[i] * chart_h
        h3 = h3_vals[i] * chart_h
        h5 = h5_vals[i] * chart_h

        # Hit@1
        svg.append(
            f'  <rect x="{b1_x:.1f}" y="{chart_bottom - h1:.1f}" width="{bar_w}" height="{h1:.1f}" rx="3" fill="#3b82f6"/>\n'
        )
        svg.append(
            f'  <text x="{b1_x + bar_w/2:.1f}" y="{chart_bottom - h1 - 5:.1f}" text-anchor="middle" class="val-label">{h1_vals[i]:.2f}</text>\n'
        )

        # Hit@3
        svg.append(
            f'  <rect x="{b2_x:.1f}" y="{chart_bottom - h3:.1f}" width="{bar_w}" height="{h3:.1f}" rx="3" fill="#8b5cf6"/>\n'
        )
        svg.append(
            f'  <text x="{b2_x + bar_w/2:.1f}" y="{chart_bottom - h3 - 5:.1f}" text-anchor="middle" class="val-label">{h3_vals[i]:.2f}</text>\n'
        )

        # Hit@5
        svg.append(
            f'  <rect x="{b3_x:.1f}" y="{chart_bottom - h5:.1f}" width="{bar_w}" height="{h5:.1f}" rx="3" fill="#10b981"/>\n'
        )
        svg.append(
            f'  <text x="{b3_x + bar_w/2:.1f}" y="{chart_bottom - h5 - 5:.1f}" text-anchor="middle" class="val-label">{h5_vals[i]:.2f}</text>\n'
        )

        # Strategy label
        lbl = labels[i]
        svg.append(
            f'  <text x="{center_x:.1f}" y="{chart_bottom + 25}" text-anchor="middle" class="axis-label">{lbl}</text>\n'
        )

    # Base line
    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append("</svg>")

    out_file = OUTPUT_DIR / "hit_at_k_comparison.svg"
    out_file.write_text("".join(svg), encoding="utf-8")
    print(f"Generated {out_file}")


def generate_mrr_ndcg_chart(data: dict):
    width, height = 960, 480
    strategies = list(data["strategies"].keys())
    labels = [
        "Dense Only",
        "Sparse Only",
        "Hybrid Weighted",
        "Hybrid RRF",
        "Hybrid RRF + RR",
        "Hybrid Wtd + RR",
        "Metadata Filt",
        "HyDE + RRF",
    ]

    mrr_vals = [data["strategies"][s]["overall_canonical"]["mrr"] for s in strategies]
    ndcg3_vals = [data["strategies"][s]["overall_canonical"]["ndcg@3"] for s in strategies]
    ndcg5_vals = [data["strategies"][s]["overall_canonical"]["ndcg@5"] for s in strategies]

    svg = [make_svg_header(width, height, "Ranking Quality Comparison: MRR, NDCG@3, and NDCG@5")]

    # Legend
    svg.append(
        """
  <g transform="translate(600, 25)">
    <rect x="0" y="0" width="14" height="14" rx="3" fill="#06b6d4"/>
    <text x="20" y="11" class="legend-text">MRR</text>
    <rect x="90" y="0" width="14" height="14" rx="3" fill="#f59e0b"/>
    <text x="110" y="11" class="legend-text">NDCG@3</text>
    <rect x="190" y="0" width="14" height="14" rx="3" fill="#ec4899"/>
    <text x="210" y="11" class="legend-text">NDCG@5</text>
  </g>
"""
    )

    chart_left = 60
    chart_right = 920
    chart_top = 80
    chart_bottom = 400
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    # Y-axis ticks (0.0 to 1.0)
    for y_val in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        y_pos = chart_bottom - (y_val * chart_h)
        svg.append(
            f'  <line x1="{chart_left}" y1="{y_pos}" x2="{chart_right}" y2="{y_pos}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{chart_left - 10}" y="{y_pos + 4}" text-anchor="end" class="tick-label">{y_val:.1f}</text>\n'
        )

    group_width = chart_w / len(strategies)
    bar_w = 20

    for i, strat in enumerate(strategies):
        gx = chart_left + (i * group_width)
        center_x = gx + (group_width / 2)

        b1_x = center_x - bar_w * 1.6
        b2_x = center_x - bar_w * 0.5
        b3_x = center_x + bar_w * 0.6

        h1 = mrr_vals[i] * chart_h
        h2 = ndcg3_vals[i] * chart_h
        h3 = ndcg5_vals[i] * chart_h

        # MRR
        svg.append(
            f'  <rect x="{b1_x:.1f}" y="{chart_bottom - h1:.1f}" width="{bar_w}" height="{h1:.1f}" rx="3" fill="#06b6d4"/>\n'
        )
        svg.append(
            f'  <text x="{b1_x + bar_w/2:.1f}" y="{chart_bottom - h1 - 5:.1f}" text-anchor="middle" class="val-label">{mrr_vals[i]:.2f}</text>\n'
        )

        # NDCG@3
        svg.append(
            f'  <rect x="{b2_x:.1f}" y="{chart_bottom - h2:.1f}" width="{bar_w}" height="{h2:.1f}" rx="3" fill="#f59e0b"/>\n'
        )
        svg.append(
            f'  <text x="{b2_x + bar_w/2:.1f}" y="{chart_bottom - h2 - 5:.1f}" text-anchor="middle" class="val-label">{ndcg3_vals[i]:.2f}</text>\n'
        )

        # NDCG@5
        svg.append(
            f'  <rect x="{b3_x:.1f}" y="{chart_bottom - h3:.1f}" width="{bar_w}" height="{h3:.1f}" rx="3" fill="#ec4899"/>\n'
        )
        svg.append(
            f'  <text x="{b3_x + bar_w/2:.1f}" y="{chart_bottom - h3 - 5:.1f}" text-anchor="middle" class="val-label">{ndcg5_vals[i]:.2f}</text>\n'
        )

        lbl = labels[i]
        svg.append(
            f'  <text x="{center_x:.1f}" y="{chart_bottom + 25}" text-anchor="middle" class="axis-label">{lbl}</text>\n'
        )

    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append("</svg>")

    out_file = OUTPUT_DIR / "mrr_ndcg_comparison.svg"
    out_file.write_text("".join(svg), encoding="utf-8")
    print(f"Generated {out_file}")


def generate_precision_recall_chart(data: dict):
    width, height = 960, 480
    strategies = list(data["strategies"].keys())
    labels = [
        "Dense Only",
        "Sparse Only",
        "Hybrid Weighted",
        "Hybrid RRF",
        "Hybrid RRF + RR",
        "Hybrid Wtd + RR",
        "Metadata Filt",
        "HyDE + RRF",
    ]

    p5_vals = [data["strategies"][s]["overall_canonical"]["precision@5"] for s in strategies]
    rec5_vals = [data["strategies"][s]["overall_canonical"]["recall@5"] for s in strategies]

    svg = [make_svg_header(width, height, "Retrieval Coverage & Density: Precision@5 vs Recall@5")]

    # Legend
    svg.append(
        """
  <g transform="translate(680, 25)">
    <rect x="0" y="0" width="14" height="14" rx="3" fill="#6366f1"/>
    <text x="20" y="11" class="legend-text">Precision@5</text>
    <rect x="120" y="0" width="14" height="14" rx="3" fill="#14b8a6"/>
    <text x="140" y="11" class="legend-text">Recall@5</text>
  </g>
"""
    )

    chart_left = 60
    chart_right = 920
    chart_top = 80
    chart_bottom = 400
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    for y_val in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        y_pos = chart_bottom - (y_val * chart_h)
        svg.append(
            f'  <line x1="{chart_left}" y1="{y_pos}" x2="{chart_right}" y2="{y_pos}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{chart_left - 10}" y="{y_pos + 4}" text-anchor="end" class="tick-label">{y_val:.1f}</text>\n'
        )

    group_width = chart_w / len(strategies)
    bar_w = 26

    for i, strat in enumerate(strategies):
        gx = chart_left + (i * group_width)
        center_x = gx + (group_width / 2)

        b1_x = center_x - bar_w - 3
        b2_x = center_x + 3

        h1 = p5_vals[i] * chart_h
        h2 = rec5_vals[i] * chart_h

        # Precision@5
        svg.append(
            f'  <rect x="{b1_x:.1f}" y="{chart_bottom - h1:.1f}" width="{bar_w}" height="{h1:.1f}" rx="3" fill="#6366f1"/>\n'
        )
        svg.append(
            f'  <text x="{b1_x + bar_w/2:.1f}" y="{chart_bottom - h1 - 5:.1f}" text-anchor="middle" class="val-label">{p5_vals[i]:.2f}</text>\n'
        )

        # Recall@5
        svg.append(
            f'  <rect x="{b2_x:.1f}" y="{chart_bottom - h2:.1f}" width="{bar_w}" height="{h2:.1f}" rx="3" fill="#14b8a6"/>\n'
        )
        svg.append(
            f'  <text x="{b2_x + bar_w/2:.1f}" y="{chart_bottom - h2 - 5:.1f}" text-anchor="middle" class="val-label">{rec5_vals[i]:.2f}</text>\n'
        )

        lbl = labels[i]
        svg.append(
            f'  <text x="{center_x:.1f}" y="{chart_bottom + 25}" text-anchor="middle" class="axis-label">{lbl}</text>\n'
        )

    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append("</svg>")

    out_file = OUTPUT_DIR / "precision_recall_comparison.svg"
    out_file.write_text("".join(svg), encoding="utf-8")
    print(f"Generated {out_file}")


def generate_latency_breakdown_chart(data: dict):
    width, height = 960, 480
    strategies = list(data["strategies"].keys())
    labels = [
        "Dense Only",
        "Sparse Only",
        "Hybrid Weighted",
        "Hybrid RRF",
        "Hybrid RRF + RR",
        "Hybrid Wtd + RR",
        "Metadata Filt",
        "HyDE + RRF",
    ]

    p50_vals = [data["strategies"][s]["latency"]["total"]["p50"] for s in strategies]
    p95_vals = [data["strategies"][s]["latency"]["total"]["p95"] for s in strategies]
    mean_vals = [data["strategies"][s]["latency"]["total"]["mean"] for s in strategies]

    svg = [make_svg_header(width, height, "Latency Distribution: P50 Median, P95 Tail, and Mean (ms)")]

    svg.append(
        """
  <g transform="translate(630, 25)">
    <rect x="0" y="0" width="14" height="14" rx="3" fill="#38bdf8"/>
    <text x="20" y="11" class="legend-text">P50 (Median)</text>
    <rect x="110" y="0" width="14" height="14" rx="3" fill="#fb7185"/>
    <text x="130" y="11" class="legend-text">P95 (Tail)</text>
    <rect x="210" y="0" width="14" height="14" rx="3" fill="#facc15"/>
    <text x="230" y="11" class="legend-text">Mean</text>
  </g>
"""
    )

    chart_left = 60
    chart_right = 920
    chart_top = 80
    chart_bottom = 400
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    max_lat = max(max(p95_vals), 1200.0) * 1.1

    for y_step in range(0, int(max_lat) + 1, 200):
        y_pos = chart_bottom - ((y_step / max_lat) * chart_h)
        svg.append(
            f'  <line x1="{chart_left}" y1="{y_pos}" x2="{chart_right}" y2="{y_pos}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{chart_left - 10}" y="{y_pos + 4}" text-anchor="end" class="tick-label">{y_step}ms</text>\n'
        )

    group_width = chart_w / len(strategies)
    bar_w = 20

    for i, strat in enumerate(strategies):
        gx = chart_left + (i * group_width)
        center_x = gx + (group_width / 2)

        b1_x = center_x - bar_w * 1.6
        b2_x = center_x - bar_w * 0.5
        b3_x = center_x + bar_w * 0.6

        h1 = (p50_vals[i] / max_lat) * chart_h
        h2 = (p95_vals[i] / max_lat) * chart_h
        h3 = (mean_vals[i] / max_lat) * chart_h

        # P50
        svg.append(
            f'  <rect x="{b1_x:.1f}" y="{chart_bottom - h1:.1f}" width="{bar_w}" height="{h1:.1f}" rx="3" fill="#38bdf8"/>\n'
        )
        svg.append(
            f'  <text x="{b1_x + bar_w/2:.1f}" y="{chart_bottom - h1 - 5:.1f}" text-anchor="middle" class="val-label">{p50_vals[i]:.0f}</text>\n'
        )

        # P95
        svg.append(
            f'  <rect x="{b2_x:.1f}" y="{chart_bottom - h2:.1f}" width="{bar_w}" height="{h2:.1f}" rx="3" fill="#fb7185"/>\n'
        )
        svg.append(
            f'  <text x="{b2_x + bar_w/2:.1f}" y="{chart_bottom - h2 - 5:.1f}" text-anchor="middle" class="val-label">{p95_vals[i]:.0f}</text>\n'
        )

        # Mean
        svg.append(
            f'  <rect x="{b3_x:.1f}" y="{chart_bottom - h3:.1f}" width="{bar_w}" height="{h3:.1f}" rx="3" fill="#facc15"/>\n'
        )
        svg.append(
            f'  <text x="{b3_x + bar_w/2:.1f}" y="{chart_bottom - h3 - 5:.1f}" text-anchor="middle" class="val-label">{mean_vals[i]:.0f}</text>\n'
        )

        lbl = labels[i]
        svg.append(
            f'  <text x="{center_x:.1f}" y="{chart_bottom + 25}" text-anchor="middle" class="axis-label">{lbl}</text>\n'
        )

    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append("</svg>")

    out_file = OUTPUT_DIR / "latency_breakdown.svg"
    out_file.write_text("".join(svg), encoding="utf-8")
    print(f"Generated {out_file}")


def generate_accuracy_latency_tradeoff(data: dict):
    width, height = 960, 520
    strategies = list(data["strategies"].keys())
    labels = [
        "Dense Only",
        "Sparse Only",
        "Hybrid Weighted",
        "Hybrid RRF (Prod)",
        "Hybrid RRF + RR",
        "Hybrid Wtd + RR",
        "Metadata Filt",
        "HyDE + RRF",
    ]
    colors = [
        "#38bdf8",
        "#a855f7",
        "#3b82f6",
        "#10b981",  # Prod RRF in vibrant green
        "#f59e0b",
        "#ec4899",
        "#6366f1",
        "#ef4444",
    ]

    mean_lats = [data["strategies"][s]["latency"]["total"]["mean"] for s in strategies]
    ndcg5_vals = [data["strategies"][s]["overall_canonical"]["ndcg@5"] for s in strategies]
    hit1_vals = [data["strategies"][s]["overall_canonical"]["hit@1"] for s in strategies]

    svg = [make_svg_header(width, height, "Pareto Trade-Off: Ranking Quality (NDCG@5) vs Mean Latency (ms)")]

    chart_left = 80
    chart_right = 880
    chart_top = 80
    chart_bottom = 440
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    # X-axis: 0 to 1200 ms
    max_x = 1200.0
    for x_val in range(0, 1300, 200):
        x_pos = chart_left + ((x_val / max_x) * chart_w)
        svg.append(
            f'  <line x1="{x_pos}" y1="{chart_top}" x2="{x_pos}" y2="{chart_bottom}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{x_pos}" y="{chart_bottom + 20}" text-anchor="middle" class="tick-label">{x_val}ms</text>\n'
        )

    # Y-axis: 0.50 to 0.70
    min_y = 0.50
    max_y = 0.70
    for y_step in [0.50, 0.55, 0.60, 0.65, 0.70]:
        y_pos = chart_bottom - (((y_step - min_y) / (max_y - min_y)) * chart_h)
        svg.append(
            f'  <line x1="{chart_left}" y1="{y_pos}" x2="{chart_right}" y2="{y_pos}" class="grid-line"/>\n'
        )
        svg.append(
            f'  <text x="{chart_left - 10}" y="{y_pos + 4}" text-anchor="end" class="tick-label">{y_step:.2f}</text>\n'
        )

    # Axis titles
    svg.append(
        f'  <text x="{chart_left + chart_w/2}" y="{chart_bottom + 45}" text-anchor="middle" class="axis-label">Mean Pipeline Latency (ms) — Lower is Better →</text>\n'
    )
    svg.append(
        f'  <text x="25" y="{chart_top + chart_h/2}" text-anchor="middle" transform="rotate(-90 25 {chart_top + chart_h/2})" class="axis-label">NDCG@5 — Higher is Better →</text>\n'
    )

    # Plot points
    for i, strat in enumerate(strategies):
        x_val = min(mean_lats[i], max_x)
        y_val = ndcg5_vals[i]
        cx = chart_left + ((x_val / max_x) * chart_w)
        cy = chart_bottom - (((y_val - min_y) / (max_y - min_y)) * chart_h)
        color = colors[i]
        radius = 8 + (hit1_vals[i] * 6)  # Bubble size scales with Hit@1

        svg.append(
            f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="{color}" fill-opacity="0.85" stroke="#ffffff" stroke-width="1.5"/>\n'
        )

        # Label offset
        offset_y = -14 if i % 2 == 0 else 18
        svg.append(
            f'  <text x="{cx:.1f}" y="{cy + offset_y:.1f}" text-anchor="middle" class="val-label" fill="{color}">{labels[i]} ({y_val:.3f}, {x_val:.0f}ms)</text>\n'
        )

    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append(
        f'  <line x1="{chart_left}" y1="{chart_top}" x2="{chart_left}" y2="{chart_bottom}" class="axis-line"/>\n'
    )
    svg.append("</svg>")

    out_file = OUTPUT_DIR / "accuracy_vs_latency_tradeoff.svg"
    out_file.write_text("".join(svg), encoding="utf-8")
    print(f"Generated {out_file}")


def main():
    if not BENCHMARK_PATH.exists():
        print(f"Error: {BENCHMARK_PATH} not found. Run evaluate_stage4_task50.py first.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    generate_hit_at_k_chart(data)
    generate_mrr_ndcg_chart(data)
    generate_precision_recall_chart(data)
    generate_latency_breakdown_chart(data)
    generate_accuracy_latency_tradeoff(data)
    print("All 5 standalone SVG figures generated successfully in eval_results/figures/!")


if __name__ == "__main__":
    main()

