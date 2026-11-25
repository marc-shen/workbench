"""Exercise compiler setup through the real batch package installer."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
MAC_EMACS = Path('/Applications/Emacs.app/Contents/MacOS/Emacs')
EMACS = os.environ.get('EMACS_BINARY') or (
    str(MAC_EMACS) if MAC_EMACS.is_file() else shutil.which('emacs'))


@unittest.skipUnless(EMACS, 'Requires Emacs 31.1 or newer')
class NativeCompilerTests(unittest.TestCase):
    def test_target_selection_regressions(self):
        result = subprocess.run(
            [EMACS, '--batch', '-Q', '-l', str(REPO / 'tests/test-emacs-native-comp.el')],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(platform.system() == 'Darwin', 'Requires macOS native compiler')
    def test_fresh_home_installer_can_native_compile_during_init(self):
        # Use an empty home and no inherited deployment target, as on a new Mac.
        # Compiling during init reproduces the failure before package install
        # can begin.  No package downloads or live user configuration are used.
        with tempfile.TemporaryDirectory(prefix='wb native compiler ') as tmp:
            home = Path(tmp)
            config = home / '.emacs.d'
            config.mkdir()
            (config / 'early-init.el').symlink_to(REPO / 'config/emacs/early-init.el')
            (config / 'probe.el').write_text(
                ';;; probe.el --- Native compiler probe -*- lexical-binding: t; -*-\n'
                '(defun wb-native-probe () 42)\n')
            (config / 'init.el').write_text('''
(unless (native-comp-available-p) (kill-emacs 77))
(unless (equal (getenv "MACOSX_DEPLOYMENT_TARGET") (getenv "WB_TEST_TARGET"))
  (error "Compiler target was not configured before init"))
(let ((source (expand-file-name "probe.el" user-emacs-directory))
      (output (expand-file-name "probe.eln" user-emacs-directory)))
  (native-compile source output)
  (load output nil t)
  (unless (= (wb-native-probe) 42) (error "Compiled function failed")))
(defun my-install-packages--run () (list :failures nil))
(defun my-package--format-summary (&rest _) "Native compilation succeeded\\n")
''')
            version = subprocess.check_output(
                ['/usr/bin/sw_vers', '-productVersion'], text=True).strip()
            env = dict(os.environ, HOME=str(home), EMACS_BINARY=EMACS,
                       SOFTWARE_HOME=str(home / 'Software'),
                       SOFTWARE_USER_HOME=str(home / 'Software'),
                       WORKBENCH_HOME=str(REPO), WB_TEST_TARGET='.'.join(version.split('.')[:2]),
                       XDG_CONFIG_HOME=str(home / '.config'),
                       XDG_CACHE_HOME=str(home / '.cache'))
            env.pop('MACOSX_DEPLOYMENT_TARGET', None)
            result = subprocess.run(
                ['/bin/bash', str(REPO / 'libexec/install-emacs-packages.sh')],
                env=env, capture_output=True, text=True, timeout=120)
            if result.returncode == 77:
                self.skipTest('This Emacs lacks native compilation')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((config / 'probe.eln').is_file())
            self.assertIn('Native compilation succeeded', result.stdout)


if __name__ == '__main__':
    unittest.main()
