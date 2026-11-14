"""Test daemon shell helpers with fake sudo/systemctl only; no service access."""

import os
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent
ALLOWED_SCRIPTS = set()


def audit(event, args):
    if event.startswith('socket.') or event in {'urllib.Request', 'ctypes.dlopen', 'os.system'}:
        raise RuntimeError(f'Offline shell test forbids {event}')
    if event == 'subprocess.Popen':
        command = args[1]
        if (not isinstance(command, list) or len(command) < 2
                or command[0] != '/usr/bin/bash' or command[1] not in ALLOWED_SCRIPTS):
            raise RuntimeError('Only the copied shell fixtures may be launched')


sys.addaudithook(audit)


class DaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='monitor-daemon-test-', dir='/tmp')
        cls.root = Path(cls.temp.name)
        cls.bin = cls.root / 'bin'
        cls.bin.mkdir()
        (cls.bin / 'bash').symlink_to('/usr/bin/bash')
        (cls.bin / 'dirname').symlink_to('/usr/bin/dirname')
        # PATH has only these fakes and two harmless shell utilities. No real
        # sudo/systemctl, SSH, Python, or equipment executable is reachable.
        (cls.bin / 'sudo').write_text('''#!/usr/bin/bash
printf '%s\\t' sudo "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
[[ "$1" == -- && "$2" == systemctl ]] || exit 97
shift
exec "$@"
''')
        (cls.bin / 'systemctl').write_text('''#!/usr/bin/bash
printf '%s\\t' systemctl "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
if [[ "${1:-}" == show && "${3:-}" == --property=Type && "${4:-}" == --value ]]; then
    printf '%s\n' "${FAKE_UNIT_TYPE:-exec}"
elif [[ "${1:-}" == show && "${3:-}" == --property=ExecStart ]]; then
    directory=CVC_hv_monitoring
    if [[ "$2" == blc_monitor.service ]]; then directory=BLC_hv_monitoring; fi
    printf '%s\n' "${FAKE_UNIT_START:-/usr/bin/bash /home/sks/monitor_tools/$directory/start.sh}"
elif [[ "${1:-}" == show && "${3:-}" == --property=ExecStop ]]; then
    directory=CVC_hv_monitoring
    if [[ "$2" == blc_monitor.service ]]; then directory=BLC_hv_monitoring; fi
    printf '%s\n' "${FAKE_UNIT_STOP:-/usr/bin/bash /home/sks/monitor_tools/$directory/start.sh --stop}"
elif [[ "${1:-}" == show && "${3:-}" == --property=ActiveState ]]; then
    printf '%s\n' "${FAKE_UNIT_STATE:-inactive}"
elif [[ "${1:-}" == reset-failed ]]; then
    exit "${FAKE_RESET_RC:-0}"
fi
exit "${FAKE_SYSTEMCTL_RC:-0}"
''')
        for name in ('sudo', 'systemctl'):
            (cls.bin / name).chmod(0o755)
        cls.projects = {}
        for name in ('lh2_leak_monitor', 'BLC_hv_monitoring', 'CVC_hv_monitoring',
                     'trip_monitor', 'unknown tool'):
            daemon = cls.root / name / 'daemon'
            daemon.mkdir(parents=True)
            for source in SOURCE.glob('*.sh'):
                destination = daemon / source.name
                text = source.read_text()
                if '/systemctl' in text or '/sudo' in text or 'sudo -n' in text:
                    raise RuntimeError('A shell helper bypasses the fake command lookup')
                shutil.copy2(source, destination)
                ALLOWED_SCRIPTS.add(str(destination))
            cls.projects[name] = daemon
        cls.log = cls.root / 'calls.tsv'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_helper(self, project, name, arguments=(), returncode=0, unit_type="exec", unit_start=None, unit_stop=None,
                   unit_state='inactive', resetcode=0):
        self.log.write_text('')
        environment = {'PATH': str(self.bin), 'FAKE_LOG': str(self.log),
                       'FAKE_SYSTEMCTL_RC': str(returncode), 'FAKE_UNIT_TYPE': unit_type, 'LANG': 'C'}
        environment['FAKE_UNIT_STATE'] = unit_state
        environment['FAKE_RESET_RC'] = str(resetcode)
        if unit_start is not None:
            environment['FAKE_UNIT_START'] = unit_start
        if unit_stop is not None:
            environment['FAKE_UNIT_STOP'] = unit_stop
        result = subprocess.run(['/usr/bin/bash', str(self.projects[project] / name), *arguments],
                                env=environment, text=True, capture_output=True, timeout=5)
        calls = [line.rstrip('\t').split('\t') for line in self.log.read_text().splitlines()]
        return result, calls

    def test_default_targets_and_all_actions(self):
        for project, unit in [('lh2_leak_monitor', 'lh2_monitor.service'),
                              ('BLC_hv_monitoring', 'blc_monitor.service'),
                              ('CVC_hv_monitoring', 'ftof_monitor.service'),
                              ('trip_monitor', 'trip_monitor.service')]:
            for action in ('start', 'stop', 'status'):
                with self.subTest(project=project, action=action):
                    result, calls = self.run_helper(project, ('monitor.sh' if action == 'status' else action + '.sh'),
                                                    unit_type='oneshot' if unit in {'ftof_monitor.service', 'blc_monitor.service'} else 'exec')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if action == 'status':
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(calls[0][:3], ['systemctl', 'show', unit])
                    else:
                        operations = [['disable', '--now', unit]]
                        expected = []
                        if action == 'start':
                            expected.append(['systemctl', 'show', unit, '--property=Type', '--value'])
                            if unit in {'ftof_monitor.service', 'blc_monitor.service'}:
                                expected.extend([
                                    ['systemctl', 'show', unit, '--property=ExecStart', '--value'],
                                    ['systemctl', 'show', unit, '--property=ExecStop', '--value'],
                                ])
                            expected.append(['systemctl', 'show', unit, '--property=ActiveState', '--value'])
                            operations = [['enable', '--now', unit]]
                        for args in operations:
                            if os.geteuid() != 0:
                                expected.append(['sudo', '--', 'systemctl', *args])
                            expected.append(['systemctl', *args])
                        self.assertEqual(calls, expected)

    def test_explicit_target_works_in_parent_or_renamed_directory(self):
        for target, unit in [('lh2', 'lh2_monitor.service'), ('blc', 'blc_monitor.service'),
                             ('ftof', 'ftof_monitor.service'), ('trip', 'trip_monitor.service')]:
            with self.subTest(target=target):
                result, calls = self.run_helper('unknown tool', 'monitor.sh', [target])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[0][:3], ['systemctl', 'show', unit])

    def test_old_oneshot_units_are_not_started(self):
        result, calls = self.run_helper('lh2_leak_monitor', 'start.sh', unit_type='oneshot')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ['systemctl', 'show', 'lh2_monitor.service', '--property=Type', '--value'])

    def test_caen_monitors_require_updated_tmux_definitions_and_reject_old_paths(self):
        for project in ('BLC_hv_monitoring', 'CVC_hv_monitoring'):
            for unit_type, start, stop in (
                    ('exec', None, None),
                    ('oneshot', '/home/sks/share/monitor-tools/' + project + '/start.sh', None),
                    ('oneshot', None, '/usr/bin/tmux kill-session -t old')):
                with self.subTest(project=project, unit_type=unit_type, start=start, stop=stop):
                    result, calls = self.run_helper(project, 'start.sh',
                        unit_type=unit_type, unit_start=start, unit_stop=stop)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse(any('enable' in c or 'reset-failed' in c for c in calls))

    def test_inactive_ftof_starts_even_if_reset_failed_would_report_unit_not_loaded(self):
        result, calls = self.run_helper('CVC_hv_monitoring', 'start.sh',
            unit_type='oneshot', unit_state='inactive', resetcode=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('reset-failed' in c for c in calls))
        self.assertEqual(calls[-1], ['systemctl', 'enable', '--now', 'ftof_monitor.service'])

    def test_failed_service_resets_failure_before_starting(self):
        result, calls = self.run_helper('CVC_hv_monitoring', 'start.sh',
            unit_type='oneshot', unit_state='failed')
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = [c for c in calls if c[0] == 'systemctl' and c[1] != 'show']
        self.assertEqual(operations, [['systemctl', 'reset-failed', 'ftof_monitor.service'],
                                      ['systemctl', 'enable', '--now', 'ftof_monitor.service']])

    def test_reset_error_for_failed_service_is_not_hidden(self):
        result, calls = self.run_helper('CVC_hv_monitoring', 'start.sh',
            unit_type='oneshot', unit_state='failed', resetcode=7)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(any('enable' in c for c in calls))

    def test_default_action_is_read_only_status(self):
        result, calls = self.run_helper('lh2_leak_monitor', 'monitor.sh')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0][:3], ['systemctl', 'show', 'lh2_monitor.service'])

    def test_invalid_actions_targets_and_extra_arguments_do_nothing(self):
        for arguments in (['destroy', 'lh2'], ['stop', 'other.service'],
                          ['stop', 'lh2; touch unexpected'], ['stop', 'lh2', 'extra']):
            with self.subTest(arguments=arguments):
                result, calls = self.run_helper('unknown tool', 'monitor.sh', arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_unknown_default_directory_does_nothing(self):
        result, calls = self.run_helper('unknown tool', 'stop.sh')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_help_does_not_access_systemd(self):
        result, calls = self.run_helper('unknown tool', 'monitor.sh', ['--help'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_backend_errors_are_not_hidden(self):
        for action in ('start', 'stop', 'status'):
            with self.subTest(action=action):
                result, _ = self.run_helper('lh2_leak_monitor', ('monitor.sh' if action == 'status' else action + '.sh'), returncode=7)
                self.assertEqual(result.returncode, 7)


class TmuxLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='caen-launcher-test-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'dirname').symlink_to('/usr/bin/dirname')
        self.log = self.root / 'calls'
        self.log.touch()
        (self.bin / 'tmux').write_text('''#!/usr/bin/bash
printf 'tmux\\t' >> "$FAKE_LOG"
printf '%s\\t' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
case "$1" in
 has-session) [[ "${FAKE_EXISTS:-0}" == 1 ]] ;;
 new-session) exit "${FAKE_NEW_RC:-0}" ;;
 kill-session) exit "${FAKE_KILL_RC:-0}" ;;
 *) exit 97 ;;
esac
''')
        (self.bin / 'python').write_text('''#!/usr/bin/bash
printf 'python\\t' >> "$FAKE_LOG"
printf '%s\\t' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
exit "${FAKE_PYTHON_RC:-1}"
''')
        (self.bin / 'sleep').write_text('''#!/usr/bin/bash
printf 'sleep\\t%s\\n' "$1" >> "$FAKE_LOG"
''')
        for name in ('tmux', 'python', 'sleep'):
            (self.bin / name).chmod(0o755)
        source = (SOURCE.parent / 'start.sh').read_text()
        source = source.replace('TMUX_PATH=/usr/bin/tmux', 'TMUX_PATH=' + shlex.quote(str(self.bin / 'tmux')))
        source = source.replace('VENV_PYTHON_PATH=/home/sks/.venv/bin/python',
                                'VENV_PYTHON_PATH=' + shlex.quote(str(self.bin / 'python')))
        source = source.replace('exec /bin/bash', 'exit 0')  # No interactive shell in tests.
        assert '/usr/bin/tmux' not in source and '/home/sks/.venv/bin/python' not in source
        self.projects = {}
        for project in ('BLC_hv_monitoring', 'CVC_hv_monitoring', 'unknown'):
            directory = self.root / 'workspace with spaces' / project
            directory.mkdir(parents=True)
            script = directory / 'start.sh'
            script.write_text(source)
            ALLOWED_SCRIPTS.add(str(script))
            self.projects[project] = script

    def launch(self, project='CVC_hv_monitoring', args=(), **values):
        self.log.write_text('')
        environment = {'PATH': str(self.bin), 'FAKE_LOG': str(self.log), 'LANG': 'C', **values}
        script = self.projects[project]
        result = subprocess.run(['/usr/bin/bash', str(script), *args], cwd=script.parent,
            env=environment, text=True, capture_output=True, timeout=5)
        calls = [line.rstrip('\t').split('\t') for line in self.log.read_text().splitlines()]
        return result, calls

    def test_checkout_directory_selects_correct_session_and_working_directory(self):
        for project, session in [('BLC_hv_monitoring', 'blc'), ('CVC_hv_monitoring', 'ftof')]:
            result, calls = self.launch(project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[0], ['tmux', 'has-session', '-t', '=' + session])
            self.assertEqual(calls[1][:7], ['tmux', 'new-session', '-d', '-s', session, '-c', str(self.projects[project].parent)])
            self.assertEqual(shlex.split(calls[1][7]), ['exec', '/usr/bin/bash', str(self.projects[project]), '--run'])
            self.assertEqual(len(calls), 2)

    def test_existing_session_is_not_adopted_or_started_twice(self):
        result, calls = self.launch(FAKE_EXISTS='1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(calls), 1)

    def test_tmux_launch_failure_is_propagated(self):
        result, _ = self.launch(FAKE_NEW_RC='7')
        self.assertEqual(result.returncode, 7)

    def test_stop_kills_only_the_matching_session(self):
        for project, session in [('BLC_hv_monitoring', 'blc'), ('CVC_hv_monitoring', 'ftof')]:
            result, calls = self.launch(project, ['--stop'], FAKE_EXISTS='1')
            self.assertEqual(result.returncode, 0)
            self.assertEqual(calls, [['tmux', 'has-session', '-t', '=' + session],
                                     ['tmux', 'kill-session', '-t', '=' + session]])

    def test_stop_without_session_succeeds_without_touching_server(self):
        result, calls = self.launch(args=['--stop'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [['tmux', 'has-session', '-t', '=ftof']])

    def test_stop_failure_is_propagated(self):
        result, _ = self.launch(args=['--stop'], FAKE_EXISTS='1', FAKE_KILL_RC='9')
        self.assertEqual(result.returncode, 9)

    def test_python_retries_are_bounded_and_keep_original_arguments(self):
        for status in ('0', '1'):
            result, calls = self.launch(args=['--run'], FAKE_PYTHON_RC=status)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sum(c[0] == 'python' for c in calls), 5)
            self.assertEqual([c for c in calls if c[0] == 'sleep'], [['sleep', '30']] * 4)
            self.assertTrue(all(c == ['python', '-u', './monitor_caen.py', 'param/conf.yml']
                                for c in calls if c[0] == 'python'))
            self.assertIn('5 starts within one hour', result.stdout)

    def test_unknown_directory_does_nothing(self):
        result, calls = self.launch('unknown')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_invalid_argument_does_nothing(self):
        result, calls = self.launch(args=['unexpected'])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
