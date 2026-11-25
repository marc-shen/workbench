"""Check Dock request ordering and evaluate frame selection without a GUI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'config/emacs/Emacs Client.app/Contents/Resources/activate-emacs'
EMACS = shutil.which('emacs') or '/Applications/Emacs.app/Contents/MacOS/Emacs'


class ActivationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and Path(
        '/Applications/Emacs.app/Contents/Resources/include/emacs-module.h').is_file(),
        'Requires macOS and Emacs module headers')
    def test_native_dock_menu_and_reopen_appleevent(self):
        with tempfile.TemporaryDirectory(prefix='emacs dock event ') as tmp:
            executable = Path(tmp) / 'test-client-menu'
            source = Path(__file__).with_name('test-emacs-client-menu-native.m')
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wall', '-Wextra',
                            '-Werror', '-Wno-unused-parameter',
                            '-I', '/Applications/Emacs.app/Contents/Resources/include',
                            '-framework', 'Cocoa', str(source), '-o', str(executable)],
                           check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run([str(executable)], check=True, capture_output=True,
                                    text=True, timeout=15)
            self.assertIn('all checks passed', result.stdout)

    def request(self, arguments=(), existing=True, previous_pid=None):
        with tempfile.TemporaryDirectory(prefix='emacs activation ') as tmp:
            root = Path(tmp)
            client = root / 'client'
            client.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
if os.environ.get('TEST_PREVIOUS_PID'):
    try: os.kill(int(os.environ['TEST_PREVIOUS_PID']), 0)
    except ProcessLookupError: pass
    else: raise AssertionError('Contacted the server before the old process exited')
with open(os.environ['TEST_LOG'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
print('nil' if '(let* ((frames' in sys.argv[-1] and os.environ['TEST_EXISTING'] == '0' else 't')
''')
            client.chmod(0o755)
            script = root / 'activate'
            script.write_text(SOURCE.read_text().replace(
                'if [[ ! -x $client ]]; then client=/Applications/Emacs.app/Contents/MacOS/bin/emacsclient; fi',
                'client="' + str(client) + '"'))
            log = root / 'calls'
            subprocess.run(['/bin/bash', str(script), *arguments], check=True,
                           capture_output=True, timeout=10,
                           env=dict(os.environ, TEST_LOG=str(log),
                                    TEST_EXISTING='1' if existing else '0',
                                    TEST_PREVIOUS_PID=str(previous_pid or '')))
            return [json.loads(line) for line in log.read_text().splitlines()]

    def test_graceful_restart_waits_before_contacting_server(self):
        old = subprocess.Popen(['/bin/sleep', '1'])
        waiter = threading.Thread(target=old.wait)
        waiter.start()
        try:
            calls = self.request(('--after-pid=' + str(old.pid), '--new-window'),
                                 previous_pid=old.pid)
            self.assertTrue(any('--create-frame' in call for call in calls))
            self.assertFalse(any('--after-pid=' in arg for call in calls for arg in call))
            self.assertEqual(old.poll(), 0)
        finally:
            if old.poll() is None: old.terminate()
            waiter.join(timeout=3)

    def test_invalid_restart_pid_is_rejected(self):
        for pid in ('0', '-1', 'invalid', ''):
            with self.subTest(pid=pid), self.assertRaises(subprocess.CalledProcessError):
                self.request(('--after-pid=' + pid, '--new-window'))

    def test_existing_and_new_frames_activate_after_final_policy_change(self):
        for arguments, existing in [((), True), ((), False), (('--new-window',), True),
                                    (('a file.txt',), True)]:
            with self.subTest(arguments=arguments, existing=existing):
                calls = self.request(arguments, existing)
                evaluations = [c[-1] for c in calls if '--eval' in c]
                self.assertIn('setActivationPolicy:0', evaluations[-1])
                self.assertFalse(any('setActivationPolicy:1' in e for e in evaluations))
                self.assertTrue(any('(menu-bar-mode 1)' in e for e in evaluations))
                self.assertLess(evaluations[-1].index('setActivationPolicy:0'),
                                evaluations[-1].index('activateIgnoringOtherApps:true'))
                self.assertIn('activateIgnoringOtherApps:true', evaluations[-1])
                self.assertIn('select-frame-set-input-focus', evaluations[-1])
                self.assertFalse(any('select-frame-set-input-focus' in e for e in evaluations[:-1]))
                self.assertEqual(any('--create-frame' in c for c in calls),
                                 bool(arguments) or not existing)

    @unittest.skipUnless(Path(EMACS).is_file(), 'Requires Emacs')
    def test_selection_keeps_current_frame_and_excludes_child_and_terminal(self):
        expression = next(c[-1] for c in self.request() if '(let* ((frames' in c[-1])
        # Run the actual emitted Lisp with fake frames, never touching GUI focus.
        # Mocking built-ins in -Q must not invoke the host's native compiler;
        # compiler setup is exercised separately by test_emacs_native_comp.py.
        program = '''(progn (require 'cl-lib) (require 'seq)
          (setq native-comp-enable-subr-trampolines nil)
          (dolist (selected '(second child terminal))
            (let (target)
              (cl-letf (((symbol-function 'frame-list)
                         (lambda () '(terminal child first second)))
                        ((symbol-function 'display-graphic-p)
                         (lambda (f) (not (eq f 'terminal))))
                        ((symbol-function 'frame-parent)
                         (lambda (f) (and (eq f 'child) 'second)))
                        ((symbol-function 'selected-frame) (lambda () selected))
                        ((symbol-function 'set-frame-parameter)
                         (lambda (f _p _v) (setq target f))))
                EXPRESSION
                (unless (eq target (if (memq selected '(second child)) 'second 'first))
                  (error "Wrong activation target: %S" target))))))'''.replace('EXPRESSION', expression)
        subprocess.run([EMACS, '--batch', '-Q', '--eval', program], check=True,
                       capture_output=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
