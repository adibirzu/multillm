# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Thought-level model fusion: panel → judge → synthesis → one answer.

Implements the OpenRouter-Fusion / FusionFactory *thought-level* approach: send a
prompt to a panel of models in parallel, then have a judge model produce a
structured comparative analysis (consensus, contradictions, partial coverage,
unique insights, blind spots) and synthesize a single grounded answer that is
better than any individual response. The result is returned as one response, so
callers treat ``fusion`` like any other model.

This module is pure orchestration: it takes an injected async ``query_fn`` (one
model call → result dict) so the pipeline is unit-tested without a live backend.
Cost is the sum of every panel completion plus the judge call, matching how
per-call usage is billed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Optional

# Marker the judge emits to separate its analysis from the user-facing answer.
FINAL_ANSWER_MARKER = "===FINAL ANSWER==="

# Fusion deliberately runs with a generous token budget so neither the panel
# nor the judge is starved — the whole point is the best possible answer, not a
# cheap one. Each call gets at least these many tokens (more if the request asks
# for more). The judge needs the most room: it writes the comparative analysis
# AND a thorough final answer in one call.
PANEL_TOKEN_FLOOR = 4096
JUDGE_TOKEN_FLOOR = 8192

# Aliases that must never appear in a panel/judge — they would recurse into
# fusion. OpenRouter blocks recursive fusion for the same reason.
_RECURSIVE_ALIASES = {"fusion", "auto"}

# query_fn(alias, prompt, max_tokens, temperature) -> result dict shaped like
# gateway._council_query_one: {alias, backend, text, inputTokens, outputTokens,
# actualCostUSD, latencyMs, error}.
QueryFn = Callable[[str, str, int, float], Awaitable[dict]]


def _is_recursive(alias: str) -> bool:
    return (
        alias in _RECURSIVE_ALIASES
        or alias.startswith("fusion/")
        or alias.startswith("auto/")
    )


def sanitize_panel(panel: list[str], judge: str) -> tuple[list[str], str]:
    """Drop recursive aliases from the panel and judge (fail-safe defaults)."""
    clean_panel = [m for m in panel if not _is_recursive(m)]
    clean_judge = judge if not _is_recursive(judge) else ""
    return clean_panel, clean_judge


def build_judge_prompt(user_prompt: str, panel: list[dict]) -> str:
    """Build the single judge prompt: structured analysis + a final answer.

    Doing analysis and synthesis in one call (rather than two) halves judge
    latency and cost while still grounding the answer in the comparison.
    """
    blocks = []
    for i, r in enumerate(panel, 1):
        blocks.append(
            f"--- Response {i} (from {r.get('alias', '?')}) ---\n{r.get('text', '').strip()}"
        )
    responses = "\n\n".join(blocks)
    return (
        "You are the judge of a multi-model panel. Several models independently "
        "answered the user's question. Fuse them into one answer that is more "
        "accurate and complete than any single response.\n\n"
        f"USER QUESTION:\n{user_prompt.strip()}\n\n"
        f"PANEL RESPONSES:\n{responses}\n\n"
        "Output format — follow it EXACTLY:\n"
        "1. A structured analysis: Consensus, Contradictions, Partial coverage, "
        "Unique insights, Blind spots (write 'none' where empty).\n"
        f"2. Then a line containing exactly {FINAL_ANSWER_MARKER}\n"
        "3. Then the single best, most complete and accurate answer to the user's "
        "question — grounded in the analysis, resolving contradictions, and "
        "incorporating the strongest points from every response. Be as thorough "
        "as the question warrants; do not artificially shorten it. Address the "
        "user directly; do NOT mention the panel or the analysis.\n\n"
        f"The {FINAL_ANSWER_MARKER} line and the answer below it are MANDATORY — "
        "always reach them; never end on the analysis."
    )


# Analysis section labels a judge emits when it ignores the marker. Used to
# strip a leading analysis block as a fallback so the user sees a clean answer.
_ANALYSIS_LABELS = (
    "consensus",
    "contradictions",
    "partial coverage",
    "unique insights",
    "blind spots",
    "structured analysis",
)


def _looks_like_analysis_line(line: str) -> bool:
    s = line.strip().lstrip("#-*0123456789. ").lower()
    return any(s.startswith(label) for label in _ANALYSIS_LABELS)


def split_judge_output(text: str) -> tuple[str, str]:
    """Return (analysis, final_answer) from the judge output.

    Primary path: split on the explicit marker. Fallback (judge ignored the
    marker): strip a leading analysis block — drop the contiguous run of lines
    starting with a known analysis label (and the lines under them) and treat
    the remainder as the answer. If that leaves nothing, return the full text so
    the caller still gets a usable response.
    """
    if FINAL_ANSWER_MARKER in text:
        analysis, _, answer = text.partition(FINAL_ANSWER_MARKER)
        answer = answer.strip()
        if answer:
            return analysis.strip(), answer

    lines = text.strip().splitlines()
    # The answer is whatever follows the LAST analysis-label line (the labels are
    # written inline, e.g. "- Consensus: ..."). Only applies if we saw a label.
    last_label_idx = -1
    for i, line in enumerate(lines):
        if _looks_like_analysis_line(line):
            last_label_idx = i

    if last_label_idx >= 0:
        answer = "\n".join(lines[last_label_idx + 1 :]).strip()
        analysis = "\n".join(lines[: last_label_idx + 1]).strip()
        if answer:
            return analysis, answer

    return text.strip(), text.strip()


def _sum_cost(*results: Optional[dict]) -> float:
    return round(sum((r or {}).get("actualCostUSD", 0) or 0 for r in results), 6)


async def run_fusion(
    *,
    prompt: str,
    panel: list[str],
    judge: str,
    query_fn: QueryFn,
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> dict:
    """Run the fusion pipeline and return the synthesized answer plus metadata.

    Degrades gracefully:
      - 0 panel members succeed → ``finalAnswer`` empty, ``status='no_panel'``
      - 1 panel member succeeds → return it directly (no judge needed)
      - judge call fails → fall back to the best (longest) panel answer
    """
    panel, judge = sanitize_panel(panel, judge)
    if not panel:
        return {
            "status": "no_panel",
            "finalAnswer": "",
            "panel": [],
            "judge": judge,
            "analysis": "",
            "totals": {"costUSD": 0.0, "panelSucceeded": 0},
        }

    # Give the panel a generous budget (at least the floor) so each member can
    # give a full answer to fuse from.
    panel_max_tokens = max(max_tokens, PANEL_TOKEN_FLOOR)
    panel_results = list(
        await asyncio.gather(
            *[query_fn(m, prompt, panel_max_tokens, temperature) for m in panel]
        )
    )
    succeeded = [
        r for r in panel_results if not r.get("error") and (r.get("text") or "").strip()
    ]

    if not succeeded:
        return {
            "status": "no_panel",
            "finalAnswer": "",
            "panel": panel_results,
            "judge": judge,
            "analysis": "",
            "totals": {"costUSD": _sum_cost(*panel_results), "panelSucceeded": 0},
        }

    if len(succeeded) == 1:
        only = succeeded[0]
        return {
            "status": "single",
            "finalAnswer": only["text"],
            "panel": panel_results,
            "judge": None,
            "analysis": "",
            "totals": {"costUSD": _sum_cost(*panel_results), "panelSucceeded": 1},
        }

    # Judge: one call → structured analysis + grounded final answer. Give it a
    # token floor so the analysis doesn't starve the final answer (a small
    # request max_tokens would otherwise truncate before the answer is written).
    judge_prompt = build_judge_prompt(prompt, succeeded)
    judge_max_tokens = max(max_tokens, JUDGE_TOKEN_FLOOR)
    judge_result = await query_fn(judge, judge_prompt, judge_max_tokens, temperature)

    if judge_result.get("error") or not (judge_result.get("text") or "").strip():
        # Judge failed — fall back to the longest panel answer so the caller
        # still gets a real response rather than an error.
        best = max(succeeded, key=lambda r: len(r.get("text", "")))
        return {
            "status": "judge_failed",
            "finalAnswer": best["text"],
            "panel": panel_results,
            "judge": judge,
            "analysis": "",
            "totals": {
                "costUSD": _sum_cost(*panel_results, judge_result),
                "panelSucceeded": len(succeeded),
            },
        }

    analysis, final_answer = split_judge_output(judge_result["text"])
    return {
        "status": "fused",
        "finalAnswer": final_answer,
        "analysis": analysis,
        "panel": panel_results,
        "judge": judge,
        "judgeUsage": {
            "inputTokens": judge_result.get("inputTokens", 0),
            "outputTokens": judge_result.get("outputTokens", 0),
            "costUSD": judge_result.get("actualCostUSD", 0),
        },
        "totals": {
            "costUSD": _sum_cost(*panel_results, judge_result),
            "panelSucceeded": len(succeeded),
            "panelQueried": len(panel_results),
        },
    }
