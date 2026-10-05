"""Read-only batch input preparation using the canonical CSV/path APIs."""

from dataclasses import asdict, dataclass
import glob
import os
from pathlib import Path
import uuid

from pymolplugins.annotation.command_templates import format_commands
from pymolplugins.annotation.mapping_store import get_mapping_profile_readonly
from pymolplugins.annotation.path_resolver import PathResolver
from pymolplugins.annotation.sources.csv_source import CsvDatasetSource
from pymolplugins.core.windows_paths import windows_long_path
from .batch_plan import BatchPair, id_pairs, id_pattern_glob, natural_key, reference_pairs, select_ids


STRUCTURE_SUFFIXES = (".pdb", ".cif", ".mmcif", ".mol", ".mol2", ".sdf",
                      ".pdb.gz", ".cif.gz", ".mmcif.gz")


@dataclass(frozen=True)
class BatchConfig:
    a: str = ""
    b: str = ""
    mode: str = "reference"
    source: str = "files"
    match: str = "template"
    a_pattern: str = r"A_(?P<id>.+)\.pdb"
    b_pattern: str = r"B_(?P<id>.+)\.pdb"
    commands: str = "align {A}, {B}"
    output: str = ""
    ids: str = ""
    a_column: str = "structure_file"
    id_column: str = ""
    encoding: str = "utf-8-sig"
    delimiter: str = "auto"
    mapping_profile: str = "custom"
    source_base: str = ""
    target_format: str = "custom"
    wsl_distribution: str = ""
    save_b: bool = True
    on_error: str = "stop"


@dataclass(frozen=True)
class BatchItem:
    pair: BatchPair
    output_a: str
    output_b: str
    commands: tuple
    object_a: str
    object_b: str
    object_reference: str


@dataclass(frozen=True)
class BatchPlan:
    config: BatchConfig
    items: tuple
    stamps: tuple
    report_path: str
    events_path: str

    def to_dict(self):
        return asdict(self)


def _cancelled(event):
    if event is not None and event.is_set():
        raise InterruptedError("Batch preparation cancelled")


def native_path(value):
    return windows_long_path(os.path.abspath(os.path.expanduser(str(value))))


def _same_file(left, right):
    """Mapped filesystems can report inode zero for every distinct file."""
    if os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right)):
        return True
    first, second = os.stat(left), os.stat(right)
    return bool(first.st_ino and second.st_ino and os.path.samestat(first, second))


def _file_inputs(config):
    left = native_path(config.a)
    if config.mode == "paired":
        right = native_path(config.b)
        if config.match == "regex":
            if not os.path.isdir(left) or not os.path.isdir(right):
                raise ValueError("Regex pairing requires A and B directories, plus separate filename expressions")
            with os.scandir(left) as entries:
                left_names = [entry.path for entry in entries if entry.is_file()]
            with os.scandir(right) as entries:
                right_names = [entry.path for entry in entries if entry.is_file()]
            left_pattern, right_pattern = config.a_pattern, config.b_pattern
        else:
            left_names, right_names = (glob.glob(id_pattern_glob(path, config.match)) for path in (left, right))
            left_pattern, right_pattern = left, right
        return id_pairs(left_names, right_names, left_pattern, right_pattern, config.ids,
                        case_sensitive=os.name != "nt", match=config.match)
    pattern = os.path.join(glob.escape(left), "*") if os.path.isdir(left) else left
    names = sorted((name for name in glob.glob(pattern)
                    if os.path.isfile(name) and name.lower().endswith(STRUCTURE_SUFFIXES)), key=natural_key)
    reference = native_path(config.b)
    pairs = reference_pairs([name for name in names if not _same_file(name, reference)], reference)
    indexed = {pair.identifier: pair for pair in pairs}
    return tuple(indexed[value] for value in select_ids(indexed, config.ids))


def _resolver(config):
    if config.mapping_profile in {"", "custom"}:
        profile = {"rules": [], "source_format": "custom"}
    else:
        profile = get_mapping_profile_readonly(config.mapping_profile)
        if profile is None:
            raise ValueError("Unknown mapping profile: " + config.mapping_profile)
    return PathResolver.from_profile(
        profile, target_format=config.target_format,
        wsl_distribution=config.wsl_distribution,
        exists=lambda path: os.path.exists(windows_long_path(path)),
        readable=lambda path: os.access(windows_long_path(path), os.R_OK),
        is_file=lambda path: os.path.isfile(windows_long_path(path)))


def _resolved(resolver, raw, base, identifier, side):
    result = resolver.resolve(raw, source_base=base, require_exists=True)
    if result.status != "available":
        raise ValueError("ID %s, %s path %r: %s (%s)" %
                         (identifier, side, raw, result.status, "; ".join(result.diagnostics)))
    return native_path(result.resolved_path)


def _csv_inputs(config, event):
    if config.mode == "paired" and not config.id_column:
        raise ValueError("Paired CSV processing requires an explicit ID column")
    dataset = CsvDatasetSource(native_path(config.a), config.a_column,
                               id_column=config.id_column, display_columns=(),
                               source_base=config.source_base, encoding=config.encoding,
                               delimiter=config.delimiter).load(cancel_event=event)
    if config.mode == "paired" and config.b not in dataset.columns:
        raise ValueError("B column is not an exact CSV header: " + config.b)
    indexed = {}
    for entry in dataset.entries:
        _cancelled(event)
        if entry.validation_errors:
            raise ValueError("CSV row %s: %s" % (entry.row_number, "; ".join(entry.validation_errors)))
        identifier = entry.metadata[config.id_column] if config.id_column else str(entry.ordinal + 1)
        if not identifier.strip() or identifier in indexed:
            raise ValueError("Empty or duplicate CSV ID: %r" % identifier)
        indexed[identifier] = entry
    ids = select_ids(indexed, config.ids) if config.ids else tuple(indexed)
    resolver, pairs = _resolver(config), []
    reference = native_path(config.b) if config.mode == "reference" else ""
    for identifier in ids:
        _cancelled(event)
        entry = indexed[identifier]
        left = _resolved(resolver, entry.source_path, dataset.source_base, identifier, "A")
        right = reference or _resolved(resolver, entry.metadata[config.b], dataset.source_base, identifier, "B")
        if _same_file(left, right):
            if config.mode == "reference":
                continue
            raise ValueError("ID %s uses the same file for A and B" % identifier)
        pairs.append(BatchPair(identifier, left, right))
    return tuple(pairs)


def file_stamp(path):
    stat = os.stat(windows_long_path(path))
    if not os.path.isfile(windows_long_path(path)) or stat.st_size == 0:
        raise ValueError("Structure input is not a nonempty regular file: " + str(path))
    return (str(path), stat.st_size, stat.st_mtime_ns)


def prepare_batch(config, cancel_event=None):
    """Read, resolve and render a batch without any scene or filesystem writes."""
    if config.mode not in {"reference", "paired"} or config.source not in {"files", "csv"}:
        raise ValueError("Mode must be reference/paired and source must be files/csv")
    if config.on_error not in {"stop", "continue"}:
        raise ValueError("On error must be stop or continue")
    if config.match not in {"template", "glob", "regex"}:
        raise ValueError("Pair matching must be template, glob or regex")
    if not config.a.strip() or not config.b.strip() or not config.output.strip():
        raise ValueError("A input, B reference/pattern/column and output directory are required")
    _cancelled(cancel_event)
    csv_stamp = file_stamp(native_path(config.a)) if config.source == "csv" else None
    pairs = _csv_inputs(config, cancel_event) if config.source == "csv" else _file_inputs(config)
    if not pairs:
        raise ValueError("No A/B pairs to process")
    output = Path(native_path(config.output))
    if output.exists() and not output.is_dir():
        raise ValueError("Output must be a directory")
    sources = dict.fromkeys(path for pair in pairs for path in (pair.left, pair.right))
    if output.exists() and any(_same_file(str(output), str(Path(path).parent)) for path in sources):
        raise ValueError("Choose a separate output directory, not an input structure directory")
    if config.source == "csv":
        sources[native_path(config.a)] = None
    stamps = []
    for path in sources:
        _cancelled(cancel_event)
        stamp = file_stamp(path)
        if csv_stamp is not None and path == csv_stamp[0] and stamp != csv_stamp:
            raise ValueError("CSV changed while preparing; prepare again: " + path)
        stamps.append(stamp)
    token = uuid.uuid4().hex[:12]
    items = []
    # The 3.1 Host GUI lost selectors when deleted object names were recycled.
    for index, pair in enumerate(pairs, 1):
        _cancelled(cancel_event)
        object_a, object_b, reference = ("pp_batch_%s_%d_%s" % (token, index, suffix)
                                        for suffix in ("A", "B", "ref"))
        if _same_file(pair.left, pair.right):
            raise ValueError("ID %s uses the same file for A and B" % pair.identifier)
        path_a = windows_long_path(str(output / ("%06d_A_%s" % (index, Path(pair.left).name))))
        path_b = windows_long_path(str(output / ("%06d_B_%s" % (index, Path(pair.right).name)))) if config.save_b else ""
        for path in (path_a, path_b):
            if path and os.path.lexists(path):
                raise ValueError("Output already exists; nothing was overwritten: " + path)
        context = {"A": object_a, "B": object_b, "id": pair.identifier, "index": index,
                   "A_source": pair.left, "B_source": pair.right, "output": path_a,
                   "output_A": path_a, "output_dir": str(output)}
        if path_b:
            context["output_B"] = path_b
        commands = tuple(format_commands(config.commands, context))
        if not commands:
            raise ValueError("At least one command is required")
        items.append(BatchItem(pair, path_a, path_b, commands, object_a, object_b, reference))
    return BatchPlan(config, tuple(items), tuple(stamps),
                     windows_long_path(str(output / ("batch-%s.json" % token))),
                     windows_long_path(str(output / ("batch-%s.jsonl" % token))))
