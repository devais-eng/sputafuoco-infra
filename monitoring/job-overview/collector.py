"""Read-only ARC job metadata exporter. No GitHub credentials or host mounts."""
import json
import logging
import os
from pathlib import Path
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def active_jobs(runners, pods, node):
    local = {p['metadata']['name'] for p in pods
             if p.get('spec', {}).get('nodeName') == node
             and p.get('status', {}).get('phase') == 'Running'
             and not p['metadata'].get('deletionTimestamp')}
    rows = []
    for runner in runners:
        meta, status = runner['metadata'], runner.get('status', {})
        if (meta['name'] not in local or meta.get('deletionTimestamp')
                or status.get('phase') != 'Running' or not status.get('jobId')
                or not status.get('jobDisplayName') or not status.get('workflowRunId')):
            continue
        repository = status.get('jobRepositoryName', '')
        workflow, _, ref = status.get('jobWorkflowRef', '').partition('@')
        workflow = workflow.removeprefix(repository + '/').removeprefix('.github/workflows/')
        run_id = str(status['workflowRunId'])
        if not repository or not run_id.isdigit():
            continue
        rows.append(dict(node=node, runner=meta['name'], repository=repository,
                         workflow=workflow, ref=ref, job_name=status['jobDisplayName'],
                         run_id=run_id,
                         run_url='https://github.com/' + quote(repository, safe='/') + '/actions/runs/' + run_id))
    return rows


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def render(rows, success, timestamp, node):
    label = 'node="' + escape(node) + '"'
    lines = ['# TYPE ci_arc_job_collection_success gauge',
             f'ci_arc_job_collection_success{{{label}}} {int(success)}',
             '# TYPE ci_arc_job_collection_timestamp_seconds gauge',
             f'ci_arc_job_collection_timestamp_seconds{{{label}}} {timestamp}',
             '# TYPE ci_arc_running_job_info gauge']
    if success:
        for row in rows:
            labels = ','.join(f'{k}="{escape(v)}"' for k, v in sorted(row.items()))
            lines.append(f'ci_arc_running_job_info{{{labels}}} 1')
    return '\n'.join(lines) + '\n'


class Kubernetes:
    def __init__(self, namespace):
        self.namespace = namespace
        self.credentials = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.context = ssl.create_default_context(cafile=str(self.credentials / 'ca.crt'))
        self.base = 'https://' + os.environ['KUBERNETES_SERVICE_HOST'] + ':' + os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')

    def list(self, resource):
        prefix = '/api/v1' if resource == 'pods' else '/apis/actions.github.com/v1alpha1'
        path = prefix + '/namespaces/' + quote(self.namespace, safe='') + '/' + resource
        items, continuation = [], ''
        while True:
            query = urlencode({'limit': 500, 'continue': continuation})
            req = Request(self.base + path + '?' + query, headers={
                'Authorization': 'Bearer ' + (self.credentials / 'token').read_text().strip()})
            with urlopen(req, context=self.context, timeout=10) as response:
                data = json.load(response)
            items.extend(data['items'])
            continuation = data.get('metadata', {}).get('continue', '')
            if not continuation:
                return items


class Collector:
    def __init__(self, api, node):
        self.api, self.node = api, node
        self.lock = threading.Lock()
        self.rows, self.success, self.timestamp = [], False, 0

    def collect(self):
        try:
            rows = active_jobs(self.api.list('ephemeralrunners'), self.api.list('pods'), self.node)
        except Exception as error:
            # Do not retain stale active-job rows or log response bodies/tokens.
            logging.warning('Kubernetes collection failed: %s', type(error).__name__)
            with self.lock:
                self.rows, self.success = [], False
            return
        with self.lock:
            self.rows, self.success, self.timestamp = rows, True, time.time()

    def metrics(self):
        with self.lock:
            return render(self.rows, self.success, self.timestamp, self.node)


def main():
    logging.basicConfig(level=logging.INFO)
    collector = Collector(Kubernetes(os.environ.get('RUNNER_NAMESPACE', 'arc-runners')),
                          os.environ.get('NODE_NAME', 'sputafuoco'))

    def loop():
        while True:
            collector.collect()
            time.sleep(15)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ['/metrics', '/healthz']:
                self.send_error(404)
                return
            body = (collector.metrics() if self.path == '/metrics' else 'ok\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 9109), Handler).serve_forever()


if __name__ == '__main__':
    main()
