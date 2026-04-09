![Knack](../../assets/knack-logo-sm.png)

# Knack Benchmark Report: SMALL

| Field | Value |
|-------|-------|
| **Scenario** | small |
| **Run timestamp** | 2026-04-08 17:14:24 |
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
| CPU | 37.34% | 0.04% |
| Memory | 348.6MiB / 2GiB | 8.977MiB / 2GiB |
| Memory % | 17.02% | 0.44% |

---

## 2. Startup & Recovery

> **What this measures:** Time for the broker to become ready from a cold start, and time to recover after a SIGKILL (simulating a crash). Lower is better — faster recovery means shorter outage windows.

![Startup & Recovery](charts/02_startup_recovery.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Cold start | 1,540 ms | 1,784 ms |
| SIGKILL recovery | 945 ms | 1,671 ms |

---

## 3. Producer Throughput (Python Client)

> **What this measures:** Maximum producer message rate using the Python client library (confluent-kafka for Kafka, nats-py for NATS). Runs multiple producer threads at full speed for a fixed duration. Higher is better, but note this reflects **client library performance** — Python GIL and async overhead affect results. See §3b for raw broker capacity.

![Throughput](charts/03_throughput.png)

| Run | Kafka (msg/s) | NATS (msg/s) |
|-----|---------------|--------------|
| 1 | 75,896.2 | 10,579.4 |
| 2 | 65,017.5 | 11,705.1 |
| 3 | 66,301.6 | 11,656.2 |
| **Median** | **66,301.6** | **11,656.2** |

---

## 3b. CLI-Native Throughput

> **What this measures:** Raw broker throughput using each broker's optimised CLI tools (kcat for Kafka, nats bench for NATS). These run **on the host** without Python overhead, giving the fairest broker-vs-broker comparison. Higher is better. The decision engine uses these numbers (not Python client numbers) for the throughput comparison.

![CLI Throughput](charts/03b_cli_throughput.png)

| Test | Kafka (msg/s) | NATS (msg/s) |
|------|---------------|--------------|
| Producer | 44,523.6 | 66,522.0 |
| Consumer | 27,917.4 | 50,218.0 |
| ProdCon (pub) | 41,562.8 | 24,940.0 |
| ProdCon (sub) | 11,896.3 | 12,475.0 |

---

## 4. Latency Under Load

> **What this measures:** End-to-end publish-to-receive latency at 50% of peak throughput. Percentiles (p50, p95, p99, p99.9) show how latency distributes under realistic load. Lower is better — p99 is critical for SLA compliance.

![Latency](charts/04_latency.png)

| Percentile | Kafka | NATS |
|------------|-------|------|
| p50 | 2.44 s | 2.3 ms |
| p95 | 5.22 s | 4.2 ms |
| p99 | 6.71 s | 5.6 ms |
| p99.9 | 11.28 s | 10.5 ms |
| max | 11.35 s | 22.4 ms |

---

## 5. Memory Stress

> **What this measures:** Broker stability under progressively restricted Docker memory limits (4g → 512m). For each level the producer benchmark runs and records throughput + errors. PASS = broker stays alive and serves traffic. "Errors" are **produce() call failures** — typically BufferError (producer queue overflow) or broker rejections when memory pressure causes slow message acceptance. NATS shows 0 errors because its backpressure design rejects at the protocol level rather than partially accepting.

![Memory Stress](charts/05_memory_stress.png)

| RAM Level | Kafka | NATS |
|-----------|-------|------|
| 4g | PASS (69,654 msg/s, 480 errs) | PASS (9,676 msg/s, 0 errs) |
| 2g | PASS (66,519 msg/s, 596 errs) | PASS (9,371 msg/s, 0 errs) |
| 1g | PASS (76,429 msg/s, 488 errs) | PASS (10,416 msg/s, 0 errs) |
| 512m | PASS (65,563 msg/s, 718 errs) | PASS (10,880 msg/s, 0 errs) |
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
| 1 | 36,821.7 | 14,971.7 |
| 2 | 36,292.4 | 14,817.0 |
| 3 | 39,307.6 | 13,795.0 |
| **Median** | **36,821.7** | **14,817.0** |

---

## 8. Simultaneous Producer + Consumer

> **What this measures:** Throughput when producers and consumers run concurrently (the realistic scenario). Shows whether the broker can handle bidirectional traffic without degradation. The P/C ratio reveals backpressure behaviour. "Producer errors" = failed produce() calls (queue buffer overflow or broker rejection under load).

![ProdCon](charts/08_prodcon.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Producer rate (msg/s) | 43,461.2 | 7,476.2 |
| Consumer rate (msg/s) | 31,397.4 | 4,879.9 |
| Producer errors | 7,853 | 0 |
| Duration (s) | 611.0 | 600.0 |

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
| 4.0 | 73,493.0 | 46,702.8 | 220.1 | 867.2 | PASS |
| 3.0 | 65,673.8 | 48,164.9 | 211.6 | 793.1 | PASS |
| 2.0 | 72,077.8 | 32,354.1 | 189.2 | 744.4 | PASS |
| 1.5 | 58,862.7 | 43,192.8 | 148.0 | 807.9 | PASS |
| 1.0 | 60,122.9 | 32,864.5 | 104.7 | 753.9 | PASS |
| 0.5 | 36,805.7 | 19,727.0 | 54.3 | 773.0 | PASS |

### NATS

| CPU Limit | Python (msg/s) | CLI (msg/s) | Peak CPU % | Peak Mem (MB) | Status |
|-----------|---------------|-------------|------------|---------------|--------|
| 4.0 | 10,399.8 | 52,820.0 | 106.4 | 92.0 | PASS |
| 3.0 | 9,483.8 | 40,316.0 | 120.0 | 105.4 | PASS |
| 2.0 | 10,560.5 | 44,759.0 | 123.3 | 97.5 | PASS |
| 1.5 | 10,404.7 | 53,958.0 | 103.6 | 94.6 | PASS |
| 1.0 | 10,364.6 | 42,592.0 | 97.9 | 97.4 | PASS |
| 0.5 | 10,744.6 | 27,205.0 | 50.3 | 99.7 | PASS |

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
| Producer | KAFKA | 4 | 16,578 | 1,380 | 8.3 |
| Producer | NATS | 4 | 2,914 | 0 | 0.0 |
| Consumer | KAFKA | 4 | 11,528 | 3,279 | 28.4 |
| Consumer | NATS | 4 | 3,711 | 7 | 0.2 |
| ProdCon (prod) | KAFKA | 4 | 10,898 | 3,625 | 33.3 |
| ProdCon (prod) | NATS | 4 | 1,869 | 8 | 0.4 |

---

## 14. Error Rate Breakdown

> **What this shows:** Total produce() failures across all tests. Kafka errors are typically BufferError (producer queue full) or broker delivery failures under load/memory pressure. NATS uses protocol-level backpressure, so the Python client sees 0 exceptions. Lower is better.

![Error Breakdown](charts/14_error_breakdown.png)

| Test | Kafka Errors | NATS Errors |
|------|-------------|-------------|
| Throughput (all runs) | 6,978 | 0 |
| Consumer (all runs) | 0 | 0 |
| ProdCon | 7,853 | 0 |
| Mem 4g | 480 | 0 |
| Mem 2g | 596 | 0 |
| Mem 1g | 488 | 0 |
| Mem 512m | 718 | 0 |

---

## 15. Throughput Stability Across Repetitions

> **What this measures:** Consistency of throughput across 3 repeated runs. CV% (coefficient of variation) → lower is better. High CV% means results vary significantly between runs, suggesting the broker's performance is sensitive to transient conditions.

![Throughput Stability](charts/15_throughput_stability.png)

| Test | Broker | Run 1 | Run 2 | Run 3 | Mean | StdDev | CV% |
|------|--------|-------|-------|-------|------|--------|-----|
| Producer | KAFKA | 75,896 | 65,018 | 66,302 | 69,072 | 5,945 | 8.6 |
| Producer | NATS | 10,579 | 11,705 | 11,656 | 11,314 | 636 | 5.6 |
| Consumer | KAFKA | 36,822 | 36,292 | 39,308 | 37,474 | 1,610 | 4.3 |
| Consumer | NATS | 14,972 | 14,817 | 13,795 | 14,528 | 639 | 4.4 |

---

## 16. Producer / Consumer Balance Ratio

> **What this measures:** The ratio of producer-to-consumer throughput during simultaneous operation. A ratio near 1.0x means balanced; >1.0x means the producer outpaces the consumer (messages queue up = backpressure risk).

![ProdCon Balance](charts/16_prodcon_balance.png)

| Broker | Producer (msg/s) | Consumer (msg/s) | Ratio (P/C) | Interpretation |
|--------|-----------------|-----------------|-------------|----------------|
| KAFKA | 43,461 | 31,397 | 1.38x | Producer faster (backpressure) |
| NATS | 7,476 | 4,880 | 1.53x | Producer faster (backpressure) |

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
| 4.0 | 18,373 | 2,600 | 100% | 100% |
| 3.0 | 21,891 | 3,161 | 119% | 122% |
| 2.0 | 36,039 | 5,280 | 196% | 203% |
| 1.5 | 39,242 | 6,936 | 214% | 267% |
| 1.0 | 60,123 | 10,365 | 327% | 399% |
| 0.5 | 73,611 | 21,489 | 401% | 827% |

---

## 20. Latency Measurement Context

> **What this shows:** The conditions under which latency was measured — load percentage, target rate, number of samples, and the full percentile breakdown in microseconds. This context is critical for interpreting the latency numbers in §4.

![Latency Context](charts/20_latency_context.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Load % | 50% | 50% |
| Target Rate (msg/s) | 37,948 | 5,289 |
| Samples | 3,755,667 | 132,225 |
| Messages Sent | 3,794,800 | 132,225 |
| p50 (µs) | 2,439,465 | 2,309 |
| p95 (µs) | 5,223,649 | 4,187 |
| p99 (µs) | 6,710,121 | 5,603 |
| p99.9 (µs) | 11,280,834 | 10,531 |
| Max (µs) | 11,349,196 | 22,408 |

---

## Decision

**Recommendation: MIGRATE_TO_NATS**

### Reasoning

- Kafka min viable RAM: 512m, NATS min viable RAM: 512m
- Kafka p99=6,710,120.7us, NATS p99=5,602.9us (ratio=1197.62x)
- CLI Throughput — Kafka: 44,523.6 msg/s, NATS: 66,522.0 msg/s (ratio=0.67x)
- Python Client Throughput — Kafka: 66,301.6 msg/s, NATS: 11,656.2 msg/s (reflects client library design, not broker capacity)
- Consumer Throughput — Kafka: 36,821.7 msg/s, NATS: 14,817.0 msg/s (ratio=2.49x)
- ProdCon — Kafka: P=43,461.2/C=31,397.4 msg/s, NATS: P=7,476.2/C=4,879.9 msg/s
- NATS wins both throughput and latency

---

*Report generated: 2026-04-08T19:49:44-04:00*
