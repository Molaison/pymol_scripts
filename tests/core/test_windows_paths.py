import unittest

from utilities.windows_paths import windows_long_path


class WindowsLongPathTests(unittest.TestCase):
    def test_non_windows_and_short_windows_paths_are_unchanged(self):
        self.assertEqual(windows_long_path("relative/file.pdb", platform_name="linux"), "relative/file.pdb")
        self.assertEqual(
            windows_long_path(
                "short.pdb",
                platform_name="win32",
                abspath=lambda value: "C:\\data\\short.pdb",
            ),
            "short.pdb",
        )

    def test_long_drive_path_uses_drive_prefix(self):
        absolute = "C:\\data\\" + ("nested\\" * 40) + "model.pdb"
        self.assertEqual(
            windows_long_path("model.pdb", platform_name="win32", abspath=lambda value: absolute),
            "\\\\?\\" + absolute,
        )

    def test_long_unc_path_uses_unc_prefix(self):
        absolute = "\\\\server\\share\\" + ("nested\\" * 40) + "model.pdb"
        self.assertEqual(
            windows_long_path("model.pdb", platform_name="win32", abspath=lambda value: absolute),
            "\\\\?\\UNC\\server\\share\\" + ("nested\\" * 40) + "model.pdb",
        )

    def test_existing_device_prefix_is_preserved(self):
        path = "\\\\?\\UNC\\server\\share\\model.pdb"
        self.assertEqual(windows_long_path(path, platform_name="win32"), path)


if __name__ == "__main__":
    unittest.main()
