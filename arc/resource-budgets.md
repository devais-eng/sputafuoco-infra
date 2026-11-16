# Standard runner resource budgets

The standard scale set keeps `maxRunners: 10` and `minRunners: 6`. ARC targets six idle runners plus assigned jobs, capped at ten total. Runners remain ephemeral and are replaced after each job.

| Container | CPU request | CPU limit | Memory request | Memory limit |
|---|---:|---:|---:|---:|
| runner | 750m | 4 | 2 GiB | 3 GiB |
| dind | 150m | 1 | 256 MiB | 768 MiB |

Requests total 900m CPU and 2.25 GiB per job. Budgets are separate: spare runner capacity is not lent to DinD. CPU excess is throttled; memory excess can cause OOM failures. Summed limits across ten jobs exceed host memory; these limits bound individual jobs, not simultaneous peak demand. The GPU scale set is unchanged.

## Why the DinD startup wrapper is required

On this k3s/containerd/cgroup-v2 host, privileged DinD sees the host cgroup hierarchy. The original daemon put Docker containers in `/docker/...`, outside the DinD container's limited scope. Merely adding Kubernetes limits would not limit those children, and previous DinD metrics undercounted their consumption.

The wrapper detects its containerd scope, moves the daemon process into a `dockerd` child, enables CPU/memory/PID delegation on the now-empty parent, and supplies that parent through `dockerd --cgroup-parent`. Docker containers and BuildKit execution processes then share the original DinD scope's aggregate limits. A daemon leaf is required for cgroup-v2 domain-controller delegation and compatibility with Docker containers that set their own memory limits.

The wrapper fails early on unsupported cgroup layouts. The PID is read with a shell builtin instead of double-dollar syntax, which Kubernetes expands in container arguments. The normal Docker entrypoint, startup probe and Unix socket are retained. This config targets trusted CI jobs; privileged DinD and Docker's explicit per-container cgroup-parent override are not a security boundary.

## Verification performed on sputafuoco

- An isolated pod cloned from the live runner template used the proposed limits and a sleeping runner, without GitHub registration.
- Runner kernel limits: `cpu.max=400000 100000`, `memory.max=3221225472`.
- DinD aggregate kernel limits: `cpu.max=100000 100000`, `memory.max=805306368`.
- A Docker child was observed beneath the DinD scope.
- `docker run --memory=64m --cpus=0.25` succeeded with delegation enabled.
- Two busy loops for five seconds increased the DinD parent's `nr_throttled` from 15 to 65.
- The PID executing `RUN sleep 13` during a Docker build was observed beneath `<DinD scope>/buildkit/...`.
- Kubernetes exec and the Docker startup probe still worked after moving the daemon into its child cgroup.

The initial 768 MiB DinD budget needs validation against real jobs now that Docker children are correctly accounted. Its historical daemon-only memory peak is not evidence that all Docker workloads fit. Monitor OOMs, restarts, throttling and job durations before treating this sizing as settled.

Rollback through Helm should restore the previous complete values (including the original DinD command/args), not only remove limits. New templates apply to new runner pods; existing jobs are not migrated in place.
