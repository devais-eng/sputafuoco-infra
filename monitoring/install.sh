#!/usr/bin/env bash
# Run on sputafuoco as devais. Does not alter runner scale-set values.
set -euo pipefail
cd "$(dirname "$0")"
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/config}
[[ $(hostname) == sputafuoco ]] || { echo 'Expected sputafuoco'; exit 1; }
for tool in helm kubectl python3; do command -v "$tool" >/dev/null; done
umask 077
helm pull oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 92.21.2
helm pull oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller --version 0.15.1
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f prepare-storage.yaml -f storage.yaml
kubectl -n monitoring wait --for=condition=complete job/ci-monitoring-prepare-storage --timeout=120s
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  if [[ ! -f grafana-admin-password ]]; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > grafana-admin-password
  fi
  kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-user=admin --from-file=admin-password=grafana-admin-password
fi
helm upgrade --install ci-monitoring ./kube-prometheus-stack-92.21.2.tgz -n monitoring -f values.yaml --wait --timeout=25m
helm get values arc -n arc-systems > "arc-values-before-$(date +%s).yaml"
helm upgrade arc ./gha-runner-scale-set-controller-0.15.1.tgz -n arc-systems --reuse-values -f arc-metrics-values.yaml --wait --timeout=3m
kubectl -n monitoring create configmap ci-volume-usage-code --from-file=volume-usage/collector.py --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f volume-usage/resources.yaml -f dashboard.yaml -f alerts.yaml -f private-access.yaml
kubectl -n monitoring rollout restart deployment/ci-volume-usage deployment/grafana-loopback-proxy
kubectl -n monitoring rollout status deployment/grafana-loopback-proxy --timeout=180s
printf '%s\n' 'Private access: sudo tailscale serve --bg --http=3000 http://127.0.0.1:13000'
