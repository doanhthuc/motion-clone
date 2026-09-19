# shellcheck shell=bash
#
# Which cloud THIS run rents from, and whether it can have a Network Volume.
#
# Sourced by pod-wait.sh, pod-bootstrap.sh and pod-smoke.sh. The caller must define env_get()
# (reads KEY from ./.env) first.
#
# Why this exists: those scripts read only .env, but a Vast run must not change the root .env
# (a crash would leave it pointing at the wrong cloud). drain.py --provider exports
# GPU_PROVIDER instead; this is where the scripts learn to prefer it. Same order as
# pod-provision.sh:21: environment, then .env, then vast.

gpu_provider() {
  local p="${GPU_PROVIDER:-$(env_get GPU_PROVIDER)}"
  printf '%s' "${p:-vast}"
}

# A Network Volume is a RunPod feature. .env keeps the RunPod volume for the home provider, so
# a Vast run must not inherit it — pod-bootstrap.sh would otherwise try to wire /workspace onto a
# box that has no such mount.
pod_volume() {
  # printf, not a bare env_get: keeps the output newline-free like gpu_provider (callers capture it
  # with $(...), which strips a newline anyway; a direct reader would not).
  if [ "$(gpu_provider)" = "runpod" ]; then printf '%s' "$(env_get POD_VOLUME)"; fi
  return 0
}
