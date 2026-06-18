# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Heuristic prompt-complexity estimation for selective fusion invocation.

Fusion is 2-3x slower and more expensive than a single call, so it should only
fire for prompts that genuinely benefit from multiple perspectives. This module
scores a prompt 0..1 from cheap, deterministic signals (length, reasoning
vocabulary, multiple questions, enumerated sub-tasks, embedded code) — no model
call required. The ``auto`` model slug compares the score to a threshold.
"""

from __future__ import annotations

import re

# Vocabulary that signals analysis/research/reasoning rather than a lookup.
_REASONING_KEYWORDS = (
    "analyze", "analyse", "compare", "contrast", "trade-off", "tradeoff",
    "design", "architect", "evaluate", "prove", "derive", "why does",
    "root cause", "debug", "optimize", "optimise", "strategy", "plan",
    "pros and cons", "implications", "research", "investigate", "synthesize",
    "synthesise", "comprehensive", "in depth", "in-depth", "step by step",
    "explain why", "recommend", "should i", "best approach",
)

_CODE_RE = re.compile(r"```|\bdef \b|\bclass \b|\bfunction\b|=>|;\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+")


def estimate_complexity(prompt: str) -> dict:
    """Return ``{score, wordCount, reasons}`` for a prompt.

    The score is a bounded sum of independent signals so no single factor
    dominates; weights were chosen so a long multi-part analytical prompt clears
    ~0.6 while a short factual question stays near 0.
    """
    text = (prompt or "").strip()
    lower = text.lower()
    words = len(text.split())
    questions = text.count("?")
    kw = sum(1 for k in _REASONING_KEYWORDS if k in lower)
    has_code = bool(_CODE_RE.search(text))
    bullets = len(_BULLET_RE.findall(text))

    score = 0.0
    score += min(0.30, words / 150 * 0.30)            # length
    score += min(0.40, kw * 0.10)                     # reasoning vocabulary
    score += min(0.15, max(0, questions - 1) * 0.10)  # multiple questions
    score += min(0.15, bullets * 0.05)                # enumerated sub-asks
    score += 0.10 if has_code else 0.0                # embedded code
    # Synergy: a substantial prompt that also uses reasoning vocabulary is the
    # clearest "needs multiple perspectives" signal — neither alone is enough.
    if kw >= 2 and words >= 20:
        score += 0.15
    score = round(min(1.0, score), 3)

    reasons = []
    if words > 120:
        reasons.append(f"long prompt ({words} words)")
    if kw:
        reasons.append(f"{kw} reasoning keyword(s)")
    if questions > 1:
        reasons.append(f"{questions} questions")
    if bullets:
        reasons.append(f"{bullets} sub-tasks")
    if has_code:
        reasons.append("contains code")
    return {"score": score, "wordCount": words, "reasons": reasons}
