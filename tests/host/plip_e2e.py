"""Real Windows PyMOL -> WSL PLIP -> exact-ligand render acceptance script.

Required environment:

``PP_PLIP_E2E_PDB``
    Windows-visible path to RCSB 1A4W PDB text.

Optional environment variables select the worker and external evidence paths.
The script is intended for PyMOL ``-cq`` execution, not WSL unit discovery.
"""

import importlib.util
import json
import os
import sys
import tempfile
import traceback

from pymol import cmd


_SCRIPT_PATH = globals().get("__script__", globals().get("__file__", ""))
REPOSITORY = os.path.abspath(
    os.path.join(os.path.dirname(_SCRIPT_PATH), "..", "..")
)
PDB_PATH = os.environ.get("PP_PLIP_E2E_PDB", "").strip()
WORKER_URL = os.environ.get(
    "PYMOLPLUGINS_WORKER_URL", "http://127.0.0.1:8765"
).rstrip("/")
RESULT_PATH = os.environ.get(
    "PP_PLIP_E2E_RESULT",
    os.path.join(tempfile.gettempdir(), "PymolPlugins-plip-host-e2e-result.json"),
)
EVIDENCE_PATH = os.environ.get(
    "PP_PLIP_E2E_EVIDENCE",
    os.path.join(tempfile.gettempdir(), "PymolPlugins-plip-host-e2e-evidence.json"),
)


def _load_entry() -> None:
    entry = os.path.join(REPOSITORY, "plugins", "pymol_plugins.py")
    spec = importlib.util.spec_from_file_location("_pymolplugins_plip_e2e", entry)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin entry: %s" % entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    loaded = module.pp_load()
    if loaded.get("errors"):
        raise RuntimeError("plugin load failed: %r" % loaded["errors"])


def main() -> None:
    if not PDB_PATH or not os.path.isfile(PDB_PATH):
        raise RuntimeError("PP_PLIP_E2E_PDB must name a Windows-visible 1A4W PDB")
    os.environ["PYMOLPLUGINS_WORKER_URL"] = WORKER_URL
    _load_entry()
    cmd.reinitialize()
    cmd.load(PDB_PATH, "host_1a4w", state=1, quiet=1)
    source_atom_count = cmd.count_atoms("host_1a4w", state=1)
    ligand_selection = "host_1a4w and resn QWE and chain H and resi 373"
    ligand_atom_count = cmd.count_atoms(ligand_selection, state=1)
    if source_atom_count != 2439 or ligand_atom_count != 42:
        raise RuntimeError(
            "unexpected Host fixture counts: source=%d ligand=%d"
            % (source_atom_count, ligand_atom_count)
        )

    from pymolplugins.interactions.controller import get_controller, pp_interact_run

    group = pp_interact_run(
        "plip_overview",
        source="host_1a4w",
        partner2="resn QWE and chain H and resi 373",
        group="pp_plip_host_e2e",
        output_json=RESULT_PATH,
        async_mode=0,
        url=WORKER_URL,
        timeout=120.0,
    )
    record = get_controller(cmd).get(group)
    counts = dict(record.result.summary["counts_by_type"])
    site_counts = {
        site.site_id: len(site.interactions) for site in record.result.sites
    }
    expected_counts = {
        "hydrogen_bond": 6,
        "hydrophobic": 4,
        "pi_stacking": 1,
        "salt_bridge": 1,
        "water_bridge": 2,
    }
    if group != "pp_plip_host_e2e":
        raise RuntimeError("unexpected output group: %s" % group)
    if site_counts != {"QWE:H:373": 14}:
        raise RuntimeError("unexpected exact-ligand result: %r" % site_counts)
    if counts != expected_counts:
        raise RuntimeError("unexpected exact-ligand type counts: %r" % counts)
    if len(record.manifest.created) != 12:
        raise RuntimeError(
            "unexpected Host manifest size: %d" % len(record.manifest.created)
        )
    if cmd.count_atoms("host_1a4w", state=1) != source_atom_count:
        raise RuntimeError("source atom count changed during remote analysis")

    evidence = {
        "schema": "pymolplugins.plip-host-e2e/1.0",
        "status": "pass",
        "pymol_version": cmd.get_version(),
        "worker_url": WORKER_URL,
        "source_atom_count": source_atom_count,
        "ligand_atom_count": ligand_atom_count,
        "group": group,
        "site_counts": site_counts,
        "counts_by_type": counts,
        "manifest_entry_count": len(record.manifest.created),
        "result_path": RESULT_PATH,
    }
    temporary = EVIDENCE_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, EVIDENCE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("PLIP HOST E2E PASS " + json.dumps(evidence, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    raise
