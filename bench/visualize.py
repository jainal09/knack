#!/usr/bin/env python3
"""Visualize benchmark results — generates PNG charts from results/*.json.

Single-scenario mode (default):
    uv run python3 bench/visualize.py
    RESULTS_DIR=results/large uv run python3 bench/visualize.py

Cross-scenario comparison mode:
    SCENARIO_NAMES="large medium small" uv run python3 bench/visualize.py --compare
"""

import json
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec

_project_root = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("RESULTS_DIR", str(_project_root / "results")))
CHARTS = RESULTS / "charts"

KAFKA_COLOR = "#E04E39"
NATS_COLOR = "#27AAE1"
BROKERS = ["kafka", "nats"]
COLORS = {"kafka": KAFKA_COLOR, "nats": NATS_COLOR}
SCENARIO_COLORS = {"large": "#4CAF50", "medium": "#FF9800", "small": "#F44336"}


def load(name):
    p = RESULTS / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def load_jsonl(name):
    """Load file with one JSON object per line."""
    p = RESULTS / name
    if not p.exists():
        return []
    entries = []
    for line in p.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def setup_style():
    plt.rcParams.update(
        {
            "figure.facecolor": "#1e1e1e",
            "axes.facecolor": "#2d2d2d",
            "axes.edgecolor": "#555",
            "axes.labelcolor": "#ccc",
            "text.color": "#ccc",
            "xtick.color": "#aaa",
            "ytick.color": "#aaa",
            "grid.color": "#444",
            "grid.alpha": 0.5,
            "font.size": 11,
            "figure.dpi": 150,
        }
    )


# ── Chart 1: Idle Footprint ──────────────────────────────────────────


def chart_idle():
    """Bar chart: idle RAM + CPU for both brokers."""
    data = {}
    for b in BROKERS:
        d = load(f"{b}_idle_stats.json")
        if d:
            # Parse mem_usage like "428MiB / 4GiB"
            mem_str = d.get("mem_usage", "0MiB / 0GiB")
            used = mem_str.split("/")[0].strip()
            if "GiB" in used:
                mb = float(used.replace("GiB", "").strip()) * 1024
            elif "MiB" in used:
                mb = float(used.replace("MiB", "").strip())
            elif "KiB" in used:
                mb = float(used.replace("KiB", "").strip()) / 1024
            else:
                mb = 0
            cpu = float(d.get("cpu_pct", "0%").replace("%", ""))
            data[b] = {"ram_mb": mb, "cpu_pct": cpu}

    if not data:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    fig.suptitle("Idle Resource Footprint (5 min, no connections)", fontweight="bold")

    brokers = list(data.keys())
    ram = [data[b]["ram_mb"] for b in brokers]
    cpu = [data[b]["cpu_pct"] for b in brokers]
    colors = [COLORS[b] for b in brokers]

    ax1.bar(brokers, ram, color=colors, width=0.5)
    ax1.set_ylabel("RAM (MiB)")
    ax1.set_title("Memory Usage")
    for i, v in enumerate(ram):
        ax1.text(i, v + max(ram) * 0.02, f"{v:.0f}", ha="center", fontweight="bold")

    ax2.bar(brokers, cpu, color=colors, width=0.5)
    ax2.set_ylabel("CPU %")
    ax2.set_title("CPU Usage")
    for i, v in enumerate(cpu):
        ax2.text(i, v + max(cpu) * 0.02, f"{v:.1f}%", ha="center", fontweight="bold")

    plt.tight_layout()
    fig.savefig(CHARTS / "01_idle_footprint.png", bbox_inches="tight")
    plt.close()
    print("  -> 01_idle_footprint.png")


# ── Chart 2: Startup & Recovery ───────────────────────────────────────


def chart_startup():
    """Grouped bar: startup vs recovery time."""
    data = {}
    for b in BROKERS:
        entries = load_jsonl(f"{b}_startup.json")
        if entries:
            data[b] = {}
            for e in entries:
                data[b][e["type"]] = e["ms"]

    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.suptitle("Startup & SIGKILL Recovery Time", fontweight="bold")

    x = np.arange(len(data))
    w = 0.3
    brokers = list(data.keys())

    startup = [data[b].get("startup", 0) for b in brokers]
    recovery = [data[b].get("recovery", 0) for b in brokers]

    bars1 = ax.bar(
        x - w / 2,
        startup,
        w,
        label="Cold Start",
        color=[COLORS[b] for b in brokers],
        alpha=0.85,
    )
    bars2 = ax.bar(
        x + w / 2,
        recovery,
        w,
        label="SIGKILL Recovery",
        color=[COLORS[b] for b in brokers],
        alpha=0.5,
    )

    ax.set_ylabel("Time (ms)")
    ax.set_xticks(x)
    ax.set_xticklabels([b.upper() for b in brokers])
    ax.legend()

    for bar in bars1:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{bar.get_height():.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )
    for bar in bars2:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{bar.get_height():.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )

    plt.tight_layout()
    fig.savefig(CHARTS / "02_startup_recovery.png", bbox_inches="tight")
    plt.close()
    print("  -> 02_startup_recovery.png")


# ── Chart 3: Throughput ───────────────────────────────────────────────


def chart_throughput():
    """Bar chart with error markers: median throughput across 3 runs."""
    data = {}
    for b in BROKERS:
        rates = []
        for i in range(1, 4):
            d = load(f"{b}_throughput_run{i}.json")
            if d and "aggregate_rate" in d:
                rates.append(d["aggregate_rate"])
        if rates:
            data[b] = {"rates": rates, "median": sorted(rates)[len(rates) // 2]}

    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    fig.suptitle("Sustained Throughput (median of 3 runs)", fontweight="bold")

    brokers = list(data.keys())
    medians = [data[b]["median"] for b in brokers]
    all_rates = [data[b]["rates"] for b in brokers]
    colors = [COLORS[b] for b in brokers]

    ax.bar(brokers, medians, color=colors, width=0.5)

    # scatter individual runs
    for i, b in enumerate(brokers):
        ax.scatter(
            [i] * len(all_rates[i]),
            all_rates[i],
            color="white",
            zorder=5,
            s=30,
            edgecolors="#333",
            linewidth=0.5,
        )

    ax.set_ylabel("Messages / sec (aggregate)")
    for i, v in enumerate(medians):
        ax.text(i, v + max(medians) * 0.02, f"{v:,.0f}", ha="center", fontweight="bold")

    plt.tight_layout()
    fig.savefig(CHARTS / "03_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> 03_throughput.png")


# ── Chart 4: Latency Percentiles ─────────────────────────────────────


def chart_latency():
    """Grouped bar: p50/p95/p99/p999/max latency."""
    data = {}
    for b in BROKERS:
        d = load(f"{b}_latency.json")
        if d:
            data[b] = d

    if not data:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("End-to-End Latency @ 50% Peak Throughput", fontweight="bold")

    percentiles = ["p50_us", "p95_us", "p99_us", "p999_us", "max_us"]
    labels = ["p50", "p95", "p99", "p99.9", "max"]
    x = np.arange(len(percentiles))
    w = 0.3
    brokers = list(data.keys())

    for i, b in enumerate(brokers):
        vals = [data[b].get(p, 0) for p in percentiles]
        offset = (i - (len(brokers) - 1) / 2) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )

    ax.set_ylabel("Latency (µs)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(CHARTS / "04_latency.png", bbox_inches="tight")
    plt.close()
    print("  -> 04_latency.png")


# ── Chart 5: Memory Stress ───────────────────────────────────────────


def chart_memory_stress():
    """Heatmap-style chart: pass/fail per memory level per broker."""
    levels = ["4g", "2g", "1g", "512m"]
    data = {b: {} for b in BROKERS}

    for b in BROKERS:
        for mem in levels:
            d = load(f"{b}_mem_{mem}.json")
            if d:
                data[b][mem] = d.get("status", "UNKNOWN")
                if data[b][mem] == "PASS" and "aggregate_rate" in d:
                    data[b][f"{mem}_rate"] = d["aggregate_rate"]

    if not any(data[b] for b in BROKERS):
        return

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.suptitle("Memory Stress — Min Viable RAM", fontweight="bold")

    x = np.arange(len(levels))
    w = 0.3

    for i, b in enumerate(BROKERS):
        statuses = [data[b].get(mem, "N/A") for mem in levels]
        colors_list = []
        for s in statuses:
            if s == "PASS":
                colors_list.append(COLORS[b])
            elif "FAIL" in s:
                colors_list.append("#666")
            else:
                colors_list.append("#444")

        offset = (i - 0.5) * w
        vals = [1] * len(levels)  # uniform height, color encodes pass/fail
        bars = ax.bar(
            x + offset, vals, w, color=colors_list, edgecolor="#888", linewidth=0.5
        )

        for j, (bar, status) in enumerate(zip(bars, statuses)):
            label = "PASS" if status == "PASS" else "FAIL"
            rate_key = f"{levels[j]}_rate"
            extra = ""
            if rate_key in data[b]:
                extra = f"\n{data[b][rate_key]:,.0f}/s"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                0.5,
                f"{b.upper()}\n{label}{extra}",
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(levels)
    ax.set_xlabel("Memory Limit")
    ax.set_yticks([])
    ax.set_ylim(0, 1.2)

    plt.tight_layout()
    fig.savefig(CHARTS / "05_memory_stress.png", bbox_inches="tight")
    plt.close()
    print("  -> 05_memory_stress.png")


# ── Chart 6: Summary Scorecard ────────────────────────────────────────


def chart_scorecard():
    """Table-style summary of all metrics side by side."""
    report = load("full_report.json")
    if not report:
        return

    rows = []

    # Throughput
    for b in BROKERS:
        t = report.get("throughput", {}).get(b, {})
        rate = t.get("median_aggregate_rate")
        if rate:
            rows.append((f"{b.upper()} Throughput", f"{rate:,.0f} msg/s"))

    # Latency
    for b in BROKERS:
        lat = report.get("latency", {}).get(b, {})
        p99 = lat.get("p99_us")
        if p99:
            rows.append((f"{b.upper()} p99 Latency", f"{p99:,.0f} µs"))

    # Memory
    for b in BROKERS:
        mem = report.get("memory_stress", {}).get(b, {})
        min_ram = mem.get("min_viable_ram")
        if min_ram:
            rows.append((f"{b.upper()} Min RAM", min_ram.upper()))

    # Decision
    dec = report.get("decision", {})
    if dec.get("recommendation"):
        rows.append(("RECOMMENDATION", dec["recommendation"]))

    if not rows:
        return

    fig, ax = plt.subplots(figsize=(8, 0.5 * len(rows) + 1))
    fig.suptitle("Benchmark Scorecard", fontweight="bold", fontsize=14)
    ax.axis("off")

    table = ax.table(
        cellText=[[r[1]] for r in rows],
        rowLabels=[r[0] for r in rows],
        colLabels=["Value"],
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 1.8)

    # Style cells
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#555")
        if row == 0:
            cell.set_facecolor("#444")
            cell.set_text_props(fontweight="bold")
        else:
            cell.set_facecolor("#2d2d2d")

    plt.tight_layout()
    fig.savefig(CHARTS / "06_scorecard.png", bbox_inches="tight")
    plt.close()
    print("  -> 06_scorecard.png")


# ── Cross-Scenario Comparison Charts ──────────────────────────────────


def _load_scenario(scenario_name, filename):
    """Load a JSON file from a specific scenario's results dir."""
    p = _project_root / "results" / scenario_name / filename
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _load_scenario_jsonl(scenario_name, filename):
    p = _project_root / "results" / scenario_name / filename
    if not p.exists():
        return []
    entries = []
    for line in p.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def compare_idle(scenarios, out_dir):
    """Grouped bar: idle RAM for Kafka vs NATS across scenarios."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            d = _load_scenario(sc, f"{b}_idle_stats.json")
            if d:
                mem_str = d.get("mem_usage", "0MiB / 0GiB")
                used = mem_str.split("/")[0].strip()
                if "GiB" in used:
                    mb = float(used.replace("GiB", "").strip()) * 1024
                elif "MiB" in used:
                    mb = float(used.replace("MiB", "").strip())
                elif "KiB" in used:
                    mb = float(used.replace("KiB", "").strip()) / 1024
                else:
                    mb = 0
                data[sc][b] = mb

    if not any(data[sc] for sc in scenarios):
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("Idle RAM — Cross-Scenario Comparison", fontweight="bold")

    x = np.arange(len(scenarios))
    w = 0.3
    for i, b in enumerate(BROKERS):
        vals = [data[sc].get(b, 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

    ax.set_ylabel("RAM (MiB)")
    ax.set_xticks(x)
    ax.set_xticklabels([s.upper() for s in scenarios])
    ax.set_xlabel("Hardware Scenario")
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "cmp_01_idle.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_01_idle.png")


def compare_startup(scenarios, out_dir):
    """Grouped bar: startup + recovery across scenarios."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            entries = _load_scenario_jsonl(sc, f"{b}_startup.json")
            for e in entries:
                data[sc][(b, e["type"])] = e["ms"]

    if not any(data[sc] for sc in scenarios):
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("Startup & Recovery — Cross-Scenario Comparison", fontweight="bold")

    for ax, metric, title in zip(
        axes, ["startup", "recovery"], ["Cold Start (ms)", "SIGKILL Recovery (ms)"]
    ):
        x = np.arange(len(scenarios))
        w = 0.3
        for i, b in enumerate(BROKERS):
            vals = [data[sc].get((b, metric), 0) for sc in scenarios]
            offset = (i - 0.5) * w
            bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height(),
                        f"{v:.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=8,
                        fontweight="bold",
                    )
        ax.set_ylabel("Time (ms)")
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels([s.upper() for s in scenarios])
        ax.legend()

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_02_startup.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_02_startup.png")


def compare_throughput(scenarios, out_dir):
    """Grouped bar: median throughput (Python client) across scenarios."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            rates = []
            for i in range(1, 4):
                d = _load_scenario(sc, f"{b}_throughput_run{i}.json")
                if d and "aggregate_rate" in d:
                    rates.append(d["aggregate_rate"])
            if rates:
                data[sc][b] = sorted(rates)[len(rates) // 2]

    if not any(data[sc] for sc in scenarios):
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle(
        "Sustained Throughput — Cross-Scenario Comparison (Python Client)",
        fontweight="bold",
    )

    x = np.arange(len(scenarios))
    w = 0.3
    for i, b in enumerate(BROKERS):
        vals = [data[sc].get(b, 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

    ax.set_ylabel("Messages / sec")
    ax.set_xticks(x)
    ax.set_xticklabels([s.upper() for s in scenarios])
    ax.set_xlabel("Hardware Scenario")
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "cmp_03_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_03_throughput.png")


def compare_cli_throughput(scenarios, out_dir):
    """Grouped bar: CLI-native throughput across scenarios."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            d = _load_scenario(sc, f"{b}_cli_throughput.json")
            if d and "msgs_per_sec" in d:
                data[sc][b] = d["msgs_per_sec"]

    if not any(data[sc] for sc in scenarios):
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("CLI-Native Throughput — Cross-Scenario Comparison", fontweight="bold")

    x = np.arange(len(scenarios))
    w = 0.3
    for i, b in enumerate(BROKERS):
        vals = [data[sc].get(b, 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

    ax.set_ylabel("Messages / sec")
    ax.set_xticks(x)
    ax.set_xticklabels([s.upper() for s in scenarios])
    ax.set_xlabel("Hardware Scenario")
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "cmp_04_cli_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_04_cli_throughput.png")


def compare_latency(scenarios, out_dir):
    """Grouped bar: p99 latency across scenarios for each broker."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            d = _load_scenario(sc, f"{b}_latency.json")
            if d:
                data[sc][b] = d

    if not any(data[sc] for sc in scenarios):
        return

    percentiles = ["p50_us", "p95_us", "p99_us"]
    labels = ["p50", "p95", "p99"]

    fig, axes = plt.subplots(1, len(BROKERS), figsize=(14, 5), sharey=True)
    fig.suptitle("Latency Percentiles — Cross-Scenario Comparison", fontweight="bold")

    for ax, b in zip(axes, BROKERS):
        x = np.arange(len(percentiles))
        w = 0.8 / len(scenarios)
        for j, sc in enumerate(scenarios):
            vals = [data[sc].get(b, {}).get(p, 0) for p in percentiles]
            offset = (j - (len(scenarios) - 1) / 2) * w
            bars = ax.bar(
                x + offset,
                vals,
                w,
                label=sc.upper(),
                color=SCENARIO_COLORS.get(sc, "#888"),
            )
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height(),
                        f"{v:,.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        fontweight="bold",
                    )
        ax.set_title(b.upper(), fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_ylabel("Latency (µs)" if b == BROKERS[0] else "")
        ax.set_yscale("log")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_05_latency.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_05_latency.png")


def compare_memory_stress(scenarios, out_dir):
    """Heatmap table: pass/fail across scenarios and memory levels."""
    levels = ["4g", "2g", "1g", "512m"]
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            for mem in levels:
                d = _load_scenario(sc, f"{b}_mem_{mem}.json")
                if d:
                    data[sc][(b, mem)] = d.get("status", "UNKNOWN")

    if not any(data[sc] for sc in scenarios):
        return

    fig, ax = plt.subplots(figsize=(12, 4))
    fig.suptitle("Memory Stress Results — Cross-Scenario", fontweight="bold")

    # Build table: rows = (scenario, broker), cols = memory levels
    row_labels = []
    cell_text = []
    cell_colors = []
    for sc in scenarios:
        for b in BROKERS:
            row_labels.append(f"{sc.upper()} / {b.upper()}")
            row = []
            row_c = []
            for mem in levels:
                status = data[sc].get((b, mem), "N/A")
                row.append(status)
                if status == "PASS":
                    row_c.append(COLORS[b])
                elif "FAIL" in status:
                    row_c.append("#663333")
                else:
                    row_c.append("#444")
            cell_text.append(row)
            cell_colors.append(row_c)

    ax.axis("off")
    table = ax.table(
        cellText=cell_text,
        rowLabels=row_labels,
        colLabels=levels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.8)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#555")
        if row == 0:
            cell.set_facecolor("#444")
            cell.set_text_props(fontweight="bold", color="#ccc")
        elif col == -1:
            cell.set_facecolor("#333")
            cell.set_text_props(color="#ccc")
        else:
            cell.set_facecolor(cell_colors[row - 1][col])
            cell.set_text_props(color="#fff", fontweight="bold")

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_06_memory_stress.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_06_memory_stress.png")


def create_mega_image(scenarios, out_dir):
    """Combine all comparison PNGs into a single mega-image."""
    from PIL import Image

    chart_files = sorted(out_dir.glob("cmp_*.png"))
    if not chart_files:
        print("  No comparison charts found for mega-image.")
        return

    images = [Image.open(f) for f in chart_files]

    # Also include per-scenario scorecards if they exist
    for sc in scenarios:
        sc_scorecard = _project_root / "results" / sc / "charts" / "06_scorecard.png"
        if sc_scorecard.exists():
            images.append(Image.open(sc_scorecard))

    # Layout: 2 columns
    cols = 2
    rows = (len(images) + cols - 1) // cols

    # Scale all images to the same width
    target_w = max(img.width for img in images)
    scaled = []
    for img in images:
        ratio = target_w / img.width
        new_h = int(img.height * ratio)
        scaled.append(img.resize((target_w, new_h), Image.LANCZOS))

    # Compute row heights (max in each row pair)
    row_heights = []
    for r in range(rows):
        h = 0
        for c in range(cols):
            idx = r * cols + c
            if idx < len(scaled):
                h = max(h, scaled[idx].height)
        row_heights.append(h)

    padding = 20
    total_w = cols * target_w + (cols + 1) * padding
    total_h = sum(row_heights) + (rows + 1) * padding

    mega = Image.new("RGB", (total_w, total_h), color=(30, 30, 30))

    y_offset = padding
    for r in range(rows):
        x_offset = padding
        for c in range(cols):
            idx = r * cols + c
            if idx < len(scaled):
                mega.paste(scaled[idx], (x_offset, y_offset))
            x_offset += target_w + padding
        y_offset += row_heights[r] + padding

    out_path = out_dir / "mega_comparison.png"
    mega.save(out_path, optimize=True)
    print(f"  -> mega_comparison.png ({total_w}x{total_h})")

    # Cleanup
    for img in images:
        img.close()


def run_compare(scenarios):
    """Generate cross-scenario comparison charts."""
    out_dir = _project_root / "results" / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Generating cross-scenario charts for: {', '.join(s.upper() for s in scenarios)}"
    )

    compare_idle(scenarios, out_dir)
    compare_startup(scenarios, out_dir)
    compare_throughput(scenarios, out_dir)
    compare_cli_throughput(scenarios, out_dir)
    compare_latency(scenarios, out_dir)
    compare_memory_stress(scenarios, out_dir)
    create_mega_image(scenarios, out_dir)

    print(f"\nComparison charts saved to {out_dir}/")


# ── Main ──────────────────────────────────────────────────────────────


def main():
    if "--compare" in sys.argv:
        setup_style()
        names_str = os.environ.get("SCENARIO_NAMES", "large medium small")
        scenarios = names_str.split()
        run_compare(scenarios)
        return

    if not RESULTS.exists():
        print(f"No results directory at {RESULTS}", file=sys.stderr)
        sys.exit(1)

    CHARTS.mkdir(exist_ok=True)
    setup_style()

    print("Generating charts from results/ ...")

    chart_idle()
    chart_startup()
    chart_throughput()
    chart_latency()
    chart_memory_stress()
    chart_scorecard()

    print(f"\nAll charts saved to {CHARTS}/")


if __name__ == "__main__":
    main()
