import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collector.py'))
collector = importlib.util.module_from_spec(SPEC) if SPEC and SPEC.loader and Path(SPEC.origin).exists() else None
if collector:
    SPEC.loader.exec_module(collector)

NAME = 'pvc-12345678-1234-1234-1234-123456789abc_arc-runners_runner-one-work'

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(collector, 'collector implementation is required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.runner-ssd-volume').write_text('runner-ssd\n')
        self.volume = self.root / NAME
        self.volume.mkdir()
        (self.volume / 'data').write_bytes(b'x' * 8192)
        self.exporter = collector.Collector(self.root, node='sputafuoco', timeout=2)

    def test_allocated_bytes_and_success_metrics(self):
        expected = sum(p.stat().st_blocks * 512 for p in [self.volume, self.volume / 'data'])
        self.exporter.collect()
        metrics = self.exporter.metrics()
        self.assertIn('ci_runner_volume_used_bytes{node="sputafuoco",namespace="arc-runners",pod="runner-one",volume="work"} ' + str(expected), metrics)
        self.assertIn('ci_runner_volume_collection_success 1', metrics)
        self.assertGreater(self.exporter.timestamp, 0)

    def test_disappeared_volume_removed_after_success(self):
        self.exporter.collect()
        shutil.rmtree(self.volume)
        self.exporter.collect()
        self.assertNotIn('pod="runner-one"', self.exporter.metrics())
        self.assertIn('ci_runner_volume_collection_success 1', self.exporter.metrics())

    def test_missing_empty_or_symlink_marker_fails_closed(self):
        marker = self.root / '.runner-ssd-volume'
        for kind in ('missing', 'empty', 'symlink'):
            with self.subTest(kind=kind):
                marker.unlink(missing_ok=True)
                if kind == 'empty':
                    marker.touch()
                elif kind == 'symlink':
                    marker.symlink_to(self.volume / 'data')
                self.exporter.collect()
                self.assertIn('ci_runner_volume_collection_success 0', self.exporter.metrics())
                self.assertEqual(self.exporter.timestamp, 0)
                self.assertNotIn('pod="runner-one"', self.exporter.metrics())

    def test_unsafe_names_and_symlink_roots_ignored(self):
        (self.root / NAME.replace('runner-one', 'runner-two')).symlink_to(self.volume, target_is_directory=True)
        for name in ('unrelated', NAME.replace('arc-runners', 'default'), NAME.replace('runner-one', 'bad"name'), NAME.replace('12345678', 'invalid')):
            (self.root / name).mkdir()
        self.exporter.collect()
        self.assertEqual(self.exporter.metrics().count('ci_runner_volume_used_bytes{'), 1)

    def test_failure_and_timeout_retain_last_success(self):
        self.exporter.collect()
        timestamp = self.exporter.timestamp
        old_sample = next(line for line in self.exporter.metrics().splitlines() if line.startswith('ci_runner_volume_used_bytes{'))
        for error in (subprocess.CalledProcessError(1, 'du'), subprocess.TimeoutExpired('du', 0.01)):
            with self.subTest(error=error), patch.object(collector.subprocess, 'run', side_effect=error):
                self.exporter.collect()
                self.assertEqual(self.exporter.timestamp, timestamp)
                self.assertIn(old_sample, self.exporter.metrics())
                self.assertIn('ci_runner_volume_collection_success 0', self.exporter.metrics())
        self.exporter.collect()
        self.assertGreater(self.exporter.timestamp, timestamp)

    def test_full_cycle_deadline_keeps_previous_cache(self):
        self.exporter.collect()
        timestamp = self.exporter.timestamp
        self.exporter.timeout = 0.001
        real_run = subprocess.run
        def slow_run(*args, **kwargs):
            time.sleep(0.01)
            return real_run(*args, **kwargs)
        with patch.object(collector.subprocess, 'run', side_effect=slow_run):
            self.exporter.collect()
        self.assertEqual(self.exporter.timestamp, timestamp)
        self.assertIn('ci_runner_volume_collection_success 0', self.exporter.metrics())

    def test_label_escaping(self):
        self.exporter.node = 'a"b\\c\nd'
        self.exporter.collect()
        self.assertIn('node="a\\"b\\\\c\\nd"', self.exporter.metrics())

    def test_symlink_root_fails_closed(self):
        link = self.root / 'linked-root'
        link.symlink_to(self.root, target_is_directory=True)
        instance = collector.Collector(link, timeout=2)
        instance.collect()
        self.assertIn('ci_runner_volume_collection_success 0', instance.metrics())

    def test_dind_storage_and_nested_symlink_not_double_counted(self):
        dind = self.root / NAME.replace('-work', '-dind-storage')
        dind.mkdir()
        (dind / 'link').symlink_to(self.volume, target_is_directory=True)
        expected = dind.stat().st_blocks * 512 + (dind / 'link').lstat().st_blocks * 512
        self.exporter.collect()
        self.assertIn('pod="runner-one",volume="dind-storage"} ' + str(expected), self.exporter.metrics())

    def test_real_du_process_is_killed_on_timeout(self):
        binary_dir = self.root / 'bin'
        binary_dir.mkdir()
        fake_du = binary_dir / 'du'
        fake_du.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(10)\n')
        fake_du.chmod(0o755)
        self.exporter.timeout = 0.03
        started = time.monotonic()
        with patch.dict(os.environ, {'PATH': str(binary_dir)}):
            self.exporter.collect()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn('ci_runner_volume_collection_success 0', self.exporter.metrics())
        self.assertEqual(self.exporter.timestamp, 0)

    def test_http_metrics_and_liveness_do_not_trigger_collection(self):
        self.exporter.collect()
        timestamp = self.exporter.timestamp
        for path, expected in (('/metrics', b'ci_runner_volume_collection_success 1'), ('/healthz', b'ok\n'), ('/missing', b'404')):
            handler = collector.handler_for(self.exporter).__new__(collector.handler_for(self.exporter))
            handler.path = path
            handler.request_version = 'HTTP/1.1'
            handler.requestline = 'GET ' + path + ' HTTP/1.1'
            handler.command = 'GET'
            handler.wfile = io.BytesIO()
            handler.do_GET()
            self.assertIn(expected, handler.wfile.getvalue())
        self.assertEqual(self.exporter.timestamp, timestamp)

    def test_background_collection_refreshes_cache(self):
        stop = threading.Event()
        worker = threading.Thread(target=self.exporter.run, args=(0.01, stop))
        worker.start()
        try:
            deadline = time.monotonic() + 1
            while self.exporter.timestamp == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            timestamp = self.exporter.timestamp
            self.assertGreater(timestamp, 0)
            shutil.rmtree(self.volume)
            while 'pod="runner-one"' in self.exporter.metrics() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertNotIn('pod="runner-one"', self.exporter.metrics())
            self.assertGreater(self.exporter.timestamp, timestamp)
        finally:
            stop.set()
            worker.join(1)
        self.assertFalse(worker.is_alive())

if __name__ == '__main__':
    unittest.main()
