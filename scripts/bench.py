"""
scripts/bench.py — Benchmark SLM latency and token usage.
Runs N live calls against Groq if key is present, or logs 'not yet measured'.
Writes measured data to docs/latency.json.
"""

import os
import sys
import json
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from config import GROQ_API_KEY, SLM_MODEL
from slm import _call_groq, _build_user_message
from schemas import RefRegistry

def run_bench(n=5):
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(docs_dir, exist_ok=True)
    out_file = os.path.join(docs_dir, "latency.json")

    if not GROQ_API_KEY or "your_groq_api_key" in GROQ_API_KEY:
        data = {
            "status": "not yet measured",
            "reason": "GROQ_API_KEY not configured for live bench testing.",
            "model": SLM_MODEL,
            "n_samples": 0
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print("Bench skipped: GROQ_API_KEY not set. Recorded 'not yet measured' in docs/latency.json")
        return

    print(f"Running live latency benchmark ({n} requests) against {SLM_MODEL}...")
    registry = RefRegistry()
    registry.register_text(1, "I have high fever and shivering since yesterday night.")
    msg = _build_user_message(
        [("I have high fever and shivering since yesterday night.", "text", "text:MSG_1")],
        registry=registry
    )

    latencies = []
    tokens_in = []
    tokens_out = []

    for i in range(n):
        try:
            _, meta = _call_groq(msg, attempt=1)
            latencies.append(meta["latency_ms"])
            tokens_in.append(meta["tokens_in"])
            tokens_out.append(meta["tokens_out"])
            print(f"  Call {i+1}: {meta['latency_ms']:.1f}ms, in: {meta['tokens_in']}, out: {meta['tokens_out']}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  Call {i+1} failed: {e}")

    if not latencies:
        print("Benchmark failed: all calls failed.")
        return

    latencies.sort()
    p50 = latencies[int(len(latencies) * 0.5)]
    p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]

    data = {
        "status": "measured",
        "model": SLM_MODEL,
        "n_samples": len(latencies),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "avg_tokens_in": round(sum(tokens_in) / len(tokens_in), 1),
        "avg_tokens_out": round(sum(tokens_out) / len(tokens_out), 1),
        "raw_latencies_ms": [round(l, 1) for l in latencies]
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Benchmark completed: p50={data['p50_ms']}ms, p95={data['p95_ms']}ms. Saved to docs/latency.json")

if __name__ == "__main__":
    run_bench()
