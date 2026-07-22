import threading
from types import SimpleNamespace
import unittest

from pymolplugins.worker.backends.rosetta import RosettaBackend, RosettaBackendError


class FakePDBInfo:
    def __init__(self, pose):
        self.pose = pose

    def chain(self, index):
        return self.pose.chains[index - 1]


class FakePose:
    def __init__(self, chains=()):
        self.chains = list(chains)

    def total_residue(self):
        return len(self.chains)

    def pdb_info(self):
        return FakePDBInfo(self)

    def clone(self):
        return FakePose(self.chains)


class FakeChainSelector:
    def __init__(self, expression):
        self.expression = expression


class FakeDockingPartners:
    def __init__(self):
        self.expression = None

    def docking_partners_from_string(self, expression):
        self.expression = expression


class FakePyRosetta:
    __version__ = "fake-pyrosetta-1"

    def __init__(
        self,
        pose_chains=("A", "A", "B"),
        cms_value=12.5,
        bound_bunsat_value=7.0,
        interface_bunsat_value=2.0,
        init_error=None,
    ):
        self.pose_chains = list(pose_chains)
        self.cms_value = cms_value
        self.bound_bunsat_value = bound_bunsat_value
        self.interface_bunsat_value = interface_bunsat_value
        self.init_error = init_error
        self.init_calls = []
        self.import_calls = []
        self.cms_instances = []
        self.bunsat_instances = []
        self.interface_instances = []

        owner = self

        class FakeVersion:
            @staticmethod
            def version():
                return "fake-rosetta-1"

        class FakeCMSFilter:
            def __init__(self):
                self.selector1_value = None
                self.selector2_value = None
                self.use_rosetta_radii_value = None
                self.quick_value = None
                self.distance_weight_value = None
                self.filtered_area_value = None
                owner.cms_instances.append(self)

            def selector1(self, selector):
                self.selector1_value = selector

            def selector2(self, selector):
                self.selector2_value = selector

            def use_rosetta_radii(self, value):
                self.use_rosetta_radii_value = value

            def quick(self, value):
                self.quick_value = value

            def distance_weight(self, value):
                self.distance_weight_value = value

            def filtered_area(self, value):
                self.filtered_area_value = value

            def compute(self, pose):
                return owner.cms_value

        class FakeBunsatFilter:
            def __init__(self):
                self.report_all_heavy_atom_unsats = None
                self.ignore_bb_heavy_unsats = None
                self.probe_radius = None
                self.burial_cutoff = None
                self.max_hbond_energy = None
                owner.bunsat_instances.append(self)

            def set_report_all_heavy_atom_unsats(self, value):
                self.report_all_heavy_atom_unsats = value

            def set_ignore_bb_heavy_unsats(self, value):
                self.ignore_bb_heavy_unsats = value

            def set_probe_radius(self, value):
                self.probe_radius = value

            def set_burial_cutoff(self, value):
                self.burial_cutoff = value

            def set_max_hbond_energy(self, value):
                self.max_hbond_energy = value

            def compute(self, pose):
                return owner.bound_bunsat_value

        class FakeInterfaceAnalyzer:
            def __init__(self, interface):
                self.interface = interface
                self.compute_unsat = None
                self.calc_dsasa = None
                self.compute_energy = None
                self.compute_sc = None
                self.compute_packstat = None
                self.applied = False
                owner.interface_instances.append(self)

            def set_compute_interface_delta_hbond_unsat(self, value):
                self.compute_unsat = value

            def set_calc_dSASA(self, value):
                self.calc_dsasa = value

            def set_compute_interface_energy(self, value):
                self.compute_energy = value

            def set_compute_interface_sc(self, value):
                self.compute_sc = value

            def set_compute_packstat(self, value):
                self.compute_packstat = value

            def apply(self, pose):
                self.applied = True

            def get_interface_delta_hbond_unsat(self):
                return owner.interface_bunsat_value

            def get_interface_delta_sasa(self):
                return 101.25

            def get_interface_dG(self):
                return -8.5

            def get_all_data(self):
                return SimpleNamespace(sc_value=0.72)

            def get_interface_packstat(self):
                return 0.61

        def pose_from_pdbstring(pose, text):
            owner.import_calls.append(text)
            if text == "IMPORT_ERROR":
                raise RuntimeError("synthetic import failure")
            pose.chains = list(owner.pose_chains)

        self.rosetta = SimpleNamespace(
            utility=SimpleNamespace(Version=FakeVersion),
            core=SimpleNamespace(
                import_pose=SimpleNamespace(pose_from_pdbstring=pose_from_pdbstring),
                select=SimpleNamespace(
                    residue_selector=SimpleNamespace(ChainSelector=FakeChainSelector)
                ),
                pose=SimpleNamespace(DockingPartners=FakeDockingPartners),
            ),
            protocols=SimpleNamespace(
                simple_filters=SimpleNamespace(
                    ContactMolecularSurfaceFilter=FakeCMSFilter,
                    BuriedUnsatHbondFilter=FakeBunsatFilter,
                ),
                analysis=SimpleNamespace(InterfaceAnalyzerMover=FakeInterfaceAnalyzer),
            ),
        )

    def init(self, flags):
        self.init_calls.append(flags)
        if self.init_error is not None:
            raise self.init_error

    def Pose(self):
        return FakePose()


def backend_with(fake):
    """Construct a backend without running its real PyRosetta import path."""
    backend = RosettaBackend.__new__(RosettaBackend)
    backend.init_flags = "-fake-flags"
    backend._pyrosetta = fake
    backend._import_error = None
    backend._initialized = False
    backend._lock = threading.Lock()
    return backend


def request_for(metrics=None, options=None):
    return {
        "schema_version": 1,
        "structure": {"format": "pdb", "text": "FAKE_PDB"},
        "partners": {"target_chains": ["A"], "binder_chains": ["B"]},
        "metrics": list(metrics or ["cms"]),
        "options": dict(options or {}),
    }


class RosettaBackendContractTests(unittest.TestCase):
    def test_initialize_happens_once_across_multiple_requests(self):
        fake = FakePyRosetta()
        backend = backend_with(fake)

        first = backend.analyze(request_for())
        second = backend.analyze(request_for())

        self.assertEqual(fake.init_calls, ["-fake-flags"])
        self.assertEqual(len(fake.import_calls), 2)
        self.assertEqual(first["metrics"]["cms"], 12.5)
        self.assertEqual(second["metrics"]["cms"], 12.5)
        self.assertTrue(backend.health()["initialized"])

    def test_unknown_fields_are_rejected_before_initialization(self):
        cases = (
            (("unexpected",), "unknown request fields"),
            (("structure", "path"), "unknown structure fields"),
            (("partners", "selection"), "unknown partners fields"),
            (("options", "command"), "unknown options fields"),
        )
        for path, message in cases:
            with self.subTest(path=path):
                fake = FakePyRosetta()
                backend = backend_with(fake)
                request = request_for()
                if len(path) == 1:
                    request[path[0]] = "forbidden"
                else:
                    request[path[0]][path[1]] = "forbidden"
                with self.assertRaisesRegex(RosettaBackendError, message):
                    backend.analyze(request)
                self.assertEqual(fake.init_calls, [])

    def test_options_are_strictly_typed_and_bounded(self):
        invalid = (
            ({"quick": "false"}, "options.quick must be a boolean"),
            ({"distance_weight": True}, "options.distance_weight must be a number"),
            ({"probe_radius": 0}, "options.probe_radius must be > 0"),
            ({"burial_cutoff": -1}, "options.burial_cutoff must be >= 0"),
        )
        for options, message in invalid:
            with self.subTest(options=options):
                fake = FakePyRosetta()
                backend = backend_with(fake)
                with self.assertRaisesRegex(RosettaBackendError, message):
                    backend.analyze(request_for(options=options))
                self.assertEqual(fake.init_calls, [])

    def test_schema_version_requires_integer_one(self):
        fake = FakePyRosetta()
        backend = backend_with(fake)
        request = request_for()
        request["schema_version"] = 1.0

        with self.assertRaisesRegex(RosettaBackendError, "integer 1"):
            backend.analyze(request)
        self.assertEqual(fake.init_calls, [])

    def test_partner_overlap_is_rejected_before_initialization(self):
        fake = FakePyRosetta()
        backend = backend_with(fake)
        request = request_for()
        request["partners"]["binder_chains"] = ["A"]

        with self.assertRaisesRegex(RosettaBackendError, "must be disjoint"):
            backend.analyze(request)
        self.assertEqual(fake.init_calls, [])

    def test_target_and_binder_chains_must_exist_in_imported_pose(self):
        cases = (
            (("B",), "missing target chains: A"),
            (("A",), "missing binder chains: B"),
        )
        for pose_chains, message in cases:
            with self.subTest(pose_chains=pose_chains):
                fake = FakePyRosetta(pose_chains=pose_chains)
                backend = backend_with(fake)
                with self.assertRaisesRegex(RosettaBackendError, message):
                    backend.analyze(request_for())

    def test_first_three_metrics_are_separate_typed_fields_with_provenance(self):
        fake = FakePyRosetta(
            cms_value=13.25,
            bound_bunsat_value=7.0,
            interface_bunsat_value=3.0,
        )
        backend = backend_with(fake)
        request = request_for(
            metrics=("cms", "bunsat_bound_heavy", "bunsat_interface_delta"),
            options={
                "use_rosetta_radii": False,
                "filtered_area": 4.5,
                "probe_radius": 1.4,
                "burial_cutoff": 2.0,
                "max_hbond_energy": -0.5,
            },
        )

        result = backend.analyze(request)

        self.assertEqual(
            set(result["metrics"]),
            {"cms", "bunsat_bound_heavy", "bunsat_interface_delta"},
        )
        self.assertEqual(result["metrics"]["cms"], 13.25)
        self.assertIsInstance(result["metrics"]["cms"], float)
        self.assertEqual(result["metrics"]["bunsat_bound_heavy"], 7)
        self.assertIsInstance(result["metrics"]["bunsat_bound_heavy"], int)
        self.assertEqual(result["metrics"]["bunsat_interface_delta"], 3)
        self.assertIsInstance(result["metrics"]["bunsat_interface_delta"], int)

        provenance = result["provenance"]
        self.assertEqual(provenance["selectors"]["target"]["chains"], ["A"])
        self.assertEqual(provenance["selectors"]["binder"]["chains"], ["B"])
        self.assertEqual(provenance["selectors"]["interface"]["expression"], "A_B")
        self.assertEqual(provenance["selectors"]["pose_chains"], ["A", "B"])
        radii = provenance["resolved_options"]["cms"]["use_rosetta_radii"]
        self.assertFalse(radii["requested_value"])
        self.assertTrue(radii["default_value"])
        self.assertFalse(radii["effective_value"])
        self.assertEqual(radii["source"], "request")
        self.assertEqual(
            provenance["cutoffs"]["cms"]["filtered_area"]["value"], 4.5
        )
        self.assertIsNone(
            provenance["cutoffs"]["cms"]["distance_weight"]["value"]
        )
        self.assertFalse(
            provenance["cutoffs"]["cms"]["distance_weight"]["resolved"]
        )
        self.assertEqual(
            provenance["cutoffs"]["cms"]["distance_weight"]["source"],
            "installed_rosetta_default",
        )
        self.assertIsNone(
            provenance["cutoffs"]["cms"]["distance_weight"]["default_value"]
        )
        self.assertIsNone(
            provenance["cutoffs"]["cms"]["distance_weight"]["effective_value"]
        )
        self.assertEqual(
            provenance["cutoffs"]["bunsat_bound_heavy"]["probe_radius"]["value"],
            1.4,
        )
        self.assertTrue(
            provenance["resolved_options"]["bunsat_bound_heavy"]
            ["report_all_heavy_atom_unsats"]["value"]
        )
        self.assertFalse(
            provenance["cutoffs"]["bunsat_interface_delta"]
            ["interface_analyzer_defaults"]["resolved"]
        )
        self.assertIsNone(provenance["scorefunction"]["name"])
        self.assertIsNone(provenance["scorefunction"]["weights"])
        self.assertIn("does not construct", provenance["scorefunction"]["note"])

        cms = fake.cms_instances[0]
        self.assertEqual(cms.selector1_value.expression, "A")
        self.assertEqual(cms.selector2_value.expression, "B")
        self.assertFalse(cms.use_rosetta_radii_value)
        bunsat = fake.bunsat_instances[0]
        self.assertTrue(bunsat.report_all_heavy_atom_unsats)
        self.assertFalse(bunsat.ignore_bb_heavy_unsats)
        self.assertEqual(bunsat.probe_radius, 1.4)
        self.assertEqual(fake.interface_instances[0].interface, "A_B")

    def test_non_integral_bound_bunsat_is_a_typed_error(self):
        fake = FakePyRosetta(bound_bunsat_value=2.5)
        backend = backend_with(fake)

        with self.assertRaisesRegex(RosettaBackendError, "non-integral count"):
            backend.analyze(request_for(metrics=("bunsat_bound_heavy",)))

    def test_initialization_failure_is_a_typed_error_and_not_marked_initialized(self):
        fake = FakePyRosetta(init_error=RuntimeError("synthetic init failure"))
        backend = backend_with(fake)

        with self.assertRaisesRegex(
            RosettaBackendError, "PyRosetta initialization failed"
        ):
            backend.analyze(request_for())
        self.assertFalse(backend._initialized)
        self.assertEqual(fake.init_calls, ["-fake-flags"])


if __name__ == "__main__":
    unittest.main()
