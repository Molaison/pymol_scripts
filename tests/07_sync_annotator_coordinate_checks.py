"""Check or update the Collections coordinate preflight vendored in standalone Annotator."""

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "workflows/dataset_navigation/collections_core.py"
ANNOTATOR = ROOT / "plugins/structure_annotator.py"
BEGIN = "# BEGIN GENERATED COORDINATE CHECKS\n"
END = "# END GENERATED COORDINATE CHECKS"


def coordinate_checks(core):
    constants = core[core.index("PDB_SUSPECT_COORDINATES ="):core.index("def orientation_config(")]
    limit = next(line for line in core.splitlines() if line.startswith("NATIVE_COORDINATE_OVERFLOW ="))
    validators = core[core.index("def _nonfinite_coordinate("):core.index("def load_structure(")]
    return constants.rstrip() + "\n" + limit + "\n\n\n" + validators.rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    source = ANNOTATOR.read_text(encoding="utf-8")
    begin = source.index(BEGIN) + len(BEGIN)
    end = source.index(END, begin)
    expected = source[:begin] + coordinate_checks(CORE.read_text(encoding="utf-8")) + source[end:]
    if expected == source:
        return
    if not args.write:
        raise SystemExit("Run tests/07_sync_annotator_coordinate_checks.py --write to refresh Annotator.")
    ANNOTATOR.write_bytes(expected.encode("utf-8"))


if __name__ == "__main__":
    main()
