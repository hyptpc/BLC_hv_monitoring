"""Offline credential/CLI tests: no real CAEN library, socket, subprocess, or DB."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent.parent


def guard(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'ctypes.dlopen'}:
        raise RuntimeError(f'Offline credential test forbids {event}')
    if event == 'import' and args[0].startswith('caen_libs'):
        if not getattr(sys.modules.get(args[0]), '__offline_fake__', False):
            raise RuntimeError('Real CAEN library import forbidden')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).absolute()
        if path.parent == ROOT / 'param':
            raise RuntimeError('Reading production configuration/credentials is forbidden')


sys.addaudithook(guard)
sys.path.insert(0, str(ROOT))
from monitor_credentials import CredentialsError, load_credentials


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blc-credentials-test-', dir='/tmp')
        self.directory = Path(self.temp.name) / 'param'
        self.directory.mkdir()
        self.config = self.directory / 'conf.yml'
        self.credentials = self.directory / 'credentials.local.yml'

    def tearDown(self):
        self.temp.cleanup()

    def write_credentials(self, content='username: test-user\npassword: test-password\n', mode=0o600):
        self.credentials.write_text(content)
        self.credentials.chmod(mode)

    def test_automatically_reads_beside_existing_config_without_environment(self):
        self.write_credentials()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_credentials(self.config), ('test-user', 'test-password'))

    def test_relative_config_path_uses_its_own_directory(self):
        self.write_credentials()
        relative = os.path.relpath(self.config, Path.cwd())
        self.assertEqual(load_credentials(relative), ('test-user', 'test-password'))

    def test_password_spacing_is_preserved(self):
        self.write_credentials('username: test-user\npassword: "  test password  "\n')
        self.assertEqual(load_credentials(self.config)[1], '  test password  ')

    def test_missing_file_has_no_hardcoded_fallback(self):
        with self.assertRaises(CredentialsError):
            load_credentials(self.config)

    def test_group_or_world_readable_file_is_rejected(self):
        for mode in (0o640, 0o644, 0o666):
            with self.subTest(mode=oct(mode)):
                self.write_credentials(mode=mode)
                with self.assertRaisesRegex(CredentialsError, 'chmod 600'):
                    load_credentials(self.config)

    def test_symlinks_are_rejected(self):
        target = self.directory / 'other.yml'
        target.write_text('username: test-user\npassword: test-password\n')
        target.chmod(0o600)
        self.credentials.symlink_to(target)
        with self.assertRaisesRegex(CredentialsError, 'symlink'):
            load_credentials(self.config)

    def test_other_owners_are_rejected(self):
        self.write_credentials()
        actual = self.credentials.stat()
        fake = types.SimpleNamespace(st_mode=actual.st_mode, st_uid=os.geteuid() + 1)
        with patch('monitor_credentials.os.fstat', return_value=fake):
            with self.assertRaisesRegex(CredentialsError, 'owned by'):
                load_credentials(self.config)

    def test_invalid_content_does_not_reveal_secret_values(self):
        for content in ('username: test-user\npassword: "DO-NOT-PRINT',
                        'username: test-user\npassword: [DO-NOT-PRINT]\n',
                        'username: test-user\npassword: DO-NOT-PRINT\nextra: bad\n'):
            with self.subTest(content_type=content[:8]):
                self.write_credentials(content)
                with self.assertRaises(CredentialsError) as error:
                    load_credentials(self.config)
                self.assertNotIn('DO-NOT-PRINT', str(error.exception))

    def test_empty_or_non_string_fields_are_rejected(self):
        for content in ('', 'username: ""\npassword: ""\n',
                        'username: true\npassword: 123\n', '- username\n- password\n'):
            with self.subTest(content=content):
                self.write_credentials(content)
                with self.assertRaises(CredentialsError):
                    load_credentials(self.config)

    def test_yaml_object_construction_is_rejected(self):
        self.write_credentials('!!python/object/apply:os.system ["must-not-run"]\n')
        with self.assertRaises(CredentialsError):
            load_credentials(self.config)

    def application(self):
        package = types.ModuleType('caen_libs')
        package.__path__ = []
        package.__offline_fake__ = True
        wrapper = types.ModuleType('caen_libs.caenhvwrapper')
        wrapper.__offline_fake__ = True
        wrapper.SystemType = {'SY1527': 'fake-system'}
        wrapper.LinkType = {'TCPIP': 'fake-link'}
        wrapper.Error = type('FakeHVError', (Exception,), {})
        wrapper.Device = types.SimpleNamespace(open=MagicMock(return_value=MagicMock()))
        package.caenhvwrapper = wrapper
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'caen_libs': package, 'caen_libs.caenhvwrapper': wrapper}).start()
        spec = importlib.util.spec_from_file_location('offline_monitor_caen', ROOT / 'monitor_caen.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, wrapper

    def write_config(self):
        self.config.write_text('''caen_connection:
  host: 192.0.2.1
  systype: SY1527
  linktype: TCPIP
database:
  db_file: must-not-open-real-db
  logging_interval_sec: 60
monitoring_targets:
  4: ALL
''')

    def test_original_command_line_uses_file_credentials_without_new_arguments(self):
        self.write_config()
        self.write_credentials()
        module, wrapper = self.application()
        with patch.object(sys, 'argv', ['monitor_caen.py', str(self.config)]), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(module.sqlite3, 'connect', return_value=MagicMock()), \
                patch.object(module, 'create_database_table'), \
                patch.object(module, 'log_hv_status', side_effect=KeyboardInterrupt), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            module.main()
        wrapper.Device.open.assert_called_once_with('fake-system', 'fake-link', '192.0.2.1',
                                                    'test-user', 'test-password')

    def test_missing_credentials_stop_before_any_device_or_database_connection(self):
        self.write_config()
        module, wrapper = self.application()
        with patch.object(sys, 'argv', ['monitor_caen.py', str(self.config)]), \
                patch.object(module.sqlite3, 'connect') as database, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                self.assertRaises(SystemExit) as error:
            module.main()
        self.assertEqual(error.exception.code, 1)
        wrapper.Device.open.assert_not_called()
        database.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
