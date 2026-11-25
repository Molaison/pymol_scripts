"""Host commands for residue analysis; Qt is loaded only for the panel."""

import math

from pymolplugins.core.parameters import as_bool, as_int
from . import analysis, presentation
from ._coretosurf import DEFAULT_CUTOFFS


def _number(value, name):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(name + " must be finite and non-negative")
    return value


def pp_residues(selection="", mode="layers", core=None, boundary=None, unit="abs",
                state=-1, color=1, selections=0, prefix="pp_residues", recompute=0,
                cutoff=8.0, gap=2, exposure=25.0, hydrophobicity=0.0, outfile=""):
    """Analyze residue exposure, hydropathy and nonlocal C-alpha contacts.
    Args:
        selection: Protein selection, or empty to open the Qt panel.
        mode: layers, rsa, hydropathy, exposed, or contacts (coloring metric).
        core/boundary: Upper cutoffs, default 20/40 Angstrom² or 20/50 percent.
        unit: abs (whole-residue SASA) or rel (Tien maximum ASA percentage).
        state: Positive state, or 0/-1 for current object state.
        color: Preview carbon colors; pp_residue_clear restores them.
        selections: Explicitly create namespaced layer selections.
        prefix: Prefix for generated layer selections.
        recompute: Discard cached SASA.
        cutoff: Nonlocal C-alpha contact distance in Angstrom (default 8).
        gap: Exclude this many observed sequence neighbors (default 2).
        exposure/hydrophobicity: Minimum RSA percent and strict KD cutoff for exposed mode.
        outfile: Optional per-residue CSV path.
    Returns: Analysis report, or the modeless panel when selection is empty.
    """
    if not str(selection).strip():
        from .panel import show_panel
        return show_panel()
    unit, mode = str(unit).lower(), str(mode).lower()
    if unit not in DEFAULT_CUTOFFS:
        raise ValueError("unit must be abs or rel")
    if mode not in presentation.MODES:
        raise ValueError("mode must be " + ", ".join(presentation.MODES))
    defaults = DEFAULT_CUTOFFS[unit]
    core = _number(defaults[0] if core is None else core, "core")
    boundary = _number(defaults[1] if boundary is None else boundary, "boundary")
    if core > boundary:
        raise ValueError("core must not exceed boundary")
    exposure = _number(exposure, "exposure")
    hydrophobicity = float(hydrophobicity)
    if not math.isfinite(hydrophobicity):
        raise ValueError("hydrophobicity must be finite")
    color, selections, force = as_bool(color), as_bool(selections), as_bool(recompute)
    report = analysis.analyze(str(selection), as_int(state, minimum=-1, name="state"), force,
                              float(cutoff), as_int(gap, minimum=0, name="gap"))
    analysis.classify(report, core, boundary, unit)
    report.update(mode=mode, exposed_min_rsa=exposure, hydropathy_threshold=hydrophobicity)
    if selections:
        report["selections"] = presentation.create_selections(report, str(prefix))
    if color:
        presentation.color_report(report, mode, exposure, hydrophobicity)
    if outfile:
        report["outfile"] = presentation.write_csv(report, outfile)
    return report


def pp_core_surface(selection="", core=None, boundary=None, unit="abs", state=-1,
                    recompute=0, quiet=0, selections=0):
    """Open residue analysis, or apply CoreToSurf-compatible SASA thresholds.

    Args:
        selection: Protein selection; empty opens the interactive panel.
        core: Upper SASA/RSA threshold for core residues.
        boundary: Upper SASA/RSA threshold for boundary residues.
        unit: abs (Angstrom²) or rel (percent of Tien maximum ASA).
        state: Positive state or -1/0 for current object state.
        recompute: Force SASA recalculation.
        quiet: Suppress residue counts.
        selections: Create pp_residues_core/boundary/surface selections.
    Returns:
        Analysis report or the modeless panel.
    """
    result = pp_residues(selection, "layers", core, boundary, unit, state,
                         recompute=recompute, selections=selections)
    if isinstance(result, dict) and not as_bool(quiet):
        print(" Residue layers: " + ", ".join("%s %d" % item for item in result["counts"].items()))
    return result


def coretosurf(selection="", core=None, boundary=None, unit="abs", state=-1,
               recompute=0, quiet=0):
    """CoreToSurf spelling; uses namespaced selections to preserve scene names."""
    report = pp_core_surface(selection, core, boundary, unit, state, recompute,
                             quiet, selections=1)
    return report["counts"] if isinstance(report, dict) else report


def pp_residue_clear():
    """Restore residue-analysis colors and remove its explicitly created selections."""
    return presentation.clear()
