# -*- coding: utf-8 -*-
r"""
Dry-run skeleton for the reconstructed 0xp3nny / e46m3 strategy.

This file does not place orders. It encodes the decision logic we can defend
from docs + local chain/activity evidence:

  - fee-aware neg-risk full-basket edge
  - small concurrent per-leg orders
  - immediate full-set conversion when balances allow
  - subset conversion only when residual value is explicitly accounted for

Run:
  $env:PYTHONIOENCODING='utf-8'
  python C:\Users\zexi\pmscan\audit\p3nny_algo_sim.py
"""

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class Leg:
    name: str
    no_price: float
    ask_size: float
    fee_rate: float
    no_balance: float = 0.0
    yes_balance: float = 0.0


@dataclass(frozen=True)
class FullBasketDecision:
    should_trade: bool
    target_sets: float
    gross_edge: float
    fee_cost: float
    net_edge: float
    all_in_cost: float
    reason: str


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)


def full_basket_edge(legs: List[Leg]) -> Tuple[float, float, float, float]:
    """Return gross_edge, fee_cost, net_edge, all_in_cost per one full set."""
    n = len(legs)
    raw_cost = sum(leg.no_price for leg in legs)
    fee_cost = sum(taker_fee_per_share(leg.no_price, leg.fee_rate) for leg in legs)
    gross_edge = (n - 1.0) - raw_cost
    all_in_cost = raw_cost + fee_cost
    net_edge = (n - 1.0) - all_in_cost
    return gross_edge, fee_cost, net_edge, all_in_cost


def choose_full_basket(
    legs: List[Leg],
    *,
    min_net_edge_abs: float = 0.005,
    min_net_edge_pct: float = 0.001,
    max_sets: float = 100.0,
) -> FullBasketDecision:
    gross, fee, net, all_in = full_basket_edge(legs)
    q = min([leg.ask_size for leg in legs] + [max_sets])
    if q <= 0:
        return FullBasketDecision(False, 0, gross, fee, net, all_in, "no common depth")
    if net < min_net_edge_abs:
        return FullBasketDecision(False, 0, gross, fee, net, all_in, "net edge below absolute floor")
    if all_in > 0 and net / all_in < min_net_edge_pct:
        return FullBasketDecision(False, 0, gross, fee, net, all_in, "net edge below pct floor")
    return FullBasketDecision(True, q, gross, fee, net, all_in, "fee-adjusted full basket positive")


def planned_concurrent_orders(legs: List[Leg], target_sets: float) -> List[Dict[str, float | str]]:
    """One independent marketable order per leg. This is not an atomic basket."""
    orders = []
    for leg in legs:
        need = max(0.0, target_sets - leg.no_balance)
        if need <= 0:
            continue
        orders.append(
            {
                "leg": leg.name,
                "side": "BUY",
                "outcome": "NO",
                "shares": need,
                "limit_price": leg.no_price,
                "max_fee": taker_fee_per_share(leg.no_price, leg.fee_rate) * need,
            }
        )
    return orders


def full_set_convert_amount(legs: List[Leg], batch_min: float = 1.0) -> float:
    q = min(leg.no_balance for leg in legs)
    return q if q >= batch_min else 0.0


def subset_convert_candidates(
    legs: List[Leg],
    *,
    min_subset_size: int = 2,
    min_profit: float = 0.0,
) -> List[Dict[str, object]]:
    """
    Enumerate subset conversions with explicit residual accounting.

    For subset S, convertPositions burns NO in S and returns:
      collateral = (len(S)-1) * q
      complementary YES = q for every outcome not in S

    This dry-run only values complementary YES by immediate merge against
    existing NO balances on the complement legs. It does not assume mark-to-
    market profit for leftovers.
    """
    out = []
    n = len(legs)
    idx = list(range(n))
    for r in range(min_subset_size, n):
        for subset in combinations(idx, r):
            q = min(legs[i].no_balance for i in subset)
            if q <= 0:
                continue
            complement = [i for i in idx if i not in subset]
            collateral = (r - 1.0) * q
            merge_value = sum(min(q, legs[i].no_balance) for i in complement)
            # Cost basis is intentionally not estimated here; real code needs FIFO.
            immediate_value = collateral + merge_value
            if immediate_value >= min_profit:
                out.append(
                    {
                        "subset": [legs[i].name for i in subset],
                        "amount": q,
                        "collateral": collateral,
                        "mergeable_complement_yes": merge_value,
                        "immediate_value_before_cost_basis": immediate_value,
                    }
                )
    return sorted(out, key=lambda x: float(x["immediate_value_before_cost_basis"]), reverse=True)


def demo() -> None:
    # Example: weather-like 3-outcome event with fee_rate=0.05.
    legs = [
        Leg("A", no_price=0.53, ask_size=5.0, fee_rate=0.05),
        Leg("B", no_price=0.72, ask_size=5.0, fee_rate=0.05),
        Leg("C", no_price=0.70, ask_size=5.0, fee_rate=0.05),
    ]
    decision = choose_full_basket(legs, min_net_edge_abs=0.001, min_net_edge_pct=0.0001)
    print("full basket decision:", decision)
    if decision.should_trade:
        print("orders:")
        for order in planned_concurrent_orders(legs, decision.target_sets):
            print(" ", order)


if __name__ == "__main__":
    demo()
