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

## Run full benchmark suite

```bash
./run_all.sh                          # full run (~3 hrs)
./run_all.sh --quick                  # smoke test (~15 min)
./run_all.sh --duration 120           # 2 min per throughput run
./run_all.sh --duration 300 --reps 2  # 5 min runs, 2 reps
./run_all.sh --ui                     # start UIs (Redpanda Console, Nui) with each broker
./run_all.sh --help                   # see all options
```

Results stream to stdout. JSON files land in `results/`.

### Or run scenarios individually

```bash
# AC-3: Idle footprint (takes ~12 min — 5 min idle × 2 brokers)
bash scripts/bench_idle.sh

# AC-4: Startup & recovery (~2 min)
bash scripts/bench_startup.sh

# AC-5: Throughput — 3 reps × 10 min × 2 brokers (~65 min)
bash scripts/bench_throughput.sh

# AC-6: Latency — 5 min × 2 brokers (~12 min)
bash scripts/bench_latency.sh

# AC-7: Memory stress — 4 levels × 2 min × 2 brokers (~20 min)
bash scripts/bench_memory_stress.sh
```

## Generate charts

```bash
uv run python3 bench/visualize.py
```

PNGs go to `results/charts/`.

## Aggregate report + decision

```bash
uv run python3 bench/aggregate_results.py
```

Writes `results/full_report.json` and prints the recommendation.

## Where results live

```text
results/
├── *_idle_stats.json             # idle RAM/CPU/disk
├── *_startup.json                # startup + recovery ms
├── *_throughput_run{1,2,3}.json  # per-run throughput
├── *_latency.json                # p50/p95/p99/p999/max
├── *_mem_{4g,2g,1g,512m}.json   # memory stress pass/fail
├── docker_stats.csv              # time-series resource usage
├── full_report.json              # aggregated report + recommendation
└── charts/
    ├── 01_idle_footprint.png
    ├── 02_startup_recovery.png
    ├── 03_throughput.png
    ├── 04_latency.png
    ├── 05_memory_stress.png
    └── 06_scorecard.png
```

## Tweak parameters

Edit `kafka-client.env` / `nats-client.env` at project root. Shell env vars override file values.

Quick short run for testing:

```bash
TEST_DURATION_SEC=30 BASELINE_RATE=100 NUM_PRODUCERS=1 uv run python3 bench/producer_kafka.py
```

## Cleanup

See [Teardown](#teardown) above.
