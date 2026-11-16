# Standard runner resource budgets

The standard scale set keeps `maxRunners: 6` and `minRunners: 6`. ARC targets six idle runners plus assigned jobs, capped at six total. Runners remain ephemeral and are replaced after each job.

| Container | CPU request | CPU limit | Memory request | Memory limit |
|---|---:|---:|---:|---:|
| runner | 750m | 3 | 2 GiB | 3 GiB |
| dind | 150m | 2 | 256 MiB | 4 GiB |

Requests total 900m CPU and 2.25 GiB per job. Budgets are separate: spare runner capacity is not lent to DinD. CPU excess is throttled; memory excess can cause OOM failures. Summed limits across six jobs exceed host memory; these limits bound individual jobs, not simultaneous peak demand. The GPU scale set is unchanged.

## Why the DinD startup wrapper is required

On this k3s/containerd/cgroup-v2 host, privileged DinD sees the host cgroup hierarchy. The original daemon put Docker containers in `/docker/...`, outside the DinD container's limited scope. Merely adding Kubernetes limits would not limit those children, and previous DinD metrics undercounted their consumption.

The wrapper detects its containerd scope, moves the daemon process into a `dockerd` child, enables CPU/memory/PID delegation on the now-empty parent, and supplies that parent through `dockerd --cgroup-parent`. Docker containers and BuildKit execution processes then share the original DinD scope's aggregate limits. A daemon leaf is required for cgroup-v2 domain-controller delegation and compatibility with Docker containers that set their own memory limits.

The wrapper fails early on unsupported cgroup layouts. The PID is read with a shell builtin instead of double-dollar syntax, which Kubernetes expands in container arguments. The normal Docker entrypoint, startup probe and Unix socket are retained. This config targets trusted CI jobs; privileged DinD and Docker's explicit per-container cgroup-parent override are not a security boundary.

## Initial delegation verification performed on sputafuoco (768 MiB limit)

- An isolated pod cloned from the live runner template used the proposed limits and a sleeping runner, without GitHub registration.
- Runner kernel limits: `cpu.max=400000 100000`, `memory.max=3221225472`.
- DinD aggregate kernel limits: `cpu.max=100000 100000`, `memory.max=805306368`.
- A Docker child was observed beneath the DinD scope.
- `docker run --memory=64m --cpus=0.25` succeeded with delegation enabled.
- Two busy loops for five seconds increased the DinD parent's `nr_throttled` from 15 to 65.
- The PID executing `RUN sleep 13` during a Docker build was observed beneath `<DinD scope>/buildkit/...`.
- Kubernetes exec and the Docker startup probe still worked after moving the daemon into its child cgroup.

## Memory budget correction (2026-11-16)

The framework-only smoke job 133357261387 failed after the kernel killed dockerd and PostgreSQL within the 768 MiB DinD cgroup. Daemon-only historical metrics had underestimated the budget for nested containers.

A local `task ci:smoke:framework` run from the requested OmneX-openai-yaml checkout passed all 11 tests in 23.66 seconds (35.73 seconds including installation/checks). Sampling new Docker cgroups every 200 ms measured a simultaneous aggregate `memory.current` peak of 734.55 MiB, including tmpfs and page cache but excluding the host Docker daemon. Aggregate working memory peaked at 726.75 MiB. GNU time reported maximum RSS of 700.57 MiB for the test command tree's largest individual process, separately from Docker; this is not aggregate runner memory.

DinD was initially increased to 2 GiB to leave room for Docker and startup peaks. The local run is evidence for this smoke suite, not a guarantee for every build or simultaneous load from ten jobs. Requests remain unchanged; with the current 4 GiB DinD limit, six runners can have a combined 42 GiB memory ceiling on the roughly 30 GiB host. Continue monitoring node pressure, kernel OOMs and job duration; a zero cAdvisor OOM counter did not exclude the observed kernel OOM kills.

For rollback of the delegation change, restore the previous complete values, including the original DinD command/args. A memory-only rollback can restore the previous limit, but 768 MiB is known to fail this workload. Helm templates apply to new pods. On this cluster, existing DinD sidecars can receive the memory-limit increase through the Pod resize subresource; verify the actual kernel limit and container restart count after resizing.

## Increase to 4 GiB after sallo-ai OOMs

Two further cgroup OOM episodes occurred at 14:55 and 16:04 UTC on 2026-11-16, both while running the sallo-ai test module with a 2 GiB DinD limit. Available node memory remained at least 11.6 GiB over the observation period; these were container-limit failures, not host-wide RAM exhaustion. Increase DinD to 4 GiB for all standard runners. Keep runner memory at 3 GiB, all requests and CPU limits unchanged, and min/max runners now at 6/6. GPU sizing remains separate.

The 4 GiB cap is a monitored trial, not a reservation or a guarantee that every workload fits. Watch kernel OOMs, container restarts and node memory availability; reduce concurrency if simultaneous peaks cause host pressure. Disk I/O pressure is a separate bottleneck that this memory change does not address.

## Concurrency reduced to six standard runners

After the 4 GiB increase, no further kernel OOMs were observed through 21:33 UTC on 2026-11-16. Several tests nevertheless hit subprocess/startup timeouts. During 17:20–18:08 UTC, node I/O full pressure had a median of 57.82% and a peak of 83.76%, with at least 12.29 GiB RAM available and negligible runner CPU throttling. Reduce maxRunners from 10 to 6 while retaining minRunners=6 and the 4 GiB DinD cap. This is a concurrency trial to reduce I/O contention, not proof that every test failure was caused by the node. ARC manages scale-down; do not forcibly delete busy runners.

## CPU limits redistributed between runner and DinD

Standard runner CPU limits are now 3 cores for the runner and 2 cores for DinD, including its nested Docker workloads. Requests remain 750m and 150m respectively. This preserves the combined ceiling of 5 cores per job while allowing Docker services more CPU headroom; it does not allocate exclusive cores. Memory limits remain 3 GiB/4 GiB and the standard pool stays at min/max 6. Historical delegation checks above record the original 4/1 CPU limits.
