![Knack](../../assets/knack-logo-sm.png)

# Knack Benchmark Report: MEDIUM

| Field | Value |
|-------|-------|
| **Scenario** | medium |
| **Run timestamp** | 2026-04-08 17:50:07 |
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
| CPU | 1.22% | 0.03% |
| Memory | 361.8MiB / 4GiB | 8.828MiB / 4GiB |
| Memory % | 8.83% | 0.22% |

---

## 2. Startup & Recovery

> **What this measures:** Time for the broker to become ready from a cold start, and time to recover after a SIGKILL (simulating a crash). Lower is better — faster recovery means shorter outage windows.

![Startup & Recovery](charts/02_startup_recovery.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Cold start | 1,457 ms | 1,658 ms |
| SIGKILL recovery | 872 ms | 1,125 ms |

---

## 3. Producer Throughput (Python Client)

> **What this measures:** Maximum producer message rate using the Python client library (confluent-kafka for Kafka, nats-py for NATS). Runs multiple producer threads at full speed for a fixed duration. Higher is better, but note this reflects **client library performance** — Python GIL and async overhead affect results. See §3b for raw broker capacity.

![Throughput](charts/03_throughput.png)

| Run | Kafka (msg/s) | NATS (msg/s) |
|-----|---------------|--------------|
| 1 | 75,376.1 | 10,071.0 |
| 2 | 73,793.0 | 10,846.4 |
| 3 | 58,833.2 | 11,469.5 |
| **Median** | **73,793.0** | **10,846.4** |

---

## 3b. CLI-Native Throughput

> **What this measures:** Raw broker throughput using each broker's optimised CLI tools (kcat for Kafka, nats bench for NATS). These run **on the host** without Python overhead, giving the fairest broker-vs-broker comparison. Higher is better. The decision engine uses these numbers (not Python client numbers) for the throughput comparison.

![CLI Throughput](charts/03b_cli_throughput.png)

| Test | Kafka (msg/s) | NATS (msg/s) |
|------|---------------|--------------|
| Producer | 32,594.5 | 53,325.0 |
| Consumer | 40,883.1 | 44,678.0 |
| ProdCon (pub) | 21,097.0 | 18,849.0 |
| ProdCon (sub) | 9,311.0 | 10,707.0 |

---

## 4. Latency Under Load

> **What this measures:** End-to-end publish-to-receive latency at 50% of peak throughput. Percentiles (p50, p95, p99, p99.9) show how latency distributes under realistic load. Lower is better — p99 is critical for SLA compliance.

![Latency](charts/04_latency.png)

| Percentile | Kafka | NATS |
|------------|-------|------|
| p50 | 3.46 s | 2.4 ms |
| p95 | 5.35 s | 4.1 ms |
| p99 | 6.52 s | 5.4 ms |
| p99.9 | 7.11 s | 9.6 ms |
| max | 7.19 s | 21.5 ms |

---

## 5. Memory Stress

> **What this measures:** Broker stability under progressively restricted Docker memory limits (4g → 512m). For each level the producer benchmark runs and records throughput + errors. PASS = broker stays alive and serves traffic. "Errors" are **produce() call failures** — typically BufferError (producer queue overflow) or broker rejections when memory pressure causes slow message acceptance. NATS shows 0 errors because its backpressure design rejects at the protocol level rather than partially accepting.

![Memory Stress](charts/05_memory_stress.png)

| RAM Level | Kafka | NATS |
|-----------|-------|------|
| 4g | PASS (79,443 msg/s, 186 errs) | PASS (10,490 msg/s, 0 errs) |
| 2g | PASS (81,894 msg/s, 106 errs) | PASS (10,668 msg/s, 0 errs) |
| 1g | PASS (85,531 msg/s, 132 errs) | PASS (9,744 msg/s, 0 errs) |
| 512m | PASS (70,998 msg/s, 425 errs) | PASS (9,724 msg/s, 0 errs) |
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
| 1 | 29,262.0 | 16,644.6 |
| 2 | 36,686.2 | 16,399.9 |
| 3 | 49,696.4 | 17,028.5 |
| **Median** | **36,686.2** | **16,644.6** |

---

## 8. Simultaneous Producer + Consumer

> **What this measures:** Throughput when producers and consumers run concurrently (the realistic scenario). Shows whether the broker can handle bidirectional traffic without degradation. The P/C ratio reveals backpressure behaviour. "Producer errors" = failed produce() calls (queue buffer overflow or broker rejection under load).

![ProdCon](charts/08_prodcon.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Producer rate (msg/s) | 47,153.0 | 6,774.9 |
| Consumer rate (msg/s) | 35,617.1 | 4,124.6 |
| Producer errors | 9,639 | 0 |
| Duration (s) | 611.6 | 600.1 |

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
| 4.0 | 81,086.1 | 32,084.2 | 238.2 | 828.2 | PASS |
| 3.0 | 67,774.3 | 44,037.3 | 203.5 | 754.1 | PASS |
| 2.0 | 76,069.4 | 37,070.0 | 196.0 | 876.2 | PASS |
| 1.5 | 69,996.2 | 40,293.3 | 157.1 | 896.2 | PASS |
| 1.0 | 36,643.4 | 29,463.8 | 106.0 | 837.7 | PASS |
| 0.5 | 34,186.4 | 19,213.8 | 52.7 | 795.3 | PASS |

### NATS

| CPU Limit | Python (msg/s) | CLI (msg/s) | Peak CPU % | Peak Mem (MB) | Status |
|-----------|---------------|-------------|------------|---------------|--------|
| 4.0 | 10,024.1 | 43,668.0 | 131.9 | 98.3 | PASS |
| 3.0 | 10,532.0 | 57,145.0 | 122.8 | 100.8 | PASS |
| 2.0 | 9,864.3 | 55,141.0 | 115.4 | 95.8 | PASS |
| 1.5 | 10,734.5 | 50,581.0 | 107.1 | 99.3 | PASS |
| 1.0 | 10,281.5 | 45,693.0 | 100.5 | 98.2 | PASS |
| 0.5 | 10,024.2 | 24,895.0 | 51.7 | 101.8 | PASS |

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
| Producer | KAFKA | 4 | 18,459 | 164 | 0.9 |
| Producer | NATS | 4 | 2,712 | 0 | 0.0 |
| Consumer | KAFKA | 4 | 11,704 | 2,821 | 24.1 |
| Consumer | NATS | 4 | 4,167 | 5 | 0.1 |
| ProdCon (prod) | KAFKA | 4 | 11,847 | 8,993 | 75.9 |
| ProdCon (prod) | NATS | 4 | 1,694 | 4 | 0.3 |

---

## 14. Error Rate Breakdown

> **What this shows:** Total produce() failures across all tests. Kafka errors are typically BufferError (producer queue full) or broker delivery failures under load/memory pressure. NATS uses protocol-level backpressure, so the Python client sees 0 exceptions. Lower is better.

![Error Breakdown](charts/14_error_breakdown.png)

| Test | Kafka Errors | NATS Errors |
|------|-------------|-------------|
| Throughput (all runs) | 5,573 | 0 |
| Consumer (all runs) | 0 | 0 |
| ProdCon | 9,639 | 0 |
| Mem 4g | 186 | 0 |
| Mem 2g | 106 | 0 |
| Mem 1g | 132 | 0 |
| Mem 512m | 425 | 0 |

---

## 15. Throughput Stability Across Repetitions

> **What this measures:** Consistency of throughput across 3 repeated runs. CV% (coefficient of variation) → lower is better. High CV% means results vary significantly between runs, suggesting the broker's performance is sensitive to transient conditions.

![Throughput Stability](charts/15_throughput_stability.png)

| Test | Broker | Run 1 | Run 2 | Run 3 | Mean | StdDev | CV% |
|------|--------|-------|-------|-------|------|--------|-----|
| Producer | KAFKA | 75,376 | 73,793 | 58,833 | 69,334 | 9,128 | 13.2 |
| Producer | NATS | 10,071 | 10,846 | 11,470 | 10,796 | 701 | 6.5 |
| Consumer | KAFKA | 29,262 | 36,686 | 49,696 | 38,548 | 10,344 | 26.8 |
| Consumer | NATS | 16,645 | 16,400 | 17,028 | 16,691 | 317 | 1.9 |

---

## 16. Producer / Consumer Balance Ratio

> **What this measures:** The ratio of producer-to-consumer throughput during simultaneous operation. A ratio near 1.0x means balanced; >1.0x means the producer outpaces the consumer (messages queue up = backpressure risk).

![ProdCon Balance](charts/16_prodcon_balance.png)

| Broker | Producer (msg/s) | Consumer (msg/s) | Ratio (P/C) | Interpretation |
|--------|-----------------|-----------------|-------------|----------------|
| KAFKA | 47,153 | 35,617 | 1.32x | Producer faster (backpressure) |
| NATS | 6,775 | 4,125 | 1.64x | Producer faster (backpressure) |

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
| 4.0 | 20,272 | 2,506 | 100% | 100% |
| 3.0 | 22,591 | 3,511 | 111% | 140% |
| 2.0 | 38,035 | 4,932 | 188% | 197% |
| 1.5 | 46,664 | 7,156 | 230% | 286% |
| 1.0 | 36,643 | 10,282 | 181% | 410% |
| 0.5 | 68,373 | 20,048 | 337% | 800% |

---

## 20. Latency Measurement Context

> **What this shows:** The conditions under which latency was measured — load percentage, target rate, number of samples, and the full percentile breakdown in microseconds. This context is critical for interpreting the latency numbers in §4.

![Latency Context](charts/20_latency_context.png)

| Metric | Kafka | NATS |
|--------|-------|------|
| Load % | 50% | 50% |
| Target Rate (msg/s) | 37,688 | 5,035 |
| Samples | 3,090,390 | 130,910 |
| Messages Sent | 3,165,792 | 130,910 |
| p50 (µs) | 3,459,028 | 2,404 |
| p95 (µs) | 5,354,016 | 4,067 |
| p99 (µs) | 6,522,615 | 5,364 |
| p99.9 (µs) | 7,112,984 | 9,609 |
| Max (µs) | 7,189,762 | 21,505 |

---

## Decision

**Recommendation: MIGRATE_TO_NATS**

### Reasoning

- Kafka min viable RAM: 512m, NATS min viable RAM: 512m
- Kafka p99=6,522,614.8us, NATS p99=5,364.3us (ratio=1215.93x)
- CLI Throughput — Kafka: 32,594.5 msg/s, NATS: 53,325.0 msg/s (ratio=0.61x)
- Python Client Throughput — Kafka: 73,793.0 msg/s, NATS: 10,846.4 msg/s (reflects client library design, not broker capacity)
- Consumer Throughput — Kafka: 36,686.2 msg/s, NATS: 16,644.6 msg/s (ratio=2.20x)
- ProdCon — Kafka: P=47,153.0/C=35,617.1 msg/s, NATS: P=6,774.9/C=4,124.6 msg/s
- NATS wins both throughput and latency

---

*Report generated: 2026-04-08T19:49:42-04:00*
