#!/usr/bin/env python3
"""Total the Claude tokens (and API-equivalent cost) spent building this repo.

Reads Claude Code session transcripts (JSONL, one per session, plus any subagent
transcripts), de-duplicates assistant messages by message id (a streamed message
is logged once per content block), labels each one by the work segment its
timestamp falls in, and prints a Markdown table per label.

    python scripts/token_usage.py ~/.claude/projects/<project>/<session>.jsonl \
        --subagent ~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl \
        --segments scripts/token_segments.json

Segments file: {"segments": [["<ISO start timestamp>", "<label>"], ...],
                "subagent_label": "<label for subagent transcripts>"}

Prices are USD per million tokens for the model that did the work; update
PRICES if you rerun this for another model or after a price change.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
from pathlib import Path

# claude-opus-5-5 list prices (USD / 1M tokens), as of 2026-10.
# Cache writes: 1.25x input for the 5-minute TTL, 2x for the 1-hour TTL.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "write_5m": 5.00, "write_1h": 8.00},
}
FIELDS = ("messages", "input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def load(path: str) -> dict[str, tuple[str, str, dict]]:
    """message id -> (timestamp, model, usage); the last logged copy wins."""
    out: dict[str, tuple[str, str, dict]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") or {}
            if d.get("type") != "assistant" or not m.get("usage"):
                continue
            out[m.get("id") or d.get("uuid")] = (d.get("timestamp", ""), m.get("model", "?"), m["usage"])
    return out


def label_for(ts: str, segments: list[list[str]]) -> str:
    current = segments[0][1]
    for start, name in segments:
        if ts >= start:
            current = name
    return current


def cost(row: dict, model: str) -> float:
    p = PRICES.get(model)
    if p is None:
        return float("nan")
    return (row["input"] * p["input"] + row["output"] * p["output"] + row["cache_read"] * p["cache_read"]
            + row["cache_write_5m"] * p["write_5m"] + row["cache_write_1h"] * p["write_1h"]) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcripts", nargs="+", help="main session transcript(s) (.jsonl)")
    ap.add_argument("--subagent", action="append", default=[], help="subagent transcript(s); globs allowed")
    ap.add_argument("--segments", default=str(Path(__file__).with_name("token_segments.json")))
    args = ap.parse_args()

    cfg = json.loads(Path(args.segments).read_text(encoding="utf-8"))
    segments = sorted(cfg["segments"])
    totals: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)

    def add(messages: dict, fixed_label: str | None) -> None:
        for ts, model, u in messages.values():
            label = fixed_label or label_for(ts, segments)
            c = totals[(label, model)]
            cc = u.get("cache_creation") or {}
            write_5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
            write_1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            if not cc:  # older transcripts: no TTL breakdown, assume 5-minute writes
                write_5m = u.get("cache_creation_input_tokens", 0) or 0
            c["messages"] += 1
            c["input"] += u.get("input_tokens", 0) or 0
            c["output"] += u.get("output_tokens", 0) or 0
            c["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
            c["cache_write_5m"] += write_5m
            c["cache_write_1h"] += write_1h

    for t in args.transcripts:
        add(load(t), None)
    for pattern in args.subagent:
        for t in sorted(glob.glob(pattern)):
            add(load(t), cfg.get("subagent_label", "subagent"))

    print("| Work | Model | API calls | Input | Output | Cache write 5m | Cache write 1h | Cache read | Cost (USD) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    grand = 0.0
    for (label, model), c in sorted(totals.items()):
        usd = cost(c, model)
        grand += usd
        print(f"| {label} | `{model}` | " + " | ".join(f"{c[k]:,}" for k in FIELDS) + f" | ${usd:,.2f} |")
    print(f"| **Total** | | | | | | | | **${grand:,.2f}** |")


if __name__ == "__main__":
    main()
