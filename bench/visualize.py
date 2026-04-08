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

_project_root = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("RESULTS_DIR", str(_project_root / "results")))
CHARTS = RESULTS / "charts"

KAFKA_COLOR = "#E04E39"
NATS_COLOR = "#27AAE1"
BROKERS = ["kafka", "nats"]
COLORS = {"kafka": KAFKA_COLOR, "nats": NATS_COLOR}
SCENARIO_COLORS = {"large": "#4CAF50", "medium": "#FF9800", "small": "#F44336"}
REPS = int(os.environ.get("REPS", "3"))


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


def _annotate_direction(ax, text, loc="upper right"):
    """Add a small 'higher/lower is better' annotation to a chart."""
    anchor = {
        "upper right": (0.98, 0.97),
        "upper left": (0.02, 0.97),
        "lower right": (0.98, 0.03),
        "lower left": (0.02, 0.03),
    }
    ha = "right" if "right" in loc else "left"
    va = "top" if "upper" in loc else "bottom"
    xy = anchor.get(loc, (0.98, 0.97))
    ax.annotate(
        text,
        xy=xy,
        xycoords="axes fraction",
        ha=ha,
        va=va,
        fontsize=8,
        fontstyle="italic",
        color="#888",
        bbox=dict(boxstyle="round,pad=0.3", fc="#222", ec="#555", alpha=0.8),
    )


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
    _annotate_direction(ax1, "\u2193 Lower is better")

    ax2.bar(brokers, cpu, color=colors, width=0.5)
    ax2.set_ylabel("CPU %")
    ax2.set_title("CPU Usage")
    for i, v in enumerate(cpu):
        ax2.text(i, v + max(cpu) * 0.02, f"{v:.1f}%", ha="center", fontweight="bold")
    _annotate_direction(ax2, "\u2193 Lower is better")

    fig.text(
        0.5,
        -0.02,
        "Resource consumption when broker is running but idle (no producers/consumers connected).\n"
        "Shows the baseline cost of running each broker. Lower = less wasted resources when traffic is quiet.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

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
    _annotate_direction(ax, "\u2193 Lower is better")

    fig.text(
        0.5,
        -0.02,
        "Time from 'docker start' to broker accepting connections (Cold Start) and after SIGKILL crash (Recovery).\n"
        "Measures operational resilience — how fast the broker recovers from restarts or failures.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "02_startup_recovery.png", bbox_inches="tight")
    plt.close()
    print("  -> 02_startup_recovery.png")


# ── Chart 3: Throughput ───────────────────────────────────────────────


def chart_throughput():
    """Grouped bar: Python client vs CLI producer throughput for both brokers."""
    py_data = {}
    cli_data = {}
    for b in BROKERS:
        # Python client
        rates = []
        for i in range(1, REPS + 1):
            d = load(f"{b}_throughput_run{i}.json")
            if d and "aggregate_rate" in d:
                rates.append(d["aggregate_rate"])
        if rates:
            py_data[b] = sorted(rates)[len(rates) // 2]
        # CLI
        d = load(f"{b}_cli_throughput.json")
        if d and "msgs_per_sec" in d:
            cli_data[b] = d["msgs_per_sec"]

    if not py_data and not cli_data:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("Producer Throughput — Python Client vs CLI", fontweight="bold")

    x = np.arange(len(BROKERS))
    w = 0.3

    py_vals = [py_data.get(b, 0) for b in BROKERS]
    cli_vals = [cli_data.get(b, 0) for b in BROKERS]

    bars1 = ax.bar(
        x - w / 2,
        py_vals,
        w,
        label="Python Client",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.85,
    )
    bars2 = ax.bar(
        x + w / 2,
        cli_vals,
        w,
        label="CLI (kcat / nats bench)",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.5,
    )

    ax.set_ylabel("Messages / sec")
    ax.set_xticks(x)
    ax.set_xticklabels([b.upper() for b in BROKERS])
    ax.legend()

    for bar in list(bars1) + list(bars2):
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h,
                f"{h:,.0f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )
    _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "Compares message production speed using Python client libraries vs native CLI tools.\n"
        "Python client reflects application-level performance; CLI reflects raw broker capacity.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "03_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> 03_throughput.png")


# ── Chart 3b: CLI-only Throughput ────────────────────────────────────


def chart_cli_throughput():
    """Standalone bar chart: CLI producer, consumer, and prodcon for both brokers."""
    metrics = []

    # CLI Producer
    for b in BROKERS:
        d = load(f"{b}_cli_throughput.json")
        if d and "msgs_per_sec" in d:
            metrics.append(("Producer", b, d["msgs_per_sec"]))

    # CLI Consumer
    for b in BROKERS:
        d = load(f"{b}_cli_consumer.json")
        if d and "msgs_per_sec" in d:
            metrics.append(("Consumer", b, d["msgs_per_sec"]))

    # CLI ProdCon
    for b in BROKERS:
        d = load(f"{b}_cli_prodcon.json")
        if d:
            pr = d.get("producer_msgs_per_sec", 0)
            cr = d.get("consumer_msgs_per_sec", 0)
            if pr:
                metrics.append(("ProdCon Pub", b, pr))
            if cr:
                metrics.append(("ProdCon Sub", b, cr))

    if not metrics:
        return

    # Group by test type
    test_types = list(dict.fromkeys(m[0] for m in metrics))
    x = np.arange(len(test_types))
    w = 0.3

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle("CLI-Native Throughput — kcat vs nats bench", fontweight="bold")

    for i, b in enumerate(BROKERS):
        vals = []
        for tt in test_types:
            v = next((m[2] for m in metrics if m[0] == tt and m[1] == b), 0)
            vals.append(v)
        offset = (i - 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b], alpha=0.85)
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h,
                    f"{h:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )

    ax.set_ylabel("Messages / sec")
    ax.set_xlabel("Test Type")
    ax.set_xticks(x)
    ax.set_xticklabels(test_types)
    ax.legend()
    _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "Raw broker throughput measured with native CLI tools (kcat for Kafka, nats bench for NATS).\n"
        "Eliminates Python client overhead — reflects broker protocol efficiency and I/O design.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "03b_cli_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> 03b_cli_throughput.png")


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
    ax.set_xlabel("Latency Percentile")
    ax.grid(axis="y", alpha=0.3)
    _annotate_direction(ax, "\u2193 Lower is better")

    fig.text(
        0.5,
        -0.02,
        "End-to-end latency (producer \u2192 consumer) measured at 50% of peak throughput.\n"
        "p99 = 99th percentile — the worst latency experienced by 99% of messages. Critical for SLA compliance.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

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

    fig.text(
        0.5,
        -0.02,
        "Tests broker stability under progressively restricted memory (Docker cgroup limits).\n"
        "PASS = broker runs and serves traffic. Shows minimum RAM needed for each broker to function.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "05_memory_stress.png", bbox_inches="tight")
    plt.close()
    print("  -> 05_memory_stress.png")


# ── Chart 6: Summary Scorecard ────────────────────────────────────────


def chart_scorecard():
    """Comprehensive table scorecard: Kafka vs NATS with Python + CLI data."""
    report = load("full_report.json")
    if not report:
        return

    # Build rows: (Metric, Kafka, NATS, Direction, Winner)
    rows = []

    def _winner(kafka_val, nats_val, lower_better=False):
        if kafka_val is None or nats_val is None:
            return ""
        if kafka_val == nats_val:
            return "TIE"
        if lower_better:
            return "KAFKA" if kafka_val < nats_val else "NATS"
        return "KAFKA" if kafka_val > nats_val else "NATS"

    # Idle footprint
    kafka_idle = load("kafka_idle_stats.json")
    nats_idle = load("nats_idle_stats.json")
    if kafka_idle and nats_idle:
        k_mem = kafka_idle.get("mem_usage", "?")
        n_mem = nats_idle.get("mem_usage", "?")
        k_cpu = kafka_idle.get("cpu_pct", "?")
        n_cpu = nats_idle.get("cpu_pct", "?")
        rows.append(
            (
                "Idle RAM",
                k_mem.split("/")[0].strip(),
                n_mem.split("/")[0].strip(),
                "\u2193 Lower",
                "NATS",
            )
        )
        rows.append(("Idle CPU", k_cpu, n_cpu, "\u2193 Lower", "NATS"))

    # Producer throughput (Python)
    k_tp = report.get("throughput", {}).get("kafka", {}).get("median_aggregate_rate")
    n_tp = report.get("throughput", {}).get("nats", {}).get("median_aggregate_rate")
    if k_tp or n_tp:
        rows.append(
            (
                "Producer (Python)",
                f"{k_tp:,.0f}/s" if k_tp else "—",
                f"{n_tp:,.0f}/s" if n_tp else "—",
                "\u2191 Higher",
                _winner(k_tp, n_tp),
            )
        )

    # Producer throughput (CLI)
    k_cli = load("kafka_cli_throughput.json")
    n_cli = load("nats_cli_throughput.json")
    k_cli_tp = k_cli.get("msgs_per_sec") if k_cli else None
    n_cli_tp = n_cli.get("msgs_per_sec") if n_cli else None
    if k_cli_tp or n_cli_tp:
        rows.append(
            (
                "Producer (CLI)",
                f"{k_cli_tp:,.0f}/s" if k_cli_tp else "—",
                f"{n_cli_tp:,.0f}/s" if n_cli_tp else "—",
                "\u2191 Higher",
                _winner(k_cli_tp, n_cli_tp),
            )
        )

    # Consumer throughput (Python)
    k_ct = (
        report.get("consumer_throughput", {})
        .get("kafka", {})
        .get("median_aggregate_rate")
    )
    n_ct = (
        report.get("consumer_throughput", {})
        .get("nats", {})
        .get("median_aggregate_rate")
    )
    if k_ct or n_ct:
        rows.append(
            (
                "Consumer (Python)",
                f"{k_ct:,.0f}/s" if k_ct else "—",
                f"{n_ct:,.0f}/s" if n_ct else "—",
                "\u2191 Higher",
                _winner(k_ct, n_ct),
            )
        )

    # Consumer throughput (CLI)
    k_cli_c = load("kafka_cli_consumer.json")
    n_cli_c = load("nats_cli_consumer.json")
    k_cli_ct = k_cli_c.get("msgs_per_sec") if k_cli_c else None
    n_cli_ct = n_cli_c.get("msgs_per_sec") if n_cli_c else None
    if k_cli_ct or n_cli_ct:
        rows.append(
            (
                "Consumer (CLI)",
                f"{k_cli_ct:,.0f}/s" if k_cli_ct else "—",
                f"{n_cli_ct:,.0f}/s" if n_cli_ct else "—",
                "\u2191 Higher",
                _winner(k_cli_ct, n_cli_ct),
            )
        )

    # ProdCon (Python)
    for b in BROKERS:
        pc = report.get("prodcon", {}).get(b, {})
        if pc:
            pr = pc.get("producer", {}).get("aggregate_rate")
            cr = pc.get("consumer", {}).get("aggregate_rate")
            if pr and cr:
                if b == "kafka":
                    k_pc_str = f"P:{pr:,.0f} C:{cr:,.0f}"
                    k_pc_total = pr + cr
                else:
                    n_pc_str = f"P:{pr:,.0f} C:{cr:,.0f}"
                    n_pc_total = pr + cr
    if "k_pc_str" in dir() and "n_pc_str" in dir():
        rows.append(
            (
                "ProdCon (Python)",
                k_pc_str,
                n_pc_str,
                "\u2191 Higher",
                _winner(k_pc_total, n_pc_total),
            )
        )

    # ProdCon (CLI)
    k_cli_pc = load("kafka_cli_prodcon.json")
    n_cli_pc = load("nats_cli_prodcon.json")
    if k_cli_pc and n_cli_pc:
        k_pp = k_cli_pc.get("producer_msgs_per_sec", 0)
        k_cp = k_cli_pc.get("consumer_msgs_per_sec", 0)
        n_pp = n_cli_pc.get("producer_msgs_per_sec", 0)
        n_cp = n_cli_pc.get("consumer_msgs_per_sec", 0)
        rows.append(
            (
                "ProdCon (CLI)",
                f"P:{k_pp:,.0f} C:{k_cp:,.0f}",
                f"P:{n_pp:,.0f} C:{n_cp:,.0f}",
                "\u2191 Higher",
                _winner(k_pp + k_cp, n_pp + n_cp),
            )
        )

    # Latency
    k_lat = report.get("latency", {}).get("kafka", {}).get("p99_us")
    n_lat = report.get("latency", {}).get("nats", {}).get("p99_us")
    if k_lat or n_lat:
        rows.append(
            (
                "p99 Latency",
                f"{k_lat:,.0f} \u00b5s" if k_lat else "—",
                f"{n_lat:,.0f} \u00b5s" if n_lat else "—",
                "\u2193 Lower",
                _winner(k_lat, n_lat, lower_better=True),
            )
        )

    # Min RAM
    k_ram = report.get("memory_stress", {}).get("kafka", {}).get("min_viable_ram")
    n_ram = report.get("memory_stress", {}).get("nats", {}).get("min_viable_ram")
    if k_ram or n_ram:
        rows.append(
            (
                "Min Viable RAM",
                (k_ram or "—").upper(),
                (n_ram or "—").upper(),
                "\u2193 Lower",
                "TIE" if k_ram == n_ram else "",
            )
        )

    # Decision
    dec = report.get("decision", {})
    if dec.get("recommendation"):
        rows.append(("RECOMMENDATION", "", dec["recommendation"], "", ""))

    if not rows:
        return

    # Build table
    col_labels = ["Metric", "KAFKA", "NATS", "Direction", "Winner"]
    cell_text = [[r[0], r[1], r[2], r[3], r[4]] for r in rows]

    fig_height = max(5, 0.45 * len(rows) + 1.5)
    fig, ax = plt.subplots(figsize=(14, fig_height))
    fig.suptitle(
        "Benchmark Scorecard — Kafka vs NATS JetStream",
        fontweight="bold",
        fontsize=16,
        y=0.98,
    )
    ax.axis("off")

    table = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.0, 2.0)

    # Style cells
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#555")
        if row == 0:
            # Header row
            cell.set_facecolor("#444")
            cell.set_text_props(fontweight="bold", fontsize=11, color="#fff")
            cell.set_height(0.06)
        else:
            cell.set_facecolor("#2d2d2d")
            # Color the winner column
            if col == 4:
                txt = cell.get_text().get_text()
                if txt == "KAFKA":
                    cell.set_text_props(color=KAFKA_COLOR, fontweight="bold")
                elif txt == "NATS":
                    cell.set_text_props(color=NATS_COLOR, fontweight="bold")
            # Color metric names
            if col == 0:
                cell.set_text_props(fontweight="bold")
                cell.set_facecolor("#333")
            # Highlight recommendation row
            if row == len(rows) and col >= 0:
                cell.set_facecolor("#1a3a1a")
                cell.set_text_props(fontweight="bold", fontsize=11)

    # Set column widths
    col_widths = [0.22, 0.25, 0.25, 0.13, 0.12]
    for (row, col), cell in table.get_celld().items():
        cell.set_width(col_widths[col])

    plt.tight_layout()
    fig.savefig(CHARTS / "06_scorecard.png", bbox_inches="tight")
    plt.close()
    print("  -> 06_scorecard.png")


# ── Chart 7: Consumer Throughput ──────────────────────────────────────


def chart_consumer_throughput():
    """Grouped bar: Python client vs CLI consumer throughput for both brokers."""
    py_data = {}
    cli_data = {}
    for b in BROKERS:
        # Python client
        rates = []
        for i in range(1, REPS + 1):
            d = load(f"{b}_consumer_run{i}.json")
            if d and "aggregate_rate" in d:
                rates.append(d["aggregate_rate"])
        if rates:
            py_data[b] = sorted(rates)[len(rates) // 2]
        # CLI
        d = load(f"{b}_cli_consumer.json")
        if d and "msgs_per_sec" in d:
            cli_data[b] = d["msgs_per_sec"]

    if not py_data and not cli_data:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("Consumer Throughput — Python Client vs CLI", fontweight="bold")

    x = np.arange(len(BROKERS))
    w = 0.3

    py_vals = [py_data.get(b, 0) for b in BROKERS]
    cli_vals = [cli_data.get(b, 0) for b in BROKERS]

    bars1 = ax.bar(
        x - w / 2,
        py_vals,
        w,
        label="Python Client",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.85,
    )
    bars2 = ax.bar(
        x + w / 2,
        cli_vals,
        w,
        label="CLI (kcat / nats bench)",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.5,
    )

    ax.set_ylabel("Messages / sec")
    ax.set_xticks(x)
    ax.set_xticklabels([b.upper() for b in BROKERS])
    ax.legend()

    for bar in list(bars1) + list(bars2):
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h,
                f"{h:,.0f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )
    _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "Pure consumer speed — pre-populated messages consumed as fast as possible.\n"
        "Measures how quickly each broker can deliver stored messages to consumers.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "07_consumer_throughput.png", bbox_inches="tight")
    plt.close()
    print("  -> 07_consumer_throughput.png")


# ── Chart 8: Simultaneous Producer+Consumer ──────────────────────────


def chart_prodcon():
    """Grouped bar: Python client vs CLI for simultaneous producer+consumer load."""
    py_data = {}
    cli_data = {}
    for b in BROKERS:
        # Python client
        d = load(f"{b}_prodcon.json")
        if d and "producer" in d and "consumer" in d:
            py_data[b] = d
        # CLI
        d = load(f"{b}_cli_prodcon.json")
        if d:
            cli_data[b] = d

    if not py_data and not cli_data:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        "Simultaneous Producer+Consumer — Python Client vs CLI", fontweight="bold"
    )

    # Left subplot: Producer rates
    x = np.arange(len(BROKERS))
    w = 0.3

    py_prod = [
        py_data.get(b, {}).get("producer", {}).get("aggregate_rate", 0) for b in BROKERS
    ]
    cli_prod = [cli_data.get(b, {}).get("producer_msgs_per_sec", 0) for b in BROKERS]

    bars1 = ax1.bar(
        x - w / 2,
        py_prod,
        w,
        label="Python Client",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.85,
    )
    bars2 = ax1.bar(
        x + w / 2,
        cli_prod,
        w,
        label="CLI",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.5,
    )
    ax1.set_ylabel("Messages / sec")
    ax1.set_title("Producer Rate (under simultaneous load)")
    ax1.set_xticks(x)
    ax1.set_xticklabels([b.upper() for b in BROKERS])
    ax1.legend(fontsize=9)
    for bar in list(bars1) + list(bars2):
        h = bar.get_height()
        if h > 0:
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                h,
                f"{h:,.0f}",
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
            )

    # Right subplot: Consumer rates
    py_cons = [
        py_data.get(b, {}).get("consumer", {}).get("aggregate_rate", 0) for b in BROKERS
    ]
    cli_cons = [cli_data.get(b, {}).get("consumer_msgs_per_sec", 0) for b in BROKERS]

    bars3 = ax2.bar(
        x - w / 2,
        py_cons,
        w,
        label="Python Client",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.85,
    )
    bars4 = ax2.bar(
        x + w / 2,
        cli_cons,
        w,
        label="CLI",
        color=[COLORS[b] for b in BROKERS],
        alpha=0.5,
    )
    ax2.set_ylabel("Messages / sec")
    ax2.set_title("Consumer Rate (under simultaneous load)")
    ax2.set_xticks(x)
    ax2.set_xticklabels([b.upper() for b in BROKERS])
    ax2.legend(fontsize=9)
    for bar in list(bars3) + list(bars4):
        h = bar.get_height()
        if h > 0:
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                h,
                f"{h:,.0f}",
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
            )
    _annotate_direction(ax1, "\u2191 Higher is better")
    _annotate_direction(ax2, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "Producers and consumers running simultaneously — simulates real-world bidirectional load.\n"
        "Shows how each broker handles contention when both writing and reading at the same time.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "08_prodcon.png", bbox_inches="tight")
    plt.close()
    print("  -> 08_prodcon.png")


# ── Docker stats CSV helpers ─────────────────────────────────────────

# The CSV header declares 9 columns (timestamp, container, cpu_pct,
# mem_usage, mem_limit, mem_pct, net_io, block_io, pids) but data rows
# have only 8 because docker merges mem_usage and mem_limit into one
# field like "171.1MiB / 7.761GiB".  _DOCKER_COLS maps logical column
# names to correct 0-based indices for 8-field data rows.
_DOCKER_COLS = {
    "timestamp": 0,
    "container": 1,
    "cpu_pct": 2,
    "mem_usage": 3,
    "mem_pct": 4,
    "net_io": 5,
    "block_io": 6,
    "pids": 7,
}


def _parse_mem_usage(mem_str):
    """Parse memory string like '428MiB / 4GiB' -> MB float."""
    used = mem_str.split("/")[0].strip()
    if "GiB" in used:
        return float(used.replace("GiB", "").strip()) * 1024
    elif "MiB" in used:
        return float(used.replace("MiB", "").strip())
    elif "KiB" in used:
        return float(used.replace("KiB", "").strip()) / 1024
    return 0


def chart_resource_timeline():
    """Time-series chart: CPU% and Memory over time from docker_stats.csv."""
    import csv

    csv_path = RESULTS / "docker_stats.csv"
    if not csv_path.exists():
        return

    # Parse CSV
    container_data = {}  # {container: [(timestamp, cpu, mem_mb), ...]}
    target_containers = {"bench-kafka", "bench-nats"}

    with open(csv_path) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 5:
                continue
            try:
                ts = int(row[_DOCKER_COLS["timestamp"]])
                container = row[_DOCKER_COLS["container"]]
                if container not in target_containers:
                    continue
                cpu = float(row[_DOCKER_COLS["cpu_pct"]].replace("%", ""))
                mem_mb = _parse_mem_usage(row[_DOCKER_COLS["mem_usage"]])

                if container not in container_data:
                    container_data[container] = []
                container_data[container].append((ts, cpu, mem_mb))
            except (ValueError, IndexError):
                continue

    if not container_data:
        return

    # Find global min timestamp for relative time
    all_ts = []
    for points in container_data.values():
        all_ts.extend(p[0] for p in points)
    t0 = min(all_ts) if all_ts else 0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(
        "Resource Usage Over Time (from docker_stats.csv)",
        fontweight="bold",
        fontsize=14,
    )

    container_colors = {"bench-kafka": KAFKA_COLOR, "bench-nats": NATS_COLOR}

    for container, points in sorted(container_data.items()):
        points.sort(key=lambda p: p[0])
        color = container_colors.get(container, "#888")
        label = container.replace("bench-", "").upper()

        # Break line at gaps > 30s
        segments_t = []
        segments_cpu = []
        segments_mem = []
        seg_t, seg_cpu, seg_mem = [], [], []

        for i, (ts, cpu, mem) in enumerate(points):
            elapsed = (ts - t0) / 60.0  # Convert to minutes
            if seg_t and elapsed - seg_t[-1] > 0.5:  # 30s gap in minutes
                segments_t.append(seg_t)
                segments_cpu.append(seg_cpu)
                segments_mem.append(seg_mem)
                seg_t, seg_cpu, seg_mem = [], [], []
            seg_t.append(elapsed)
            seg_cpu.append(cpu)
            seg_mem.append(mem)

        if seg_t:
            segments_t.append(seg_t)
            segments_cpu.append(seg_cpu)
            segments_mem.append(seg_mem)

        for j, (st, sc, sm) in enumerate(zip(segments_t, segments_cpu, segments_mem)):
            lbl = label if j == 0 else None
            ax1.plot(st, sc, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax1.fill_between(st, sc, alpha=0.1, color=color)
            ax2.plot(st, sm, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax2.fill_between(st, sm, alpha=0.1, color=color)

    ax1.set_ylabel("CPU %")
    ax1.legend(fontsize=10, loc="upper right")
    ax1.grid(axis="both", alpha=0.2)
    _annotate_direction(ax1, "\u2193 Lower is better", loc="upper left")

    ax2.set_ylabel("Memory (MiB)")
    ax2.set_xlabel("Elapsed Time (minutes)")
    ax2.legend(fontsize=10, loc="upper right")
    ax2.grid(axis="both", alpha=0.2)
    _annotate_direction(ax2, "\u2193 Lower is better", loc="upper left")

    fig.text(
        0.5,
        -0.02,
        "Live CPU and memory usage captured every 5s via 'docker stats' across the entire benchmark run.\n"
        "Each cluster of activity = one benchmark phase. Gaps = broker restart between tests.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "09_resource_timeline.png", bbox_inches="tight")
    plt.close()
    print("  -> 09_resource_timeline.png")


# ── Chart 10: Resource Scaling Slope ─────────────────────────────────


def chart_resource_scaling():
    """Slope chart: Python + CLI throughput vs CPU limit (dual Y-axis for memory)."""
    data = {}
    for b in BROKERS:
        d = load(f"{b}_scaling.json")
        if d and isinstance(d, list):
            data[b] = d

    if not data:
        return

    fig, ax1 = plt.subplots(figsize=(12, 7))
    ax2 = ax1.twinx()
    fig.suptitle(
        "Resource Scaling \u2014 Throughput vs CPU Limit",
        fontweight="bold",
        fontsize=14,
    )

    # Lighter shades for CLI lines
    CLI_COLORS = {"kafka": "#F4A89A", "nats": "#7DD4F0"}

    for b in BROKERS:
        if b not in data:
            continue

        entries = data[b]
        color = COLORS[b]
        cli_color = CLI_COLORS[b]
        label = b.upper()

        # Separate pass and fail entries
        pass_entries = [e for e in entries if e.get("status") == "PASS"]
        fail_entries = [e for e in entries if e.get("status") != "PASS"]

        if pass_entries:
            cpus = [e["cpu_limit"] for e in pass_entries]
            throughputs = [e["throughput"] for e in pass_entries]
            cli_throughputs = [e.get("cli_throughput", 0) for e in pass_entries]
            has_cli = any(t > 0 for t in cli_throughputs)
            peak_mems = [e["peak_mem_mb"] for e in pass_entries]

            # Python client throughput (solid, filled marker)
            ax1.plot(
                cpus,
                throughputs,
                color=color,
                marker="o",
                linewidth=2.5,
                label=f"{label} Python client (msg/s)",
                markersize=9,
            )
            for xi, yi in zip(cpus, throughputs):
                ax1.annotate(
                    f"{yi:,.0f}",
                    (xi, yi),
                    textcoords="offset points",
                    xytext=(0, 12),
                    ha="center",
                    fontsize=7,
                    color=color,
                    fontweight="bold",
                )

            # CLI throughput (solid, triangle marker)
            if has_cli:
                ax1.plot(
                    cpus,
                    cli_throughputs,
                    color=cli_color,
                    marker="^",
                    linewidth=2.5,
                    label=f"{label} CLI native (msg/s)",
                    markersize=9,
                )
                for xi, yi in zip(cpus, cli_throughputs):
                    if yi > 0:
                        ax1.annotate(
                            f"{yi:,.0f}",
                            (xi, yi),
                            textcoords="offset points",
                            xytext=(0, -16),
                            ha="center",
                            fontsize=7,
                            color=cli_color,
                            fontweight="bold",
                        )

            # Peak memory (dashed, right Y-axis)
            ax2.plot(
                cpus,
                peak_mems,
                color=color,
                marker="s",
                linewidth=1.5,
                linestyle="--",
                alpha=0.6,
                label=f"{label} peak memory (MiB)",
                markersize=7,
            )

        # Mark failures with red X
        if fail_entries:
            fail_cpus = [e["cpu_limit"] for e in fail_entries]
            ax1.scatter(
                fail_cpus,
                [0] * len(fail_cpus),
                color="#FF0000",
                marker="x",
                s=150,
                linewidths=3,
                zorder=10,
                label=f"{label} FAIL",
            )

    ax1.set_xlabel(
        "CPU Limit (cores) \u2014 higher = more resources available", fontsize=11
    )
    ax1.set_ylabel("Throughput (msg/s) \u2014 solid lines (\u25cf Python, \u25b2 CLI)", fontsize=11)
    ax2.set_ylabel("Peak Memory (MiB) \u2014 dashed lines", fontsize=11)

    # Invert x-axis so highest CPU is on the left (degradation slope reads left-to-right)
    ax1.invert_xaxis()

    # Combine legends from both axes
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(
        lines1 + lines2,
        labels1 + labels2,
        loc="center right",
        fontsize=10,
        framealpha=0.8,
        edgecolor="#555",
    )

    ax1.grid(axis="both", alpha=0.2)

    fig.text(
        0.5,
        -0.03,
        "Shows how throughput degrades as CPU is progressively restricted (left\u2192right = fewer CPUs).\n"
        "Solid circles = Python client throughput; triangles = CLI-native throughput (raw broker capacity).\n"
        "The 'knee' where throughput drops sharply reveals each broker's minimum viable CPU.\n"
        "Red X = broker failed to start or OOM'd at that CPU level.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "10_resource_scaling.png", bbox_inches="tight")
    plt.close()
    print("  -> 10_resource_scaling.png")


# ── Chart 11: Disk I/O Over Time ─────────────────────────────────────


def _parse_block_io(bio_str):
    """Parse block I/O string like '12.3MB / 456kB' -> (read_mb, write_mb)."""
    parts = bio_str.split("/")
    if len(parts) != 2:
        return 0.0, 0.0

    def _to_mb(s):
        s = s.strip()
        if "GB" in s:
            return float(s.replace("GB", "").strip()) * 1024
        elif "MB" in s:
            return float(s.replace("MB", "").strip())
        elif "kB" in s:
            return float(s.replace("kB", "").strip()) / 1024
        elif "B" in s:
            return float(s.replace("B", "").strip()) / (1024 * 1024)
        return 0.0

    return _to_mb(parts[0]), _to_mb(parts[1])


def chart_disk_io_timeline():
    """Time-series chart: disk read/write (block I/O) over time from docker_stats.csv."""
    import csv

    csv_path = RESULTS / "docker_stats.csv"
    if not csv_path.exists():
        return

    container_data = {}
    target_containers = {"bench-kafka", "bench-nats"}

    with open(csv_path) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 7:
                continue
            try:
                ts = int(row[_DOCKER_COLS["timestamp"]])
                container = row[_DOCKER_COLS["container"]]
                if container not in target_containers:
                    continue
                read_mb, write_mb = _parse_block_io(row[_DOCKER_COLS["block_io"]])

                if container not in container_data:
                    container_data[container] = []
                container_data[container].append((ts, read_mb, write_mb))
            except (ValueError, IndexError):
                continue

    if not container_data:
        return

    all_ts = []
    for points in container_data.values():
        all_ts.extend(p[0] for p in points)
    t0 = min(all_ts) if all_ts else 0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(
        "Disk I/O Over Time (from docker_stats.csv)", fontweight="bold", fontsize=14
    )

    container_colors = {"bench-kafka": KAFKA_COLOR, "bench-nats": NATS_COLOR}

    for container, points in sorted(container_data.items()):
        points.sort(key=lambda p: p[0])
        color = container_colors.get(container, "#888")
        label = container.replace("bench-", "").upper()

        segments_t, segments_r, segments_w = [], [], []
        seg_t, seg_r, seg_w = [], [], []

        for i, (ts, rmb, wmb) in enumerate(points):
            elapsed = (ts - t0) / 60.0
            if seg_t and elapsed - seg_t[-1] > 0.5:
                segments_t.append(seg_t)
                segments_r.append(seg_r)
                segments_w.append(seg_w)
                seg_t, seg_r, seg_w = [], [], []
            seg_t.append(elapsed)
            seg_r.append(rmb)
            seg_w.append(wmb)

        if seg_t:
            segments_t.append(seg_t)
            segments_r.append(seg_r)
            segments_w.append(seg_w)

        for j, (st, sr, sw) in enumerate(zip(segments_t, segments_r, segments_w)):
            lbl = label if j == 0 else None
            ax1.plot(st, sr, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax1.fill_between(st, sr, alpha=0.1, color=color)
            ax2.plot(st, sw, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax2.fill_between(st, sw, alpha=0.1, color=color)

    ax1.set_ylabel("Cumulative Read (MB)")
    ax1.set_title("Block I/O — Read")
    ax1.legend(fontsize=10, loc="upper right")
    ax1.grid(axis="both", alpha=0.2)

    ax2.set_ylabel("Cumulative Write (MB)")
    ax2.set_title("Block I/O — Write")
    ax2.set_xlabel("Elapsed Time (minutes)")
    ax2.legend(fontsize=10, loc="upper right")
    ax2.grid(axis="both", alpha=0.2)

    fig.text(
        0.5,
        -0.03,
        "Cumulative disk read/write reported by Docker's block I/O accounting (cgroup blkio).\n"
        "Shows how aggressively each broker hits disk — important for SSD wear and I/O-bound workloads.\n"
        "Spikes correlate with benchmark phases (throughput, latency, memory stress).",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "11_disk_io_timeline.png", bbox_inches="tight")
    plt.close()
    print("  -> 11_disk_io_timeline.png")


# ── Chart 12: Throughput vs Resource Efficiency ──────────────────────


def chart_throughput_vs_resources():
    """Scatter/bar chart: throughput normalized by CPU and RAM usage."""
    import csv

    data = {}
    for b in BROKERS:
        # Get median throughput from runs
        rates = []
        for i in range(1, REPS + 1):
            d = load(f"{b}_throughput_run{i}.json")
            if d and "aggregate_rate" in d:
                rates.append(d["aggregate_rate"])
        if not rates:
            continue
        median_tp = sorted(rates)[len(rates) // 2]

        # Get peak CPU and mem during throughput from scaling or stats
        scaling = load(f"{b}_scaling.json")
        peak_cpu = None
        peak_mem = None
        if scaling and isinstance(scaling, list):
            # Use the entry closest to actual BENCH_CPUS
            for entry in scaling:
                if entry.get("status") == "PASS":
                    peak_cpu = entry.get("peak_cpu_pct")
                    peak_mem = entry.get("peak_mem_mb")
                    break  # highest CPU limit first
        if peak_cpu is None or peak_mem is None:
            # Fallback: scan docker_stats.csv for peak values
            csv_path = RESULTS / "docker_stats.csv"
            if csv_path.exists():
                container_name = f"bench-{b}"
                max_cpu, max_mem = 0, 0
                with open(csv_path) as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    for row in reader:
                        if len(row) < 5 or row[_DOCKER_COLS["container"]] != container_name:
                            continue
                        try:
                            cpu = float(row[_DOCKER_COLS["cpu_pct"]].replace("%", ""))
                            mem = _parse_mem_usage(row[_DOCKER_COLS["mem_usage"]])
                            max_cpu = max(max_cpu, cpu)
                            max_mem = max(max_mem, mem)
                        except (ValueError, IndexError):
                            continue
                if max_cpu > 0:
                    peak_cpu = max_cpu
                if max_mem > 0:
                    peak_mem = max_mem

        if peak_cpu and peak_mem and peak_cpu > 0 and peak_mem > 0:
            data[b] = {
                "throughput": median_tp,
                "peak_cpu": peak_cpu,
                "peak_mem": peak_mem,
                "tp_per_cpu_pct": median_tp
                / (peak_cpu / 100),  # msgs/s per CPU core (approx)
                "tp_per_gb_ram": median_tp / (peak_mem / 1024),  # msgs/s per GB RAM
            }

    if not data:
        return

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle(
        "Resource Efficiency — Throughput vs Resources", fontweight="bold", fontsize=14
    )

    brokers = list(data.keys())
    colors = [COLORS[b] for b in brokers]
    labels = [b.upper() for b in brokers]

    # Panel 1: Raw throughput vs peak CPU
    ax = axes[0]
    for i, b in enumerate(brokers):
        ax.bar(i, data[b]["throughput"], color=colors[i], width=0.5)
        ax.text(
            i,
            data[b]["throughput"],
            f"{data[b]['throughput']:,.0f}\n({data[b]['peak_cpu']:.0f}% CPU)",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )
    ax.set_xticks(range(len(brokers)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Messages / sec")
    ax.set_title("Throughput (annotated with peak CPU%)")
    _annotate_direction(ax, "\u2191 Higher is better")

    # Panel 2: Throughput per CPU core
    ax = axes[1]
    vals = [data[b]["tp_per_cpu_pct"] for b in brokers]
    for i, (v, b) in enumerate(zip(vals, brokers)):
        ax.bar(i, v, color=colors[i], width=0.5)
        ax.text(
            i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9, fontweight="bold"
        )
    ax.set_xticks(range(len(brokers)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Messages / sec / CPU core")
    ax.set_title("Throughput per CPU Core")
    _annotate_direction(ax, "\u2191 Higher is better")

    # Panel 3: Throughput per GB RAM
    ax = axes[2]
    vals = [data[b]["tp_per_gb_ram"] for b in brokers]
    for i, (v, b) in enumerate(zip(vals, brokers)):
        ax.bar(i, v, color=colors[i], width=0.5)
        ax.text(
            i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9, fontweight="bold"
        )
    ax.set_xticks(range(len(brokers)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Messages / sec / GB RAM")
    ax.set_title("Throughput per GB RAM")
    _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.04,
        "Resource efficiency = how much throughput you get per unit of CPU and memory consumed.\n"
        "Higher = broker extracts more performance from the same hardware budget.\n"
        "Left: raw throughput with peak CPU annotation. Center: msgs/s per CPU core. Right: msgs/s per GB RAM.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "12_throughput_vs_resources.png", bbox_inches="tight")
    plt.close()
    print("  -> 12_throughput_vs_resources.png")


# ── Chart 13: Worker Load Balance ────────────────────────────────────


def chart_worker_balance():
    """Bar chart: per-worker throughput showing load distribution across workers."""
    test_data = {}  # {test_label: {broker: [per_worker_rates]}}

    for b in BROKERS:
        # Producer throughput (median run)
        rates_by_run = []
        for i in range(1, REPS + 1):
            d = load(f"{b}_throughput_run{i}.json")
            if d and "per_worker" in d:
                rates_by_run.append(d)
        if rates_by_run:
            # Pick median run by aggregate_rate
            rates_by_run.sort(key=lambda r: r.get("aggregate_rate", 0))
            median_run = rates_by_run[len(rates_by_run) // 2]
            workers = [w["avg_rate"] for w in median_run["per_worker"]]
            test_data.setdefault("Producer", {})[b] = workers

        # Consumer throughput (median run)
        rates_by_run = []
        for i in range(1, REPS + 1):
            d = load(f"{b}_consumer_run{i}.json")
            if d and "per_worker" in d:
                rates_by_run.append(d)
        if rates_by_run:
            rates_by_run.sort(key=lambda r: r.get("aggregate_rate", 0))
            median_run = rates_by_run[len(rates_by_run) // 2]
            workers = [w["avg_rate"] for w in median_run["per_worker"]]
            test_data.setdefault("Consumer", {})[b] = workers

        # ProdCon producer side
        d = load(f"{b}_prodcon.json")
        if d and "producer" in d and "per_worker" in d["producer"]:
            workers = [w["avg_rate"] for w in d["producer"]["per_worker"]]
            test_data.setdefault("ProdCon (prod)", {})[b] = workers

    if not test_data:
        return

    n_tests = len(test_data)
    fig, axes = plt.subplots(1, n_tests, figsize=(6 * n_tests, 5), squeeze=False)
    fig.suptitle("Worker Load Balance — Per-Worker Throughput", fontweight="bold", fontsize=14)

    for idx, (test_label, broker_data) in enumerate(test_data.items()):
        ax = axes[0][idx]
        ax.set_title(test_label, fontsize=11)

        for bi, b in enumerate(BROKERS):
            if b not in broker_data:
                continue
            rates = broker_data[b]
            n_workers = len(rates)
            x = np.arange(n_workers) + bi * (n_workers + 1)
            bars = ax.bar(x, rates, color=COLORS[b], alpha=0.85, label=b.upper())
            mean_rate = np.mean(rates)
            ax.axhline(mean_rate, color=COLORS[b], linestyle="--", alpha=0.5, linewidth=1)

            # Annotate stddev / CV
            if len(rates) > 1:
                cv = np.std(rates) / mean_rate * 100 if mean_rate > 0 else 0
                ax.text(
                    np.mean(x), max(rates) * 1.05,
                    f"CV={cv:.1f}%",
                    ha="center", fontsize=8, color=COLORS[b], fontweight="bold",
                )

        ax.set_ylabel("Messages / sec")
        ax.set_xlabel("Worker index")
        ax.legend(fontsize=9)
        ax.grid(axis="y", alpha=0.2)

    _annotate_direction(axes[0][0], "\u2191 Higher + even is better", loc="upper left")

    fig.text(
        0.5, -0.03,
        "Per-worker throughput reveals partition/queue distribution imbalance.\n"
        "CV (Coefficient of Variation) < 10% = good balance. Dashed line = mean rate.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "13_worker_balance.png", bbox_inches="tight")
    plt.close()
    print("  -> 13_worker_balance.png")


# ── Chart 14: Error Rate Breakdown ───────────────────────────────────


def chart_error_breakdown():
    """Grouped bar: error counts across test types and memory stress levels."""
    errors = {}  # {test_label: {broker: error_count}}

    for b in BROKERS:
        # Throughput errors (sum across runs)
        total_tp_err = 0
        for i in range(1, REPS + 1):
            d = load(f"{b}_throughput_run{i}.json")
            if d:
                total_tp_err += d.get("total_errors", 0)
        errors.setdefault("Throughput", {})[b] = total_tp_err

        # Consumer errors
        total_cons_err = 0
        for i in range(1, REPS + 1):
            d = load(f"{b}_consumer_run{i}.json")
            if d:
                total_cons_err += d.get("total_errors", 0)
        errors.setdefault("Consumer", {})[b] = total_cons_err

        # ProdCon errors
        d = load(f"{b}_prodcon.json")
        if d and "producer" in d:
            errors.setdefault("ProdCon", {})[b] = d["producer"].get("total_errors", 0)

        # Memory stress levels
        for mem in ["4g", "2g", "1g", "512m"]:
            d = load(f"{b}_mem_{mem}.json")
            if d:
                errors.setdefault(f"Mem {mem}", {})[b] = d.get("total_errors", 0)

    if not errors:
        return

    test_labels = list(errors.keys())
    x = np.arange(len(test_labels))
    w = 0.3

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle("Error Rate Breakdown by Test Type", fontweight="bold", fontsize=14)

    for i, b in enumerate(BROKERS):
        vals = [errors.get(t, {}).get(b, 0) for t in test_labels]
        offset = (i - (len(BROKERS) - 1) / 2) * w
        bars = ax.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b], alpha=0.85)
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{v:,}", ha="center", va="bottom", fontsize=8, fontweight="bold",
                )

    ax.set_ylabel("Total Errors")
    ax.set_xticks(x)
    ax.set_xticklabels(test_labels, rotation=30, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.2)
    _annotate_direction(ax, "\u2193 Lower is better")

    fig.text(
        0.5, -0.05,
        "Total error counts per benchmark type (summed across all runs/workers).\n"
        "Errors include produce failures, timeouts, and broker rejections under load.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "14_error_breakdown.png", bbox_inches="tight")
    plt.close()
    print("  -> 14_error_breakdown.png")


# ── Chart 15: Throughput Stability ───────────────────────────────────


def chart_throughput_stability():
    """Bar chart with error bars: throughput across 3 repetitions showing stability."""
    data = {}  # {broker: {"producer": [rates], "consumer": [rates]}}

    for b in BROKERS:
        prod_rates, cons_rates = [], []
        for i in range(1, REPS + 1):
            d = load(f"{b}_throughput_run{i}.json")
            if d and "aggregate_rate" in d:
                prod_rates.append(d["aggregate_rate"])
            d = load(f"{b}_consumer_run{i}.json")
            if d and "aggregate_rate" in d:
                cons_rates.append(d["aggregate_rate"])
        if prod_rates or cons_rates:
            data[b] = {"producer": prod_rates, "consumer": cons_rates}

    if not data:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Throughput Stability Across Repetitions", fontweight="bold", fontsize=14)

    # Producer stability
    for bi, b in enumerate(BROKERS):
        if b not in data:
            continue
        rates = data[b]["producer"]
        if not rates:
            continue
        mean_r = np.mean(rates)
        std_r = np.std(rates) if len(rates) > 1 else 0
        cv = (std_r / mean_r * 100) if mean_r > 0 else 0

        ax1.bar(bi, mean_r, color=COLORS[b], alpha=0.85, width=0.5, label=b.upper())
        ax1.errorbar(bi, mean_r, yerr=std_r, color="white", capsize=8, capthick=2, linewidth=2)

        # Individual run dots
        for rate in rates:
            ax1.scatter(bi, rate, color="white", s=30, zorder=5, edgecolors=COLORS[b])

        ax1.text(bi, mean_r + std_r + mean_r * 0.02,
                 f"CV={cv:.1f}%\nσ={std_r:,.0f}",
                 ha="center", fontsize=8, fontweight="bold")

    ax1.set_title("Producer Throughput")
    ax1.set_ylabel("Messages / sec")
    ax1.set_xticks(range(len(BROKERS)))
    ax1.set_xticklabels([b.upper() for b in BROKERS if b in data])
    ax1.legend(fontsize=9)
    ax1.grid(axis="y", alpha=0.2)

    # Consumer stability
    for bi, b in enumerate(BROKERS):
        if b not in data:
            continue
        rates = data[b]["consumer"]
        if not rates:
            continue
        mean_r = np.mean(rates)
        std_r = np.std(rates) if len(rates) > 1 else 0
        cv = (std_r / mean_r * 100) if mean_r > 0 else 0

        ax2.bar(bi, mean_r, color=COLORS[b], alpha=0.85, width=0.5, label=b.upper())
        ax2.errorbar(bi, mean_r, yerr=std_r, color="white", capsize=8, capthick=2, linewidth=2)

        for rate in rates:
            ax2.scatter(bi, rate, color="white", s=30, zorder=5, edgecolors=COLORS[b])

        ax2.text(bi, mean_r + std_r + mean_r * 0.02,
                 f"CV={cv:.1f}%\nσ={std_r:,.0f}",
                 ha="center", fontsize=8, fontweight="bold")

    ax2.set_title("Consumer Throughput")
    ax2.set_ylabel("Messages / sec")
    ax2.set_xticks(range(len(BROKERS)))
    ax2.set_xticklabels([b.upper() for b in BROKERS if b in data])
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.2)

    _annotate_direction(ax1, "\u2191 Higher + stable is better", loc="upper left")
    _annotate_direction(ax2, "\u2191 Higher + stable is better", loc="upper left")

    fig.text(
        0.5, -0.03,
        f"Mean throughput ± stddev across {REPS} repetitions. White dots = individual runs.\n"
        "CV (Coefficient of Variation) < 5% = highly stable. Higher CV = less predictable performance.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "15_throughput_stability.png", bbox_inches="tight")
    plt.close()
    print("  -> 15_throughput_stability.png")


# ── Chart 16: ProdCon Balance Ratio ──────────────────────────────────


def chart_prodcon_balance():
    """Stacked bar: producer vs consumer rate ratio during simultaneous load."""
    data = {}

    for b in BROKERS:
        d = load(f"{b}_prodcon.json")
        if d and "producer" in d and "consumer" in d:
            prod_rate = d["producer"].get("aggregate_rate", 0)
            cons_rate = d["consumer"].get("aggregate_rate", 0)
            if prod_rate > 0 or cons_rate > 0:
                data[b] = {"producer": prod_rate, "consumer": cons_rate}

    if not data:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("Producer / Consumer Balance Ratio", fontweight="bold", fontsize=14)

    brokers = [b for b in BROKERS if b in data]

    # Left: stacked bar showing absolute rates
    for i, b in enumerate(brokers):
        prod = data[b]["producer"]
        cons = data[b]["consumer"]
        ax1.bar(i, prod, color=COLORS[b], alpha=0.85, width=0.5, label="Producer" if i == 0 else None)
        ax1.bar(i, cons, bottom=prod, color=COLORS[b], alpha=0.45, width=0.5,
                label="Consumer" if i == 0 else None, hatch="//")
        ax1.text(i, prod / 2, f"P: {prod:,.0f}", ha="center", va="center", fontsize=8, fontweight="bold")
        ax1.text(i, prod + cons / 2, f"C: {cons:,.0f}", ha="center", va="center", fontsize=8, fontweight="bold")

    ax1.set_xticks(range(len(brokers)))
    ax1.set_xticklabels([b.upper() for b in brokers])
    ax1.set_ylabel("Messages / sec")
    ax1.set_title("Absolute Rates (Stacked)")
    ax1.legend(fontsize=9)
    ax1.grid(axis="y", alpha=0.2)

    # Right: ratio bar (producer/consumer)
    ratios = []
    for b in brokers:
        prod = data[b]["producer"]
        cons = data[b]["consumer"]
        ratios.append(prod / cons if cons > 0 else 0)

    bars = ax2.bar(range(len(brokers)), ratios, color=[COLORS[b] for b in brokers], width=0.5, alpha=0.85)
    ax2.axhline(1.0, color="#888", linestyle="--", linewidth=1, label="Balanced (1:1)")
    for i, (bar, ratio) in enumerate(zip(bars, ratios)):
        ax2.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height(),
            f"{ratio:.2f}x", ha="center", va="bottom", fontsize=10, fontweight="bold",
        )
    ax2.set_xticks(range(len(brokers)))
    ax2.set_xticklabels([b.upper() for b in brokers])
    ax2.set_ylabel("Producer / Consumer Ratio")
    ax2.set_title("Backpressure Indicator")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.2)

    fig.text(
        0.5, -0.03,
        "Left: absolute producer + consumer rates during simultaneous load.\n"
        "Right: ratio > 1 means producer outpaces consumer (backpressure building). Ratio = 1 means balanced.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "16_prodcon_balance.png", bbox_inches="tight")
    plt.close()
    print("  -> 16_prodcon_balance.png")


# ── Chart 17: Network I/O Timeline ──────────────────────────────────


def _parse_net_io(net_str):
    """Parse network I/O string like '1.7kB / 126B' -> (rx_mb, tx_mb)."""
    parts = net_str.split("/")
    if len(parts) != 2:
        return 0.0, 0.0

    def _to_mb(s):
        s = s.strip()
        if "GB" in s:
            return float(s.replace("GB", "").strip()) * 1024
        elif "MB" in s:
            return float(s.replace("MB", "").strip())
        elif "kB" in s:
            return float(s.replace("kB", "").strip()) / 1024
        elif "B" in s:
            return float(s.replace("B", "").strip()) / (1024 * 1024)
        return 0.0

    return _to_mb(parts[0]), _to_mb(parts[1])


def chart_network_io_timeline():
    """Time-series chart: network receive/transmit over time from docker_stats.csv."""
    import csv

    csv_path = RESULTS / "docker_stats.csv"
    if not csv_path.exists():
        return

    container_data = {}
    target_containers = {"bench-kafka", "bench-nats"}

    with open(csv_path) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 6:
                continue
            try:
                ts = int(row[_DOCKER_COLS["timestamp"]])
                container = row[_DOCKER_COLS["container"]]
                if container not in target_containers:
                    continue
                rx_mb, tx_mb = _parse_net_io(row[_DOCKER_COLS["net_io"]])

                if container not in container_data:
                    container_data[container] = []
                container_data[container].append((ts, rx_mb, tx_mb))
            except (ValueError, IndexError):
                continue

    if not container_data:
        return

    all_ts = []
    for points in container_data.values():
        all_ts.extend(p[0] for p in points)
    t0 = min(all_ts) if all_ts else 0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle("Network I/O Over Time (from docker_stats.csv)", fontweight="bold", fontsize=14)

    container_colors = {"bench-kafka": KAFKA_COLOR, "bench-nats": NATS_COLOR}

    for container, points in sorted(container_data.items()):
        points.sort(key=lambda p: p[0])
        color = container_colors.get(container, "#888")
        label = container.replace("bench-", "").upper()

        segments_t, segments_rx, segments_tx = [], [], []
        seg_t, seg_rx, seg_tx = [], [], []

        for ts, rx, tx in points:
            elapsed = (ts - t0) / 60.0
            if seg_t and elapsed - seg_t[-1] > 0.5:
                segments_t.append(seg_t)
                segments_rx.append(seg_rx)
                segments_tx.append(seg_tx)
                seg_t, seg_rx, seg_tx = [], [], []
            seg_t.append(elapsed)
            seg_rx.append(rx)
            seg_tx.append(tx)

        if seg_t:
            segments_t.append(seg_t)
            segments_rx.append(seg_rx)
            segments_tx.append(seg_tx)

        for j, (st, sr, stx) in enumerate(zip(segments_t, segments_rx, segments_tx)):
            lbl = label if j == 0 else None
            ax1.plot(st, sr, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax1.fill_between(st, sr, alpha=0.1, color=color)
            ax2.plot(st, stx, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax2.fill_between(st, stx, alpha=0.1, color=color)

    ax1.set_ylabel("Cumulative Received (MB)")
    ax1.set_title("Network I/O — Receive (RX)")
    ax1.legend(fontsize=10, loc="upper right")
    ax1.grid(axis="both", alpha=0.2)

    ax2.set_ylabel("Cumulative Transmitted (MB)")
    ax2.set_title("Network I/O — Transmit (TX)")
    ax2.set_xlabel("Elapsed Time (minutes)")
    ax2.legend(fontsize=10, loc="upper right")
    ax2.grid(axis="both", alpha=0.2)

    fig.text(
        0.5, -0.03,
        "Cumulative network receive/transmit reported by Docker's network accounting.\n"
        "Shows bandwidth consumption patterns — spikes correlate with benchmark phases.\n"
        "Important for estimating network costs in cloud deployments.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "17_network_io_timeline.png", bbox_inches="tight")
    plt.close()
    print("  -> 17_network_io_timeline.png")


# ── Chart 18: Memory Headroom ────────────────────────────────────────


def chart_memory_headroom():
    """Time-series chart: memory usage percentage over time showing headroom."""
    import csv

    csv_path = RESULTS / "docker_stats.csv"
    if not csv_path.exists():
        return

    container_data = {}
    target_containers = {"bench-kafka", "bench-nats"}

    with open(csv_path) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 5:
                continue
            try:
                ts = int(row[_DOCKER_COLS["timestamp"]])
                container = row[_DOCKER_COLS["container"]]
                if container not in target_containers:
                    continue
                mem_pct = float(row[_DOCKER_COLS["mem_pct"]].replace("%", ""))

                if container not in container_data:
                    container_data[container] = []
                container_data[container].append((ts, mem_pct))
            except (ValueError, IndexError):
                continue

    if not container_data:
        return

    all_ts = []
    for points in container_data.values():
        all_ts.extend(p[0] for p in points)
    t0 = min(all_ts) if all_ts else 0

    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle("Memory Headroom — Usage % Over Time", fontweight="bold", fontsize=14)

    container_colors = {"bench-kafka": KAFKA_COLOR, "bench-nats": NATS_COLOR}

    for container, points in sorted(container_data.items()):
        points.sort(key=lambda p: p[0])
        color = container_colors.get(container, "#888")
        label = container.replace("bench-", "").upper()

        segments_t, segments_pct = [], []
        seg_t, seg_pct = [], []

        for ts, pct in points:
            elapsed = (ts - t0) / 60.0
            if seg_t and elapsed - seg_t[-1] > 0.5:
                segments_t.append(seg_t)
                segments_pct.append(seg_pct)
                seg_t, seg_pct = [], []
            seg_t.append(elapsed)
            seg_pct.append(pct)

        if seg_t:
            segments_t.append(seg_t)
            segments_pct.append(seg_pct)

        peak_pct = max(p[1] for p in points)
        for j, (st, sp) in enumerate(zip(segments_t, segments_pct)):
            lbl = f"{label} (peak {peak_pct:.1f}%)" if j == 0 else None
            ax.plot(st, sp, color=color, label=lbl, linewidth=1.0, alpha=0.8)
            ax.fill_between(st, sp, alpha=0.1, color=color)

    # Danger zones
    ax.axhline(80, color="#FF6600", linestyle="--", linewidth=1, alpha=0.6, label="Warning (80%)")
    ax.axhline(95, color="#FF0000", linestyle="--", linewidth=1, alpha=0.6, label="Critical (95%)")

    ax.set_ylabel("Memory Usage %")
    ax.set_xlabel("Elapsed Time (minutes)")
    ax.set_ylim(0, 105)
    ax.legend(fontsize=10, loc="upper right")
    ax.grid(axis="both", alpha=0.2)
    _annotate_direction(ax, "\u2193 Lower is better", loc="upper left")

    fig.text(
        0.5, -0.03,
        "Shows how close each broker gets to its container memory limit during the full benchmark.\n"
        "Crossing 80% = danger zone. Crossing 95% = likely OOM kill imminent.\n"
        "Lower peak = more headroom for traffic spikes in production.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "18_memory_headroom.png", bbox_inches="tight")
    plt.close()
    print("  -> 18_memory_headroom.png")


# ── Chart 19: Scaling Efficiency ─────────────────────────────────────


def chart_scaling_efficiency():
    """Line chart: throughput-per-CPU-core at each CPU limit, showing diminishing returns."""
    data = {}
    for b in BROKERS:
        d = load(f"{b}_scaling.json")
        if d and isinstance(d, list):
            data[b] = d

    if not data:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Scaling Efficiency — Throughput per CPU Core", fontweight="bold", fontsize=14)

    # Left: throughput per core at each CPU level
    for b in BROKERS:
        if b not in data:
            continue
        entries = [e for e in data[b] if e.get("status") == "PASS"]
        if not entries:
            continue

        cpus = [e["cpu_limit"] for e in entries]
        tp_per_core = [e["throughput"] / e["cpu_limit"] for e in entries]

        ax1.plot(cpus, tp_per_core, color=COLORS[b], marker="o", linewidth=2.5,
                 label=b.upper(), markersize=9)
        for xi, yi in zip(cpus, tp_per_core):
            ax1.annotate(
                f"{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(0, 10),
                ha="center", fontsize=8, color=COLORS[b], fontweight="bold",
            )

    ax1.set_xlabel("CPU Limit (cores)")
    ax1.set_ylabel("Messages / sec / core")
    ax1.set_title("Per-Core Efficiency")
    ax1.legend(fontsize=10)
    ax1.grid(axis="both", alpha=0.2)
    ax1.invert_xaxis()
    _annotate_direction(ax1, "\u2191 Higher is better")

    # Right: efficiency ratio (normalized to highest CPU)
    for b in BROKERS:
        if b not in data:
            continue
        entries = sorted(
            [e for e in data[b] if e.get("status") == "PASS"],
            key=lambda e: -e["cpu_limit"],
        )
        if not entries:
            continue

        base_efficiency = entries[0]["throughput"] / entries[0]["cpu_limit"]
        cpus = [e["cpu_limit"] for e in entries]
        efficiency_pct = [
            (e["throughput"] / e["cpu_limit"]) / base_efficiency * 100
            for e in entries
        ]

        ax2.plot(cpus, efficiency_pct, color=COLORS[b], marker="s", linewidth=2.5,
                 label=b.upper(), markersize=9)
        for xi, yi in zip(cpus, efficiency_pct):
            ax2.annotate(
                f"{yi:.0f}%", (xi, yi), textcoords="offset points", xytext=(0, 10),
                ha="center", fontsize=8, color=COLORS[b], fontweight="bold",
            )

    ax2.axhline(100, color="#888", linestyle="--", linewidth=1, alpha=0.5, label="Baseline (max CPU)")
    ax2.set_xlabel("CPU Limit (cores)")
    ax2.set_ylabel("Efficiency % (vs max CPU)")
    ax2.set_title("Efficiency Degradation")
    ax2.legend(fontsize=10)
    ax2.grid(axis="both", alpha=0.2)
    ax2.invert_xaxis()

    fig.text(
        0.5, -0.03,
        "Left: absolute throughput-per-core at each CPU limit — the 'knee' shows diminishing returns.\n"
        "Right: efficiency normalized to 100% at maximum CPU. Drop below 100% = overhead from resource contention.\n"
        "Flatter curve = better scaling. Sharp drop = broker struggles with limited resources.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "19_scaling_efficiency.png", bbox_inches="tight")
    plt.close()
    print("  -> 19_scaling_efficiency.png")


# ── Chart 20: Latency Load Context ──────────────────────────────────


def chart_latency_context():
    """Table chart: latency measurement context — load%, target rate, samples."""
    data = {}
    for b in BROKERS:
        d = load(f"{b}_latency.json")
        if d:
            data[b] = d

    if not data:
        return

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.suptitle("Latency Measurement Context", fontweight="bold", fontsize=14)

    # Build table data
    columns = ["Metric"] + [b.upper() for b in BROKERS if b in data]
    rows = []
    metrics = [
        ("Load %", "load_pct", "{:.0f}%"),
        ("Target Rate (msg/s)", "target_rate", "{:,.0f}"),
        ("Samples Collected", "samples", "{:,.0f}"),
        ("Messages Sent", "sent", "{:,.0f}"),
        ("p50 Latency (µs)", "p50_us", "{:,.0f}"),
        ("p95 Latency (µs)", "p95_us", "{:,.0f}"),
        ("p99 Latency (µs)", "p99_us", "{:,.0f}"),
        ("p99.9 Latency (µs)", "p999_us", "{:,.0f}"),
        ("Max Latency (µs)", "max_us", "{:,.0f}"),
    ]

    for label, key, fmt in metrics:
        row = [label]
        for b in BROKERS:
            if b in data and key in data[b]:
                val = data[b][key]
                row.append(fmt.format(val))
            else:
                row.append("—")
        rows.append(row)

    ax.axis("off")
    table = ax.table(
        cellText=rows,
        colLabels=columns,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)

    # Style the table
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#555")
        if key[0] == 0:  # Header row
            cell.set_facecolor("#444")
            cell.set_text_props(fontweight="bold", color="#ccc")
        else:
            cell.set_facecolor("#2d2d2d")
            cell.set_text_props(color="#ccc")

    # Color broker columns
    broker_cols = {i + 1: b for i, b in enumerate(BROKERS) if b in data}
    for (row_idx, col_idx), cell in table.get_celld().items():
        if col_idx in broker_cols and row_idx > 0:
            broker = broker_cols[col_idx]
            cell.set_text_props(color=COLORS[broker])

    fig.text(
        0.5, 0.02,
        "Full context for the latency test — at what load % and target rate was the measurement taken.\n"
        "This context is critical for interpreting the latency numbers in Chart 04.",
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.tight_layout()
    fig.savefig(CHARTS / "20_latency_context.png", bbox_inches="tight")
    plt.close()
    print("  -> 20_latency_context.png")


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
            for i in range(1, REPS + 1):
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

    # Annotation: explain Python client throughput measures client library, not broker
    ax.annotate(
        "Note: Measures Python client library throughput, not raw broker capacity.\n"
        "Kafka's librdkafka buffers async; nats-py awaits each ack. See CLI chart for broker comparison.",
        xy=(0.5, 0.01),
        xycoords="figure fraction",
        ha="center",
        fontsize=7.5,
        fontstyle="italic",
        color="#888888",
    )

    plt.tight_layout(rect=[0, 0.06, 1, 1])
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
    _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "CLI producer throughput (kcat / nats bench) across hardware profiles. Reflects raw broker capacity.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

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


def compare_consumer_throughput(scenarios, out_dir):
    """Grouped bar: consumer throughput (Python + CLI) across scenarios."""
    py_data = {}
    cli_data = {}
    for sc in scenarios:
        py_data[sc] = {}
        cli_data[sc] = {}
        for b in BROKERS:
            # Python client
            rates = []
            for i in range(1, REPS + 1):
                d = _load_scenario(sc, f"{b}_consumer_run{i}.json")
                if d and "aggregate_rate" in d:
                    rates.append(d["aggregate_rate"])
            if rates:
                py_data[sc][b] = sorted(rates)[len(rates) // 2]
            # CLI
            d = _load_scenario(sc, f"{b}_cli_consumer.json")
            if d and "msgs_per_sec" in d:
                cli_data[sc][b] = d["msgs_per_sec"]

    if not any(py_data[sc] or cli_data[sc] for sc in scenarios):
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5))
    fig.suptitle(
        "Consumer Throughput \u2014 Cross-Scenario Comparison (Python + CLI)",
        fontweight="bold",
    )

    # Left: Python Client
    x = np.arange(len(scenarios))
    w = 0.3
    for i, b in enumerate(BROKERS):
        vals = [py_data[sc].get(b, 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax1.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )
    ax1.set_ylabel("Messages / sec")
    ax1.set_title("Python Client")
    ax1.set_xticks(x)
    ax1.set_xticklabels([s.upper() for s in scenarios])
    ax1.set_xlabel("Hardware Scenario")
    ax1.legend(fontsize=9)
    _annotate_direction(ax1, "\u2191 Higher is better")

    # Right: CLI
    for i, b in enumerate(BROKERS):
        vals = [cli_data[sc].get(b, 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax2.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax2.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )
    ax2.set_ylabel("Messages / sec")
    ax2.set_title("CLI (kcat / nats bench)")
    ax2.set_xticks(x)
    ax2.set_xticklabels([s.upper() for s in scenarios])
    ax2.set_xlabel("Hardware Scenario")
    ax2.legend(fontsize=9)
    _annotate_direction(ax2, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.02,
        "Consumer throughput comparison across hardware profiles. Left = Python client library, Right = native CLI tools.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_07_consumer.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_07_consumer.png")


def compare_prodcon(scenarios, out_dir):
    """Grouped bar: prodcon rates (Python + CLI) across scenarios."""
    py_data = {}
    cli_data = {}
    for sc in scenarios:
        py_data[sc] = {}
        cli_data[sc] = {}
        for b in BROKERS:
            d = _load_scenario(sc, f"{b}_prodcon.json")
            if d and "producer" in d and "consumer" in d:
                py_data[sc][b] = d
            d = _load_scenario(sc, f"{b}_cli_prodcon.json")
            if d:
                cli_data[sc][b] = d

    if not any(py_data[sc] or cli_data[sc] for sc in scenarios):
        return

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(
        "Simultaneous ProdCon \u2014 Cross-Scenario (Python + CLI)",
        fontweight="bold",
        fontsize=14,
    )

    titles = [
        ("Python Client \u2014 Producer Rate", "producer", "aggregate_rate", py_data),
        ("Python Client \u2014 Consumer Rate", "consumer", "aggregate_rate", py_data),
        ("CLI \u2014 Producer Rate", None, "producer_msgs_per_sec", cli_data),
        ("CLI \u2014 Consumer Rate", None, "consumer_msgs_per_sec", cli_data),
    ]

    for ax, (title, metric_key, rate_key, src) in zip(axes.flat, titles):
        x = np.arange(len(scenarios))
        w = 0.3
        for i, b in enumerate(BROKERS):
            if metric_key:
                vals = [
                    src[sc].get(b, {}).get(metric_key, {}).get(rate_key, 0)
                    for sc in scenarios
                ]
            else:
                vals = [src[sc].get(b, {}).get(rate_key, 0) for sc in scenarios]
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
                        fontsize=8,
                        fontweight="bold",
                    )
        ax.set_ylabel("Messages / sec")
        ax.set_title(title, fontsize=11)
        ax.set_xticks(x)
        ax.set_xticklabels([s.upper() for s in scenarios])
        ax.legend(fontsize=9)
        _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.01,
        "Simultaneous producer+consumer load across hardware profiles. Top = Python client, Bottom = CLI tools.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_08_prodcon.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_08_prodcon.png")


def compare_resource_scaling(scenarios, out_dir):
    """Scaling slopes per scenario — one subplot per broker."""
    data = {}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            d = _load_scenario(sc, f"{b}_scaling.json")
            if d and isinstance(d, list):
                data[sc][b] = d

    if not any(data[sc] for sc in scenarios):
        return

    fig, axes = plt.subplots(1, len(BROKERS), figsize=(14, 5), sharey=True)
    fig.suptitle(
        "Resource Scaling Slope — Cross-Scenario Comparison", fontweight="bold"
    )

    for ax, b in zip(axes, BROKERS):
        ax.set_title(b.upper(), fontweight="bold")
        for sc in scenarios:
            entries = data[sc].get(b, [])
            pass_entries = [e for e in entries if e.get("status") == "PASS"]
            if pass_entries:
                cpus = [e["cpu_limit"] for e in pass_entries]
                tps = [e["throughput"] for e in pass_entries]
                ax.plot(
                    cpus,
                    tps,
                    marker="o",
                    linewidth=2,
                    label=sc.upper(),
                    color=SCENARIO_COLORS.get(sc, "#888"),
                    markersize=6,
                )
            fail_entries = [e for e in entries if e.get("status") != "PASS"]
            if fail_entries:
                fail_cpus = [e["cpu_limit"] for e in fail_entries]
                ax.scatter(
                    fail_cpus,
                    [0] * len(fail_cpus),
                    color=SCENARIO_COLORS.get(sc, "#888"),
                    marker="x",
                    s=80,
                    linewidths=2,
                    zorder=10,
                )
        ax.invert_xaxis()
        ax.set_xlabel("CPU Limit (cores)")
        ax.set_ylabel("Throughput (msg/s)" if b == BROKERS[0] else "")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)
        _annotate_direction(ax, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.03,
        "How throughput degrades as CPU allocation is reduced (left\u2192right = fewer CPUs).\n"
        "Each line = one hardware scenario. The slope reveals how sensitive each broker is to CPU starvation.\n"
        "Flat lines = broker is not CPU-bound at these levels. Steep drops = broker hits a CPU bottleneck.\n"
        "Red X = broker failed to start or crashed (OOM / timeout) at that CPU level.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_09_resource_scaling.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_09_resource_scaling.png")


def _load_scenario_csv(scenario_name):
    """Load docker_stats.csv from a specific scenario's results dir."""
    import csv

    p = _project_root / "results" / scenario_name / "docker_stats.csv"
    if not p.exists():
        return {}

    container_data = {}
    target_containers = {"bench-kafka", "bench-nats"}

    with open(p) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 7:
                continue
            try:
                ts = int(row[_DOCKER_COLS["timestamp"]])
                container = row[_DOCKER_COLS["container"]]
                if container not in target_containers:
                    continue
                cpu = float(row[_DOCKER_COLS["cpu_pct"]].replace("%", ""))
                mem_mb = _parse_mem_usage(row[_DOCKER_COLS["mem_usage"]])
                read_mb, write_mb = _parse_block_io(row[_DOCKER_COLS["block_io"]])

                if container not in container_data:
                    container_data[container] = []
                container_data[container].append((ts, cpu, mem_mb, read_mb, write_mb))
            except (ValueError, IndexError):
                continue

    return container_data


def compare_resource_timeline(scenarios, out_dir):
    """Cross-scenario comparison: CPU%, RAM, disk I/O over time — one row per scenario."""
    all_data = {}
    for sc in scenarios:
        all_data[sc] = _load_scenario_csv(sc)

    if not any(all_data[sc] for sc in scenarios):
        return

    n_scenarios = len(scenarios)
    fig, axes = plt.subplots(
        n_scenarios, 3, figsize=(20, 5 * n_scenarios), squeeze=False
    )
    fig.suptitle(
        "Resource Usage Over Time — Cross-Scenario Comparison",
        fontweight="bold",
        fontsize=16,
        y=1.01,
    )

    container_colors = {"bench-kafka": KAFKA_COLOR, "bench-nats": NATS_COLOR}
    col_titles = ["CPU %", "Memory (MiB)", "Disk Write (MB)"]

    for row_idx, sc in enumerate(scenarios):
        cdata = all_data[sc]
        if not cdata:
            for col_idx in range(3):
                axes[row_idx][col_idx].text(
                    0.5,
                    0.5,
                    "No data",
                    ha="center",
                    va="center",
                    fontsize=12,
                    color="#888",
                )
            continue

        all_ts = []
        for points in cdata.values():
            all_ts.extend(p[0] for p in points)
        t0 = min(all_ts) if all_ts else 0

        for container, points in sorted(cdata.items()):
            points.sort(key=lambda p: p[0])
            color = container_colors.get(container, "#888")
            label = container.replace("bench-", "").upper()

            # Break at gaps > 30s
            segments = []
            seg = []
            for pt in points:
                elapsed = (pt[0] - t0) / 60.0
                if seg and elapsed - seg[-1][0] > 0.5:
                    segments.append(seg)
                    seg = []
                seg.append((elapsed, pt[1], pt[2], pt[3], pt[4]))
            if seg:
                segments.append(seg)

            for j, seg in enumerate(segments):
                lbl = label if j == 0 else None
                t = [s[0] for s in seg]
                # CPU
                axes[row_idx][0].plot(
                    t,
                    [s[1] for s in seg],
                    color=color,
                    label=lbl,
                    linewidth=1.0,
                    alpha=0.8,
                )
                axes[row_idx][0].fill_between(
                    t, [s[1] for s in seg], alpha=0.08, color=color
                )
                # Memory
                axes[row_idx][1].plot(
                    t,
                    [s[2] for s in seg],
                    color=color,
                    label=lbl,
                    linewidth=1.0,
                    alpha=0.8,
                )
                axes[row_idx][1].fill_between(
                    t, [s[2] for s in seg], alpha=0.08, color=color
                )
                # Disk Write
                axes[row_idx][2].plot(
                    t,
                    [s[4] for s in seg],
                    color=color,
                    label=lbl,
                    linewidth=1.0,
                    alpha=0.8,
                )
                axes[row_idx][2].fill_between(
                    t, [s[4] for s in seg], alpha=0.08, color=color
                )

        for col_idx in range(3):
            ax = axes[row_idx][col_idx]
            if row_idx == 0:
                ax.set_title(col_titles[col_idx], fontsize=12, fontweight="bold")
            ax.set_ylabel(f"{sc.upper()}", fontsize=10, fontweight="bold")
            ax.legend(fontsize=8, loc="upper right")
            ax.grid(axis="both", alpha=0.2)
            if row_idx == n_scenarios - 1:
                ax.set_xlabel("Elapsed Time (minutes)")

    fig.text(
        0.5,
        -0.02,
        "Side-by-side resource consumption across hardware scenarios.\n"
        "Each row = one scenario (different CPU/RAM allocations). Columns = CPU, Memory, Disk write.\n"
        "Compare how each broker's resource appetite changes with available hardware.",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_10_resource_timeline.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_10_resource_timeline.png")


def compare_throughput_vs_resources(scenarios, out_dir):
    """Cross-scenario: throughput efficiency (msgs/s per CPU core and per GB RAM)."""
    import csv

    data = {}  # {scenario: {broker: {throughput, peak_cpu, peak_mem, ...}}}
    for sc in scenarios:
        data[sc] = {}
        for b in BROKERS:
            rates = []
            for i in range(1, REPS + 1):
                d = _load_scenario(sc, f"{b}_throughput_run{i}.json")
                if d and "aggregate_rate" in d:
                    rates.append(d["aggregate_rate"])
            if not rates:
                continue
            median_tp = sorted(rates)[len(rates) // 2]

            # Try scaling data first
            peak_cpu, peak_mem = None, None
            scaling = _load_scenario(sc, f"{b}_scaling.json")
            if scaling and isinstance(scaling, list):
                for entry in scaling:
                    if entry.get("status") == "PASS":
                        peak_cpu = entry.get("peak_cpu_pct")
                        peak_mem = entry.get("peak_mem_mb")
                        break

            # Fallback to docker_stats.csv
            if peak_cpu is None or peak_mem is None:
                csv_path = _project_root / "results" / sc / "docker_stats.csv"
                if csv_path.exists():
                    container_name = f"bench-{b}"
                    max_cpu, max_mem = 0, 0
                    with open(csv_path) as f:
                        reader = csv.reader(f)
                        next(reader, None)
                        for row in reader:
                            if len(row) < 5 or row[_DOCKER_COLS["container"]] != container_name:
                                continue
                            try:
                                cpu_val = float(row[_DOCKER_COLS["cpu_pct"]].replace("%", ""))
                                mem_val = _parse_mem_usage(row[_DOCKER_COLS["mem_usage"]])
                                max_cpu = max(max_cpu, cpu_val)
                                max_mem = max(max_mem, mem_val)
                            except (ValueError, IndexError):
                                continue
                    if max_cpu > 0:
                        peak_cpu = max_cpu
                    if max_mem > 0:
                        peak_mem = max_mem

            if peak_cpu and peak_mem and peak_cpu > 0 and peak_mem > 0:
                data[sc][b] = {
                    "throughput": median_tp,
                    "peak_cpu": peak_cpu,
                    "peak_mem": peak_mem,
                    "tp_per_cpu": median_tp / (peak_cpu / 100),
                    "tp_per_gb": median_tp / (peak_mem / 1024),
                }

    if not any(data[sc] for sc in scenarios):
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle(
        "Resource Efficiency — Cross-Scenario Comparison",
        fontweight="bold",
        fontsize=14,
    )

    x = np.arange(len(scenarios))
    w = 0.3

    # Left: Throughput per CPU core
    for i, b in enumerate(BROKERS):
        vals = [data[sc].get(b, {}).get("tp_per_cpu", 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax1.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )

    ax1.set_ylabel("Messages / sec / CPU core")
    ax1.set_title("Throughput per CPU Core")
    ax1.set_xticks(x)
    ax1.set_xticklabels([s.upper() for s in scenarios])
    ax1.set_xlabel("Hardware Scenario")
    ax1.legend()
    _annotate_direction(ax1, "\u2191 Higher is better")

    # Right: Throughput per GB RAM
    for i, b in enumerate(BROKERS):
        vals = [data[sc].get(b, {}).get("tp_per_gb", 0) for sc in scenarios]
        offset = (i - 0.5) * w
        bars = ax2.bar(x + offset, vals, w, label=b.upper(), color=COLORS[b])
        for bar, v in zip(bars, vals):
            if v > 0:
                ax2.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{v:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )

    ax2.set_ylabel("Messages / sec / GB RAM")
    ax2.set_title("Throughput per GB RAM")
    ax2.set_xticks(x)
    ax2.set_xticklabels([s.upper() for s in scenarios])
    ax2.set_xlabel("Hardware Scenario")
    ax2.legend()
    _annotate_direction(ax2, "\u2191 Higher is better")

    fig.text(
        0.5,
        -0.03,
        "Resource efficiency = throughput normalized by peak resource consumption.\n"
        "Higher = broker squeezes more performance from the same hardware.\n"
        "Compares how efficiency changes across hardware profiles (more resources doesn't always mean proportional gains).",
        ha="center",
        fontsize=8,
        color="#888",
        style="italic",
    )

    plt.tight_layout()
    fig.savefig(out_dir / "cmp_11_throughput_vs_resources.png", bbox_inches="tight")
    plt.close()
    print("  -> cmp_11_throughput_vs_resources.png")


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
    compare_consumer_throughput(scenarios, out_dir)
    compare_prodcon(scenarios, out_dir)
    compare_resource_scaling(scenarios, out_dir)
    compare_resource_timeline(scenarios, out_dir)
    compare_throughput_vs_resources(scenarios, out_dir)
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
    chart_cli_throughput()
    chart_latency()
    chart_memory_stress()
    chart_scorecard()
    chart_consumer_throughput()
    chart_prodcon()
    chart_resource_timeline()
    chart_resource_scaling()
    chart_disk_io_timeline()
    chart_throughput_vs_resources()
    chart_worker_balance()
    chart_error_breakdown()
    chart_throughput_stability()
    chart_prodcon_balance()
    chart_network_io_timeline()
    chart_memory_headroom()
    chart_scaling_efficiency()
    chart_latency_context()

    print(f"\nAll charts saved to {CHARTS}/")


if __name__ == "__main__":
    main()
