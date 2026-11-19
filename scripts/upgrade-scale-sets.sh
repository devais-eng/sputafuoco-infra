#!/usr/bin/env bash
# Applies arc/values-*.yaml from this repository to the runner scale sets, with helm upgrade.
#
# Usage (on the k3s node, as a sudoer):
#   scripts/upgrade-scale-sets.sh [<git ref>]
#
# The values are read from GitHub at a pinned commit, so the node needs no checkout.
# Without an argument the script resolves the current commit of main; a branch, tag or
# commit SHA can be passed instead. Labels and names cannot change with an upgrade: for
# those, uninstall and reinstall the scale set (see README).
set -euo pipefail

REPO=devais-eng/sputafuoco-infra
CHART=oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set
CHART_VERSION=0.15.1
NAMESPACE=arc-runners
KUBECONFIG_PATH=/etc/rancher/k3s/k3s.yaml
# The legacy SSD pool is retained in arc/ but is not deployed.
SCALE_SETS=(sputafuoco-arc-gpu sputafuoco-arc-tmpfs)

ref=${1:-main}
sha=$(git ls-remote "https://github.com/$REPO.git" "$ref" "refs/heads/$ref" "refs/tags/$ref" 2>/dev/null | awk 'NR==1{print $1}')
if [ -z "$sha" ]; then
  # Not a branch or tag name: accept it as a commit SHA.
  [[ $ref =~ ^[0-9a-f]{7,40}$ ]] || { echo "Unknown ref: $ref" >&2; exit 1; }
  sha=$ref
fi
raw="https://raw.githubusercontent.com/$REPO/$sha/arc"
echo "Applying $REPO@$sha"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Download and sanity-check every values file before touching the cluster.
for set in "${SCALE_SETS[@]}"; do
  curl -fsSL --retry 3 -o "$tmp/$set.yaml" "$raw/values-$set.yaml"
  grep -q "^runnerScaleSetName: $set$" "$tmp/$set.yaml" \
    || { echo "values-$set.yaml at $sha does not define scale set $set" >&2; exit 1; }
done

sudo -v   # ask for the password once, up front

for set in "${SCALE_SETS[@]}"; do
  echo "--- helm upgrade $set"
  # --force-conflicts: take back fields changed by hand (e.g. kubectl patch of maxRunners),
  # which Helm's server-side apply would otherwise refuse to overwrite.
  sudo helm --kubeconfig "$KUBECONFIG_PATH" upgrade --install "$set" "$CHART" \
    --namespace "$NAMESPACE" --version "$CHART_VERSION" -f "$tmp/$set.yaml" --force-conflicts
done

echo "--- runner image per scale set"
sudo k3s kubectl -n "$NAMESPACE" get autoscalingrunnersets \
  -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,PULL:.spec.template.spec.containers[0].imagePullPolicy,MAX:.spec.maxRunners'
