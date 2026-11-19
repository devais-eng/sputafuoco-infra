import copy
import unittest
from collector import active_jobs, render, Collector


def fixture():
    runner = {'metadata': {'name': 'worker-1'}, 'status': {
        'phase': 'Running', 'jobId': 'job-guid', 'jobDisplayName': 'Test "API"\ncase',
        'jobRepositoryName': 'example/project', 'workflowRunId': 123,
        'jobWorkflowRef': 'example/project/.github/workflows/ci.yml@refs/heads/main'}}
    pod = {'metadata': {'name': 'worker-1'}, 'spec': {'nodeName': 'sputafuoco'},
           'status': {'phase': 'Running'}}
    return runner, pod


class Tests(unittest.TestCase):
    def test_running_metadata_and_run_link(self):
        r, p = fixture()
        rows = active_jobs([r], [p], 'sputafuoco')
        self.assertEqual(rows[0]['workflow'], 'ci.yml')
        self.assertEqual(rows[0]['run_url'], 'https://github.com/example/project/actions/runs/123')
        text = render(rows, True, 123, 'sputafuoco')
        self.assertIn('job_name="Test \\"API\\"\\ncase"', text)

    def test_idle_pending_finished_and_foreign_node_excluded(self):
        r, p = fixture()
        for phase in ['Pending', 'Succeeded', 'Failed']:
            candidate = copy.deepcopy(r)
            candidate['status']['phase'] = phase
            self.assertEqual(active_jobs([candidate], [p], 'sputafuoco'), [])
        r['status'].pop('jobId')
        self.assertEqual(active_jobs([r], [p], 'sputafuoco'), [])
        r, p = fixture()
        p['spec']['nodeName'] = 'another-node'
        self.assertEqual(active_jobs([r], [p], 'sputafuoco'), [])

    def test_deleted_or_missing_pod_excluded(self):
        r, p = fixture()
        p['metadata']['deletionTimestamp'] = 'now'
        self.assertEqual(active_jobs([r], [p], 'sputafuoco'), [])
        self.assertEqual(active_jobs([r], [], 'sputafuoco'), [])

    def test_failure_drops_stale_job_rows(self):
        class API:
            fail = False
            def list(self, resource):
                if self.fail:
                    raise TimeoutError()
                r, p = fixture()
                return [p] if resource == 'pods' else [r]
        api = API()
        c = Collector(api, 'sputafuoco')
        c.collect()
        self.assertIn('ci_arc_running_job_info{', c.metrics())
        stamp = c.timestamp
        api.fail = True
        c.collect()
        self.assertNotIn('ci_arc_running_job_info{', c.metrics())
        self.assertEqual(c.timestamp, stamp)
        self.assertIn('ci_arc_job_collection_success{node="sputafuoco"} 0', c.metrics())

    def test_empty_healthy_collection_is_distinct_from_failure(self):
        text = render([], True, 123, 'sputafuoco')
        self.assertIn('ci_arc_job_collection_success{node="sputafuoco"} 1', text)
        self.assertNotIn('ci_arc_running_job_info{', text)


if __name__ == '__main__':
    unittest.main()
