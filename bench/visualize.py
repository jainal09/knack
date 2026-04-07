#!/usr/bin/env python3
"""Visualize benchmark results — generates PNG charts from results/*.json."""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

RESULTS = Path(__file__).resolve().parent.parent / "results"
CHARTS = RESULTS / "charts"

KAFKA_COLOR = "#E04E39"
NATS_COLOR = "#27AAE1"
BROKERS = ["kafka", "nats"]
COLORS = {"kafka": KAFKA_COLOR, "nats": NATS_COLOR}


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


# ── Main ──────────────────────────────────────────────────────────────


def main():
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
