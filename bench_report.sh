#!/usr/bin/env bash
# bench_report.sh — Generate a Markdown benchmark report with charts + data tables
#
# Usage:
#   ./bench_report.sh                          # all completed scenarios
#   ./bench_report.sh --scenario large         # specific scenario
#   ./bench_report.sh --scenario large medium  # multiple scenarios
#
set -uo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

# ─── Args ────────────────────────────────────────────────────────────────────
SCENARIOS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario) shift; while [[ $# -gt 0 && "$1" != --* ]]; do SCENARIOS+=("$1"); shift; done ;;
    -h|--help)
      echo "Usage: $0 [--scenario NAME ...]"
      echo "Generate Markdown benchmark report with embedded charts and data tables."
      echo ""
      echo "Options:"
      echo "  --scenario NAME   Specific scenario(s) to report (default: all with full_report.json)"
      echo "  -h, --help        Show this help"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Auto-detect scenarios if none specified
if [[ ${#SCENARIOS[@]} -eq 0 ]]; then
  for d in "$PROJECT_ROOT"/results/*/; do
    if [[ -f "$d/full_report.json" ]]; then
      SCENARIOS+=("$(basename "$d")")
    fi
  done
fi

if [[ ${#SCENARIOS[@]} -eq 0 ]]; then
  echo "No completed scenarios found (no full_report.json). Run benchmarks first."
  exit 1
fi

# ─── Helpers ─────────────────────────────────────────────────────────────────
# Read a JSON field — uses python for reliability
jq_py() {
  python3 -c "
import json, sys
data = json.load(open('$1'))
keys = '$2'.split('.')
for k in keys:
    if isinstance(data, list):
        data = data[int(k)]
    else:
        data = data.get(k, '')
print(data if not isinstance(data, (dict, list)) else json.dumps(data))
" 2>/dev/null
}

# Format number with commas
fmt_num() {
  python3 -c "
v = '$1'
try:
    f = float(v)
    if f == int(f) and '.' not in v:
        print(f'{int(f):,}')
    else:
        print(f'{f:,.1f}')
except: print(v)
" 2>/dev/null
}

# ─── Generate report for one scenario ───────────────────────────────────────
generate_report() {
  local scenario="$1"
  local dir="$PROJECT_ROOT/results/$scenario"
  local report="$dir/full_report.json"
  local charts_dir="$dir/charts"

  if [[ ! -f "$report" ]]; then
    echo "  Skipping $scenario — no full_report.json"
    return
  fi

  # Extract timestamp from benchmark log filename
  local bench_log run_ts
  bench_log=$(ls -t "$dir"/benchmark_*.log 2>/dev/null | head -1 || echo "")
  if [[ -n "$bench_log" ]]; then
    run_ts=$(basename "$bench_log" | grep -oP '\d{8}_\d{6}')
    run_ts="${run_ts:0:4}-${run_ts:4:2}-${run_ts:6:2} ${run_ts:9:2}:${run_ts:11:2}:${run_ts:13:2}"
  else
    run_ts="unknown"
  fi

  # Extract metadata
  local cpus mem payload producers consumers
  cpus=$(jq_py "$report" "metadata.hardware.cpus")
  mem=$(jq_py "$report" "metadata.hardware.memory")
  payload=$(jq_py "$report" "metadata.payload_bytes")
  producers=$(jq_py "$report" "metadata.num_producers")
  consumers=$(jq_py "$report" "metadata.num_consumers")

  local out="$dir/benchmark_report.md"
  echo "  Generating $out ..."

  cat > "$out" <<HEADER
# Benchmark Report: ${scenario^^}

| Field | Value |
|-------|-------|
| **Scenario** | ${scenario} |
| **Run timestamp** | ${run_ts} |
| **CPUs** | ${cpus} |
| **Memory** | ${mem} |
| **Payload** | ${payload} bytes |
| **Producers** | ${producers} |
| **Consumers** | ${consumers} |

---

HEADER

  # ── 1. Idle Footprint ──────────────────────────────────────────────────────
  local k_cpu k_mem k_mem_pct n_cpu n_mem n_mem_pct
  k_cpu=$(jq_py "$report" "idle_footprint.kafka.cpu_pct")
  k_mem=$(jq_py "$report" "idle_footprint.kafka.mem_usage")
  k_mem_pct=$(jq_py "$report" "idle_footprint.kafka.mem_pct")
  n_cpu=$(jq_py "$report" "idle_footprint.nats.cpu_pct")
  n_mem=$(jq_py "$report" "idle_footprint.nats.mem_usage")
  n_mem_pct=$(jq_py "$report" "idle_footprint.nats.mem_pct")

  cat >> "$out" <<'SECTION'
## 1. Idle Footprint

SECTION
  [[ -f "$charts_dir/01_idle_footprint.png" ]] && echo "![Idle Footprint](charts/01_idle_footprint.png)" >> "$out"
  cat >> "$out" <<TABLE

| Metric | Kafka | NATS |
|--------|-------|------|
| CPU | ${k_cpu} | ${n_cpu} |
| Memory | ${k_mem} | ${n_mem} |
| Memory % | ${k_mem_pct} | ${n_mem_pct} |

TABLE

  # ── 2. Startup & Recovery ──────────────────────────────────────────────────
  local k_start k_recov n_start n_recov
  k_start=$(python3 -c "import json; d=json.load(open('$report')); print([x['ms'] for x in d['startup_recovery']['kafka'] if x['type']=='startup'][0])" 2>/dev/null)
  k_recov=$(python3 -c "import json; d=json.load(open('$report')); print([x['ms'] for x in d['startup_recovery']['kafka'] if x['type']=='recovery'][0])" 2>/dev/null)
  n_start=$(python3 -c "import json; d=json.load(open('$report')); print([x['ms'] for x in d['startup_recovery']['nats'] if x['type']=='startup'][0])" 2>/dev/null)
  n_recov=$(python3 -c "import json; d=json.load(open('$report')); print([x['ms'] for x in d['startup_recovery']['nats'] if x['type']=='recovery'][0])" 2>/dev/null)

  cat >> "$out" <<TABLE
---

## 2. Startup & Recovery

TABLE
  [[ -f "$charts_dir/02_startup_recovery.png" ]] && echo "![Startup & Recovery](charts/02_startup_recovery.png)" >> "$out"
  cat >> "$out" <<TABLE

| Metric | Kafka | NATS |
|--------|-------|------|
| Cold start | $(fmt_num "$k_start") ms | $(fmt_num "$n_start") ms |
| SIGKILL recovery | $(fmt_num "$k_recov") ms | $(fmt_num "$n_recov") ms |

TABLE

  # ── 3. Producer Throughput (Python) ────────────────────────────────────────
  # Extract median run rates
  local k_tp n_tp
  k_tp=$(python3 -c "
import json
d = json.load(open('$report'))
rates = sorted([r['aggregate_rate'] for r in d['throughput']['kafka']['runs']])
print(rates[len(rates)//2])
" 2>/dev/null)
  n_tp=$(python3 -c "
import json
d = json.load(open('$report'))
rates = sorted([r['aggregate_rate'] for r in d['throughput']['nats']['runs']])
print(rates[len(rates)//2])
" 2>/dev/null)

  cat >> "$out" <<TABLE
---

## 3. Producer Throughput (Python Client)

TABLE
  [[ -f "$charts_dir/03_throughput.png" ]] && echo "![Throughput](charts/03_throughput.png)" >> "$out"

  # Per-run table
  echo "" >> "$out"
  echo "| Run | Kafka (msg/s) | NATS (msg/s) |" >> "$out"
  echo "|-----|---------------|--------------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
kr = d['throughput']['kafka']['runs']
nr = d['throughput']['nats']['runs']
for i in range(max(len(kr), len(nr))):
    kv = f\"{kr[i]['aggregate_rate']:,.1f}\" if i < len(kr) else '-'
    nv = f\"{nr[i]['aggregate_rate']:,.1f}\" if i < len(nr) else '-'
    print(f'| {i+1} | {kv} | {nv} |')
kr_s = sorted([r['aggregate_rate'] for r in kr])
nr_s = sorted([r['aggregate_rate'] for r in nr])
km = kr_s[len(kr_s)//2]; nm = nr_s[len(nr_s)//2]
print(f'| **Median** | **{km:,.1f}** | **{nm:,.1f}** |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 3b. CLI Throughput ─────────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 3b. CLI-Native Throughput

TABLE
  [[ -f "$charts_dir/03b_cli_throughput.png" ]] && echo "![CLI Throughput](charts/03b_cli_throughput.png)" >> "$out"

  echo "" >> "$out"
  echo "| Test | Kafka (msg/s) | NATS (msg/s) |" >> "$out"
  echo "|------|---------------|--------------|" >> "$out"
  python3 -c "
import json, os
d = '$dir'
def load(f):
    try: return json.load(open(os.path.join(d, f)))
    except: return {}
kt = load('kafka_cli_throughput.json')
nt = load('nats_cli_throughput.json')
kc = load('kafka_cli_consumer.json')
nc = load('nats_cli_consumer.json')
kp = load('kafka_cli_prodcon.json')
np_ = load('nats_cli_prodcon.json')
def fv(d, k='msgs_per_sec'): return f\"{d.get(k, 0):,.1f}\" if d else '-'
print(f'| Producer | {fv(kt)} | {fv(nt)} |')
print(f'| Consumer | {fv(kc)} | {fv(nc)} |')
print(f'| ProdCon (pub) | {fv(kp, \"producer_msgs_per_sec\")} | {fv(np_, \"producer_msgs_per_sec\")} |')
print(f'| ProdCon (sub) | {fv(kp, \"consumer_msgs_per_sec\")} | {fv(np_, \"consumer_msgs_per_sec\")} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 4. Latency ─────────────────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 4. Latency Under Load

TABLE
  [[ -f "$charts_dir/04_latency.png" ]] && echo "![Latency](charts/04_latency.png)" >> "$out"

  echo "" >> "$out"
  echo "| Percentile | Kafka | NATS |" >> "$out"
  echo "|------------|-------|------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
kl = d['latency']['kafka']
nl = d['latency']['nats']
def us_fmt(v):
    v = float(v)
    if v >= 1_000_000: return f'{v/1_000_000:,.2f} s'
    if v >= 1_000: return f'{v/1_000:,.1f} ms'
    return f'{v:,.1f} us'
for p in ['p50_us','p95_us','p99_us','p999_us','max_us']:
    label = p.replace('_us','').replace('p','p').replace('999','99.9')
    print(f'| {label} | {us_fmt(kl[p])} | {us_fmt(nl[p])} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 5. Memory Stress ───────────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 5. Memory Stress

TABLE
  [[ -f "$charts_dir/05_memory_stress.png" ]] && echo "![Memory Stress](charts/05_memory_stress.png)" >> "$out"

  echo "" >> "$out"
  echo "| RAM Level | Kafka | NATS |" >> "$out"
  echo "|-----------|-------|------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
ms = d['memory_stress']
kl = ms['kafka'].get('levels', ms['kafka'])
nl = ms['nats'].get('levels', ms['nats'])
levels = ['4g','2g','1g','512m']
for lvl in levels:
    kd = kl.get(lvl, {})
    nd = nl.get(lvl, {})
    def status(x):
        if not x: return '—'
        if 'total_errors' in x:
            errs = x.get('total_errors', 0)
            rate = x.get('aggregate_rate', 0)
            return f'PASS ({rate:,.0f} msg/s, {errs} errs)' if rate > 0 else 'FAIL'
        s = x.get('status', x.get('pass', ''))
        if isinstance(s, bool): return 'PASS' if s else 'FAIL'
        return str(s).upper() if s else '—'
    ks = status(kd)
    ns = status(nd)
    print(f'| {lvl} | {ks} | {ns} |')
kmin = ms['kafka'].get('min_viable_ram', '—')
nmin = ms['nats'].get('min_viable_ram', '—')
print(f'| **Min viable** | **{kmin}** | **{nmin}** |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 6. Scorecard ───────────────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 6. Scorecard

TABLE
  [[ -f "$charts_dir/06_scorecard.png" ]] && echo "![Scorecard](charts/06_scorecard.png)" >> "$out"
  echo "" >> "$out"

  # ── 7. Consumer Throughput ─────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 7. Consumer Throughput

TABLE
  [[ -f "$charts_dir/07_consumer_throughput.png" ]] && echo "![Consumer Throughput](charts/07_consumer_throughput.png)" >> "$out"

  echo "" >> "$out"
  echo "| Run | Kafka (msg/s) | NATS (msg/s) |" >> "$out"
  echo "|-----|---------------|--------------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
kr = d['consumer_throughput']['kafka']['runs']
nr = d['consumer_throughput']['nats']['runs']
for i in range(max(len(kr), len(nr))):
    kv = f\"{kr[i]['aggregate_rate']:,.1f}\" if i < len(kr) else '-'
    nv = f\"{nr[i]['aggregate_rate']:,.1f}\" if i < len(nr) else '-'
    print(f'| {i+1} | {kv} | {nv} |')
kr_s = sorted([r['aggregate_rate'] for r in kr])
nr_s = sorted([r['aggregate_rate'] for r in nr])
km = kr_s[len(kr_s)//2]; nm = nr_s[len(nr_s)//2]
print(f'| **Median** | **{km:,.1f}** | **{nm:,.1f}** |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 8. Simultaneous Producer + Consumer ────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 8. Simultaneous Producer + Consumer

TABLE
  [[ -f "$charts_dir/08_prodcon.png" ]] && echo "![ProdCon](charts/08_prodcon.png)" >> "$out"

  echo "" >> "$out"
  echo "| Metric | Kafka | NATS |" >> "$out"
  echo "|--------|-------|------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
kp = d['prodcon']['kafka']
np_ = d['prodcon']['nats']
print(f'| Producer rate (msg/s) | {kp[\"producer\"][\"aggregate_rate\"]:,.1f} | {np_[\"producer\"][\"aggregate_rate\"]:,.1f} |')
print(f'| Consumer rate (msg/s) | {kp[\"consumer\"][\"aggregate_rate\"]:,.1f} | {np_[\"consumer\"][\"aggregate_rate\"]:,.1f} |')
print(f'| Producer errors | {kp[\"producer\"][\"total_errors\"]:,} | {np_[\"producer\"][\"total_errors\"]:,} |')
print(f'| Duration (s) | {kp[\"producer\"][\"wall_sec\"]:.1f} | {np_[\"producer\"][\"wall_sec\"]:.1f} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 9. Resource Timeline ───────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 9. Resource Timeline

TABLE
  [[ -f "$charts_dir/09_resource_timeline.png" ]] && echo "![Resource Timeline](charts/09_resource_timeline.png)" >> "$out"
  echo "" >> "$out"
  echo "*Data source: docker_stats.csv — continuous sampling during benchmark run.*" >> "$out"
  echo "" >> "$out"

  # ── 10. Resource Scaling ───────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 10. Resource Scaling

TABLE
  [[ -f "$charts_dir/10_resource_scaling.png" ]] && echo "![Resource Scaling](charts/10_resource_scaling.png)" >> "$out"

  echo "" >> "$out"
  echo "### Kafka" >> "$out"
  echo "" >> "$out"
  echo "| CPU Limit | Throughput (msg/s) | Peak CPU % | Peak Mem (MB) | Status |" >> "$out"
  echo "|-----------|-------------------|------------|---------------|--------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
for r in d['resource_scaling']['kafka']:
    print(f'| {r[\"cpu_limit\"]} | {r[\"throughput\"]:,.1f} | {r[\"peak_cpu_pct\"]:.1f} | {r[\"peak_mem_mb\"]:.1f} | {r[\"status\"]} |')
" 2>/dev/null >> "$out"

  echo "" >> "$out"
  echo "### NATS" >> "$out"
  echo "" >> "$out"
  echo "| CPU Limit | Throughput (msg/s) | Peak CPU % | Peak Mem (MB) | Status |" >> "$out"
  echo "|-----------|-------------------|------------|---------------|--------|" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
for r in d['resource_scaling']['nats']:
    print(f'| {r[\"cpu_limit\"]} | {r[\"throughput\"]:,.1f} | {r[\"peak_cpu_pct\"]:.1f} | {r[\"peak_mem_mb\"]:.1f} | {r[\"status\"]} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 11. Disk I/O Timeline ──────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 11. Disk I/O Timeline

TABLE
  [[ -f "$charts_dir/11_disk_io_timeline.png" ]] && echo "![Disk I/O Timeline](charts/11_disk_io_timeline.png)" >> "$out"
  echo "" >> "$out"
  echo "*Data source: docker_stats.csv block_io column.*" >> "$out"
  echo "" >> "$out"

  # ── 12. Throughput vs Resources ────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 12. Throughput vs Resources

TABLE
  [[ -f "$charts_dir/12_throughput_vs_resources.png" ]] && echo "![Throughput vs Resources](charts/12_throughput_vs_resources.png)" >> "$out"
  echo "" >> "$out"

  # ── 13. Worker Load Balance ──────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 13. Worker Load Balance

TABLE
  [[ -f "$charts_dir/13_worker_balance.png" ]] && echo "![Worker Balance](charts/13_worker_balance.png)" >> "$out"

  echo "" >> "$out"
  echo "| Test | Broker | Workers | Mean (msg/s) | StdDev | CV% |" >> "$out"
  echo "|------|--------|---------|-------------|--------|-----|" >> "$out"
  python3 -c "
import json, os, statistics
d = '$dir'
report = json.load(open('$report'))
def show(label, runs_key, rate_key='per_worker'):
    for b in ['kafka', 'nats']:
        section = report.get(runs_key, {}).get(b, {})
        runs = section.get('runs', [section] if rate_key in section else [])
        if not runs:
            continue
        # Use median run
        runs_sorted = sorted(runs, key=lambda r: r.get('aggregate_rate', 0))
        run = runs_sorted[len(runs_sorted)//2]
        workers = run.get(rate_key, [])
        rates = [w.get('avg_rate', 0) for w in workers]
        if not rates:
            continue
        m = statistics.mean(rates)
        s = statistics.stdev(rates) if len(rates) > 1 else 0
        cv = (s / m * 100) if m > 0 else 0
        print(f'| {label} | {b.upper()} | {len(rates)} | {m:,.0f} | {s:,.0f} | {cv:.1f} |')
show('Producer', 'throughput')
show('Consumer', 'consumer_throughput')
for b in ['kafka', 'nats']:
    pc = report.get('prodcon', {}).get(b, {})
    if 'producer' in pc and 'per_worker' in pc['producer']:
        rates = [w.get('avg_rate', 0) for w in pc['producer']['per_worker']]
        if rates:
            m = statistics.mean(rates)
            s = statistics.stdev(rates) if len(rates) > 1 else 0
            cv = (s / m * 100) if m > 0 else 0
            print(f'| ProdCon (prod) | {b.upper()} | {len(rates)} | {m:,.0f} | {s:,.0f} | {cv:.1f} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 14. Error Rate Breakdown ─────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 14. Error Rate Breakdown

TABLE
  [[ -f "$charts_dir/14_error_breakdown.png" ]] && echo "![Error Breakdown](charts/14_error_breakdown.png)" >> "$out"

  echo "" >> "$out"
  echo "| Test | Kafka Errors | NATS Errors |" >> "$out"
  echo "|------|-------------|-------------|" >> "$out"
  python3 -c "
import json, os
report = json.load(open('$report'))
d = '$dir'
def load(f):
    try: return json.load(open(os.path.join(d, f)))
    except: return {}
# Throughput
kt = sum(r.get('total_errors', 0) for r in report.get('throughput', {}).get('kafka', {}).get('runs', []))
nt = sum(r.get('total_errors', 0) for r in report.get('throughput', {}).get('nats', {}).get('runs', []))
print(f'| Throughput (all runs) | {kt:,} | {nt:,} |')
# Consumer
kc = sum(r.get('total_errors', 0) for r in report.get('consumer_throughput', {}).get('kafka', {}).get('runs', []))
nc = sum(r.get('total_errors', 0) for r in report.get('consumer_throughput', {}).get('nats', {}).get('runs', []))
print(f'| Consumer (all runs) | {kc:,} | {nc:,} |')
# ProdCon
kp = report.get('prodcon', {}).get('kafka', {}).get('producer', {}).get('total_errors', 0)
np_ = report.get('prodcon', {}).get('nats', {}).get('producer', {}).get('total_errors', 0)
print(f'| ProdCon | {kp:,} | {np_:,} |')
# Memory stress
ms = report.get('memory_stress', {})
for lvl in ['4g', '2g', '1g', '512m']:
    kl = ms.get('kafka', {}).get('levels', ms.get('kafka', {}))
    nl = ms.get('nats', {}).get('levels', ms.get('nats', {}))
    ke = kl.get(lvl, {}).get('total_errors', 0) if isinstance(kl, dict) else 0
    ne = nl.get(lvl, {}).get('total_errors', 0) if isinstance(nl, dict) else 0
    print(f'| Mem {lvl} | {ke:,} | {ne:,} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 15. Throughput Stability ─────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 15. Throughput Stability Across Repetitions

TABLE
  [[ -f "$charts_dir/15_throughput_stability.png" ]] && echo "![Throughput Stability](charts/15_throughput_stability.png)" >> "$out"

  echo "" >> "$out"
  echo "| Test | Broker | Run 1 | Run 2 | Run 3 | Mean | StdDev | CV% |" >> "$out"
  echo "|------|--------|-------|-------|-------|------|--------|-----|" >> "$out"
  python3 -c "
import json, statistics
report = json.load(open('$report'))
for test, key in [('Producer', 'throughput'), ('Consumer', 'consumer_throughput')]:
    for b in ['kafka', 'nats']:
        runs = report.get(key, {}).get(b, {}).get('runs', [])
        rates = [r.get('aggregate_rate', 0) for r in runs]
        if not rates:
            continue
        m = statistics.mean(rates)
        s = statistics.stdev(rates) if len(rates) > 1 else 0
        cv = (s / m * 100) if m > 0 else 0
        cols = ' | '.join(f'{r:,.0f}' for r in rates)
        while len(rates) < 3:
            cols += ' | -'
            rates.append(0)
        print(f'| {test} | {b.upper()} | {cols} | {m:,.0f} | {s:,.0f} | {cv:.1f} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 16. ProdCon Balance Ratio ────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 16. Producer / Consumer Balance Ratio

TABLE
  [[ -f "$charts_dir/16_prodcon_balance.png" ]] && echo "![ProdCon Balance](charts/16_prodcon_balance.png)" >> "$out"

  echo "" >> "$out"
  echo "| Broker | Producer (msg/s) | Consumer (msg/s) | Ratio (P/C) | Interpretation |" >> "$out"
  echo "|--------|-----------------|-----------------|-------------|----------------|" >> "$out"
  python3 -c "
import json
report = json.load(open('$report'))
for b in ['kafka', 'nats']:
    pc = report.get('prodcon', {}).get(b, {})
    prod = pc.get('producer', {}).get('aggregate_rate', 0)
    cons = pc.get('consumer', {}).get('aggregate_rate', 0)
    if prod == 0 and cons == 0:
        continue
    ratio = prod / cons if cons > 0 else 0
    interp = 'Balanced' if 0.9 <= ratio <= 1.1 else 'Producer faster (backpressure)' if ratio > 1.1 else 'Consumer faster'
    print(f'| {b.upper()} | {prod:,.0f} | {cons:,.0f} | {ratio:.2f}x | {interp} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 17. Network I/O ──────────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 17. Network I/O Timeline

TABLE
  [[ -f "$charts_dir/17_network_io_timeline.png" ]] && echo "![Network I/O](charts/17_network_io_timeline.png)" >> "$out"
  echo "" >> "$out"
  echo "*Data source: docker_stats.csv net_io column.*" >> "$out"
  echo "" >> "$out"

  # ── 18. Memory Headroom ──────────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 18. Memory Headroom

TABLE
  [[ -f "$charts_dir/18_memory_headroom.png" ]] && echo "![Memory Headroom](charts/18_memory_headroom.png)" >> "$out"
  echo "" >> "$out"
  echo "*Data source: docker_stats.csv mem_pct column. Warning threshold: 80%. Critical threshold: 95%.*" >> "$out"
  echo "" >> "$out"

  # ── 19. Scaling Efficiency ───────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 19. Scaling Efficiency — Throughput per CPU Core

TABLE
  [[ -f "$charts_dir/19_scaling_efficiency.png" ]] && echo "![Scaling Efficiency](charts/19_scaling_efficiency.png)" >> "$out"

  echo "" >> "$out"
  echo "| CPU Limit | Kafka (msg/s/core) | NATS (msg/s/core) | Kafka Eff% | NATS Eff% |" >> "$out"
  echo "|-----------|-------------------|-------------------|-----------|----------|" >> "$out"
  python3 -c "
import json, os
d = '$dir'
def load(f):
    try: return json.load(open(os.path.join(d, f)))
    except: return []
ks = load('kafka_scaling.json')
ns = load('nats_scaling.json')
kpass = sorted([e for e in ks if e.get('status')=='PASS'], key=lambda e: -e['cpu_limit'])
npass = sorted([e for e in ns if e.get('status')=='PASS'], key=lambda e: -e['cpu_limit'])
k_base = (kpass[0]['throughput'] / kpass[0]['cpu_limit']) if kpass else 1
n_base = (npass[0]['throughput'] / npass[0]['cpu_limit']) if npass else 1
all_cpus = sorted(set(e['cpu_limit'] for e in kpass + npass), reverse=True)
kd = {e['cpu_limit']: e for e in kpass}
nd = {e['cpu_limit']: e for e in npass}
for cpu in all_cpus:
    ke = kd.get(cpu)
    ne = nd.get(cpu)
    k_tpc = ke['throughput'] / ke['cpu_limit'] if ke else 0
    n_tpc = ne['throughput'] / ne['cpu_limit'] if ne else 0
    k_eff = (k_tpc / k_base * 100) if k_base > 0 and ke else 0
    n_eff = (n_tpc / n_base * 100) if n_base > 0 and ne else 0
    kv = f'{k_tpc:,.0f}' if ke else '—'
    nv = f'{n_tpc:,.0f}' if ne else '—'
    kp = f'{k_eff:.0f}%' if ke else '—'
    np_ = f'{n_eff:.0f}%' if ne else '—'
    print(f'| {cpu} | {kv} | {nv} | {kp} | {np_} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── 20. Latency Load Context ─────────────────────────────────────────────
  cat >> "$out" <<TABLE
---

## 20. Latency Measurement Context

TABLE
  [[ -f "$charts_dir/20_latency_context.png" ]] && echo "![Latency Context](charts/20_latency_context.png)" >> "$out"

  echo "" >> "$out"
  echo "| Metric | Kafka | NATS |" >> "$out"
  echo "|--------|-------|------|" >> "$out"
  python3 -c "
import json
report = json.load(open('$report'))
lat = report.get('latency', {})
metrics = [
    ('Load %', 'load_pct', '{:.0f}%'),
    ('Target Rate (msg/s)', 'target_rate', '{:,.0f}'),
    ('Samples', 'samples', '{:,.0f}'),
    ('Messages Sent', 'sent', '{:,.0f}'),
    ('p50 (µs)', 'p50_us', '{:,.0f}'),
    ('p95 (µs)', 'p95_us', '{:,.0f}'),
    ('p99 (µs)', 'p99_us', '{:,.0f}'),
    ('p99.9 (µs)', 'p999_us', '{:,.0f}'),
    ('Max (µs)', 'max_us', '{:,.0f}'),
]
for label, key, fmt in metrics:
    kv = fmt.format(lat.get('kafka', {}).get(key, 0)) if key in lat.get('kafka', {}) else '—'
    nv = fmt.format(lat.get('nats', {}).get(key, 0)) if key in lat.get('nats', {}) else '—'
    print(f'| {label} | {kv} | {nv} |')
" 2>/dev/null >> "$out"
  echo "" >> "$out"

  # ── Decision / Recommendation ──────────────────────────────────────────────
  local recommendation
  recommendation=$(jq_py "$report" "decision.recommendation")

  cat >> "$out" <<TABLE
---

## Decision

**Recommendation: ${recommendation}**

TABLE

  echo "### Reasoning" >> "$out"
  echo "" >> "$out"
  python3 -c "
import json
d = json.load(open('$report'))
for r in d['decision']['reasoning']:
    print(f'- {r}')
" 2>/dev/null >> "$out"

  echo "" >> "$out"
  echo "---" >> "$out"
  echo "" >> "$out"
  echo "*Report generated: $(date -Iseconds)*" >> "$out"

  echo "  Done: $out"
}

# ─── Main ────────────────────────────────────────────────────────────────────
echo "Generating benchmark reports..."
echo ""

for scenario in "${SCENARIOS[@]}"; do
  generate_report "$scenario"
done

# ─── Consolidated report ─────────────────────────────────────────────────────
if [[ ${#SCENARIOS[@]} -gt 1 ]]; then
  COMBINED="$PROJECT_ROOT/results/benchmark_report.md"
  echo "  Generating consolidated $COMBINED ..."

  # Sort scenarios for deterministic order: small, medium, large, then anything else alpha
  IFS=$'\n' SORTED=($(for s in "${SCENARIOS[@]}"; do
    case "$s" in
      small)  echo "1 $s" ;;
      medium) echo "2 $s" ;;
      large)  echo "3 $s" ;;
      *)      echo "4 $s" ;;
    esac
  done | sort | sed 's/^[0-9] //'))
  unset IFS

  cat > "$COMBINED" <<'HEADER'
# Kafka vs NATS — Consolidated Benchmark Report

This report combines results from all scenario sizes into a single document.

## Table of Contents

HEADER

  # Build TOC
  for scenario in "${SORTED[@]}"; do
    anchor=$(echo "$scenario" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
    echo "- [Scenario: ${scenario^^}](#scenario-${anchor})" >> "$COMBINED"
  done
  echo "" >> "$COMBINED"
  echo "---" >> "$COMBINED"
  echo "" >> "$COMBINED"

  # Append each per-scenario report
  for scenario in "${SORTED[@]}"; do
    local_report="$PROJECT_ROOT/results/$scenario/benchmark_report.md"
    if [[ -f "$local_report" ]]; then
      # Rewrite relative chart paths to point to the scenario subdirectory
      sed "s|(charts/|(${scenario}/charts/|g" "$local_report" >> "$COMBINED"
      echo "" >> "$COMBINED"
      echo "---" >> "$COMBINED"
      echo "" >> "$COMBINED"
    fi
  done

  echo "*Consolidated report generated: $(date -Iseconds)*" >> "$COMBINED"
  echo "  Done: $COMBINED"
fi

echo ""
echo "Complete."
