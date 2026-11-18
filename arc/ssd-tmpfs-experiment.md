# SSD versus tmpfs workspace experiment

Approved 2026-11-18: reduce the standard pool from six workers to one without interrupting active jobs, then add one tmpfs worker. Use identical CPU and memory budgets for both pools.

| Pool | runs-on | Workspace | Runner request / limit | DinD limit |
|---|---|---|---|---|
| sputafuoco-arc | sallo | ephemeral SSD PVC | 6 / 7 GiB, 750m / 3 CPU | 4 GiB, 2 CPU |
| sputafuoco-arc-tmpfs | sallo | emptyDir Memory, 4 GiB | 6 / 7 GiB, 750m / 3 CPU | 4 GiB, 2 CPU |

Both have minRunners=maxRunners=1. DinD storage remains on SSD; the shared uv cache remains on HDD. The existing PostgreSQL test tmpfs configuration is unchanged. A tmpfs size limit is not a reservation; pages are charged to the writing container, including DinD if Docker writes to the shared workspace. Pods have swap disabled. Runner requests of 6 GiB reserve scheduling headroom for workspace plus processes. GPU configuration is unchanged.

## Deployment and drain

Back up Helm values and runner status. Upgrade the existing release in place; never uninstall it or delete busy pods. ARC 0.15.1 asks the Actions service to remove a runner and skips deletion on JobStillRunningError. Its template rollout also preserves busy registered runners. Wait for the old jobs to finish and for one runner using the new template before installing the second pool. Existing jobs retain their original limits until completion.

Run scripts/upgrade-scale-sets.sh with this branch's pinned commit for subsequent reconciliation. It installs missing releases as well as upgrading existing releases. Do not use main until this change is merged.

## Comparison

Both pools accept the existing `sallo` label, so unchanged workflows can run on either workspace. Identify the workspace by the runner name (the tmpfs pool has prefix `sputafuoco-arc-tmpfs-runner-`) and compare runs of the same suite and commit. Automatic routing does not guarantee equivalent workloads. Record the runner image digest and pin it for benchmark runs if latest changes. Repeat at least three times, first separately then concurrently. Keep cache restore settings and test commands identical. Measure installation/test durations separately, runner and DinD memory, workspace size, disk reads/writes, CPU throttling, I/O pressure and OOMs. Flag runs overlapping a k3s restart. Reduced concurrency alone may improve both pools: compare the pools directly, not just against earlier six-worker runs.

The SSD volume's 4 GiB request is not an enforced quota; tmpfs has a real 4 GiB ceiling. ENOSPC is an experiment failure, not evidence that tmpfs is slower. SSD volume collector metrics exclude the tmpfs workspace; measure it inside the pod with df/du and use container memory metrics.

## Rollback

Set the `sputafuoco-arc-tmpfs` pool to min/max zero and let ARC drain it; existing workflows keep using `sallo`. Restore the previous SSD release values with Helm (six runners, runner request 2 GiB / limit 3 GiB). Do not delete a busy pod or uninstall a busy release. Keep monitoring enabled.
