import pathlib
import unittest


class WindowsLauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = pathlib.Path(__file__).resolve().parents[2]
        cls.launcher = cls.repository / "scripts" / "windows" / "run_host_smoke.ps1"
        cls.source = cls.launcher.read_text(encoding="utf-8")

    def test_launcher_uses_configurable_paths(self):
        self.assertIn("[string]$PymolExe", self.source)
        self.assertIn("[string]$RepositoryPath", self.source)
        self.assertIn("[string]$OutputPath", self.source)
        self.assertIn("[string]$WorkerUrl", self.source)

    def test_pymol_starts_after_switching_to_local_temporary_cwd(self):
        push = self.source.index("Push-Location -LiteralPath $tempDirectory")
        invoke = self.source.index("& $resolvedPymol")
        self.assertLess(push, invoke)
        self.assertIn("[System.IO.Path]::GetTempPath()", self.source)

    def test_bootstrap_asks_pymol_to_run_repository_script(self):
        self.assertIn('cmd.run($hostScriptLiteral)', self.source)
        self.assertIn('tests\\host\\host_smoke.py', self.source)

    def test_unc_repository_uses_native_provider_path(self):
        self.assertIn("$resolvedRepository.ProviderPath", self.source)
        self.assertNotIn(
            "(Resolve-Path -LiteralPath $RepositoryPath -ErrorAction Stop).Path",
            self.source,
        )

    def test_launcher_parses_versioned_record_and_enforces_gate(self):
        self.assertIn("ConvertFrom-Json", self.source)
        self.assertIn('pymolplugins.host-smoke/1.0', self.source)
        self.assertIn('$record.automatic_status -ne "pass"', self.source)

    def test_optional_worker_probe_is_forwarded_to_host_pymol(self):
        self.assertIn("PYMOLPLUGINS_WORKER_URL", self.source)
        self.assertIn("PP_HOST_SMOKE_CHECK_WORKER", self.source)
        self.assertIn('$WorkerUrl.TrimEnd("/")', self.source)


if __name__ == "__main__":
    unittest.main()
