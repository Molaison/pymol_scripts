"""Optional, serial PLIP backend used by the WSL worker."""

import os
import tempfile
import threading
from typing import Dict, Mapping

from pymolplugins.interactions.plip_protocol import (
    PlipProtocolError,
    interaction_result_from_plip,
    validate_plip_request,
)
from pymolplugins.interactions.plip_runner import build_raw_payload, get_plip_version


class PlipBackendError(RuntimeError):
    pass


class PlipRequestError(PlipBackendError):
    """The PLIP request failed host-independent validation."""


class PlipUnavailableError(PlipBackendError):
    """PLIP is not importable in this worker environment."""


class PlipScientificContractError(PlipBackendError):
    """PLIP produced data outside the canonical interaction contract."""


class PlipBackend:
    def __init__(self):
        self._error = None
        self._classes = None
        self._version = None
        self._lock = threading.Lock()
        try:
            import plip
            from plip.structure.preparation import PDBComplex
            from plip.basic.remote import VisualizerData

            self._classes = (PDBComplex, VisualizerData)
            self._version = get_plip_version(plip)
        except Exception as exc:
            self._error = exc

    def health(self) -> Dict[str, object]:
        return {
            "available": self._classes is not None,
            "version": self._version,
            "error": str(self._error) if self._error else None,
            "action": (
                None
                if self._classes is not None
                else (
                    "Install or activate PLIP in this worker environment, then "
                    "restart the worker."
                )
            ),
        }

    def analyze(self, request: Mapping[str, object]) -> Dict[str, object]:
        try:
            parsed = validate_plip_request(request)
        except PlipProtocolError as exc:
            raise PlipRequestError(f"invalid PLIP request: {exc}") from exc
        if self._classes is None:
            raise PlipUnavailableError(f"PLIP is unavailable: {self._error}")
        with self._lock:
            return self._analyze_validated(parsed)

    def _analyze_validated(self, parsed) -> Dict[str, object]:
        try:
            PDBComplex, VisualizerData = self._classes
            mol = PDBComplex()
            try:
                mol.load_pdb(parsed.structure_text, as_string=True)
            except TypeError:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".pdb", encoding="utf-8", delete=False
                ) as handle:
                    handle.write(parsed.structure_text)
                    path = handle.name
                try:
                    mol.load_pdb(path)
                finally:
                    os.unlink(path)
            mol.analyze()
            raw = build_raw_payload(
                mol,
                VisualizerData,
                parsed.structure_text,
                self._version or "unknown",
            )
            return interaction_result_from_plip(parsed, raw).to_dict()
        except PlipProtocolError as exc:
            raise PlipScientificContractError(f"invalid PLIP result: {exc}") from exc
        except Exception as exc:
            raise PlipBackendError(f"PLIP analysis failed: {exc}") from exc
