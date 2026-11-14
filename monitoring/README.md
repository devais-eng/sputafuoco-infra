# CI monitoring on sputafuoco

Dedicated kube-prometheus-stack 92.21.2 in namespace `monitoring`, with Prometheus, Grafana, Alertmanager, node-exporter and kube-state-metrics. ARC controller 0.15.1 exposes native Prometheus metrics; an OpenTelemetry collector is not required.

Grafana: http://grafana.example.invalid:3000/d/sputafuoco-ci
Login: `admin`. Initial password on sputafuoco: `cat ~/ci-monitoring/grafana-admin-password`. Credentials are excluded from Git. Change the password through Grafana after login; the bootstrap file then no longer reflects the changed password.

Access uses Tailscale Serve HTTP over the encrypted tailnet. Nothing listens publicly on port 3000; a Kubernetes host-network proxy listens on 127.0.0.1:13000 only. Tailnet ACLs and Grafana login control access. Enable on the host:

```bash
sudo tailscale serve --bg --http=3000 http://127.0.0.1:13000
```

Disable only this endpoint with `sudo tailscale serve --http=3000 off`.

## Installation and updates

Copy this directory to `~/ci-monitoring` on sputafuoco and run `bash install.sh`. Requires existing k3s credentials, Helm, Python3, the SSD migration and the existing ARC controller release `arc`. The preparation job validates `/srv/data` is `/dev/md1p1` before creating monitoring directories. No partitions or runner scale sets are changed. The existing `upgrade-scale-sets.sh` continues to manage only runners.

Persistent local volumes on HDD at `/srv/data/monitoring/{prometheus,grafana,alertmanager}` use Retain reclaim policy. Requested capacities (10/2/1 GiB) are scheduling declarations, not filesystem quotas. Prometheus retention is 7 days or approximately 5 GB of retained blocks, whichever comes first; WAL and head data use additional space. Scrape interval is 30 seconds. Grafana uses SQLite WAL and a 20-minute startup allowance for first-install migrations on busy HDDs. Node-exporter binds the node LAN address, not all host interfaces. Memory limits: Prometheus 2 GiB, Grafana 512 MiB, operator 256 MiB and smaller exporter limits.

The dashboard source is `dashboard.json`; `dashboard.yaml` embeds the same JSON for Grafana sidecar provisioning. Keep both synchronized. Queries use datasource UID `prometheus`. Built-in Kubernetes and node dashboards are also installed. Kubernetes scheduler/controller-manager/proxy/etcd scraping is disabled because these are not independently exposed in this k3s setup.

## Runner volume accounting

Kubelet local-PV volume stats report the entire backing filesystem, so they cannot measure individual runner directories. The read-only collector scans only valid ARC PVC directories on the SSD with `du -sx -B1`, every 120 seconds, with a 90-second cycle deadline and 30-second per-directory timeout. CPU is limited to 100m and given low scheduling priority. Samples measure allocated filesystem blocks, not logical file sizes or quotas. Deleted open files and data outside the scanned directories can make filesystem use exceed summed runner use.

Collection is independent of Prometheus scraping; failures retain cached values and expose success=0 and the last successful timestamp. Always check the dashboard freshness panels. With no runners, volume series are absent; success and freshness still report normally. Missing/nonregular SSD marker, symlinks and cross-device directories are rejected. The collector has a read-only SSD mount and only DAC_READ_SEARCH capability to read runner-owned files; it has no Kubernetes API token.

## Alerts and limitations

Custom alerts cover SSD free space below 20%, sustained I/O full pressure above 20%, failed/stale/missing volume collection. Standard stack alerts cover host filesystems, workloads and scrape failures. Alerts are visible in Prometheus/Alertmanager; external delivery is not configured.

This is single-node monitoring: if sputafuoco is down, its dashboard and alert evaluation are down too. Historical metrics remain after ephemeral pods disappear, until retention expires. PSI reports time lost to contention; high utilization and high pressure mean different things. Physical-disk throughput must not be added to RAID-device throughput (same I/O at different layers).

Run collector tests with `python3 -m unittest discover -s monitoring/volume-usage -v` from repository root. To inspect deployment: `kubectl -n monitoring get pods,pvc`; `helm -n monitoring status ci-monitoring`.

Uninstalling the Helm release leaves retained local data and custom manifests. Do not delete the directories or PVs unless their history is intentionally being discarded.

References: [ARC metrics](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets#enabling-metrics), [Grafana configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/), [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).

Run `KUBECONFIG=~/.kube/config python3 ~/ci-monitoring/verify.py` on the node after at least two scrape cycles. It validates required scrape targets, all dashboard queries and the provisioned Grafana dashboard. Interactive use prompts for the current admin password; noninteractive bootstrap verification uses the initial password file. `GRAFANA_PASSWORD` can supply a current password via the environment.

Disk legends distinguish the Kingston SSD (sda), WD HDD #1 (sdb) and WD HDD #2 (sdc), and identify the system RAID, data RAID and runner SSD filesystem. Node-level dashboard queries remove exporter pod identity so an exporter restart does not split history into duplicate legend entries. Live runner file churn and scan timeouts can skip collection cycles; consult the freshness and success panels before interpreting cached per-runner usage.
