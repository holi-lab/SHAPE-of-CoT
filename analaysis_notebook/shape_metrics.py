"""shape_metrics.py — canonical SHAPE metric computation (H-tags only).

N_space_eff = exp(H_space)
N_trans_eff = exp(H_trans) - 1
rho (Density) = N_trans_eff / N_space_eff  (computed by caller)

Only H-tags (H1–H11) contribute; N-tags and all others are excluded.
Tags are deduplicated per content unit.

Exports:
    compute_shape_metrics(units) -> {"N_space_eff": float, "N_trans_eff": float} | None
    effective_counts(units)      -> (N_space_eff: float, N_trans_eff: float)
"""

import math
import re
from collections import Counter

import numpy as np

# Matches H\d+ with optional lowercase suffix: H1, H1a, H1abc, ...
_H_TAG_PAT = re.compile(r'^(H\d+)[a-z]*$')


def _normalize_htag(tag) -> 'str | None':
    """Normalize a raw tag to its parent H-tag (e.g. 'H1a' -> 'H1'), or None."""
    if not isinstance(tag, str):
        return None
    m = _H_TAG_PAT.match(tag.strip())
    return m.group(1) if m else None


def _unit_htags(unit: dict) -> list:
    """Return deduplicated parent H-tags for one content unit."""
    raw = unit.get('ontology_tag', []) if isinstance(unit, dict) else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for t in raw:
        p = _normalize_htag(t)
        if p is not None and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _get_space_id(unit: dict, prev_sid: int = 0) -> int:
    ss = unit.get('semantic_space') if isinstance(unit, dict) else None
    if not isinstance(ss, dict):
        return prev_sid
    sid = ss.get('active_space_id', prev_sid)
    if sid is None:
        return prev_sid
    try:
        return int(sid)
    except (TypeError, ValueError):
        return prev_sid


def _shannon_entropy(weights: np.ndarray) -> float:
    w = weights[weights > 0]
    if len(w) == 0:
        return 0.0
    p = w / w.sum()
    return float(-(p * np.log(p)).sum())


def compute_shape_metrics(units: list) -> 'dict | None':
    """Return N_space_eff and N_trans_eff for one trajectory.

    H-tags (H1-H11) only; N-tags excluded. Tags are deduplicated per unit.
    Returns None if no H-tags are present.
    """
    unit_tags = [_unit_htags(u) for u in units]
    total_tags = sum(len(hs) for hs in unit_tags)
    if total_tags == 0:
        return None

    # H_space: distribution over semantic-space IDs, weighted by H-tag count per unit
    q_space = Counter()
    space_ids = []
    prev_sid = 0
    for unit, hs in zip(units, unit_tags):
        sid = _get_space_id(unit, prev_sid)
        prev_sid = sid
        space_ids.append(sid)
        if hs:
            q_space[sid] += len(hs)

    N_space_eff = math.exp(_shannon_entropy(np.array(list(q_space.values()), dtype=float)))

    # H_trans: distribution over contiguous same-space-id segments
    segments = []
    if space_ids:
        cur_id, start = space_ids[0], 0
        for i in range(1, len(space_ids)):
            if space_ids[i] != cur_id:
                segments.append((start, i))
                start, cur_id = i, space_ids[i]
        segments.append((start, len(space_ids)))

    p_trans = np.array(
        [sum(len(unit_tags[i]) for i in range(s, e)) for s, e in segments],
        dtype=float,
    )
    N_trans_eff = math.exp(_shannon_entropy(p_trans)) - 1.0

    return {"N_space_eff": N_space_eff, "N_trans_eff": N_trans_eff}


def effective_counts(units: list) -> tuple:
    """Return (N_space_eff, N_trans_eff); both nan if no H-tags present."""
    r = compute_shape_metrics(units)
    return (r['N_space_eff'], r['N_trans_eff']) if r is not None else (np.nan, np.nan)
