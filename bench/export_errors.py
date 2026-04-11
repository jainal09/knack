#!/usr/bin/env python3
"""Export benchmark metric errors and runtime log errors from results/."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOG_ERROR_RE = re.compile(r"(^|\s)(ERROR|Traceback|FATAL|Exception|✘)", re.IGNORECASE)
LOG_EXCLUDE_RE = re.compile(
    r'("errors"|"total_errors"|_errors|error_rate|errors_per|\(0 errors\)|0 errors\)|Pre-populated|WARNING:)',
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Export benchmark metric errors and runtime log errors."
    )
    parser.add_argument(
        "--results-dir",
        default=str(project_root / "results"),
        help="Results directory to scan (default: ./results)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        nargs="+",
        metavar="NAME",
        help="Specific scenario(s) to scan, e.g. --scenario large medium",
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Export format (default: json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path. Use '-' for stdout. Default: results/errors_export_<timestamp>.<ext>",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--logs-only",
        action="store_true",
        help="Export runtime log errors only",
    )
    mode.add_argument(
        "--metrics-only",
        action="store_true",
        help="Export benchmark metric errors only",
    )
    return parser.parse_args()


def flatten_scenarios(values: list[list[str]] | None) -> set[str]:
    if not values:
        return set()
    return {item for group in values for item in group}


def default_output_path(results_dir: Path, fmt: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return results_dir / f"errors_export_{timestamp}.{fmt}"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def load_json(path: Path) -> tuple[dict | list | None, str | None]:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"


def worker_error_rows(per_worker: list[dict] | None) -> list[dict]:
    rows = []
    for item in per_worker or []:
        errors = item.get("errors", 0)
        if isinstance(errors, (int, float)) and errors > 0:
            rows.append({"worker": item.get("worker"), "errors": errors})
    return rows


def collect_metric_errors(results_dir: Path, scenario: str) -> list[dict]:
    scenario_dir = results_dir / scenario
    records: list[dict] = []

    def add_record(
        *,
        category: str,
        broker: str,
        artifact: Path,
        total_errors: int | None = None,
        status: str | None = None,
        error: str | None = None,
        memory: str | None = None,
        per_worker_errors: list[dict] | None = None,
    ) -> None:
        records.append(
            {
                "scenario": scenario,
                "category": category,
                "broker": broker,
                "artifact": artifact.name,
                "path": str(artifact.resolve()),
                "total_errors": total_errors,
                "status": status,
                "error": error,
                "memory": memory,
                "per_worker_errors": per_worker_errors or [],
            }
        )

    for broker in ("kafka", "nats"):
        for path in sorted(scenario_dir.glob(f"{broker}_throughput_run*.json")):
            data, parse_error = load_json(path)
            if parse_error:
                add_record(
                    category="artifact_parse_error",
                    broker=broker,
                    artifact=path,
                    error=parse_error,
                )
                continue
            total_errors = int((data or {}).get("total_errors", 0))
            if total_errors > 0:
                add_record(
                    category="throughput",
                    broker=broker,
                    artifact=path,
                    total_errors=total_errors,
                    per_worker_errors=worker_error_rows((data or {}).get("per_worker")),
                )

        for path in sorted(scenario_dir.glob(f"{broker}_consumer_run*.json")):
            data, parse_error = load_json(path)
            if parse_error:
                add_record(
                    category="artifact_parse_error",
                    broker=broker,
                    artifact=path,
                    error=parse_error,
                )
                continue
            total_errors = int((data or {}).get("total_errors", 0))
            if total_errors > 0:
                add_record(
                    category="consumer",
                    broker=broker,
                    artifact=path,
                    total_errors=total_errors,
                    per_worker_errors=worker_error_rows((data or {}).get("per_worker")),
                )

        prodcon_path = scenario_dir / f"{broker}_prodcon.json"
        data, parse_error = load_json(prodcon_path)
        if parse_error:
            add_record(
                category="artifact_parse_error",
                broker=broker,
                artifact=prodcon_path,
                error=parse_error,
            )
        elif isinstance(data, dict):
            producer = data.get("producer", {})
            total_errors = int(producer.get("total_errors", 0))
            if total_errors > 0:
                add_record(
                    category="prodcon",
                    broker=broker,
                    artifact=prodcon_path,
                    total_errors=total_errors,
                    per_worker_errors=worker_error_rows(producer.get("per_worker")),
                )

        for path in sorted(scenario_dir.glob(f"{broker}_mem_*.json")):
            data, parse_error = load_json(path)
            if parse_error:
                add_record(
                    category="artifact_parse_error",
                    broker=broker,
                    artifact=path,
                    error=parse_error,
                )
                continue
            data = data or {}
            total_errors = int(data.get("total_errors", 0)) if isinstance(data, dict) else 0
            status = data.get("status") if isinstance(data, dict) else None
            error = data.get("error") if isinstance(data, dict) else None
            memory = data.get("memory") if isinstance(data, dict) else None
            if total_errors > 0 or (status and status != "PASS") or error:
                add_record(
                    category="memory_stress",
                    broker=broker,
                    artifact=path,
                    total_errors=total_errors,
                    status=status,
                    error=error,
                    memory=memory,
                    per_worker_errors=worker_error_rows((data or {}).get("per_worker")),
                )

    return records


def collect_log_errors(log_path: Path, scope: str, scenario: str | None = None) -> list[dict]:
    records: list[dict] = []
    if not log_path.exists():
        return records

    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            line = strip_ansi(raw_line.rstrip("\n"))
            if not LOG_ERROR_RE.search(line):
                continue
            if LOG_EXCLUDE_RE.search(line):
                continue
            records.append(
                {
                    "scope": scope,
                    "scenario": scenario,
                    "log_file": str(log_path.resolve()),
                    "line_no": line_no,
                    "message": line,
                }
            )
    return records


def detect_scenarios(results_dir: Path, requested: set[str]) -> list[str]:
    if requested:
        return sorted(name for name in requested if (results_dir / name).is_dir())

    scenarios = []
    for child in sorted(results_dir.iterdir()):
        if not child.is_dir():
            continue
        if any(child.glob("*.json")) or any(child.glob("benchmark_*.log")):
            scenarios.append(child.name)
    return scenarios


def build_export(results_dir: Path, requested_scenarios: set[str], include_metrics: bool, include_logs: bool) -> dict:
    scenarios = detect_scenarios(results_dir, requested_scenarios)
    metric_errors: list[dict] = []
    log_errors: list[dict] = []
    scenario_logs_scanned = 0
    master_logs_scanned = 0

    if include_metrics:
        for scenario in scenarios:
            metric_errors.extend(collect_metric_errors(results_dir, scenario))

    if include_logs:
        for scenario in scenarios:
            for log_path in sorted((results_dir / scenario).glob("benchmark_*.log")):
                scenario_logs_scanned += 1
                log_errors.extend(
                    collect_log_errors(log_path, scope="scenario", scenario=scenario)
                )

        if not requested_scenarios:
            for log_path in sorted(results_dir.glob("scenarios_*.log")):
                master_logs_scanned += 1
                log_errors.extend(collect_log_errors(log_path, scope="master"))

    metric_total_errors = sum(
        record["total_errors"] or 0 for record in metric_errors if record["total_errors"] is not None
    )

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "results_dir": str(results_dir.resolve()),
        "summary": {
            "scenario_count": len(scenarios),
            "scenario_logs_scanned": scenario_logs_scanned,
            "master_logs_scanned": master_logs_scanned,
            "metric_records": len(metric_errors),
            "metric_total_errors": metric_total_errors,
            "log_error_lines": len(log_errors),
        },
        "metric_errors": metric_errors,
        "log_errors": log_errors,
    }


def write_json(payload: dict, output_path: Path | None) -> None:
    text = json.dumps(payload, indent=2)
    if output_path is None:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")


def write_csv(payload: dict, output_path: Path | None) -> None:
    fieldnames = [
        "record_type",
        "scope",
        "scenario",
        "category",
        "broker",
        "artifact",
        "path",
        "total_errors",
        "status",
        "error",
        "memory",
        "per_worker_errors",
        "log_file",
        "line_no",
        "message",
    ]

    rows = []
    for record in payload["metric_errors"]:
        rows.append(
            {
                "record_type": "metric",
                "scope": "scenario",
                "scenario": record.get("scenario"),
                "category": record.get("category"),
                "broker": record.get("broker"),
                "artifact": record.get("artifact"),
                "path": record.get("path"),
                "total_errors": record.get("total_errors"),
                "status": record.get("status"),
                "error": record.get("error"),
                "memory": record.get("memory"),
                "per_worker_errors": json.dumps(record.get("per_worker_errors", [])),
                "log_file": "",
                "line_no": "",
                "message": "",
            }
        )

    for record in payload["log_errors"]:
        rows.append(
            {
                "record_type": "log",
                "scope": record.get("scope"),
                "scenario": record.get("scenario"),
                "category": "",
                "broker": "",
                "artifact": "",
                "path": "",
                "total_errors": "",
                "status": "",
                "error": "",
                "memory": "",
                "per_worker_errors": "",
                "log_file": record.get("log_file"),
                "line_no": record.get("line_no"),
                "message": record.get("message"),
            }
        )

    if output_path is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    results_dir = Path(args.results_dir).expanduser()
    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}", file=sys.stderr)
        return 1

    requested_scenarios = flatten_scenarios(args.scenario)
    include_metrics = not args.logs_only
    include_logs = not args.metrics_only

    payload = build_export(
        results_dir=results_dir,
        requested_scenarios=requested_scenarios,
        include_metrics=include_metrics,
        include_logs=include_logs,
    )

    if requested_scenarios and payload["summary"]["scenario_count"] == 0:
        missing = ", ".join(sorted(requested_scenarios))
        print(f"No matching scenario results found for: {missing}", file=sys.stderr)
        return 1

    output_path: Path | None
    if args.output == "-":
        output_path = None
    elif args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = default_output_path(results_dir, args.format)

    if args.format == "json":
        write_json(payload, output_path)
    else:
        write_csv(payload, output_path)

    if output_path is not None:
        summary = payload["summary"]
        target = str(output_path.resolve())
        print(
            "Exported "
            f"{summary['metric_records']} metric record(s) and "
            f"{summary['log_error_lines']} log error line(s) to {target}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
