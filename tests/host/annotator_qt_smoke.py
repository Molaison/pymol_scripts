"""Compatible entry point for the current Windows annotator workflow gate.

The workflow harness checks the integrated review UI, native command completion,
column filtering, and cancellation. Use --full-gui for viewport/console focus.
"""

import argparse
import datetime
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import traceback


SCHEMA = "pymolplugins.annotator-qt-smoke/2.0"


def run_qt_smoke(repository, output=None, full_gui=False):
    repository = Path(repository).resolve()
    filename = repository / "tests/host/annotator_workflow_smoke.py"
    spec = importlib.util.spec_from_file_location("_annotator_workflow_smoke", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-qt-output-") as scratch:
        destination = Path(output) if output else Path(scratch) / "result.json"
        result = module.run(200, 128, destination, full_gui=full_gui)
        if not output:
            result.pop("screenshot", None)
    result.update(schema=SCHEMA, status="pass", repository=str(repository))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--output", default="")
    parser.add_argument("--full-gui", action="store_true")
    args = parser.parse_args()
    try:
        result = run_qt_smoke(args.repository, args.output, args.full_gui)
    except Exception as exc:
        result = {"schema": SCHEMA, "status": "fail", "error": str(exc), "traceback": traceback.format_exc()}
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    code = 0 if result["status"] == "pass" else 1
    if args.full_gui:
        from pymol import cmd
        cmd.quit(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
