# Dedicated CI Monitoring Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; independent collector work may use superpowers:dispatching-parallel-agents.

**Goal:** Install and version a private Grafana dashboard for sputafuoco's node pressure, runner usage and ARC activity.
**Architecture:** kube-prometheus-stack 92.21.2 in monitoring, 30s scrapes, 7d/5GB Prometheus retention. Static local HDD PVs at /srv/data/monitoring (Retain); SSD reserved for disposable runner data. Enable existing ARC 0.15.1 metrics, collect actual per-volume allocated bytes using a read-only bounded collector. Grafana private via Tailscale, authenticated, credentials outside Git.
**Tech Stack:** Helm, Prometheus Operator, node-exporter, kube-state-metrics, Grafana, Python standard library collector.
**Spec:** User requested dedicated installation after agreeing to node/pod/ARC metric dashboard, with private Tailscale access.

## Constraints and review focus
- Do not change runner templates, limits or interrupt busy jobs.
- Persist all deployment configuration/dashboard in repository, never credentials.
- PVC local metrics are filesystem-wide; per-runner bytes must come from directory measurements without double-counting overlay mounts.
- Collector must bound runtime, reject symlinks/missing SSD marker, report errors/freshness, and never mutate host data.
- No public NodePort/Ingress; Serve may require one user sudo action because devais is not a Tailscale operator.
- Disable monitoring targets absent in k3s (standalone etcd/scheduler/controller-manager/proxy); retain useful pod/node monitoring and alerts.

## Tasks
1. [x] Prepare pinned Helm values, static HDD PV/PVC manifests, ARC metrics settings and PodMonitors; render and server validate.
2. [x] Implement/test read-only per-runner disk collector (monitoring/volume-usage/collector.py and test_collector.py).
3. [x] Provision dashboard JSON and local alert rules for pressure, disk headroom, pod/collector health.
4. [x] Deploy stack and collector, enable ARC metrics, validate scrape targets and every dashboard query against actual data; verify credentials and private Grafana access.
5. [x] Document operations, independent review, publish repository changes and report URL/login instructions and limits.

## Verification

Installed on sputafuoco: 17/17 Prometheus targets up; all 30 dashboard queries valid; required metrics nonempty; authenticated Grafana dashboard API verified; all three persistent claims bound. Collector: 12 tests passing. Helm template and shell syntax checks passing; independent review fixes incorporated. Private backend bound to 127.0.0.1:13000. Tailscale Serve activated and verified as tailnet-only. Authenticated browser rendering checked; disk/volume legends clarified and historical exporter-pod labels deduplicated.
