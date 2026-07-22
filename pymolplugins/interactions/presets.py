"""Pure-data registry for supported local interaction presets."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


_PRESET_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PresetDefinition:
    """A discoverable interaction preset and its public parameter contract."""

    name: str
    summary: str
    backend: str
    parameters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    aliases: Tuple[str, ...] = ()

    def validate(self) -> None:
        if not _PRESET_NAME.match(self.name):
            raise ValueError("invalid preset name %r" % self.name)
        if not self.summary:
            raise ValueError("preset %s has an empty summary" % self.name)
        if not self.backend:
            raise ValueError("preset %s has an empty backend" % self.name)
        for alias in self.aliases:
            if not _PRESET_NAME.match(alias):
                raise ValueError("invalid preset alias %r" % alias)
        for name, definition in self.parameters.items():
            if not _PRESET_NAME.match(name):
                raise ValueError("invalid parameter name %r" % name)
            if "default" not in definition or "description" not in definition:
                raise ValueError(
                    "parameter %s.%s needs default and description" % (self.name, name)
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "backend": self.backend,
            "parameters": {
                key: dict(value) for key, value in sorted(self.parameters.items())
            },
        }


class PresetRegistry:
    """Registry enforcing uniqueness across canonical names and aliases."""

    def __init__(self, definitions: Iterable[PresetDefinition] = ()) -> None:
        self._canonical: Dict[str, PresetDefinition] = {}
        self._names: Dict[str, str] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: PresetDefinition) -> None:
        definition.validate()
        all_names = (definition.name,) + tuple(definition.aliases)
        conflicts = sorted(name for name in all_names if name in self._names)
        if conflicts:
            raise ValueError("duplicate preset names/aliases: %s" % ", ".join(conflicts))
        self._canonical[definition.name] = definition
        for name in all_names:
            self._names[name] = definition.name

    def resolve(self, name: str) -> PresetDefinition:
        requested = str(name).strip().lower()
        canonical = self._names.get(requested)
        if canonical is None:
            raise KeyError(
                "unknown interaction preset %r; available: %s"
                % (name, ", ".join(self.names()))
            )
        return self._canonical[canonical]

    def get(self, name: str) -> Optional[PresetDefinition]:
        try:
            return self.resolve(name)
        except KeyError:
            return None

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._canonical))

    def definitions(self) -> Tuple[PresetDefinition, ...]:
        return tuple(self._canonical[name] for name in self.names())


_COMMON_LOCAL = {
    "hbond_cutoff": {
        "default": 3.5,
        "type": "number",
        "description": "Hydrogen-bond donor/acceptor cutoff in Angstrom.",
    },
    "salt_cutoff": {
        "default": 4.0,
        "type": "number",
        "description": "Positive/negative atom cutoff in Angstrom.",
    },
}


PRESETS = PresetRegistry(
    (
        PresetDefinition(
            name="interchain_basic",
            aliases=("interchain",),
            summary="Local PyMOL hydrogen bonds and salt bridges between chain partners.",
            backend="pymol-local",
            parameters=dict(_COMMON_LOCAL),
        ),
        PresetDefinition(
            name="polar_qc",
            summary=(
                "Classify good and weak donor/acceptor pairs plus "
                "donor-donor and acceptor-acceptor clashes."
            ),
            backend="pymol-local",
            parameters={
                "good_cutoff": {
                    "default": 3.6,
                    "type": "number",
                    "description": "Maximum heavy-atom distance for good polar pairs.",
                },
                "weak_cutoff": {
                    "default": 4.0,
                    "type": "number",
                    "description": "Maximum heavy-atom distance for weak polar pairs.",
                },
                "clash_cutoff": {
                    "default": 3.6,
                    "type": "number",
                    "description": "Maximum distance for like-polar donor/acceptor clashes.",
                },
            },
        ),
        PresetDefinition(
            name="ligand_quick",
            aliases=("ligand",),
            summary=(
                "Focused 4 A ligand site with sticks and hydrogen bonds; "
                "no surface, labels, or all-pair distances."
            ),
            backend="pymol-local",
            parameters={
                "site_cutoff": {
                    "default": 4.0,
                    "type": "number",
                    "description": "Binding-site residue cutoff in Angstrom.",
                },
                "hbond_cutoff": dict(_COMMON_LOCAL["hbond_cutoff"]),
            },
        ),
        PresetDefinition(
            name="ligand_site",
            aliases=("binding_site",),
            summary=(
                "Focused 5 A ligand site with hydrogen bonds, salt bridges, "
                "and property coloring; surface and labels are opt-in."
            ),
            backend="pymol-local",
            parameters={
                "site_cutoff": {
                    "default": 5.0,
                    "type": "number",
                    "description": "Binding-site residue cutoff in Angstrom.",
                },
                "hbond_cutoff": dict(_COMMON_LOCAL["hbond_cutoff"]),
                "salt_cutoff": dict(_COMMON_LOCAL["salt_cutoff"]),
                "surface": {
                    "default": False,
                    "type": "boolean",
                    "description": "Show surface on the generated interface copy.",
                },
                "labels": {
                    "default": False,
                    "type": "boolean",
                    "description": "Label residues on the generated interface copy.",
                },
            },
        ),
        PresetDefinition(
            name="plip_overview",
            summary="Complete eight-category PLIP data with one canonical result tree.",
            backend="plip-wsl",
            parameters={
                "site_cutoff": {
                    "default": 5.0,
                    "type": "number",
                    "description": "Display-interface cutoff in Angstrom.",
                },
            },
        ),
        PresetDefinition(
            name="plip_publication",
            summary=(
                "Complete PLIP data with a focused interface display; "
                "surface and labels remain opt-in."
            ),
            backend="plip-wsl",
            parameters={
                "site_cutoff": {
                    "default": 5.0,
                    "type": "number",
                    "description": "Display-interface cutoff in Angstrom.",
                },
                "surface": {
                    "default": False,
                    "type": "boolean",
                    "description": "Show surface on the generated interface copy.",
                },
                "labels": {
                    "default": False,
                    "type": "boolean",
                    "description": "Label residues on the generated interface copy.",
                },
            },
        ),
    )
)


def preset_parameters(definition: PresetDefinition) -> Dict[str, Any]:
    """Return a detached mapping of parameter defaults for a preset."""

    definition.validate()
    return {
        name: parameter["default"]
        for name, parameter in definition.parameters.items()
    }
