"""One terminal authentication, renewable without retaining a password."""
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'lib'),str(Path(__file__).resolve().parents[1]/'libexec')]
from workbench import privilege
import macos_network


class PrivilegeTests(unittest.TestCase):
    steps=[{'component':'platform','action':'install'},{'component':'mihomo','action':'apply'}]

    def test_one_authentication_and_keepalive_stop_on_failure(self):
        stop=Mock();stop.wait.side_effect=[False,True]
        worker=Mock()
        def thread_factory(*, target, daemon):
            worker.start.side_effect=target
            return worker
        with patch.object(privilege.sys.stdin,'isatty',return_value=True), \
                patch.object(privilege.subprocess,'run',return_value=Mock(returncode=0)) as run, \
                patch.object(privilege.threading,'Event',return_value=stop), \
                patch.object(privilege.threading,'Thread',side_effect=thread_factory),patch('sys.stdout',io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'installer failed'):
                with privilege.sudo_session(self.steps,'macos'):
                    raise RuntimeError('installer failed')
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             [['/usr/bin/sudo','-v'],['/usr/bin/sudo','-n','-v']])
            stop.set.assert_called_once()
            worker.join.assert_called_once()

    def test_failed_authentication_never_runs_installation(self):
        entered=False
        with patch.object(privilege.sys.stdin,'isatty',return_value=True), \
                patch.object(privilege.subprocess,'run',return_value=Mock(returncode=1)), \
                patch.object(privilege.threading,'Thread') as thread,patch('sys.stdout',io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'管理员验证失败'):
                with privilege.sudo_session(self.steps,'macos'):entered=True
            thread.assert_not_called()
        self.assertFalse(entered)

    def test_noninteractive_and_read_only_do_not_prompt(self):
        with patch.object(privilege.subprocess,'run') as run:
            with patch.object(privilege.sys.stdin,'isatty',return_value=False):
                with privilege.sudo_session(self.steps,'macos'):pass
            with patch.object(privilege.sys.stdin,'isatty',return_value=True):
                with privilege.sudo_session([{'component':'mihomo','action':'check'}],'macos'):pass
            run.assert_not_called()

    def test_admin_uses_terminal_sudo_and_noninteractive_never_prompts(self):
        for interactive in (True,False):
            with patch.object(macos_network.sys.stdin,'isatty',return_value=interactive), \
                    patch.object(macos_network,'run') as run:
                macos_network.admin(['/usr/sbin/installer','-pkg','/tmp/a b.pkg','-target','/'])
                self.assertEqual(run.call_args.args[0],['/usr/bin/sudo',*([] if interactive else ['-n']),
                    '--','/usr/sbin/installer','-pkg','/tmp/a b.pkg','-target','/'])


if __name__=='__main__':unittest.main()
