"""Canonical package for the PymolPlugins toolkit.

The package is importable from both ordinary WSL Python and Windows Host
PyMOL.  Host-only modules import :mod:`pymol` lazily or behind an explicit
adapter boundary; pure computation lives under :mod:`pymolplugins.core`.
"""

__version__ = "0.2.0"
