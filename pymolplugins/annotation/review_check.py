"""Read-only review-bundle inspection; no structures or commands are executed."""

from dataclasses import replace
import json

from .command_templates import STEP_FIELDS, format_commands
from .filtering import compile_view_filter, validate_filter_presets
from .review_file import (
    read_review_file, check_review_unchanged, validate_review_config,
    validate_review_columns,
)
from .review_steps import effective_steps, validate_steps
from .sources.csv_source import CsvDatasetSource, read_csv_header, CsvReadCancelled


def _cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise CsvReadCancelled("Bundle check cancelled")


def _attempt(issues, location, action):
    try:
        return action()
    except (ValueError, TypeError, KeyError) as exc:
        issues.append({"severity": "error", "location": location, "message": str(exc)})
        return None


def _check_layers(review, issues):
    inherited = {}
    for path, raw in review.layers:
        if not isinstance(raw, dict):
            _attempt(issues, path, lambda: validate_review_config(raw))
            continue
        for key, value in raw.items():
            _attempt(issues, path + ":" + key,
                     lambda: validate_review_config({key: value}, validate_order=False))
            if key in inherited and inherited[key] != value:
                issues.append({
                    "severity": "warning", "location": path + ":" + key,
                    "message": "Override replaces the generated value (lists are replaced in full).",
                })
        inherited.update(raw)


def _check_row_commands(entry, steps, step_cmd, issues):
    context = {key: "example" for key in STEP_FIELDS - {"object", "selection", "candidate"}}
    for index, step in enumerate(steps, 1):
        if step["type"] == "object":
            name = step["name"]
            if name == "candidate" or str(entry.metadata.get(step["path_column"], "")).strip():
                context[name] = "sa_" + name
                if name == "candidate":
                    context["object"] = context["selection"] = context[name]
                command = step.get("on_load_cmd", "")
            else:
                continue
        else:
            command = step.get("commands", step_cmd)
        _attempt(issues, f"row {entry.row_number} ({entry.display_name}):step {index}",
                 lambda: format_commands(command, context))


def check_bundle(csv_path, *, resolve_dataset, path_column=None, id_column="",
                 encoding="utf-8-sig", delimiter="auto", source_base="",
                 cancel_event=None, progress=None):
    """Inspect every row and referenced path using the caller's normal resolver.

    Args:
        csv_path: Source table, with adjacent review files.
        resolve_dataset: Read-only resolver accepting an AnnotationDataset.
    Returns:
        JSON-compatible report with errors, warnings and filter hit counts.
    """
    issues, filters = [], []
    _cancel(cancel_event)
    review = None
    try:
        review = read_review_file(csv_path, cancel_event=cancel_event, validate=False)
        _check_layers(review, issues)
        config = review.config
    except (OSError, UnicodeError, ValueError) as exc:
        issues.append({"severity": "error", "location": "review files", "message": str(exc)})
        config = {}
    header = read_csv_header(csv_path, encoding=encoding, delimiter=delimiter,
                             cancel_event=cancel_event)
    columns = header["columns"]
    _attempt(issues, "configuration/columns", lambda: validate_review_columns(config, columns))
    selected_path = path_column or config.get("path_column", "structure_path")
    if selected_path not in columns:
        issues.append({"severity": "error", "location": "path_column",
                       "message": f"Unknown path column: {selected_path!r}; candidate paths not checked."})
    if id_column and id_column not in columns:
        issues.append({"severity": "error", "location": "id_column",
                       "message": f"Unknown ID column: {id_column!r}"})
    if not id_column:
        issues.append({"severity": "warning", "location": "id_column",
                       "message": "No Row ID column selected; identity uses path + duplicate occurrence."})
    dataset = CsvDatasetSource(
        csv_path, selected_path if selected_path in columns else columns[0],
        id_column=id_column if id_column in columns else "", encoding=encoding,
        delimiter=header["delimiter"], source_base=source_base,
    ).load(cancel_event=cancel_event)
    steps = _attempt(issues, "steps/order", lambda: validate_steps(
        effective_steps(config.get("steps", []), config.get("reference_column", "")),
        config.get("step_cmd", ""), columns))
    _attempt(issues, "commands", lambda: validate_review_config(config))
    for entry in dataset.entries:
        _cancel(cancel_event)
        for error in entry.validation_errors:
            issues.append({"severity": "error", "location": f"row {entry.row_number} ({entry.display_name})",
                           "message": error, "candidate_id": entry.id})
        if steps is not None:
            _check_row_commands(entry, steps, config.get("step_cmd", ""), issues)
    paths_checked = 0
    candidates = dataset.entries
    path_columns = [selected_path] if selected_path in columns else []
    path_columns += [step["path_column"] for step in steps or []
                     if step["type"] == "object" and step["name"] != "candidate"]
    for column in dict.fromkeys(path_columns):
        _cancel(cancel_event)
        if progress:
            progress(f"Checking {column}: {len(dataset.entries):,} rows")
        entries = tuple(replace(entry, source_path=entry.metadata[column], validation_errors=())
                        for entry in dataset.entries
                        if column == selected_path or entry.metadata[column].strip())
        resolved = resolve_dataset(replace(dataset, entries=entries))
        if column == selected_path:
            candidates = resolved.entries
        paths_checked += len(resolved.entries)
        for entry in resolved.entries:
            if not entry.is_navigable:
                issues.append({
                    "severity": "error", "location": f"row {entry.row_number} ({entry.display_name}):{column}",
                    "message": entry.error or entry.status, "candidate_id": entry.id,
                    "path": entry.source_path, "resolved_path": entry.resolved_path,
                })
    presets = _attempt(issues, "filter_presets", lambda: validate_filter_presets(
        config.get("filter_presets", []))) or []
    expressions = [("current filter", config.get("filter_text", ""))]
    expressions += [(preset["name"], "where:" + preset["expression"]) for preset in presets]
    for name, expression in expressions:
        predicate = _attempt(issues, "filter:" + name, lambda: compile_view_filter(expression, columns))
        if predicate:
            count = sum(predicate(entry) for entry in candidates)
            filters.append({"name": name, "matches": count, "total": len(dataset.entries)})
            if not count:
                issues.append({"severity": "warning", "location": "filter:" + name,
                               "message": "Filter matches no candidates."})
    if review is not None:
        check_review_unchanged(csv_path, review, cancel_event=cancel_event)
    errors = sum(issue["severity"] == "error" for issue in issues)
    return {"status": "failed" if errors else "passed", "table": csv_path,
            "rows": len(dataset.entries), "paths_checked": paths_checked,
            "id_column": id_column, "errors": errors,
            "warnings": len(issues) - errors, "issues": issues, "filters": filters,
            "scope": "Paths, IDs, columns, step ordering and filters only; no commands executed."}


def main():
    import argparse
    from .path_resolver import PathResolver
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table")
    parser.add_argument("--path-column")
    parser.add_argument("--id-column", default="")
    args = parser.parse_args()
    resolver = PathResolver(rules=[], target_format="custom")

    def resolve(dataset):
        return replace(dataset, entries=tuple(entry.with_resolution(
            resolver.resolve(entry.source_path, source_base=entry.source_base, require_exists=True))
            for entry in dataset.entries))

    report = check_bundle(args.table, path_column=args.path_column,
                          id_column=args.id_column, resolve_dataset=resolve)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
