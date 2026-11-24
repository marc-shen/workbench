"""Font commands share the platform manifest without installing unrelated casks."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'lib'))
from workbench import fonts, completion
from workbench.cli import workflow
from workbench.registry import discover
from workbench.engine import plan


class FontTests(unittest.TestCase):
    def setUp(self):
        self.output=io.StringIO()
        for context in (patch('sys.stdout',self.output),patch.object(fonts.shutil,'which',return_value='/fake/brew')):
            context.start();self.addCleanup(context.stop)
        self.meslo='font-meslo-for-powerlevel10k'
        self.sarasa='font-sarasa-gothic'

    def test_check_reports_missing_font_and_does_not_refresh_or_install(self):
        result=subprocess.CompletedProcess([],0,f'{self.meslo} 2.3.3\nwezterm 1.0\n','')
        with patch.object(fonts.subprocess,'run',return_value=result) as run, \
             patch.object(fonts,'stream_command') as refresh,patch.object(fonts,'install') as install:
            self.assertEqual(fonts.font_action('check',[],REPO,'macos'),1)
            self.assertEqual(run.call_args.args[0],['/fake/brew','list','--cask','--versions'])
            refresh.assert_not_called();install.assert_not_called()
        self.assertIn('[MISSING] '+self.sarasa,self.output.getvalue())
        self.assertNotIn('wezterm',self.output.getvalue())

    def test_outdated_reports_versions_and_accepts_brew_updates_exit_status(self):
        installed=subprocess.CompletedProcess([],0,f'{self.meslo} 2.3.2\n{self.sarasa} 1.0.43\n','')
        result=subprocess.CompletedProcess([],1,json.dumps({'formulae':[], 'casks':[
            {'name':self.meslo,'installed_versions':['2.3.2'],'current_version':'2.3.3'}]}),'')
        with patch.object(fonts.subprocess,'run',side_effect=[installed,result]) as run, \
             patch.object(fonts,'stream_command',return_value=0) as refresh,patch.object(fonts,'install') as install:
            self.assertEqual(fonts.font_action('outdated',[],REPO,'macos'),0)
            refresh.assert_called_once_with(['/fake/brew','update'],label='字体更新索引')
            self.assertEqual(run.call_args.args[0][-2:],[self.meslo,self.sarasa])
            install.assert_not_called()
        self.assertIn('2.3.2 → 2.3.3',self.output.getvalue())
        self.assertIn('workbench font update',self.output.getvalue())

    def test_refresh_failure_does_not_claim_fonts_are_current(self):
        with patch.object(fonts,'stream_command',return_value=9),patch.object(fonts.subprocess,'run') as run:
            self.assertEqual(fonts.font_action('outdated',[],REPO,'macos'),9)
            run.assert_not_called()
        self.assertNotIn('最新',self.output.getvalue())

    def test_install_and_update_only_use_font_entries_from_platform_manifest(self):
        for action in ('install','update'):
            def install(brew,manifest,operation):
                self.assertEqual(brew,'/fake/brew')
                self.assertEqual(operation,action)
                names=[name for _,name,_ in fonts.entries(manifest)]
                self.assertEqual(names,[self.meslo,self.sarasa])
                return 0
            with self.subTest(action=action),patch.object(fonts,'install',side_effect=install) as run:
                self.assertEqual(fonts.font_action(action,[],REPO,'macos'),0)
                run.assert_called_once()

    def test_font_plan_does_not_install_platform_and_full_plan_keeps_fonts_early(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'Software'
            specs=discover(REPO,root)
            steps=plan(specs,'font','update',[],REPO,root,'macos')
            self.assertEqual([(s['component'],s['action']) for s in steps],[('font','update')])
            steps=workflow(specs,'install',[],REPO,root,'macos')
            names=[(s['component'],s['action']) for s in steps]
            self.assertLess(names.index(('platform','install')),names.index(('emacs','start')))
            self.assertFalse(root.exists())

    def test_completion_groups_are_sorted_and_match_platform(self):
        with tempfile.TemporaryDirectory() as folder:
            specs=discover(REPO,Path(folder))
            grouped={}
            for row in completion.component_groups(specs,'macos'):
                group,label,choice=row.split('\t')
                grouped.setdefault(group,[]).append(choice.split(':',1)[0])
            self.assertEqual(grouped['fonts'],['font'])
            self.assertEqual(next(iter(grouped)),'management')
            for names in grouped.values():self.assertEqual(names,sorted(names))
            from workbench.ui import width
            labels = list(dict.fromkeys(row.split('\t')[1] for row in completion.component_groups(specs,'macos')))
            self.assertEqual({width(label) for label in labels}, {4})
            self.assertLessEqual(width(' '.join('[' + label + ']' for label in labels)), 74)
            linux=list(completion.component_groups(specs,'fedora'))
            self.assertFalse(any('\tfont:' in row for row in linux))


if __name__=='__main__':unittest.main()
