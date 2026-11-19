"""Exercise cache-measurement races without root privileges or host mutations."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('prepare-ci-ssd-caches.sh').read_text()
START = SCRIPT.index('measure_uv_cache() {')
FUNCTION = SCRIPT[START:SCRIPT.index('\n}\n', START) + 3]


class CacheMeasurementTests(unittest.TestCase):
    def measure(self, diagnostic='', status=0, output='16384\t/cache\n'):
        with tempfile.TemporaryDirectory() as directory:
            du = Path(directory) / 'du'
            du.write_text('#!/bin/sh\nprintf "%s" "$FAKE_OUTPUT"\nprintf "%s" "$FAKE_ERROR" >&2\nexit "$FAKE_STATUS"\n')
            du.chmod(0o755)
            env = dict(os.environ, PATH=directory + ':' + os.environ['PATH'],
                       FAKE_ERROR=diagnostic, FAKE_STATUS=str(status), FAKE_OUTPUT=output)
            return subprocess.run(['bash', '-c', FUNCTION + '\nmeasure_uv_cache /cache'],
                                  env=env, capture_output=True, text=True)

    def test_success(self):
        result = self.measure()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), '16384')

    def test_disappearing_temporary_directory(self):
        result = self.measure("du: cannot access '/cache/builds-v0/.tmpABC': No such file or directory\n", 1)
        self.assertEqual(result.returncode, 0)
        self.assertIn('vanished', result.stderr)

    def test_real_errors_are_not_suppressed(self):
        for diagnostic in [
            "du: cannot read directory '/cache': Permission denied\n",
            "du: cannot access '/cache/wheels-v5/package': No such file or directory\n",
            "du: cannot access '/cache/.tmpABC': Input/output error\n",
            "du: cannot access '/cache/.tmpABC': No such file or directory\ndu: cannot read directory '/cache/private': Permission denied\n",
            '',
        ]:
            with self.subTest(diagnostic=diagnostic):
                self.assertNotEqual(self.measure(diagnostic, 1).returncode, 0)

    def test_invalid_measurement_is_rejected(self):
        self.assertNotEqual(self.measure(output='').returncode, 0)

    def test_real_du_excludes_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp = root / '.tmp-build'
            temp.mkdir()
            (temp / 'data').write_bytes(b'x' * 1048576)
            result = subprocess.run(['bash', '-c', FUNCTION + '\nmeasure_uv_cache "$1"', '_', directory],
                                    capture_output=True, text=True, check=True)
            full = subprocess.check_output(['du', '-sx', '-B1', directory], text=True).split()[0]
            self.assertLess(int(result.stdout), int(full))


if __name__ == '__main__':
    unittest.main()
