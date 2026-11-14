# sputafuoco-infra

Self-hosted GitHub Actions runners for the `devais-eng` organization: a single-node k3s cluster running Actions Runner Controller (ARC) in Docker-in-Docker mode, a Docker Hub pull-through cache, a GPU runner, and the runner image they all use.

## Overview

| Component | Version | Namespace | Source |
|---|---|---|---|
| k3s (single server) | `v1.37.2+k3s1` | — | [Install](#install-from-scratch) |
| Helm | `v4.3.2` | — | — |
| ARC controller (`gha-runner-scale-set-controller`) | `0.15.1` | `arc-systems` | — |
| Scale set `sputafuoco-arc` (label `sallo`, max 6 runners) | `0.15.1` | `arc-runners` | `arc/values-sputafuoco-arc.yaml` |
| Scale set `sputafuoco-arc-gpu` (label `sallo-gpu`, max 1 runner) | `0.15.1` | `arc-runners` | `arc/values-sputafuoco-arc-gpu.yaml` |
| Dedicated SSD local-path provisioner (`runner-ssd`) | `0.0.37` | `arc-runners` | `arc/runner-ssd-provisioner.yaml` |
| Docker Hub pull-through cache (`registry:3`) | — | `arc-runners` | `registry-mirror/registry-mirror.yaml` |
| NVIDIA device plugin | `0.20.1` | `nvidia-device-plugin` | `nvidia/values-device-plugin.yaml` |
| Runner image `ghcr.io/devais-eng/arc-runner-sallo` | — | — | `runner-image/` |

Workflows select the runners by label:

```yaml
runs-on: sallo        # regular jobs (dind)
runs-on: sallo-gpu    # jobs that use the GPU directly in the runner container
```

## Design notes

- **SSD space and I/O bound concurrency.** Job workspaces and Docker data use the roughly 60 GiB SSD partition, with a maximum of **6 regular runners + 1 GPU runner**. Existing job samples used about 5 GiB per regular runner, but peak usage and GPU jobs still need measurement. Before raising concurrency, measure free bytes/inodes and I/O pressure during a full CI run. System images, other pod volumes and the registry cache still use their existing storage.
- **Ephemeral runners.** Every job gets a fresh pod and a fresh Docker daemon. Nothing leaks between jobs, but nothing is cached either: the runner image and the pull-through cache exist to win that time back.
- **Where data lives:**

| Data | Path |
|---|---|
| k3s state (SQLite) and system images (containerd) | `/var/lib/rancher/k3s` (root filesystem) |
| Per-pod job workspace and dind `/var/lib/docker` | `/srv/runner-ssd` (`/dev/sda3`, ext4, mounted by UUID) |
| Remaining pod `emptyDir` volumes: dind socket and externals | `/srv/data/kubelet` |
| Pull-through cache | `/srv/data/ci-cache/registry` |
| .NET crash reports | `/srv/data/ci-cache/dotnet-crash` |

The workspace and Docker volumes are **generic ephemeral volumes**: each pod owns fresh PVCs, and deletion of the pod triggers PVC garbage collection and the provisioner's `Delete` reclaim policy. Cleanup is asynchronous and waits for the pod to terminate; monitor stuck pods/PVs rather than deleting their data manually. The requests of 4 GiB for workspace and 3 GiB for Docker are **not enforced quotas** with local-path-provisioner. The SSD is not RAID-backed; it holds disposable job data.

## Install from scratch

Run everything on the node as a sudoer. Under `sudo`, point Helm at the kubeconfig explicitly: recent Ubuntu `sudo` ignores `-E`.

```bash
K=/etc/rancher/k3s/k3s.yaml
ARC=oci://ghcr.io/actions/actions-runner-controller-charts
```

### 1. k3s

```bash
sudo mkdir -p /srv/data/kubelet
curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION=v1.37.2+k3s1 sh -s - server \
  --disable traefik \
  --kubelet-arg=root-dir=/srv/data/kubelet
sudo k3s kubectl get nodes
```

`--kubelet-arg=root-dir` moves pod volumes to the large data disk. The device-plugin socket directory stays at `/var/lib/kubelet/device-plugins` regardless.

### 2. Helm

```bash
V=v4.3.2
curl -fsSLO https://get.helm.sh/helm-$V-linux-amd64.tar.gz
curl -fsSL https://get.helm.sh/helm-$V-linux-amd64.tar.gz.sha256sum | sha256sum -c -
tar xzf helm-$V-linux-amd64.tar.gz && sudo install -m 0755 linux-amd64/helm /usr/local/bin/helm
```

### 3. ARC controller

```bash
sudo helm --kubeconfig $K install arc --namespace arc-systems --create-namespace \
  $ARC/gha-runner-scale-set-controller --version 0.15.1 --wait
```

### 4. GitHub App and secret

ARC authenticates with an organization-owned GitHub App, installed on the organization, with:

- Repository permissions: **Metadata: Read-only**
- Organization permissions: **Self-hosted runners: Read and write**
- Webhook: disabled

Its App ID is on the App's settings page; the Installation ID is the last segment of the installation URL (`…/settings/installations/<ID>`). The private key is generated on the App's page and never stored in this repository. Copy it to the node, create the secret, then destroy the copy:

```bash
sudo k3s kubectl create namespace arc-runners
sudo k3s kubectl -n arc-runners create secret generic github-app \
  --from-literal=github_app_id=<APP_ID> \
  --from-literal=github_app_installation_id=<INSTALLATION_ID> \
  --from-file=github_app_private_key=/path/to/key.pem
shred -u /path/to/key.pem
```

### 5. Docker Hub pull-through cache

```bash
sudo k3s kubectl apply -f registry-mirror/registry-mirror.yaml
```

The runners' dind daemons use it through `--registry-mirror=http://registry-mirror.arc-runners.svc.cluster.local:5000`. It saves downloads and Docker Hub rate limit, not disk writes: each pod still extracts the layers into its own ephemeral SSD volume. A proxy-mode registry is read-only, so it cannot host images of our own.

### 5a. SSD storage for runners

On the existing node, `/dev/sda3` is already formatted and mounted at `/srv/runner-ssd`; **do not reformat it**. On a fresh installation, inspect the partition table and prepare a dedicated ext4 filesystem first, preserving the EFI and system RAID partitions. Mount the filesystem by its own UUID in `/etc/fstab`, for example:

```fstab
UUID=<SSD_FILESYSTEM_UUID> /srv/runner-ssd ext4 defaults,noatime,nofail,x-systemd.device-timeout=10s 0 2
```

After mounting, verify that the source is the intended SSD filesystem. Create its marker only on that mounted filesystem, then install the provisioner:

```bash
findmnt --mountpoint /srv/runner-ssd
# Run the following only after checking the source device above.
sudo sh -ec '
  mountpoint -q /srv/runner-ssd
  uuid=$(findmnt -nro UUID --mountpoint /srv/runner-ssd)
  test -n "$uuid"
  printf "%s\n" "$uuid" > /srv/runner-ssd/.runner-ssd-volume
  chmod 0444 /srv/runner-ssd/.runner-ssd-volume
'
sudo k3s kubectl apply -f arc/runner-ssd-provisioner.yaml
sudo k3s kubectl -n arc-runners rollout status deployment/runner-ssd-provisioner
sudo k3s kubectl get storageclass runner-ssd
```

The dedicated provisioner serves only node `sputafuoco` and leaves k3s's default `local-path` class unchanged. It uses local PVs with `WaitForFirstConsumer` and `Delete`. Its helper scripts reject missing mount markers, unexpected paths and symlinks, preventing new runner data from silently falling back to the root filesystem when the SSD is not mounted. The marker must remain at the SSD root, outside all per-PVC directories. Do not unmount the SSD or remove the provisioner while SSD-backed pods or PVs remain.

### 6. Regular scale set

```bash
sudo helm --kubeconfig $K install sputafuoco-arc --namespace arc-runners \
  -f arc/values-sputafuoco-arc.yaml $ARC/gha-runner-scale-set --version 0.15.1
```

The values are the chart's own expanded `containerMode: dind` template, plus:

- generic ephemeral SSD volumes for `/home/runner/_work` and the dind `/var/lib/docker`, so each job gets isolated workspace, images and Docker volumes that are removed after its pod is deleted;
- `--registry-mirror` pointing at the pull-through cache;
- the runner image from this repository, with `imagePullPolicy: Always` (see [Runner image](#runner-image)).

### 7. GPU

```bash
sudo k3s kubectl get runtimeclass nvidia          # created by k3s when nvidia-container-toolkit is on the host
sudo k3s kubectl label node "$(hostname)" nvidia.com/gpu.present=true --overwrite
sudo helm --kubeconfig $K repo add nvdp https://nvidia.github.io/k8s-device-plugin
sudo helm --kubeconfig $K install nvdp nvdp/nvidia-device-plugin --namespace nvidia-device-plugin --create-namespace \
  --version 0.20.1 -f nvidia/values-device-plugin.yaml --wait
sudo k3s kubectl describe node "$(hostname)" | grep nvidia.com/gpu   # expect 1
sudo helm --kubeconfig $K install sputafuoco-arc-gpu --namespace arc-runners \
  -f arc/values-sputafuoco-arc-gpu.yaml $ARC/gha-runner-scale-set --version 0.15.1
```

The node label stands in for Node Feature Discovery: the device plugin's DaemonSet only schedules on nodes carrying it (or the NFD labels). The GPU scale set is the regular one plus `runtimeClassName: nvidia` and `nvidia.com/gpu: 1` on the runner container only; the dind sidecar does not see the GPU.

## Runner image

`runner-image/Dockerfile` builds `ghcr.io/devais-eng/arc-runner-sallo`: the official ARC runner image (`ghcr.io/actions/actions-runner`) with the dependencies our CI otherwise installs on every job.

| Tool | Version | Notes |
|---|---|---|
| `build-essential`, `gh`, `wget`, `xz-utils` | Ubuntu 24.04 apt | |
| `tesseract-ocr`, `tesseract-ocr-eng` | Ubuntu 24.04 apt | |
| Docker Compose CLI plugin | `v2.32.4` | |
| FFmpeg and FFprobe | `8.1` | BtbN static build, the same one `AnimMouse/setup-ffmpeg` downloads |
| Task | `3.48.0` | pre-seeded in the tool cache for `go-task/setup-task` |
| uv | `0.11.2` | pre-seeded in the tool cache for `astral-sh/setup-uv` |
| CPython | `3.13.12` | in `~/.local/share/uv/python`, where `setup-uv` points `UV_PYTHON_INSTALL_DIR` |

How it works:

- The runner exports `RUNNER_TOOL_CACHE` to the jobs. By default it is `_work/_tool`, on the pod's fresh workspace volume; the image sets it to `/opt/hostedtoolcache` and pre-seeds it with the exact layout `@actions/tool-cache` writes and looks up: `task/<version>/x64/bin/task` and `uv/<version>/x86_64/{uv,uvx}`, each with its `<arch>.complete` marker. `setup-task` and `setup-uv` then find them and skip the download.
- The versions must match the ones the workflows pin. A mismatch is harmless: the action downloads its own copy, as without the image.
- Every download in the Dockerfile is verified against the checksum published with its release.
- BtbN's `latest` release is rolling, so each build picks up the newest patch release of the FFmpeg branch, as the setup action does.

### Build and test locally

```bash
docker build -t arc-runner-sallo:test runner-image/
docker run --rm -i arc-runner-sallo:test bash -s < runner-image/smoke-test.sh
```

`smoke-test.sh` checks the versions, the tool-cache layout, the Python available to uv, the `runner` user's permissions, and that every tool runs.

### CI/CD

`.github/workflows/runner-image.yml` runs on GitHub-hosted runners:

| Trigger | Result |
|---|---|
| Pull request touching `runner-image/` or the workflow | build and smoke test, no publish |
| Push to `main` | build, smoke test, publish |
| Weekly (Monday 04:00 UTC) | same as a push: picks up base-image updates and FFmpeg patch releases |
| Manual (`workflow_dispatch`) | same as a push |

Each publish pushes two tags: an immutable `<runner version>-<date>-<commit>` and the moving `latest`. `latest` is only pushed after the smoke test passes.

The scale sets run `latest` with `imagePullPolicy: Always`. Runner pods are created per job, so every new job checks the registry and starts on the newest image, without touching the cluster. Pulls are cheap after the first: only changed layers are downloaded. To pin a known-good image instead, replace `latest` with an immutable tag in both `arc/values-*.yaml` (runner container and `init-dind-externals`) and run `helm upgrade`.

The package must be public (Package settings → Danger Zone → Change visibility) so the cluster can pull it without credentials.

### Making the most of it in workflows

With the image as is, Task, uv and Python are found without downloading. Two kinds of steps still do redundant work unless the workflows skip them when the tools are already present:

- `apt-get update` followed by the install of packages already in the image: guard it with `command -v gcc gh wget xz >/dev/null`.
- `AnimMouse/setup-ffmpeg` restores from the Actions cache or downloads on every run; it does not look in the tool cache. Skip it when `ffmpeg -version` already reports the pinned branch.

`apt-get install` of packages that are already installed works even without `apt-get update`: it reports "already the newest version" and exits 0.

## CI monitoring

Dedicated Prometheus and Grafana monitor node/pod pressure, filesystem capacity, ARC activity and allocated SSD bytes per runner volume. Historical data lives on HDD; Grafana is available over Tailscale with login. See [monitoring setup and operations](monitoring/README.md).

## Operations

### Applying changes to the scale sets

```bash
scripts/upgrade-scale-sets.sh            # values from the current commit of main
scripts/upgrade-scale-sets.sh <ref>      # or from a branch, tag or commit SHA
```

It reads `arc/values-*.yaml` from GitHub at a pinned commit, so the node needs no checkout, runs `helm upgrade` on both scale sets and prints the image each one runs. It asks for the sudo password once.

The values preserve SSD workspace/Docker volumes and the 6+1 limits. The script does not prepare disks or install the storage provisioner: complete [SSD storage for runners](#5a-ssd-storage-for-runners) first on a fresh node. If the provisioner manifest changes, apply it separately before updating the scale sets. Existing runner pods keep their old volumes until they terminate; the updated template applies to new pods.

### Changing a scale set's labels or name

GitHub fixes a scale set's labels when it is **created**; `helm upgrade` only updates the Kubernetes resource. To change them, uninstall and reinstall the scale set. It is re-registered with a new ID, and queued jobs are reassigned.

```bash
sudo helm --kubeconfig $K uninstall sputafuoco-arc -n arc-runners --wait
sudo helm --kubeconfig $K install sputafuoco-arc -n arc-runners \
  -f arc/values-sputafuoco-arc.yaml $ARC/gha-runner-scale-set --version 0.15.1
```

Every other value (template, `maxRunners`, image) applies with `helm upgrade`.

### Quick checks

```bash
sudo k3s kubectl -n arc-systems get pods                       # controller and listeners
sudo k3s kubectl -n arc-runners get ephemeralrunners,pods      # runners and their jobs
sudo k3s kubectl -n arc-systems logs -l app.kubernetes.io/component=runner-scale-set-listener --tail=20
sudo k3s kubectl -n arc-runners get pvc                      # per-runner SSD claims
sudo k3s kubectl get pv                                     # check cleanup of finished pods
df -h /srv/runner-ssd                                       # free bytes
df -i /srv/runner-ssd                                       # free inodes
```

If jobs stay queued while the listener logs `totalAssignedJobs=0`, GitHub is not matching the scale set to the job's `runs-on`. The usual cause is a missing label: see above.
