import unittest
from unittest.mock import MagicMock, patch

from duty_app.startup import Startup


class StartupTests(unittest.TestCase):
    def test_per_user_key_and_quoted_unicode_command(self):
        registry = MagicMock()
        startup = Startup(registry, command='"C:\\中文 路径\\DutyManager.exe" --background')
        startup.set_enabled(True)
        registry.CreateKeyEx.assert_called_once_with(registry.HKEY_CURRENT_USER, Startup.KEY, 0, registry.KEY_SET_VALUE)
        registry.SetValueEx.assert_called_once_with(registry.CreateKeyEx().__enter__(), "DutyManager", 0,
                                                   registry.REG_SZ, startup.command)
        registry.QueryValueEx.return_value = (startup.command, 1)
        self.assertTrue(startup.enabled())
        startup.set_enabled(False)
        registry.DeleteValue.assert_called_once()

    def test_missing_startup_is_false(self):
        registry = MagicMock()
        registry.OpenKey.side_effect = FileNotFoundError
        self.assertFalse(Startup(registry, command="test").enabled())

    def test_source_command_has_absolute_launcher(self):
        command = Startup.default_command()
        self.assertIn("run_duty.pyw", command)
        self.assertIn("--background", command)

    def test_installed_command_matches_installer_even_without_spaces(self):
        with patch("sys.frozen", True, create=True), patch("sys.executable", r"C:\Apps\DutyManager.exe"):
            self.assertEqual(Startup.default_command(), '"C:\\Apps\\DutyManager.exe" --background')

    def test_custom_data_dir_survives_startup(self):
        command = Startup.default_command("D:/值日 测试")
        self.assertIn("--data-dir", command)
        self.assertIn("值日 测试", command)


if __name__ == "__main__":
    unittest.main()
