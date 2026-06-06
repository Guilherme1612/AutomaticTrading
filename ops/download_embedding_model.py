#!/usr/bin/env python3
"""Download and verify the BAAI/bge-base-en-v1.5 embedding model.

Architecture.md §8.7: 768-dim vectors for Qdrant collections.
Source.md §12.4.5: Embedding model setup during install.

Usage:
    python ops/download_embedding_model.py              # Download + verify
    python ops/download_embedding_model.py --verify-only # Verify existing
    python ops/download_embedding_model.py --json        # JSON output for CI
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

MODEL_NAME = "BAAI/bge-base-en-v1.5"
EXPECTED_DIM = 768
CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"


def verify_model() -> dict:
    """Verify the embedding model loads and produces correct output.

    Returns dict with status, dimension, latency_ms.
    """
    result = {
        "model": MODEL_NAME,
        "expected_dim": EXPECTED_DIM,
        "loaded": False,
        "dimension": None,
        "latency_ms": None,
        "error": None,
    }

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        result["error"] = "sentence-transformers not installed. Run: pip install sentence-transformers"
        return result

    try:
        t0 = time.perf_counter()
        model = SentenceTransformer(MODEL_NAME)
        load_ms = (time.perf_counter() - t0) * 1000

        # Test embedding
        t0 = time.perf_counter()
        embedding = model.encode("PMACS embedding verification test", normalize_embeddings=True)
        infer_ms = (time.perf_counter() - t0) * 1000

        dim = len(embedding)
        result["loaded"] = True
        result["dimension"] = dim
        result["load_latency_ms"] = round(load_ms, 1)
        result["inference_latency_ms"] = round(infer_ms, 1)
        result["dim_ok"] = dim == EXPECTED_DIM
        result["values_range"] = {
            "min": float(embedding.min()),
            "max": float(embedding.max()),
            "norm": float((embedding ** 2).sum() ** 0.5),
        }

    except Exception as exc:
        result["error"] = str(exc)

    return result


def download_model() -> dict:
    """Download the model by loading it (sentence-transformers auto-downloads).

    Returns the verification result after download.
    """
    print(f"Downloading {MODEL_NAME}...")
    result = verify_model()
    if result["loaded"]:
        print(f"Model ready: {result['dimension']}-dim, load in {result['load_latency_ms']}ms")
    else:
        print(f"Failed: {result['error']}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Download/verify embedding model")
    parser.add_argument("--verify-only", action="store_true", help="Only verify, don't download")
    parser.add_argument("--json", action="store_true", help="JSON output for CI")
    args = parser.parse_args()

    if args.verify_only:
        result = verify_model()
    else:
        result = download_model()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "PASS" if result.get("dim_ok") else "FAIL"
        print(f"\n[{status}] {MODEL_NAME}")
        if result["loaded"]:
            print(f"  Dimension: {result['dimension']} (expected {EXPECTED_DIM})")
            print(f"  Load time: {result['load_latency_ms']}ms")
            print(f"  Inference: {result['inference_latency_ms']}ms")
            print(f"  Norm: {result['values_range']['norm']:.4f}")
        elif result["error"]:
            print(f"  Error: {result['error']}")

    sys.exit(0 if result.get("dim_ok") else 1)


if __name__ == "__main__":
    main()
