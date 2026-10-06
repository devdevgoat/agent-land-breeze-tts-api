# Token log

What building this repo cost, in Claude tokens and API-equivalent dollars. **This is a running log**: add a new entry, or regenerate the table, whenever more work is done with an AI agent.

## How it's measured

- **Source:** Claude Code session transcripts, which record the exact `usage` of every API call. They're in `~/.claude/projects/<project>/<session>.jsonl`, with subagents under `<session>/subagents/`.
- **Script:** `scripts/token_usage.py` de-duplicates streamed messages, splits the session into work segments (`scripts/token_segments.json`, one segment per user prompt that started a piece of work), and prices them.
- **Prices:** `claude-opus-5-5` list prices: $4 / 1M input, $20 / 1M output, $0.20 / 1M cache reads, $5 / 1M 5-minute cache writes, $8 / 1M 1-hour cache writes.
- **Meaning of the dollars:** it's what the tokens would cost on the Claude API. A Claude subscription bills differently.
- **Output tokens include thinking.** Most input was served from the prompt cache, which is why cache reads dominate the token counts but not the cost.

Regenerate:

```bash
python scripts/token_usage.py ~/.claude/projects/<project>/<session>.jsonl \
    --subagent "~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl"
```

## Log

| Date | Work | Model | Cost for this repo |
|---|---|---|---|
| 2026-10-04 to 2026-10-06 | Initial build: PyTorch setup and the FlashAttention/CRLF fixes, the streaming API wrapper, server-side voices and designed-voice reuse, CUDA-fault recovery, the GGUF Q8 port (done by an Opus subagent), the warm-response endpoint, and `/usage`. Plus half of the shared work on both repos (publishing: licensing, docs, AGENTS.md, this log; then the .env-driven restructure, scripts and BENCHMARKS.md) | `claude-opus-5-5` (main session + 1 subagent) | **$23.20** |

## Full session breakdown (2026-10-04 to 2026-10-06)

One Claude Code session built both `agent-land-breeze-tts-api` and `agent-land-zimage-gen-api`, plus some setup (AgentV hooks) that belongs to neither. The table covers the whole session; this repo's rows are the ones labelled `breeze-tts`, plus half of the two `(both repos)` rows.

| Work | Model | API calls | Input | Output | Cache write 5m | Cache write 1h | Cache read | Cost (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| agentv-hooks (TV bridge setup, not in either repo) | `claude-opus-5-5` | 16 | 36 | 7,234 | 0 | 121,152 | 1,972,802 | $1.51 |
| breeze-tts | `claude-opus-5-5` | 102 | 226 | 94,202 | 0 | 286,076 | 24,214,483 | $9.02 |
| breeze-tts (subagent: GGUF port) | `claude-opus-5-5` | 67 | 134 | 14,563 | 184,562 | 0 | 8,860,596 | $2.99 |
| publishing (both repos) | `claude-opus-5-5` | 40 | 80 | 56,400 | 0 | 122,244 | 26,368,219 | $7.38 |
| restructure + benchmarks (both repos) | `claude-opus-5-5` | 48 | 96 | 83,471 | 0 | 776,719 | 35,519,027 | $14.99 |
| zimage-gen | `claude-opus-5-5` | 189 | 382 | 208,642 | 0 | 1,252,374 | 67,006,506 | $27.59 |
| **Total** | | | | | | | | **$63.47** |

- **This repo:** $12.01 of its own work plus $11.19 (half of the shared work) = **$23.20**.
- **Whole session:** $63.48.
- **Last few calls:** the figure was taken just before the final commit and push, so those calls aren't included.
