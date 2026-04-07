#!/usr/bin/env python3
"""Latency helpers — embed monotonic timestamp in payload, extract on consumer side.

Works because producer and consumer share the same host clock (Docker on localhost).
"""

import struct
import time


def stamp_payload(size=1024):
    """Create a payload with a monotonic_ns timestamp in the first 8 bytes."""
    ts = time.monotonic_ns()
    return struct.pack("!Q", ts) + b"x" * (size - 8)


def extract_latency_us(data):
    """Extract the embedded timestamp and return latency in microseconds."""
    ts = struct.unpack("!Q", data[:8])[0]
    return (time.monotonic_ns() - ts) / 1000.0  # microseconds
