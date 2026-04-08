# NATS vs Kafka Experiment

## Running Infra

**Start everything (brokers + UIs) in one shot:**

```bash
docker compose -f infra/docker-compose.kafka.yml -f infra/docker-compose.nats.yml --profile tools up -d
```

### Brokers

```bash
docker compose -f infra/docker-compose.kafka.yml up -d   # Kafka    → localhost:9092
docker compose -f infra/docker-compose.nats.yml up -d     # NATS     → localhost:4222
```

### UIs (append `--profile tools`)

```bash
docker compose -f infra/docker-compose.kafka.yml --profile tools up -d   # + Redpanda Console → localhost:9080
docker compose -f infra/docker-compose.nats.yml --profile tools up -d    # + Nui → localhost:31311, nats-box CLI
```

### Teardown

```bash
docker compose -f infra/docker-compose.kafka.yml down -v
docker compose -f infra/docker-compose.nats.yml down -v
```

## Setup (one time)

```bash
uv sync
```

## Run benchmarks

> **Just use `run_scenarios.sh`.** It runs the full benchmark across multiple hardware profiles
> (large / medium / small), generates per-scenario charts, and then comparison charts automatically.
> `run_all.sh` is the lower-level engine that benchmarks one hardware config — you don't need to
> call it directly unless you're doing a custom single-config run.

### Quick start

```bash
./run_scenarios.sh                    # full run — all 3 scenarios (~9 hrs)
./run_scenarios.sh --quick            # smoke test — all 3 scenarios (~45 min)
./run_scenarios.sh --scenario large   # single scenario only
./run_scenarios.sh --list             # show scenario configs
```

| Scenario | CPUs | Memory |
|----------|------|--------|
| large    | 4.0  | 8g     |
| medium   | 3.0  | 4g     |
| small    | 2.0  | 2g     |

Per-scenario results go to `results/{large,medium,small}/`. Cross-scenario comparison charts
and a combined mega-image are generated automatically in `results/comparison/`.

### Monitor a running benchmark

```bash
./bench_status.sh                        # latest run — auto-detects scenario log
./bench_status.sh --all                  # all past runs
./bench_status.sh --latest 3             # last 3 runs
./bench_status.sh results/large/benchmark_*.log  # specific log file
```

Shows per-scenario status (RUNNING / COMPLETED / INTERRUPTED / FAILED), a step-by-step
progress checklist, elapsed time, remaining steps, and any errors extracted from the logs.

> **Ctrl+C guard:** Both `run_all.sh` and `run_scenarios.sh` intercept Ctrl+C and prompt
> `Are you sure you want to cancel? [y/N]` before aborting. Press N or Enter to resume.
> A second Ctrl+C during the prompt kills immediately.

### Re-run missing or failed steps

If a scenario ran partially (e.g. medium/small are missing some benchmarks), re-run only
the missing steps without re-doing the ones that already completed:

```bash
# Fill in missing steps for medium and small
./run_scenarios.sh --scenario medium --rerun cli_throughput,consumer,prodcon,latency,resource_scaling
./run_scenarios.sh --scenario small --rerun cli_throughput,consumer,prodcon,latency,resource_scaling

# Regenerate cross-scenario comparison charts (needed because --scenario only compares 1 at a time)
SCENARIO_NAMES="large medium small" uv run python3 bench/visualize.py --compare
```

### All flags

All flags below work with both `run_scenarios.sh` and `run_all.sh`:

| Flag | Description | Default |
|------|-------------|---------|
| `--quick` | Quick smoke run (30s duration, 1 rep, 2 producers) | — |
| `--duration SEC` | Throughput test duration per run | 600 |
| `--reps N` | Number of throughput repetitions (median used) | 3 |
| `--idle-wait SEC` | Idle wait time for footprint measurement | 300 |
| `--producers N` | Number of parallel producers | 4 |
| `--memory SIZE` | Broker memory limit (e.g., `2g`, `512m`) | 4g |
| `--ui` | Start UI containers alongside brokers | off |
| `--results-dir DIR` | Output directory for results | `results/` |
| `--resume` | Resume from last checkpoint (skip completed steps) | — |
| `--rerun STEPS` | Re-run specific steps (comma-separated), implies `--resume` | — |

`run_scenarios.sh` also accepts `--scenario NAME` and `--list`.

**Valid step names for `--rerun`:** `idle`, `startup`, `throughput`, `latency`, `memory_stress`, `cli_throughput`, `consumer`, `prodcon`, `resource_scaling`

### Benchmark steps (execution order)

| # | Step | Script | Description |
|---|------|--------|-------------|
| 1 | idle | `bench_idle.sh` | Idle resource footprint (~12 min — 5 min idle × 2 brokers) |
| 2 | startup | `bench_startup.sh` | Cold start & SIGKILL recovery time (~2 min) |
| 3 | throughput | `bench_throughput.sh` | Max producer throughput, N reps × 2 brokers (~65 min) |
| 4 | latency | `bench_latency.sh` | End-to-end latency at 50% peak rate (~12 min) |
| 5 | memory_stress | `bench_memory_stress.sh` | Stability at 4g → 2g → 1g → 512m RAM (~20 min) |
| 6 | cli_throughput | `bench_cli_throughput.sh` | CLI-native throughput (kcat / nats bench, ~5 min) |
| 7 | consumer | `bench_consumer.sh` | Consumer-only throughput, N reps × 2 brokers |
| 8 | prodcon | `bench_prodcon.sh` | Simultaneous producer + consumer load |
| 9 | resource_scaling | `bench_resource_scaling.sh` | Throughput under varying CPU limits (6 levels) |
| 10 | — | `aggregate_results.py` | Merge results into `full_report.json` + decision |
| 11 | — | `visualize.py` | Generate PNG charts |

### Advanced: single-config run with `run_all.sh`

Use `run_all.sh` directly only if you want to benchmark a single custom hardware config
without the scenario framework:

```bash
BENCH_CPUS=2.0 BENCH_MEMORY=4g ./run_all.sh                # custom config
./run_all.sh --duration 120                                 # 2 min per throughput run
./run_all.sh --resume                                       # resume from last checkpoint
./run_all.sh --rerun latency,consumer                       # re-run specific steps
```

Results go to `results/` (flat, no scenario subdirectories). No comparison charts are generated.

### Run individual benchmark scripts

```bash
bash scripts/bench_idle.sh              # Idle footprint
bash scripts/bench_startup.sh           # Startup & recovery
bash scripts/bench_throughput.sh        # Max throughput (producer)
bash scripts/bench_latency.sh           # Latency under load
bash scripts/bench_memory_stress.sh     # Memory stress
bash scripts/bench_cli_throughput.sh    # CLI-native throughput (kcat / nats bench)
bash scripts/bench_consumer.sh          # Consumer-only throughput
bash scripts/bench_prodcon.sh           # Simultaneous producer + consumer
bash scripts/bench_resource_scaling.sh  # Throughput vs CPU limit
```

## Generate charts

```bash
uv run python3 bench/visualize.py              # single-scenario charts
uv run python3 bench/visualize.py --compare    # cross-scenario comparison charts
```

### Single-scenario charts (→ `results/charts/`)

| # | File | Description |
|---|------|-------------|
| 01 | `01_idle_footprint.png` | Idle RAM + CPU (2-bar subplot) |
| 02 | `02_startup_recovery.png` | Cold start / SIGKILL recovery time |
| 03 | `03_throughput.png` | Python client vs CLI producer throughput |
| 03b | `03b_cli_throughput.png` | CLI-only: producer / consumer / prodcon throughput |
| 04 | `04_latency.png` | p50 / p95 / p99 / p99.9 / max latency (log scale) |
| 05 | `05_memory_stress.png` | Pass/fail heatmap per memory level |
| 06 | `06_scorecard.png` | Full metric comparison table with winner per row |
| 07 | `07_consumer_throughput.png` | Python vs CLI consumer throughput |
| 08 | `08_prodcon.png` | Producer + consumer rates under simultaneous load |
| 09 | `09_resource_timeline.png` | CPU% + memory over time (from `docker_stats.csv`) |
| 10 | `10_resource_scaling.png` | Throughput + peak memory vs CPU limit (dual Y-axis) |
| 11 | `11_disk_io_timeline.png` | Disk read/write (block I/O) over time |
| 12 | `12_throughput_vs_resources.png` | Resource efficiency: throughput per CPU core and per GB RAM |

### Cross-scenario comparison charts (→ `results/comparison/`)

| # | File | Description |
|---|------|-------------|
| 01 | `cmp_01_idle.png` | Idle RAM across scenarios |
| 02 | `cmp_02_startup.png` | Startup / recovery across scenarios |
| 03 | `cmp_03_throughput.png` | Python client throughput across scenarios |
| 04 | `cmp_04_cli_throughput.png` | CLI throughput across scenarios |
| 05 | `cmp_05_latency.png` | Latency percentiles per broker per scenario |
| 06 | `cmp_06_memory_stress.png` | Pass/fail heatmap table across scenarios |
| 07 | `cmp_07_consumer.png` | Consumer throughput (Python + CLI) across scenarios |
| 08 | `cmp_08_prodcon.png` | ProdCon rates (Python + CLI) across scenarios |
| 09 | `cmp_09_resource_scaling.png` | Throughput-vs-CPU scaling slopes per broker per scenario |
| 10 | `cmp_10_resource_timeline.png` | CPU / RAM / disk I/O over time, one row per scenario |
| 11 | `cmp_11_throughput_vs_resources.png` | Resource efficiency (throughput per CPU core & per GB RAM) across scenarios |
| — | `mega_comparison.png` | All comparison charts + per-scenario scorecards tiled into one image |

## Aggregate report + decision

```bash
uv run python3 bench/aggregate_results.py
```

Writes `results/full_report.json` and prints the recommendation (`KEEP_KAFKA`, `MIGRATE_TO_NATS`, `TRADEOFF`, or `INCONCLUSIVE`).

## Where results live

```text
results/
├── large/ medium/ small/                 # per-scenario (run_scenarios.sh)
│   ├── kafka_idle_stats.json
│   ├── nats_idle_stats.json
│   ├── kafka_startup.json                # JSONL (one JSON object per line)
│   ├── nats_startup.json
│   ├── kafka_throughput_run{1,2,3}.json
│   ├── nats_throughput_run{1,2,3}.json
│   ├── kafka_consumer_run{1,2,3}.json
│   ├── nats_consumer_run{1,2,3}.json
│   ├── kafka_prodcon.json
│   ├── nats_prodcon.json
│   ├── kafka_latency.json
│   ├── nats_latency.json
│   ├── kafka_mem_{4g,2g,1g,512m}.json
│   ├── nats_mem_{4g,2g,1g,512m}.json
│   ├── kafka_cli_throughput.json
│   ├── nats_cli_throughput.json
│   ├── kafka_cli_consumer.json
│   ├── nats_cli_consumer.json
│   ├── kafka_cli_prodcon.json
│   ├── nats_cli_prodcon.json
│   ├── kafka_scaling.json
│   ├── nats_scaling.json
│   ├── docker_stats.csv
│   ├── full_report.json
│   ├── checkpoint.log
│   ├── benchmark_*.log
│   └── charts/
│       ├── 01_idle_footprint.png … 12_throughput_vs_resources.png
├── comparison/                           # cross-scenario (auto-generated)
│   ├── cmp_01_idle.png … cmp_11_throughput_vs_resources.png
│   └── mega_comparison.png
├── scenarios_*.log                       # run_scenarios.sh master log
├── *.json                                # single-scenario (run_all.sh directly)
├── docker_stats.csv
├── full_report.json
└── charts/
    ├── 01_idle_footprint.png … 12_throughput_vs_resources.png
```

## Tweak parameters

Edit `kafka-client.env` / `nats-client.env` at project root, or `env.sh` for shell-level defaults.
Shell env vars override file values.

### All parameters (`env.sh`)

| Variable | Default | Description |
|----------|---------|-------------|
| `BENCH_CPUS` | `2.0` | CPU cores allocated to each broker container |
| `BENCH_MEMORY` | `4g` | Starting RAM cap (override per scenario or `--memory`) |
| `BENCH_DISK_TYPE` | `ssd` | Documents actual host disk type (metadata only) |
| `BENCH_DISK_SIZE` | `20g` | Disk size allocation |
| `PAYLOAD_BYTES` | `1024` | Message payload size in bytes (1 KB) |
| `NUM_PRODUCERS` | `4` | Parallel producer threads/tasks |
| `NUM_CONSUMERS` | `4` | Parallel consumer threads/tasks |
| `TEST_DURATION_SEC` | `600` | Sustained throughput test duration (10 min) |
| `REPS` | `3` | Number of throughput repetitions (median used) |
| `SCALING_CPU_LEVELS` | `4.0 3.0 2.0 1.5 1.0 0.5` | CPU levels for resource scaling test |
| `KAFKA_BROKER` | `localhost:9092` | Kafka bootstrap server |
| `NATS_URL` | `nats://localhost:4222` | NATS server URL |
| `KAFKA_TOPIC` | `bench` | Kafka topic name |
| `NATS_STREAM` | `BENCH` | NATS JetStream stream name |
| `NATS_SUBJECT` | `bench.data` | NATS subject pattern |

Quick short run for testing:

```bash
TEST_DURATION_SEC=30 NUM_PRODUCERS=1 uv run python3 bench/producer_kafka.py
```

## Cleanup

Stop and remove broker containers + volumes:

```bash
docker compose -f infra/docker-compose.kafka.yml down -v
docker compose -f infra/docker-compose.nats.yml down -v
```

Stop everything (brokers + UIs) at once:

```bash
docker compose -f infra/docker-compose.kafka.yml -f infra/docker-compose.nats.yml --profile tools down -v
```

Remove all benchmark results:

```bash
rm -rf results/*/charts results/comparison results/charts results/*/*.json results/*/*.csv results/*/*.log
```
