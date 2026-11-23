"""Single-object interface snapshots keyed by native atom index."""

from ._bundle_skill.acceptance_interface import capture_assembly_polar, find_buried_unsatisfied

__all__ = ["capture_interface_polar", "find_buried_unsatisfied"]


def capture_interface_polar(api, source, state, focus, partners):
    """Capture an interface within one PyMOL object."""
    rows, opposing = capture_assembly_polar(api, (source,), state, focus, partners)
    return ([(index, coords, vdw) for (_, index), coords, vdw in rows],
            {index: {other for _, other in partners} for (_, index), partners in opposing.items()})
