# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


def approx_tokens(text: str) -> int:
    """
    Very rough token estimator, used only when provider doesn't return usage.

    Heuristic:
    - CJK chars ~ 1 token
    - other chars ~ 1 token / 4 chars
    """
    s = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    other = len(s) - cjk
    return max(1, int(cjk + max(0, other) / 4))


@dataclass
class LLMBudget:
    # Primary (recommended): token budget. 0 => unlimited.
    max_total_tokens: int = 0

    # Optional: cost estimate based on user-provided rate (unitless; for display only).
    cost_per_1m_tokens: float = 0.0
    max_cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    approx_total_tokens: int = 0
    calls: int = 0
    warnings: List[str] = field(default_factory=list)
    error_counts: Dict[str, int] = field(default_factory=dict)

    def inc_error(self, key: str) -> int:
        k = (key or "").strip()
        if not k:
            return 0
        self.error_counts[k] = int(self.error_counts.get(k, 0) or 0) + 1
        return int(self.error_counts.get(k, 0) or 0)

    def tracked_total_tokens(self) -> int:
        # Some providers may return usage for only a subset of calls. We track
        # a combined total so budget enforcement stays correct.
        return int(self.total_tokens) + int(self.approx_total_tokens)

    def add_usage(self, usage: Dict[str, int]):
        pt = int((usage or {}).get("prompt_tokens", 0) or 0)
        ct = int((usage or {}).get("completion_tokens", 0) or 0)
        tt = int((usage or {}).get("total_tokens", 0) or 0)
        if tt <= 0:
            tt = pt + ct
        self.prompt_tokens += max(0, pt)
        self.completion_tokens += max(0, ct)
        self.total_tokens += max(0, tt)
        self.calls += 1

    def add_approx(self, prompt_text: str, completion_text: str):
        self.approx_total_tokens += approx_tokens(prompt_text) + approx_tokens(completion_text)
        self.calls += 1

    def estimated_cost(self) -> float:
        t = self.tracked_total_tokens()
        return float(t) * float(self.cost_per_1m_tokens) / 1_000_000.0

    def budget_remaining_cost(self) -> float:
        return float(self.max_cost) - self.estimated_cost()

    def budget_remaining_tokens(self) -> int:
        if int(self.max_total_tokens or 0) <= 0:
            return 0
        return max(0, int(self.max_total_tokens) - int(self.tracked_total_tokens()))

    def would_exceed_budget(self, *, approx_prompt_tokens: int, max_completion_tokens: int) -> bool:
        cur = self.tracked_total_tokens()
        extra = int(max(1, approx_prompt_tokens)) + int(max(0, max_completion_tokens))
        next_total = cur + extra

        # Token budget (recommended).
        mt = int(self.max_total_tokens or 0)
        if mt > 0:
            if int(cur) >= mt:
                return True
            if int(next_total) > mt:
                return True
            # When token budget is enabled, cost is treated as display-only.
            return False

        # Cost budget (legacy). Only enforce when both values are positive.
        if float(self.max_cost or 0.0) > 0.0 and float(self.cost_per_1m_tokens or 0.0) > 0.0:
            cur_cost = self.estimated_cost()
            if cur_cost >= float(self.max_cost):
                return True
            next_cost = float(next_total) * float(self.cost_per_1m_tokens) / 1_000_000.0
            return next_cost > float(self.max_cost)

        return False

    # Backward-compatible aliases (deprecated): *_rmb naming was historical.
    @property
    def cost_per_1m_tokens_rmb(self) -> float:  # pragma: no cover
        return float(self.cost_per_1m_tokens)

    @cost_per_1m_tokens_rmb.setter
    def cost_per_1m_tokens_rmb(self, v: float) -> None:  # pragma: no cover
        self.cost_per_1m_tokens = float(v or 0.0)

    @property
    def max_cost_rmb(self) -> float:  # pragma: no cover
        return float(self.max_cost)

    @max_cost_rmb.setter
    def max_cost_rmb(self, v: float) -> None:  # pragma: no cover
        self.max_cost = float(v or 0.0)

    def estimated_cost_rmb(self) -> float:  # pragma: no cover
        return float(self.estimated_cost())

    def budget_remaining_rmb(self) -> float:  # pragma: no cover
        return float(self.budget_remaining_cost())
