#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Large (4 CPUs, 8g)
BENCH_CPUS=4.0 BENCH_MEMORY=8g RESULTS_DIR=./results/large bash run_all.sh --rerun prodcon

# Medium (3 CPUs, 4g)
BENCH_CPUS=3.0 BENCH_MEMORY=4g RESULTS_DIR=./results/medium bash run_all.sh --rerun prodcon

# Small (2 CPUs, 2g)
BENCH_CPUS=2.0 BENCH_MEMORY=2g RESULTS_DIR=./results/small bash run_all.sh --rerun prodcon

# Regenerate reports
./bench_report.sh

echo "Done. All 3 NATS prodcon reruns complete and reports regenerated."
