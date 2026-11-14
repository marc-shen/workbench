import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('baseline',
    Path(__file__).resolve().parents[1] / 'platforms/steamdeck/network/baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class HostsCleanupTests(unittest.TestCase):
    def test_only_302_owned_lines_are_removed(self):
        original = ('127.0.0.1 localhost\n::1 localhost\n'
                    '127.0.0.1 login.steampowered.com #S302\n'
                    '192.168.1.2 example.test # personal\n'
                    '# keep comments\n127.0.0.1 store.steampowered.com #S302\n')
        clean = baseline.without_302_hosts(original)
        self.assertEqual(clean, '127.0.0.1 localhost\n::1 localhost\n'
                         '192.168.1.2 example.test # personal\n# keep comments\n')
        self.assertEqual(baseline.without_302_hosts(clean), clean)
