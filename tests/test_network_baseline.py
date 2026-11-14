import importlib.util
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('baseline',
    Path(__file__).resolve().parents[1] / 'platforms/steamdeck/network/baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class NativeDNSBaselineTests(unittest.TestCase):
    def test_baseline_only_applies_dns_without_302_maintenance(self):
        with mock.patch('sys.argv', ['baseline.py']), \
             mock.patch.object(baseline.os, 'geteuid', return_value=0), \
             mock.patch.object(Path, 'read_text', return_value='ID=steamos\n'), \
             mock.patch.object(baseline, 'native_dns') as dns, \
             mock.patch.object(baseline.sp, 'run') as run, \
             mock.patch.object(baseline, 'write') as write:
            baseline.main()
        dns.assert_called_once_with()
        run.assert_not_called()
        write.assert_not_called()
