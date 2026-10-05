"""Sequential PyMOL batch execution with owned temporary objects and reports."""

from dataclasses import asdict
import datetime
import json
import os
from pathlib import Path
import traceback

from pymolplugins.annotation.materializer import DirectMaterializer
from .batch_sources import file_stamp


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _save_structure(cmd, path, name):
    if not cmd.count_atoms(name):
        raise RuntimeError("Cannot save an empty structure: " + name)
    with open(path, "xb"):
        pass
    try:
        cmd.save(path, name, state=0, quiet=1)
        if not Path(path).stat().st_size:
            raise RuntimeError("PyMOL produced an empty output: " + path)
    except Exception as exc:
        try:
            if os.path.lexists(path):
                os.unlink(path)
        except OSError as cleanup:
            raise RuntimeError("Save failed: %s; removing partial output %s failed: %s" %
                               (exc, path, cleanup)) from exc
        raise


class BatchRunner:
    """Advance at most one PyMOL operation per call; no worker uses cmd.*."""

    def __init__(self, plan, command_api, execute):
        self.plan, self.cmd, self.execute = plan, command_api, execute
        self.status, self.index, self.step = "prepared", 0, ""
        self.cancel_requested, self.started_at = False, ""
        self.finished_at, self.error = "", ""
        self.completed, self.failed = 0, 0
        self.current = None
        self.owned = set()
        self.reference_source = None
        self.reference_name = ""
        self.results = [dict(id=item.pair.identifier, A=item.pair.left, B=item.pair.right,
                             status="not_run", steps=[], outputs=[], error="",
                             commands=[command for _, command in item.commands],
                             planned_outputs=[path for path in (item.output_a, item.output_b) if path])
                        for item in plan.items]

    @property
    def running(self):
        return self.status == "running"

    def start(self):
        if self.status != "prepared":
            raise RuntimeError("This batch has already started; prepare a new batch")
        for stamp in self.plan.stamps:
            if file_stamp(stamp[0]) != stamp:
                raise ValueError("Input changed after preview; prepare again: " + stamp[0])
        names = set(self.cmd.get_names("all"))
        if any(names.intersection((item.object_a, item.object_b, item.object_reference)) for item in self.plan.items):
            raise ValueError("Temporary object name collision; prepare again")
        for item in self.plan.items:
            for path in (item.output_a, item.output_b):
                if path and os.path.lexists(path):
                    raise ValueError("Output already exists; prepare a new output directory: " + path)
        for path in (self.plan.report_path, self.plan.events_path):
            if os.path.lexists(path):
                raise ValueError("Report already exists; prepare again: " + path)
        Path(self.plan.report_path).parent.mkdir(parents=True, exist_ok=True)
        self.started_at = _now()
        self.status = "running"
        self._event("started", create=True, config=asdict(self.plan.config), pairs=len(self.results))

    def cancel(self):
        self.cancel_requested = True

    def _event(self, kind, *, create=False, **values):
        with open(self.plan.events_path, "x" if create else "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event=kind, time=_now(), **values), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _delete(self, names):
        errors = []
        for name in tuple(names):
            if name in self.owned:
                try:
                    self.cmd.delete(name)
                    self.owned.remove(name)
                except Exception as exc:
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                    errors.append((name, exc))
        if errors:
            raise RuntimeError("Temporary-object cleanup failed: " + "; ".join(
                "%s: %s" % error for error in errors)) from errors[0][1]

    def _load(self, path, name):
        materialized = DirectMaterializer().materialize(path)
        self.owned.add(name)
        self.cmd.load(materialized.path, name, zoom=0, quiet=1)
        if name not in set(self.cmd.get_names("objects")) or not self.cmd.count_atoms(name):
            raise RuntimeError("PyMOL did not load " + path)

    def _operations(self, item):
        yield "Load A", lambda: self._load(item.pair.left, item.object_a)
        if self.reference_source != item.pair.right:
            yield "Release previous reference", lambda: self._delete((self.reference_name,))
            self.reference_name = item.object_reference
            yield "Load B source", lambda: self._load(item.pair.right, self.reference_name)
            yield "Hide B source", lambda: self.cmd.disable(self.reference_name)
            self.reference_source = item.pair.right
        yield "Prepare B", lambda: self._copy_reference(item.object_b)
        for number, command in item.commands:
            yield "Line %d: %s" % (number, command), lambda command=command: self.execute(self.cmd, command)
        yield "Save A", lambda: self._save(item.output_a, item.object_a)
        if item.output_b:
            yield "Save B", lambda: self._save(item.output_b, item.object_b)
        yield "Release pair", lambda: self._delete((item.object_a, item.object_b))

    def _copy_reference(self, name):
        self.owned.add(name)
        self.cmd.create(name, self.reference_name, source_state=0, target_state=0, zoom=0, quiet=1)
        if name not in set(self.cmd.get_names("objects")) or not self.cmd.count_atoms(name):
            raise RuntimeError("PyMOL did not create B for this pair")
        self.cmd.enable(name)

    def _save(self, path, name):
        _save_structure(self.cmd, path, name)
        self.results[self.index]["outputs"].append(path)

    def advance(self):
        if not self.running:
            return False
        try:
            if self.cancel_requested:
                if self.current is not None:
                    self.results[self.index]["status"] = "cancelled"
                self._finish("cancelled")
                return False
            if self.current is None:
                self.results[self.index]["status"] = "running"
                self.step = "Starting pair"
                self.current = iter(self._operations(self.plan.items[self.index]))
                self._event("pair_started", id=self.results[self.index]["id"])
            operation = next(self.current, None)
            if operation is None:
                self._complete_pair()
            else:
                self.step, callback = operation
                self._event("step_started", id=self.results[self.index]["id"], step=self.step)
                try:
                    callback()
                except Exception as exc:
                    self._failed(exc)
                else:
                    self.results[self.index]["steps"].append(self.step)
                    self._event("step_completed", id=self.results[self.index]["id"], step=self.step)
        except Exception as fatal:
            self.abort(fatal)
        return self.running

    def _complete_pair(self):
        result = self.results[self.index]
        result["status"] = "completed"
        self.completed += 1
        self._event("pair_completed", result=result)
        self.index += 1
        self.current = None
        if self.index == len(self.results):
            self._finish("partial_error" if self.failed else "completed")

    def _failed(self, exc):
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        result = self.results[min(self.index, len(self.results) - 1)]
        result.update(status="failed", error=str(exc), failed_step=self.step, traceback=traceback.format_exc())
        self.failed += 1
        self._event("pair_failed", result=result)
        self._delete(tuple(self.owned))
        self.reference_source = None
        self.current = None
        if self.plan.config.on_error == "continue" and self.index + 1 < len(self.results):
            self.index += 1
        else:
            self._finish("partial_error")

    def _finish(self, status):
        self._delete(tuple(self.owned))
        self.status, self.finished_at = status, _now()
        self._event("finished", status=status)
        with open(self.plan.report_path, "x", encoding="utf-8") as handle:
            json.dump(self.report(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def report(self):
        return dict(schema="pymolplugins.batch/1.0", status=self.status,
                    started_at=self.started_at, finished_at=self.finished_at,
                    config=asdict(self.plan.config), results=self.results,
                    completed=self.completed, failed=self.failed,
                    error=self.error, owned_objects=sorted(self.owned),
                    partial_side_effect_possible=self.status in {"partial_error", "cancelled", "error"},
                    report_path=self.plan.report_path, events_path=self.plan.events_path)

    def abort(self, exc):
        """Keep cleanup/report failures visible and stop scheduling more work."""
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        self.status, self.error, self.finished_at = "error", str(exc), _now()
        if self.index < len(self.results) and self.results[self.index]["status"] == "running":
            self.results[self.index].update(status="failed", error=str(exc), failed_step=self.step,
                                            traceback=traceback.format_exc())
            self.failed += 1
        try:
            self._delete(tuple(self.owned))
        except Exception as cleanup:
            traceback.print_exception(type(cleanup), cleanup, cleanup.__traceback__)
            self.error += "\nTemporary-object cleanup failed: " + str(cleanup)
        if self.started_at:
            try:
                self._event("aborted", error=self.error, owned_objects=sorted(self.owned))
            except Exception as logging:
                traceback.print_exception(type(logging), logging, logging.__traceback__)
                self.error += "\nWriting the failure log failed: " + str(logging)
        self.current = None
