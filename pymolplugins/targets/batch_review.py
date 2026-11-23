"""Create an Annotator review from explicit, already-matched batch inputs."""

from dataclasses import dataclass
import csv
import json
import os
from pathlib import Path
import shutil
import tempfile

from pymolplugins.annotation.review_file import validate_review_config

from .batch_sources import _cancelled, _same_file, file_stamp, prepare_pairs


@dataclass(frozen=True)
class ComparisonReview:
    pairs: tuple
    stamps: tuple
    view: str
    rounds: tuple


def _rounds(pairs):
    """A new round starts immediately after the promoted child was shown."""
    round_number = 1
    result = [round_number]
    for previous, current in zip(pairs, pairs[1:]):
        if not _same_file(previous.right, current.right):
            if not _same_file(previous.left, current.right):
                raise ValueError(
                    f"Row {current.identifier}: the new parent must be the preceding candidate "
                    f"({previous.identifier}). Put the promoted candidate last in each round."
                )
            round_number += 1
        result.append(round_number)
    return tuple(result)


def prepare_comparison_review(config, view, cancel_event=None):
    """Preview resolved pairs and check lineage order before writing a review."""
    if view not in {"overlay", "mutations", "lineage"}:
        raise ValueError("Choose an overlay, sequence differences, or an optimization path")
    if view == "lineage" and (config.source != "csv" or config.mode != "paired"):
        raise ValueError("An optimization path needs a CSV with explicit candidate and parent columns")
    pairs, stamps = prepare_pairs(config, cancel_event)
    rounds = _rounds(pairs) if view == "lineage" else ()
    _cancelled(cancel_event)
    return ComparisonReview(pairs, stamps, view, rounds)


def _configuration(review):
    lineage = review.view == "lineage"
    steps = []
    if lineage:
        # Keep a common frame even when a promoted child originally has a
        # different coordinate system from the first parent.
        steps.append({"type": "object", "name": "anchor", "path_column": "Anchor file",
                      "fixed": True, "on_load_cmd": "zoom {anchor}, 5, state=1, animate=0\n"
                                                   "disable {anchor}"})
    parent_setup = ("pp_compare {parent}, {anchor}, mode=fit" if lineage
                    else "zoom {parent}, 5, state=1, animate=0")
    steps.extend([
        {"type": "object", "name": "parent", "path_column": "Reference file",
         "fixed": True, "on_load_cmd": parent_setup},
        {"type": "object", "name": "candidate"},
        {"type": "commands"},
    ])
    view = "contacts" if lineage else review.view
    columns = ["Candidate", "Reference"]
    if lineage:
        columns = ["Round", "Candidate", "Reference", "Promotion"]
    step_cmd = f"pp_compare {{candidate}}, {{parent}}, mode={view}"
    if lineage:
        step_cmd = "disable {anchor}\n" + step_cmd
    return validate_review_config({
        "path_column": "Candidate file", "id_column": "ID", "display_columns": columns,
        "steps": steps, "step_cmd": step_cmd,
    })


def write_comparison_review(review, destination, cancel_event=None):
    """Write a new review in a unique folder. Source structures stay in place."""
    _check_inputs(review, cancel_event)
    parent_dir = Path(destination)
    if not parent_dir.is_dir():
        raise ValueError(f"Choose an existing folder for the comparison review: {destination}")
    config = _configuration(review)
    folder = Path(tempfile.mkdtemp(prefix="comparison-", dir=str(parent_dir)))
    return _write_review(review, config, folder, cancel_event)


def _check_inputs(review, cancel_event):
    for stamp in review.stamps:
        _cancelled(cancel_event)
        if file_stamp(stamp[0]) != stamp:
            raise ValueError(f"Input changed after preview. Preview the pairs again: {stamp[0]}")


def _write_review(review, config, folder, cancel_event):
    csv_path = folder / "sum_scores.csv"
    try:
        (folder / "review.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        fields = ["ID", "Candidate", "Reference", "Candidate file", "Reference file"]
        if review.rounds:
            fields.extend(["Round", "Promotion", "Anchor file"])
        # The table is published last because it is the Annotator open target.
        pending = folder / ".pairs.csv"
        with pending.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for i, pair in enumerate(review.pairs):
                _cancelled(cancel_event)
                row = {"ID": pair.identifier, "Candidate": Path(pair.left).name,
                       "Reference": Path(pair.right).name, "Candidate file": pair.left,
                       "Reference file": pair.right}
                if review.rounds:
                    last_in_round = i + 1 < len(review.rounds) and review.rounds[i] != review.rounds[i + 1]
                    row.update({"Round": review.rounds[i], "Promotion": "Next parent" if last_in_round else "",
                                "Anchor file": review.pairs[0].right})
                writer.writerow(row)
        _check_inputs(review, cancel_event)
        _cancelled(cancel_event)
        os.replace(pending, csv_path)
    except Exception:
        shutil.rmtree(folder)
        raise
    return csv_path
