#!/usr/bin/env python3
"""Read-only, cached allocated-byte metrics for local ARC runner PVCs.

GNU du's default no-dereference behavior and -x exclude symlinks and nested
filesystems (including DinD overlay mounts). Directory FDs pin roots across
renames; no shell or host writes are used. Collection is serialized and every
du has both a per-directory cap and the remaining overall cycle deadline.
"""

import logging
import math
import os
import re
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOLUME_NAME = re.compile(
    r'pvc-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    r'_arc-runners_([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)-(work|dind-storage)'
)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def escape_label(value):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


class Collector:
    def __init__(self, root='/ssd', node='sputafuoco', timeout=90):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('collection timeout must be positive and finite')
        self.root = os.fspath(root)
        self.node = node
        self.timeout = timeout
        self.timestamp = 0
        self.duration = 0
        self.success = 0
        self.samples = {}
        self._cache_lock = threading.Lock()
        self._collection_lock = threading.Lock()

    def _scan(self, deadline):
        def remaining():
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise TimeoutError('collection deadline exceeded')
            return budget

        root_fd = os.open(self.root, DIRECTORY_FLAGS)
        try:
            root_device = os.fstat(root_fd).st_dev
            marker = os.stat('.runner-ssd-volume', dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISREG(marker.st_mode) or marker.st_size <= 0:
                raise ValueError('SSD marker must be a nonempty regular file')
            samples = {}
            with os.scandir(root_fd) as entries:
                for entry in entries:
                    remaining()
                    match = VOLUME_NAME.fullmatch(entry.name)
                    if not match or not entry.is_dir(follow_symlinks=False):
                        continue
                    # O_NOFOLLOW closes the stat/open symlink race. A racing
                    # removal fails the whole cycle, preserving the old cache.
                    volume_fd = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=root_fd)
                    try:
                        if os.fstat(volume_fd).st_dev != root_device:
                            raise ValueError('runner volume root is on a different filesystem')
                        # Trailing /. traverses the pinned directory, rather
                        # than measuring the procfs symlink itself. du does not
                        # follow any symlink encountered inside that directory.
                        path = f'/proc/self/fd/{volume_fd}/.'
                        result = subprocess.run(
                            ['du', '-sx', '-B1', '--', path],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True, timeout=min(30.0, remaining()),
                            pass_fds=(volume_fd,), text=True,
                        )
                        remaining()
                        fields = result.stdout.split('\t', 1)
                        if len(fields) != 2 or not fields[0].isdigit():
                            raise ValueError('unexpected du output')
                        # Multiple PVCs may briefly belong to one pod; aggregate
                        # instead of emitting duplicate Prometheus label sets.
                        key = match.groups()
                        samples[key] = samples.get(key, 0) + int(fields[0])
                    finally:
                        os.close(volume_fd)
            remaining()
            return samples
        finally:
            os.close(root_fd)

    def collect(self):
        with self._collection_lock:
            started = time.monotonic()
            try:
                samples = self._scan(started + self.timeout)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                logging.warning('Volume collection failed: %s', error)
                with self._cache_lock:
                    self.success = 0
                    self.duration = time.monotonic() - started
                return
            with self._cache_lock:
                self.samples = samples
                self.timestamp = time.time()
                self.duration = time.monotonic() - started
                self.success = 1

    def metrics(self):
        with self._cache_lock:
            lines = [
                '# HELP ci_runner_volume_used_bytes Allocated bytes in local runner PVC directories.',
                '# TYPE ci_runner_volume_used_bytes gauge',
            ]
            for (pod, volume), size in sorted(self.samples.items()):
                labels = (f'node="{escape_label(self.node)}",namespace="arc-runners",'
                          f'pod="{escape_label(pod)}",volume="{escape_label(volume)}"')
                lines.append(f'ci_runner_volume_used_bytes{{{labels}}} {size}')
            for name, help_text, value in (
                ('success', 'Whether the latest entire collection succeeded.', self.success),
                ('timestamp_seconds', 'Unix time of the last fully successful collection.', self.timestamp),
                ('duration_seconds', 'Duration of the latest collection attempt.', self.duration),
            ):
                metric = f'ci_runner_volume_collection_{name}'
                lines.extend([f'# HELP {metric} {help_text}', f'# TYPE {metric} gauge', f'{metric} {value}'])
            return '\n'.join(lines) + '\n'

    def run(self, interval, stop):
        while not stop.is_set():
            started = time.monotonic()
            self.collect()
            stop.wait(max(0, interval - (time.monotonic() - started)))


def handler_for(collector):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                body = collector.metrics().encode()
                content_type = 'text/plain; version=0.0.4; charset=utf-8'
            elif self.path == '/healthz':
                # Process liveness; collection health is a separate metric.
                body, content_type = b'ok\n', 'text/plain; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return
    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    interval = float(os.environ.get('COLLECTION_INTERVAL_SECONDS', '120'))
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('collection interval must be positive and finite')
    collector = Collector(
        root=os.environ.get('ROOT_PATH', '/ssd'),
        node=os.environ.get('NODE_NAME', 'sputafuoco'),
        timeout=float(os.environ.get('COLLECTION_TIMEOUT_SECONDS', '90')),
    )
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '9108'))), handler_for(collector))
    stop = threading.Event()
    threading.Thread(target=collector.run, args=(interval, stop), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
