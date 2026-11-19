"""Measure empty probe-center regions in a pocket or at a molecular interface."""

import json
from pathlib import Path
import sys


GRID = 0.5
WATER = 1.4
OUTER = 4.0
REGION_MARGIN = 5.0
PADDING = 9.0


def _bulk(mask, *, diagonals):
    import numpy as np
    from scipy.ndimage import binary_propagation, generate_binary_structure

    boundary = mask.copy()
    boundary[1:-1, 1:-1, 1:-1] = False
    neighbors = np.ones((3, 3, 3)) if diagonals else generate_binary_structure(3, 1)
    return binary_propagation(boundary, structure=neighbors, mask=mask)


def _regions(mask, origin, spacing, minimum_volume):
    import numpy as np
    from scipy.ndimage import find_objects, label

    labels, _ = label(mask)
    regions = []
    stride = max(1, round(1.0 / spacing))
    for index, box in enumerate(find_objects(labels), 1):
        points = np.argwhere(labels[box] == index) + [axis.start for axis in box]
        volume = len(points) * spacing ** 3
        if volume < minimum_volume:
            continue
        sample = points[np.all(points % stride == 0, axis=1)]
        if not len(sample):
            sample = points[[len(points) // 2]]
        regions.append({
            "volume": round(volume, 3),
            "points": (origin + sample * spacing).round(4).tolist(),
        })
    return sorted(regions, key=lambda region: region["volume"], reverse=True)


def measure_pocket(atoms, ligand_indices, *, spacing=GRID, padding=PADDING):
    """Return accessible recesses and enclosed voids, with probe-center volumes."""
    if not len(ligand_indices) or len(ligand_indices) >= len(atoms):
        raise ValueError("Cavity needs separate protein and ligand heavy atoms.")
    return _measure_regions(atoms, [(ligand_indices,)], spacing, padding)


def measure_interface(atoms, interface_pairs, *, spacing=GRID, padding=PADDING):
    """Keep points close to both sides of at least one contacting partner pair."""
    return _measure_regions(atoms, interface_pairs, spacing, padding)


def _measure_regions(atoms, selections, spacing, padding):
    import numpy as np

    atoms = np.asarray(atoms, dtype=float)
    sites = [[atoms[indices, :3] for indices in selection] for selection in selections]
    centers = np.concatenate([side for site in sites for side in site])
    extent = REGION_MARGIN + padding
    lower = np.floor((centers.min(axis=0) - extent) / spacing) * spacing
    upper = np.ceil((centers.max(axis=0) + extent) / spacing) * spacing
    shape = np.rint((upper - lower) / spacing).astype(int) + 1
    if np.prod(shape) > 8_000_000:
        raise ValueError("Selection spans too large a region for cavity analysis.")
    clearance = np.full(shape, OUTER + spacing, dtype=np.float32)
    for x, y, z, radius in atoms:
        center = (np.asarray((x, y, z)) - lower) / spacing
        reach = (radius + OUTER + spacing) / spacing
        box, distance = _distance_box(center, reach, shape, spacing)
        if distance is not None:
            target = clearance[box]
            np.minimum(target, distance - radius, out=target)
    wanted = np.zeros(shape, dtype=bool)
    for site in sites:
        wanted |= np.logical_and.reduce([_near(side, lower, spacing, shape) for side in site])
    return _classify(clearance, wanted, lower, spacing)


def _near(side, lower, spacing, shape):
    import numpy as np

    near = np.zeros(shape, dtype=bool)
    for xyz in side:
        center = (xyz - lower) / spacing
        box, distance = _distance_box(center, REGION_MARGIN / spacing, shape, spacing)
        near[box] |= distance <= REGION_MARGIN
    return near


def _classify(clearance, wanted, lower, spacing):
    from scipy.ndimage import distance_transform_edt
    import numpy as np

    water_fits = clearance >= WATER
    # Leave connectivity that depends on a diagonal gap unclassified.
    water_bulk = _bulk(water_fits, diagonals=False)
    water_possible = _bulk(water_fits, diagonals=True)
    large_bulk = _bulk(clearance >= OUTER, diagonals=True)
    if not np.any(large_bulk):
        raise ValueError("No bulk region admits the large probe at this grid boundary.")
    large_reach = distance_transform_edt(~large_bulk, sampling=spacing)
    # Expand the swept large-probe region by the small-to-large radius
    # difference and half a cell diagonal to reject thin discretization rims.
    shielded = large_reach > OUTER - WATER + 3 ** 0.5 * spacing / 2
    return {
        "recesses": _regions(wanted & water_bulk & shielded, lower, spacing, 6.0),
        "enclosed": _regions(wanted & water_fits & ~water_possible, lower, spacing, 2.0),
    }


def _distance_box(center, reach, shape, spacing):
    import numpy as np

    start = np.maximum(np.floor(center - reach).astype(int), 0)
    stop = np.minimum(np.ceil(center + reach).astype(int) + 1, shape)
    if (stop <= start).any():
        return (), None
    squared = [(np.arange(lo, hi, dtype=float) - coordinate) ** 2
               for lo, hi, coordinate in zip(start, stop, center)]
    dx, dy, dz = squared
    distance = np.sqrt(dx[:, None, None] + dy[None, :, None] + dz[None, None, :]) * spacing
    return tuple(slice(lo, hi) for lo, hi in zip(start, stop)), distance


if __name__ == "__main__":
    library, source, destination = sys.argv[1:]
    if library:
        sys.path.insert(0, library)
    try:
        import numpy
        import scipy.ndimage
    except (ImportError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(42)
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    measure = measure_interface if "interface_pairs" in data else measure_pocket
    result = measure(**data)
    Path(destination).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
