#!/usr/bin/env python
"""Download the local cross-encoder reranker (BAAI/bge-reranker-base).

    python scripts/download-rerank-model.py            # ~1.1 GB into models/rerank/bge-reranker-base
    python scripts/download-rerank-model.py --check     # verify what is already on disk
    python scripts/download-rerank-model.py --force     # re-download everything

Why ModelScope and not Hugging Face: measured on 2026-10-03, `huggingface.co` and `hf-mirror.com`
both time out from this network (no proxy configured) while `modelscope.cn` answers 200. The same
author released the model on both, so this script targets ModelScope and keeps the HF id in the
manifest for anyone whose network is the other way round.

The model is ~1.06 GB of PyTorch weights and is loaded through `transformers` + `torch`, both of
which are OPTIONAL for this project (torch lives in requirements-voiceprint.txt). Without them the
knowledge base still retrieves: the reranker reports itself unavailable and /health says so.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
# The progress lines carry CJK and arrows; a Windows console defaults to cp936 and would raise
# UnicodeEncodeError once the output is redirected to a log.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEFAULT_ENDPOINT = "https://www.modelscope.cn"
DEFAULT_REPO = "BAAI/bge-reranker-base"
DEFAULT_DEST = Path("models") / "rerank" / "bge-reranker-base"
FILES = (
    "config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "tokenizer.json",
    "model.safetensors",
)


def human(size: float) -> str:
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"


def remote_size(client: httpx.Client, url: str, params: dict) -> int | None:
    response = client.get(url, params=params, headers={"Range": "bytes=0-0"})
    if response.status_code not in (200, 206):
        return None
    content_range = response.headers.get("content-range")
    if content_range and "/" in content_range:
        return int(content_range.rsplit("/", 1)[1])
    length = response.headers.get("content-length")
    return int(length) if length and response.status_code == 200 else None


def download(client: httpx.Client, url: str, params: dict, target: Path, expected: int | None) -> str:
    """Stream one file and return its SHA-256."""

    digest = hashlib.sha256()
    written = 0
    started = time.perf_counter()
    temporary = target.with_suffix(target.suffix + ".part")
    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        total = expected or int(response.headers.get("content-length") or 0)
        last_report = 0.0
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                now = time.perf_counter()
                if now - last_report > 5:
                    last_report = now
                    speed = written / max(0.001, now - started) / 1048576
                    share = f"{written * 100 / total:.0f}%" if total else "?"
                    print(f"      {human(written)} / {human(total)} ({share})  {speed:.1f} MB/s", flush=True)
    if expected and written != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {target.name}: got {written}, expected {expected}")
    temporary.replace(target)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true", help="only report what is present")
    args = parser.parse_args()

    destination = args.dest if args.dest.is_absolute() else ROOT / args.dest
    destination.mkdir(parents=True, exist_ok=True)
    url = f"{args.endpoint.rstrip('/')}/api/v1/models/{args.repo}/repo"
    print(f"  repo    : {args.repo}  ({args.endpoint})")
    print(f"  target  : {destination.relative_to(ROOT)}")

    checksums: dict[str, str] = {}
    with httpx.Client(timeout=120, trust_env=False, follow_redirects=True) as client:
        for name in FILES:
            target = destination / name
            params = {"Revision": "master", "FilePath": name}
            expected = remote_size(client, url, params)
            if expected is None:
                print(f"  ✗ {name}: 遠端查不到大小（可能不存在）")
                return 1
            if args.check:
                state = "存在" if target.is_file() else "缺"
                local = human(target.stat().st_size) if target.is_file() else "-"
                print(f"  {name:<28} 遠端 {human(expected):>10}  本地 {local:>10}  {state}")
                continue
            if target.is_file() and not args.force and target.stat().st_size == expected:
                print(f"  = {name:<28} 已存在（{human(expected)}）")
                digest = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                checksums[name] = digest.hexdigest().upper()
                continue
            print(f"  ↓ {name:<28} {human(expected)}")
            checksums[name] = download(client, url, params, target, expected)
            print(f"    ✓ {name}  sha256={checksums[name]}")

    if not args.check:
        print("\n  SHA-256（寫進 models/manifest.json 用）:")
        for name, value in checksums.items():
            print(f"    {name:<28} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
