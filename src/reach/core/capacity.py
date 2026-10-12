"""Capacity model: volume with nesting, and weight."""

from __future__ import annotations

from collections.abc import Iterable

from reach.core.model import CostParams, Item


def used_volume(items: Iterable[Item], params: CostParams) -> float:
    """Volume taken by ``items`` when stored together in one location.

    Units of the same nest group occupy the largest unit plus a fraction of each other unit::

        V_group = v_max + alpha * (sum(v) - v_max)
    """
    plain = 0.0
    group_total: dict[str, float] = {}
    group_max: dict[str, float] = {}
    for item in items:
        if item.nest_group is None:
            plain += item.volume * item.quantity
        else:
            g = item.nest_group
            group_total[g] = group_total.get(g, 0.0) + item.volume * item.quantity
            if item.quantity > 0 and item.volume > group_max.get(g, 0.0):
                group_max[g] = item.volume
    for g, total in group_total.items():
        alpha = params.nest_factors.get(g, params.nest_factor)
        vmax = group_max.get(g, 0.0)
        plain += vmax + alpha * (total - vmax)
    return plain


def used_weight(items: Iterable[Item]) -> float:
    return sum(item.weight * item.quantity for item in items)
