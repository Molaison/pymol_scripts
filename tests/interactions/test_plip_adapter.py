import copy
import hashlib
import sys
from types import ModuleType
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pymolplugins.interactions.schema import InteractionResult
from pymolplugins.interactions.plip_protocol import (
    PLIP_INTERACTION_TYPES,
    PLIP_RAW_SCHEMA,
    PLIP_RAW_VERSION,
    PlipProtocolError,
    atom_map_from_pymol_export,
    build_plip_request_from_pymol,
    interaction_result_from_plip,
    normalize_pymol_pdb_export,
    validate_plip_raw,
    validate_plip_request,
)
from pymolplugins.interactions.plip_runner import _site_payload
from pymolplugins.interactions.plip_runner import build_raw_payload, get_plip_version
from pymolplugins.worker.backends.plip import PlipBackend, PlipBackendError


ATOM_SPECS = (
    (1, "C1", "LIG", "A", 1, "", "", "C"),
    (2, "C2", "LIG", "", 42, "A", "B", "C"),
    (3, "N1", "LYS", "A", 10, "", "", "N"),
    (4, "O1", "ASP", "A", 11, "", "", "O"),
    (5, "CL1", "LIG", "A", 1, "", "", "CL"),
    (6, "O2", "GLU", "A", 12, "", "", "O"),
    (7, "OW", "HOH", "W", 2, "", "", "O"),
    (8, "ZN", "ZN", "M", 3, "", "", "ZN"),
)


def _pdb_line(spec, x=0.0):
    serial, name, resn, chain, resseq, insertion, alt, element = spec
    return (
        f"HETATM{serial:5d} {name:<4}{alt:1}{resn:>3} {chain:1}"
        f"{resseq:4d}{insertion:1}   {x:8.3f}{0.0:8.3f}{0.0:8.3f}"
        f"{1.0:6.2f}{20.0:6.2f}      {'':4}{element:>2}  \n"
    )


def _request():
    pdb_text = "".join(
        _pdb_line(spec, float(index)) for index, spec in enumerate(ATOM_SPECS)
    ) + "END\n"
    atom_map = []
    for offset, spec in enumerate(ATOM_SPECS, start=101):
        serial, name, resn, chain, resseq, insertion, alt, element = spec
        atom_map.append(
            {
                "export_serial": serial,
                "source_index": offset,
                "object": "complex",
                "name": name,
                "element": element,
                "alt": alt,
                "residue": {
                    "resn": resn,
                    "chain": chain,
                    "segi": "",
                    "resi": str(resseq) + insertion,
                    "insertion": insertion,
                },
            }
        )
    return {
        "schema_version": 1,
        "run_id": "plip-run-1",
        "preset": "plip_overview",
        "structure": {"format": "pdb", "text": pdb_text},
        "source": {"object": "complex", "state": 2, "sha256": "a" * 64},
        "atom_map": atom_map,
        "partners": [
            {"role": "receptor", "selection": "complex and polymer", "atom_count": 4},
            {"role": "ligand", "selection": "complex and resn LIG", "atom_count": 3},
        ],
        "parameters": {"preset": "plip_overview", "state": 2},
    }


def _pymol_atom(spec, source_index):
    unused_serial, name, resn, chain, resseq, insertion, alt, element = spec
    return SimpleNamespace(
        model="complex",
        index=source_index,
        name=name,
        resn=resn,
        chain=chain,
        resi=str(resseq) + insertion,
        resv=resseq,
        segi="",
        alt=alt,
        symbol=element,
    )


class _PymolExportCmd:
    def __init__(self, pdb_text, atoms, export_error=None):
        self.pdb_text = pdb_text
        self.model = SimpleNamespace(atom=list(atoms))
        self.calls = []
        self.export_error = export_error
        self.settings = {
            "pdb_conect_all": "off",
            "pdb_conect_nodup": "on",
            "pdb_retain_ids": "on",
            "pdb_use_ter_records": "0",
        }

    def get_model(self, selection, state=1):
        self.calls.append(("get_model", selection, state))
        return self.model

    def get_pdbstr(self, selection, state=1):
        self.calls.append(("get_pdbstr", selection, state))
        if self.export_error is not None:
            raise self.export_error
        return self.pdb_text

    def get_setting_text(self, name):
        return self.settings[name]

    def set(self, name, value):
        self.calls.append(("set", name, value))
        self.settings[name] = value


def _row(kind, participants, distance, angles=(), subtype="fixture", **properties):
    return {
        "type": kind,
        "subtype": subtype,
        "participants": [
            {"role": role, "export_serial": serial}
            for role, serial in participants
        ],
        "geometry": {
            "distance_angstrom": distance,
            "angles_degree": list(angles),
        },
        "properties": properties,
    }


def _raw(pdb_text):
    interactions = (
        _row("hydrophobic", (("protein", 3), ("ligand", 1)), 3.8),
        _row("hydrogen_bond", (("donor", 3), ("acceptor", 2)), 2.9, (155.0,)),
        _row("halogen_bond", (("donor", 5), ("acceptor", 6)), 3.2, (170.0, 120.0)),
        _row("water_bridge", (("donor", 3), ("acceptor", 2), ("water", 7)), 2.8, (145.0, 105.0)),
        _row("metal_complex", (("metal", 8), ("target", 6)), 2.2),
        _row(
            "pi_stacking",
            (
                ("protein_ring", 1),
                ("protein_ring", 3),
                ("ligand_ring", 2),
                ("ligand_ring", 4),
            ),
            4.6,
            (12.0,),
        ),
        _row("pi_cation", (("ring", 2), ("ring", 4), ("charge", 5), ("charge", 6)), 4.1),
        _row(
            "salt_bridge",
            (("positive", 3), ("positive", 5), ("negative", 4), ("negative", 6)),
            3.4,
        ),
    )
    return {
        "schema": PLIP_RAW_SCHEMA,
        "version": PLIP_RAW_VERSION,
        "backend": {"name": "plip", "version": "test-1.0"},
        "input_sha256": hashlib.sha256(pdb_text.encode("utf-8")).hexdigest(),
        "sites": [{"site_id": "LIG::42A", "interactions": list(interactions)}],
        "warnings": [],
    }


def _visualizer_fixture():
    return SimpleNamespace(
        hydrophobic_contacts=SimpleNamespace(pairs_ids=[(3, 1)]),
        hbonds=SimpleNamespace(ldon_id=[(2, 3)], pdon_id=[]),
        halogen_bonds=[SimpleNamespace(don_id=5, acc_id=6)],
        waterbridges=[SimpleNamespace(don_id=3, acc_id=2, water_id=7, protisdon=True)],
        metal_complexes=[SimpleNamespace(metal_id=8, target_id=6, location="protein")],
        pistacking=[
            SimpleNamespace(
                proteinring_atoms=[1, 3],
                proteinring_center=[0.0, 0.0, 0.0],
                ligandring_atoms=[2, 4],
                ligandring_center=[0.0, 0.0, 4.6],
                type="P",
            )
        ],
        pication=[
            SimpleNamespace(
                ring_center=[0.0, 0.0, 0.0],
                charge_center=[0.0, 0.0, 4.1],
                ring_atoms=[2, 4],
                charge_atoms=[5, 6],
                protcharged=False,
            )
        ],
        saltbridges=[
            SimpleNamespace(
                positive_atoms=[3, 5],
                negative_atoms=[4, 6],
                positive_center=[0.0, 0.0, 0.0],
                negative_center=[0.0, 0.0, 3.4],
                protispos=True,
            )
        ],
    )


def _interaction_set_fixture():
    ring_protein = SimpleNamespace(atoms_orig_idx=[1, 3])
    ring_ligand = SimpleNamespace(atoms_orig_idx=[2, 4])
    charge = SimpleNamespace(atoms_orig_idx=[5, 6])
    positive = SimpleNamespace(atoms_orig_idx=[3, 5])
    negative = SimpleNamespace(atoms_orig_idx=[4, 6])
    return SimpleNamespace(
        hydrophobic_contacts=[
            SimpleNamespace(bsatom_orig_idx=3, ligatom_orig_idx=1, distance=3.8)
        ],
        hbonds_ldon=[
            SimpleNamespace(
                a_orig_idx=2,
                d_orig_idx=3,
                distance_ad=2.9,
                distance_ah=1.9,
                angle=155.0,
                protisdon=False,
                type="strong",
                atype="O",
                dtype="N",
                sidechain=True,
            )
        ],
        hbonds_pdon=[],
        halogen_bonds=[
            SimpleNamespace(
                don_orig_idx=5,
                acc_orig_idx=6,
                distance=3.2,
                don_angle=170.0,
                acc_angle=120.0,
            )
        ],
        water_bridges=[
            SimpleNamespace(
                d_orig_idx=3,
                a_orig_idx=2,
                water_orig_idx=7,
                distance_dw=2.8,
                distance_aw=2.7,
                d_angle=145.0,
                w_angle=105.0,
                protisdon=True,
                type="first_deg",
            )
        ],
        metal_complexes=[
            SimpleNamespace(
                metal_orig_idx=8,
                target_orig_idx=6,
                distance=2.2,
                location="protein",
                metal_type="ZN",
                target_type="O",
                coordination_num=4,
                geometry="tetrahedral",
                rms=0.2,
                complexnum=1,
            )
        ],
        pistacking=[
            SimpleNamespace(
                proteinring=ring_protein,
                ligandring=ring_ligand,
                distance=4.6,
                angle=12.0,
                offset=0.4,
                type="P",
            )
        ],
        pication_paro=[
            SimpleNamespace(
                ring=ring_ligand,
                charge=charge,
                distance=4.1,
                offset=0.3,
                type="regular",
                protcharged=False,
            )
        ],
        pication_laro=[],
        saltbridge_lneg=[
            SimpleNamespace(
                positive=positive,
                negative=negative,
                distance=3.4,
                protispos=True,
            )
        ],
        saltbridge_pneg=[],
    )


class PymolPlipExportTests(unittest.TestCase):
    def _host_like_export(self):
        first, second = ATOM_SPECS[:2]
        return (
            _pdb_line(first, 0.0)
            + "TER   \n"
            + _pdb_line(second, 1.0)
            + "TER   \n"
            + "CONECT    1    2\n"
            + "END\n"
        )

    def test_short_ter_export_is_renumbered_and_conect_is_remapped(self):
        normalized = normalize_pymol_pdb_export(self._host_like_export())
        lines = normalized.splitlines()
        self.assertEqual(int(lines[0][6:11]), 1)
        self.assertEqual(int(lines[1][6:11]), 2)
        self.assertEqual(int(lines[2][6:11]), 3)
        self.assertEqual(int(lines[3][6:11]), 4)
        self.assertEqual(lines[4], "CONECT    1    3")

    def test_atom_map_preserves_source_identity_after_ter(self):
        atoms = [_pymol_atom(spec, index) for index, spec in enumerate(ATOM_SPECS[:2], 101)]
        normalized, atom_map = atom_map_from_pymol_export(
            self._host_like_export(), atoms
        )
        self.assertIn("HETATM    3", normalized)
        self.assertEqual([entry["export_serial"] for entry in atom_map], [1, 3])
        self.assertEqual(atom_map[1]["source_index"], 102)
        self.assertEqual(atom_map[1]["object"], "complex")
        self.assertEqual(atom_map[1]["alt"], "B")
        self.assertEqual(atom_map[1]["residue"]["chain"], "")
        self.assertEqual(atom_map[1]["residue"]["resi"], "42A")
        self.assertEqual(atom_map[1]["residue"]["insertion"], "A")

    def test_request_builder_uses_normalized_export_hash_and_state(self):
        atoms = [_pymol_atom(spec, index) for index, spec in enumerate(ATOM_SPECS[:2], 101)]
        cmd_api = _PymolExportCmd(self._host_like_export(), atoms)
        request = build_plip_request_from_pymol(
            cmd_api,
            "complex",
            state=2,
            run_id="host-run-1",
            partners=(
                {"role": "ligand", "selection": "complex and organic", "atom_count": 2},
            ),
            parameters={"state": 2},
        )
        text = request["structure"]["text"]
        self.assertEqual(
            request["source"]["sha256"],
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(request["source"]["state"], 2)
        self.assertEqual(request["run_id"], "host-run-1")
        self.assertEqual(cmd_api.calls[0], ("get_model", "complex", 2))
        self.assertIn(("get_pdbstr", "complex", 2), cmd_api.calls)
        self.assertEqual(
            cmd_api.settings,
            {
                "pdb_conect_all": "off",
                "pdb_conect_nodup": "on",
                "pdb_retain_ids": "on",
                "pdb_use_ter_records": "0",
            },
        )
        self.assertEqual(sorted(validate_plip_request(request).atom_map), [1, 3])

    def test_request_builder_restores_export_settings_after_failure(self):
        atoms = [_pymol_atom(ATOM_SPECS[0], 101)]
        cmd_api = _PymolExportCmd(
            _pdb_line(ATOM_SPECS[0]) + "END\n",
            atoms,
            export_error=RuntimeError("synthetic export failure"),
        )
        before = dict(cmd_api.settings)
        with self.assertRaisesRegex(RuntimeError, "synthetic export failure"):
            build_plip_request_from_pymol(cmd_api, "complex")
        self.assertEqual(cmd_api.settings, before)

    def test_lossy_or_ambiguous_exports_are_rejected_locally(self):
        atoms = [_pymol_atom(spec, index) for index, spec in enumerate(ATOM_SPECS[:2], 101)]
        atoms[1].chain = "AB"
        with self.assertRaisesRegex(PlipProtocolError, "identity mismatch"):
            atom_map_from_pymol_export(self._host_like_export(), atoms)

        with self.assertRaisesRegex(PlipProtocolError, "multi-model"):
            normalize_pymol_pdb_export("MODEL        1\n" + self._host_like_export())

        bad_conect = self._host_like_export().replace(
            "CONECT    1    2", "CONECT    1   99"
        )
        with self.assertRaisesRegex(PlipProtocolError, "unknown serial 99"):
            normalize_pymol_pdb_export(bad_conect)


class PlipAdapterTests(unittest.TestCase):
    def test_eight_types_preserve_identity_strings_and_geometry(self):
        request = _request()
        result = interaction_result_from_plip(
            request, _raw(request["structure"]["text"])
        )
        self.assertEqual(
            set(result.summary["counts_by_type"]), PLIP_INTERACTION_TYPES
        )
        self.assertTrue(all(count == 1 for count in result.summary["counts_by_type"].values()))
        hbond = next(item for item in result.interactions if item.type == "hydrogen_bond")
        mapped = next(item for item in hbond.participants if item.atom.export_serial == 2)
        self.assertEqual(mapped.atom.source_index, 102)
        self.assertEqual(mapped.atom.alt, "B")
        self.assertEqual(mapped.residue.chain, "")
        self.assertEqual(mapped.residue.resi, "42A")
        self.assertEqual(mapped.residue.insertion, "A")
        self.assertEqual(hbond.geometry.distance_angstrom, 2.9)
        self.assertEqual(hbond.geometry.angles_degree, (155.0,))
        self.assertEqual(result.provenance["backend_raw_schema"], PLIP_RAW_SCHEMA)
        self.assertEqual(InteractionResult.from_dict(result.to_dict()), result)

    def test_zero_site_payload_is_valid(self):
        request = _request()
        raw = _raw(request["structure"]["text"])
        raw["sites"] = []
        result = interaction_result_from_plip(request, raw)
        self.assertEqual(result.sites, ())
        self.assertEqual(result.summary["counts_by_type"], {})

    def test_duplicate_atom_map_export_serial_is_rejected(self):
        request = _request()
        request["atom_map"][1]["export_serial"] = 1
        with self.assertRaisesRegex(PlipProtocolError, "duplicate atom_map export_serial"):
            validate_plip_request(request)

    def test_duplicate_source_identity_is_rejected(self):
        request = _request()
        request["atom_map"][1]["source_index"] = request["atom_map"][0]["source_index"]
        with self.assertRaisesRegex(PlipProtocolError, "duplicate atom_map source identity"):
            validate_plip_request(request)

    def test_duplicate_pdb_serial_is_rejected(self):
        request = _request()
        request["structure"]["text"] += _pdb_line(ATOM_SPECS[0])
        with self.assertRaisesRegex(PlipProtocolError, "duplicate PDB export_serial"):
            validate_plip_request(request)

    def test_nonconsecutive_pdb_serial_is_rejected_before_plip_mapping(self):
        request = _request()
        first = ATOM_SPECS[0]
        changed = (9,) + first[1:]
        request["structure"]["text"] = request["structure"]["text"].replace(
            _pdb_line(first, 0.0), _pdb_line(changed, 0.0), 1
        )
        request["atom_map"][0]["export_serial"] = 9
        with self.assertRaisesRegex(PlipProtocolError, "PLIP record index 1"):
            validate_plip_request(request)

    def test_ter_consumes_one_plip_record_index(self):
        request = _request()
        lines = [_pdb_line(ATOM_SPECS[0], 0.0), "TER       2\n"]
        for index, spec in enumerate(ATOM_SPECS[1:], start=1):
            renumbered = (spec[0] + 1,) + spec[1:]
            lines.append(_pdb_line(renumbered, float(index)))
            request["atom_map"][index]["export_serial"] += 1
        request["structure"]["text"] = "".join(lines) + "END\n"
        parsed = validate_plip_request(request)
        self.assertEqual(sorted(parsed.atom_map), [1, 3, 4, 5, 6, 7, 8, 9])

    def test_short_pymol_ter_uses_the_same_plip_record_index(self):
        request = _request()
        lines = [_pdb_line(ATOM_SPECS[0], 0.0), "TER\n"]
        for index, spec in enumerate(ATOM_SPECS[1:], start=1):
            renumbered = (spec[0] + 1,) + spec[1:]
            lines.append(_pdb_line(renumbered, float(index)))
            request["atom_map"][index]["export_serial"] += 1
        request["structure"]["text"] = "".join(lines) + "END\n"
        parsed = validate_plip_request(request)
        self.assertEqual(sorted(parsed.atom_map), [1, 3, 4, 5, 6, 7, 8, 9])

    def test_missing_atom_map_and_unmapped_raw_serial_are_rejected(self):
        missing = _request()
        missing["atom_map"].pop()
        with self.assertRaisesRegex(PlipProtocolError, "serial mismatch"):
            validate_plip_request(missing)

        request = _request()
        raw = _raw(request["structure"]["text"])
        raw["sites"][0]["interactions"][0]["participants"][0]["export_serial"] = 99
        with self.assertRaisesRegex(PlipProtocolError, "unmapped export_serial 99"):
            interaction_result_from_plip(request, raw)

    def test_raw_schema_rejects_unknown_and_nonfinite_fields(self):
        request = _request()
        raw = _raw(request["structure"]["text"])
        raw["unexpected"] = True
        with self.assertRaisesRegex(PlipProtocolError, "unknown field"):
            validate_plip_raw(raw)

        raw = _raw(request["structure"]["text"])
        raw["sites"][0]["interactions"][0]["geometry"]["distance_angstrom"] = float("nan")
        with self.assertRaisesRegex(PlipProtocolError, "finite"):
            validate_plip_raw(raw)

    def test_schema_versions_require_integers(self):
        request = _request()
        request["schema_version"] = True
        with self.assertRaisesRegex(PlipProtocolError, "integer"):
            validate_plip_request(request)

        request = _request()
        raw = _raw(request["structure"]["text"])
        raw["version"] = 2.0
        with self.assertRaisesRegex(PlipProtocolError, "integer"):
            validate_plip_raw(raw)

    def test_raw_roles_and_required_geometry_are_strict(self):
        request = _request()
        raw = _raw(request["structure"]["text"])
        raw["sites"][0]["interactions"][0]["participants"][0]["role"] = "other"
        with self.assertRaisesRegex(PlipProtocolError, "roles must be exactly"):
            validate_plip_raw(raw)

        raw = _raw(request["structure"]["text"])
        raw["sites"][0]["interactions"][1]["geometry"]["angles_degree"] = []
        with self.assertRaisesRegex(PlipProtocolError, "must contain 1"):
            validate_plip_raw(raw)


class PlipRunnerPayloadTests(unittest.TestCase):
    def test_version_uses_plip_basic_config_package_layout(self):
        package = ModuleType("plip")
        package.__path__ = []
        basic = ModuleType("plip.basic")
        basic.__path__ = []
        config = ModuleType("plip.basic.config")
        config.__version__ = "3.0.0"
        basic.config = config
        modules = {"plip": package, "plip.basic": basic, "plip.basic.config": config}
        with patch.dict(sys.modules, modules):
            self.assertEqual(get_plip_version(package), "3.0.0")

    def test_runner_extracts_all_eight_types_with_geometry(self):
        site = _site_payload(
            _visualizer_fixture(),
            "LIG::42A",
            _interaction_set_fixture(),
        )
        rows = {row["type"]: row for row in site["interactions"]}
        self.assertEqual(set(rows), PLIP_INTERACTION_TYPES)
        self.assertEqual(rows["hydrogen_bond"]["geometry"]["distance_angstrom"], 2.9)
        self.assertEqual(rows["hydrogen_bond"]["geometry"]["angles_degree"], [155.0])
        self.assertEqual(rows["halogen_bond"]["geometry"]["angles_degree"], [170.0, 120.0])
        self.assertIn("hbonds", site)
        self.assertEqual(site["bsid"], site["site_id"])

    def test_runner_builds_a_valid_versioned_raw_payload(self):
        interaction_set = _interaction_set_fixture()
        mol = SimpleNamespace(interaction_sets={"LIG::42A": interaction_set})
        visualizer = _visualizer_fixture()
        payload = build_raw_payload(
            mol,
            lambda unused_mol, unused_site: visualizer,
            "HETATM fixture\n",
            "test-1.0",
        )
        self.assertEqual(payload["schema"], PLIP_RAW_SCHEMA)
        self.assertEqual(payload["version"], PLIP_RAW_VERSION)
        self.assertEqual(len(payload["sites"][0]["interactions"]), 8)


class PlipBackendTests(unittest.TestCase):
    def test_unavailable_backend_has_structured_failure_after_request_validation(self):
        backend = PlipBackend()
        backend._classes = None
        backend._error = ImportError("fixture unavailable")
        with self.assertRaisesRegex(PlipBackendError, "PLIP is unavailable"):
            backend.analyze(_request())

    def test_backend_returns_canonical_schema_from_mock_raw(self):
        request = _request()
        raw = _raw(request["structure"]["text"])

        class FakeComplex:
            def load_pdb(self, value, as_string=False):
                self.loaded = (value, as_string)

            def analyze(self):
                return None

        backend = PlipBackend()
        backend._classes = (FakeComplex, object)
        backend._version = "test-1.0"
        backend._error = None
        with patch("pymolplugins.worker.backends.plip.build_raw_payload", return_value=raw):
            payload = backend.analyze(request)
        self.assertEqual(payload["schema"], "pymolplugins.interactions/1.0")
        self.assertEqual(payload["run"]["backend"], {"name": "plip", "version": "test-1.0"})
        self.assertEqual(set(payload["summary"]["counts_by_type"]), PLIP_INTERACTION_TYPES)


if __name__ == "__main__":
    unittest.main()
