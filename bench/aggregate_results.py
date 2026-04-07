#!/usr/bin/env python3
"""aggregate_results.py — AC-8/AC-9: Merge all results into OMB-compatible JSON + decision."""

import json
import os
import statistics
import sys
from pathlib import Path

from dotenv import dotenv_values

_project_root = Path(__file__).resolve().parent.parent
_kafka_env = dotenv_values(_project_root / "kafka-client.env")
_nats_env = dotenv_values(_project_root / "nats-client.env")

RESULTS_DIR = str(_project_root / "results")


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Warning: could not load {path}: {e}", file=sys.stderr)
        return None


def get_median_throughput(broker):
    """Get median aggregate_rate across throughput runs."""
    rates = []
    for i in range(1, 4):
        path = os.path.join(RESULTS_DIR, f"{broker}_throughput_run{i}.json")
        data = load_json(path)
        if data and "aggregate_rate" in data:
            rates.append(data["aggregate_rate"])
    return statistics.median(rates) if rates else None


def get_min_viable_ram(broker):
    """Find lowest memory level where broker passed."""
    levels = ["512m", "1g", "2g", "4g"]
    min_viable = None
    for mem in levels:
        path = os.path.join(RESULTS_DIR, f"{broker}_mem_{mem}.json")
        data = load_json(path)
        if data and data.get("status") == "PASS":
            min_viable = mem
            break
    return min_viable


def build_report():
    report = {
        "metadata": {
            "benchmark": "kafka-vs-nats-jetstream",
            "hardware": {
                "cpus": _kafka_env["BENCH_CPUS"],
                "memory": _kafka_env["BENCH_MEMORY"],
                "disk_type": _kafka_env["BENCH_DISK_TYPE"],
            },
            "payload_bytes": int(_kafka_env["PAYLOAD_BYTES"]),
            "num_producers": int(_kafka_env["NUM_PRODUCERS"]),
        },
        "idle_footprint": {},
        "startup_recovery": {},
        "throughput": {},
        "latency": {},
        "memory_stress": {},
        "decision": {},
    }

    # --- Idle footprint (AC-3) ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_idle_stats.json"))
        if data:
            report["idle_footprint"][broker] = data

    # --- Startup / Recovery (AC-4) ---
    for broker in ["kafka", "nats"]:
        path = os.path.join(RESULTS_DIR, f"{broker}_startup.json")
        if os.path.exists(path):
            entries = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            report["startup_recovery"][broker] = entries

    # --- Throughput (AC-5) ---
    for broker in ["kafka", "nats"]:
        runs = []
        for i in range(1, 4):
            data = load_json(
                os.path.join(RESULTS_DIR, f"{broker}_throughput_run{i}.json")
            )
            if data:
                runs.append(data)
        median_rate = get_median_throughput(broker)
        report["throughput"][broker] = {
            "runs": runs,
            "median_aggregate_rate": median_rate,
        }

    # --- Latency (AC-6) ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_latency.json"))
        if data:
            report["latency"][broker] = data

    # --- Memory stress (AC-7) ---
    for broker in ["kafka", "nats"]:
        levels = {}
        for mem in ["4g", "2g", "1g", "512m"]:
            data = load_json(os.path.join(RESULTS_DIR, f"{broker}_mem_{mem}.json"))
            if data:
                levels[mem] = data
        min_ram = get_min_viable_ram(broker)
        report["memory_stress"][broker] = {
            "levels": levels,
            "min_viable_ram": min_ram,
        }

    # --- Decision (AC-9) ---
    kafka_min = get_min_viable_ram("kafka")
    nats_min = get_min_viable_ram("nats")
    kafka_median = get_median_throughput("kafka")
    nats_median = get_median_throughput("nats")

    kafka_lat = load_json(os.path.join(RESULTS_DIR, "kafka_latency.json"))
    nats_lat = load_json(os.path.join(RESULTS_DIR, "nats_latency.json"))

    recommendation = "INCONCLUSIVE"
    reasoning = []

    # Memory thresholds
    ram_order = {"512m": 512, "1g": 1024, "2g": 2048, "4g": 4096}

    if kafka_min and nats_min:
        kafka_mb = ram_order.get(kafka_min, 9999)
        nats_mb = ram_order.get(nats_min, 9999)
        reasoning.append(
            f"Kafka min viable RAM: {kafka_min}, NATS min viable RAM: {nats_min}"
        )

        if kafka_mb <= 2048 and kafka_lat and nats_lat:
            kafka_p99 = kafka_lat.get("p99_us", 0)
            nats_p99 = nats_lat.get("p99_us", 0)
            if nats_p99 > 0:
                lat_ratio = kafka_p99 / nats_p99
                reasoning.append(
                    f"Kafka p99={kafka_p99}us, NATS p99={nats_p99}us (ratio={lat_ratio:.2f})"
                )
                if lat_ratio < 1.2:
                    recommendation = "KEEP_KAFKA"
                    reasoning.append(
                        "Kafka meets <=2GB threshold and latency delta <20% vs NATS"
                    )
            else:
                recommendation = "KEEP_KAFKA"
                reasoning.append(
                    "Kafka meets <=2GB threshold; NATS latency data unavailable"
                )

        if nats_mb <= 512 and kafka_mb > 2048:
            recommendation = "MIGRATE_TO_NATS"
            reasoning.append("NATS runs at <=512MB while Kafka fails below 2GB")

    if kafka_median and nats_median:
        reasoning.append(
            f"Throughput — Kafka median: {kafka_median} msg/s, NATS median: {nats_median} msg/s"
        )

    report["decision"] = {
        "recommendation": recommendation,
        "kafka_min_viable_ram": kafka_min,
        "nats_min_viable_ram": nats_min,
        "reasoning": reasoning,
    }

    return report


def print_summary(report):
    """Print a human-readable summary table."""
    d = report["decision"]
    print("\n" + "=" * 60)
    print("  BENCHMARK DECISION SUMMARY")
    print("=" * 60)
    print(f"  Recommendation:       {d.get('recommendation', 'N/A')}")
    print(f"  Kafka min viable RAM: {d.get('kafka_min_viable_ram', 'N/A')}")
    print(f"  NATS min viable RAM:  {d.get('nats_min_viable_ram', 'N/A')}")
    print()
    for line in d.get("reasoning", []):
        print(f"  - {line}")
    print("=" * 60)


def main():
    report = build_report()

    out_path = os.path.join(RESULTS_DIR, "full_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report written to {out_path}")

    print_summary(report)


if __name__ == "__main__":
    main()
