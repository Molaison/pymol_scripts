"""Shared PyMOL atom-pair queries for interaction analysis and review views."""

from typing import Any, Tuple


H_BOND_ANGLE = 45.0


def _pair_key(value: Any) -> Tuple[str, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("PyMOL find_pairs returned an invalid atom reference: %r" % (value,))
    return (str(value[0]), int(value[1]))


def find_pairs(
    cmd_api: Any,
    selection1: str,
    selection2: str,
    cutoff: float,
    mode: int,
    state: int,
) -> Tuple[Tuple[Tuple[str, int], Tuple[str, int]], ...]:
    base = {"cutoff": cutoff, "mode": mode}
    state_kwargs = dict(base, state1=state, state2=state)
    attempts = []
    if mode == 1:
        attempts.append(dict(state_kwargs, angle=H_BOND_ANGLE))
    attempts.append(state_kwargs)
    if mode == 1:
        attempts.append(dict(base, angle=H_BOND_ANGLE))
    attempts.append(base)
    last_error = None
    for kwargs in attempts:
        try:
            raw = cmd_api.find_pairs(selection1, selection2, **kwargs)
            break
        except TypeError as exc:
            last_error = exc
    else:  # pragma: no cover - every supported PyMOL accepts the base call.
        assert last_error is not None
        raise last_error
    pairs = {(_pair_key(item[0]), _pair_key(item[1])) for item in raw}
    return tuple(sorted(pairs))


def hydrogen_bond_specs(
    partner1: str,
    partner2: str,
    cutoff: float,
    subtype: str = "donor_acceptor",
) -> Tuple[Tuple[str, str, float, int, str, str, str, str], ...]:
    return (
        (
            "(%s) and donor" % partner1,
            "(%s) and acceptor" % partner2,
            cutoff,
            1,
            "hydrogen_bond",
            subtype,
            "donor",
            "acceptor",
        ),
        (
            "(%s) and donor" % partner2,
            "(%s) and acceptor" % partner1,
            cutoff,
            1,
            "hydrogen_bond",
            subtype,
            "donor",
            "acceptor",
        ),
    )


def salt_bridge_specs(
    partner1: str, partner2: str, cutoff: float
) -> Tuple[Tuple[str, str, float, int, str, str, str, str], ...]:
    from pymolplugins.annotation._bundle_skill.acceptance_interface import NEGATIVE_ATOMS, POSITIVE_ATOMS

    return (
        (
            "(%s) and %s" % (partner1, POSITIVE_ATOMS),
            "(%s) and %s" % (partner2, NEGATIVE_ATOMS),
            cutoff,
            0,
            "salt_bridge",
            "positive_negative",
            "positive",
            "negative",
        ),
        (
            "(%s) and %s" % (partner2, POSITIVE_ATOMS),
            "(%s) and %s" % (partner1, NEGATIVE_ATOMS),
            cutoff,
            0,
            "salt_bridge",
            "positive_negative",
            "positive",
            "negative",
        ),
    )
