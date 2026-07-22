# Compatibility namespace

Canonical analysis implementations moved to `pymolplugins/alignment/`,
`pymolplugins/geometry/`, and `pymolplugins/interactions/`. Python files kept
here preserve historical imports and PyMOL `run` paths. New code must not import
from this namespace. Optional standalone tools live under `extras/`.
