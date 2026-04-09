![Knack](../../assets/knack-logo-sm.png)

# Knack Benchmark Report: LARGE

| Field | Value |
|-------|-------|
| **Scenario** | large |
| **Run timestamp** | 2026-04-08 19:14:19 |
| **CPUs** | 2.0 |
| **Memory** | 4g |
| **Payload** | 1024 bytes |
| **Producers** | 4 |
| **Consumers** | 4 |

---

## 1. Idle Footprint

> **What this measures:** CPU and memory consumed by each broker when idle (no producers/consumers active). Lower is better — a lighter idle footprint means less wasted resources when your system is between load spikes.

![Idle Footprint](charts/01_idle_footprint.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| CPU | 0.91% | 0.05% |
| Memory | 347.7MiB / 7.761GiB | 5.395MiB / 7.761GiB |
| Memory % | 4.38% | 0.07% |

---

## 2. Startup & Recovery

> **What this measures:** Time for the broker to become ready from a cold start, and time to recover after a SIGKILL (simulating a crash). Lower is better — faster recovery means shorter outage windows.

![Startup & Recovery](charts/02_startup_recovery.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Cold start | 1,437 ms | 4,335 ms |
| SIGKILL recovery | 888 ms | 1,088 ms |

---

## 3. Producer Throughput (Python Client)

> **What this measures:** Maximum producer message rate using the Python client library (confluent-kafka for Kafka, nats-py for NATS). Runs multiple producer threads at full speed for a fixed duration. Higher is better, but note this reflects **client library performance** — Python GIL and async overhead affect results. See §3b for raw broker capacity.

![Throughput](charts/03_throughput.png)

| Run | Kafka (msg/s) | NATS (msg/s) |
|-----|---------------|--------------|
| 1 | 83,002.1 | 10,541.8 |
| 2 | 80,792.0 | 10,124.6 |
| 3 | 79,437.5 | 11,965.9 |
| **Median** | **80,792.0** | **10,541.8** |

---

## 3b. CLI-Native Throughput

> **What this measures:** Raw broker throughput using each broker's optimised CLI tools (kcat for Kafka, nats bench for NATS). These run **on the host** without Python overhead, giving the fairest broker-vs-broker comparison. Higher is better. The decision engine uses these numbers (not Python client numbers) for the throughput comparison.

![CLI Throughput](charts/03b_cli_throughput.png)

| Test | Kafka (msg/s) | NATS (msg/s) |
|------|---------------|--------------|
| Producer | 37,764.4 | 48,609.0 |
| Consumer | 45,126.4 | 55,751.0 |
| ProdCon (pub) | 39,093.0 | 15,784.0 |
| ProdCon (sub) | 11,685.0 | 9,648.0 |

---

## 4. Latency Under Load

> **What this measures:** End-to-end publish-to-receive latency at 50% of peak throughput. Percentiles (p50, p95, p99, p99.9) show how latency distributes under realistic load. Lower is better — p99 is critical for SLA compliance.

![Latency](charts/04_latency.png)

| Percentile | Kafka | NATS |
|------------|-------|------|
| p50 | 2.44 s | 2.4 ms |
| p95 | 4.88 s | 4.0 ms |
| p99 | 6.68 s | 5.2 ms |
| p99.9 | 7.93 s | 9.4 ms |
| max | 8.02 s | 29.4 ms |

---

## 5. Memory Stress

> **What this measures:** Broker stability under progressively restricted Docker memory limits (4g → 512m). For each level the producer benchmark runs and records throughput + errors. PASS = broker stays alive and serves traffic. "Errors" are **produce() call failures** — typically BufferError (producer queue overflow) or broker rejections when memory pressure causes slow message acceptance. NATS shows 0 errors because its backpressure design rejects at the protocol level rather than partially accepting.

![Memory Stress](charts/05_memory_stress.png)

| RAM Level | Kafka | NATS |
|-----------|-------|------|
| 4g | PASS (84,000 msg/s, 163 errs) | PASS (10,210 msg/s, 0 errs) |
| 2g | PASS (79,005 msg/s, 278 errs) | PASS (10,711 msg/s, 0 errs) |
| 1g | PASS (83,075 msg/s, 220 errs) | PASS (10,619 msg/s, 0 errs) |
| 512m | PASS (73,540 msg/s, 379 errs) | PASS (10,072 msg/s, 0 errs) |
| **Min viable** | **512m** | **512m** |

---

## 6. Scorecard

> **What this shows:** A radar/summary chart comparing both brokers across all dimensions (throughput, latency, memory, CPU, errors, stability). Provides an at-a-glance visual comparison.

![Scorecard](charts/06_scorecard.png)

---

## 7. Consumer Throughput

> **What this measures:** Maximum consumer-side message rate using the Python client library. Multiple consumer workers pull messages as fast as possible. Higher is better.

![Consumer Throughput](charts/07_consumer_throughput.png)

| Run | Kafka (msg/s) | NATS (msg/s) |
|-----|---------------|--------------|
| 1 | 38,417.4 | 15,527.4 |
| 2 | 38,192.7 | 15,460.4 |
| 3 | 38,719.0 | 16,437.5 |
| **Median** | **38,417.4** | **15,527.4** |

---

## 8. Simultaneous Producer + Consumer

> **What this measures:** Throughput when producers and consumers run concurrently (the realistic scenario). Shows whether the broker can handle bidirectional traffic without degradation. The P/C ratio reveals backpressure behaviour. "Producer errors" = failed produce() calls (queue buffer overflow or broker rejection under load).

![ProdCon](charts/08_prodcon.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Producer rate (msg/s) | 60,227.6 | 6,998.2 |
| Consumer rate (msg/s) | 21,610.4 | 4,520.1 |
| Producer errors | 4,048 | 0 |
| Duration (s) | 612.6 | 600.1 |

---

## 9. Resource Timeline

> **What this shows:** CPU% and memory over time during the full benchmark run, sampled from docker stats. Reveals resource spikes, steady-state usage, and whether the broker releases resources between tests.

![Resource Timeline](charts/09_resource_timeline.png)

*Data source: docker_stats.csv — continuous sampling during benchmark run.*

---

## 10. Resource Scaling

> **What this measures:** How throughput degrades as CPU cores are progressively restricted (4.0 → 0.5 cores). The "knee" where throughput drops sharply reveals each broker's minimum viable CPU. Flat slope = not CPU-bound; steep drop = CPU bottleneck.

![Resource Scaling](charts/10_resource_scaling.png)

### Kafka

| CPU Limit | Python (msg/s) | CLI (msg/s) | Peak CPU % | Peak Mem (MB) | Status |
|-----------|---------------|-------------|------------|---------------|--------|
| 4.0 | 72,186.4 | 40,151.0 | 217.2 | 791.9 | PASS |
| 3.0 | 73,073.0 | 36,017.9 | 196.1 | 848.2 | PASS |
| 2.0 | 82,792.4 | 45,466.9 | 192.2 | 867.4 | PASS |
| 1.5 | 74,360.2 | 28,846.7 | 155.3 | 772.5 | PASS |
| 1.0 | 63,929.2 | 36,440.5 | 103.7 | 741.7 | PASS |
| 0.5 | 38,847.9 | 27,810.2 | 54.3 | 814.3 | PASS |

### NATS

| CPU Limit | Python (msg/s) | CLI (msg/s) | Peak CPU % | Peak Mem (MB) | Status |
|-----------|---------------|-------------|------------|---------------|--------|
| 4.0 | 10,664.6 | 52,255.0 | 121.9 | 94.9 | PASS |
| 3.0 | 9,862.6 | 47,391.0 | 127.2 | 102.6 | PASS |
| 2.0 | 10,667.0 | 53,290.0 | 122.8 | 114.9 | PASS |
| 1.5 | 10,314.0 | 54,694.0 | 118.3 | 96.7 | PASS |
| 1.0 | 10,987.6 | 46,512.0 | 98.4 | 92.8 | PASS |
| 0.5 | 11,150.9 | 26,100.0 | 50.7 | 103.4 | PASS |

---

## 11. Disk I/O Timeline

> **What this shows:** Block I/O (reads + writes) over time from docker stats. Kafka relies heavily on disk for its commit log; NATS JetStream also persists to disk. Higher disk I/O can indicate storage bottlenecks.

![Disk I/O Timeline](charts/11_disk_io_timeline.png)

*Data source: docker_stats.csv block_io column.*

---

## 12. Throughput vs Resources

> **What this shows:** Scatter/correlation plot of throughput against CPU and memory usage. Reveals whether higher resource consumption actually translates to higher throughput (efficiency).

![Throughput vs Resources](charts/12_throughput_vs_resources.png)

---

## 13. Worker Load Balance

> **What this measures:** How evenly work is distributed across producer/consumer workers. CV% (coefficient of variation) → lower is better. High CV% means some workers are doing much more work than others, indicating partition imbalance or contention.

![Worker Balance](charts/13_worker_balance.png)

| Test | Broker | Workers | Mean (msg/s) | StdDev | CV% |
|------|--------|---------|-------------|--------|-----|
| Producer | KAFKA | 4 | 20,259 | 5,342 | 26.4 |
| Producer | NATS | 4 | 2,636 | 0 | 0.0 |
| Consumer | KAFKA | 4 | 12,698 | 4,872 | 38.4 |
| Consumer | NATS | 4 | 3,885 | 4 | 0.1 |
| ProdCon (prod) | KAFKA | 4 | 15,058 | 5,162 | 34.3 |
| ProdCon (prod) | NATS | 4 | 1,750 | 6 | 0.3 |

---

## 14. Error Rate Breakdown

> **What this shows:** Total produce() failures across all tests. Kafka errors are typically BufferError (producer queue full) or broker delivery failures under load/memory pressure. NATS uses protocol-level backpressure, so the Python client sees 0 exceptions. Lower is better.

![Error Breakdown](charts/14_error_breakdown.png)

| Test | Kafka Errors | NATS Errors |
|------|-------------|-------------|
| Throughput (all runs) | 2,144 | 0 |
| Consumer (all runs) | 0 | 0 |
| ProdCon | 4,048 | 0 |
| Mem 4g | 163 | 0 |
| Mem 2g | 278 | 0 |
| Mem 1g | 220 | 0 |
| Mem 512m | 379 | 0 |

---

## 15. Throughput Stability Across Repetitions

> **What this measures:** Consistency of throughput across 3 repeated runs. CV% (coefficient of variation) → lower is better. High CV% means results vary significantly between runs, suggesting the broker's performance is sensitive to transient conditions.

![Throughput Stability](charts/15_throughput_stability.png)

| Test | Broker | Run 1 | Run 2 | Run 3 | Mean | StdDev | CV% |
|------|--------|-------|-------|-------|------|--------|-----|
| Producer | KAFKA | 83,002 | 80,792 | 79,438 | 81,077 | 1,799 | 2.2 |
| Producer | NATS | 10,542 | 10,125 | 11,966 | 10,877 | 965 | 8.9 |
| Consumer | KAFKA | 38,417 | 38,193 | 38,719 | 38,443 | 264 | 0.7 |
| Consumer | NATS | 15,527 | 15,460 | 16,438 | 15,808 | 546 | 3.5 |

---

## 16. Producer / Consumer Balance Ratio

> **What this measures:** The ratio of producer-to-consumer throughput during simultaneous operation. A ratio near 1.0x means balanced; >1.0x means the producer outpaces the consumer (messages queue up = backpressure risk).

![ProdCon Balance](charts/16_prodcon_balance.png)

| Broker | Producer (msg/s) | Consumer (msg/s) | Ratio (P/C) | Interpretation |
|--------|-----------------|-----------------|-------------|----------------|
| KAFKA | 60,228 | 21,610 | 2.79x | Producer faster (backpressure) |
| NATS | 6,998 | 4,520 | 1.55x | Producer faster (backpressure) |

---

## 17. Network I/O Timeline

> **What this shows:** Network bytes in/out over time from docker stats. Reveals network bandwidth consumption and whether either broker saturates the network interface.

![Network I/O](charts/17_network_io_timeline.png)

*Data source: docker_stats.csv net_io column.*

---

## 18. Memory Headroom

> **What this shows:** Peak memory usage as a percentage of the container's memory limit over time. Warning at 80%, critical at 95%. Low headroom means the broker is at risk of OOM-kill under load spikes.

![Memory Headroom](charts/18_memory_headroom.png)

*Data source: docker_stats.csv mem_pct column. Warning threshold: 80%. Critical threshold: 95%.*

---

## 19. Scaling Efficiency — Throughput per CPU Core

> **What this measures:** Throughput normalized per CPU core at each CPU limit. Efficiency >100% means the broker gets more throughput per core when constrained (better utilization). Shows how efficiently each broker uses additional CPU resources.

![Scaling Efficiency](charts/19_scaling_efficiency.png)

| CPU Limit | Kafka (msg/s/core) | NATS (msg/s/core) | Kafka Eff% | NATS Eff% |
|-----------|-------------------|-------------------|-----------|----------|
| 4.0 | 18,047 | 2,666 | 100% | 100% |
| 3.0 | 24,358 | 3,288 | 135% | 123% |
| 2.0 | 41,396 | 5,334 | 229% | 200% |
| 1.5 | 49,573 | 6,876 | 275% | 258% |
| 1.0 | 63,929 | 10,988 | 354% | 412% |
| 0.5 | 77,696 | 22,302 | 431% | 836% |

---

## 20. Latency Measurement Context

> **What this shows:** The conditions under which latency was measured — load percentage, target rate, number of samples, and the full percentile breakdown in microseconds. This context is critical for interpreting the latency numbers in §4.

![Latency Context](charts/20_latency_context.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Load % | 50% | 50% |
| Target Rate (msg/s) | 41,501 | 5,270 |
| Samples | 4,357,067 | 131,750 |
| Messages Sent | 4,440,607 | 131,750 |
| p50 (µs) | 2,436,871 | 2,368 |
| p95 (µs) | 4,875,582 | 3,997 |
| p99 (µs) | 6,682,653 | 5,238 |
| p99.9 (µs) | 7,930,758 | 9,366 |
| Max (µs) | 8,017,234 | 29,351 |

---

## Decision

**Recommendation: MIGRATE_TO_NATS**

### Reasoning

- Kafka min viable RAM: 512m, NATS min viable RAM: 512m
- Kafka p99=6,682,653.3us, NATS p99=5,238.1us (ratio=1275.78x)
- CLI Throughput — Kafka: 37,764.4 msg/s, NATS: 48,609.0 msg/s (ratio=0.78x)
- Python Client Throughput — Kafka: 80,792.0 msg/s, NATS: 10,541.8 msg/s (reflects client library design, not broker capacity)
- Consumer Throughput — Kafka: 38,417.4 msg/s, NATS: 15,527.4 msg/s (ratio=2.47x)
- ProdCon — Kafka: P=60,227.6/C=21,610.4 msg/s, NATS: P=6,998.2/C=4,520.1 msg/s
- NATS wins latency; throughput is comparable

---

*Report generated: 2026-04-08T19:49:41-04:00*
