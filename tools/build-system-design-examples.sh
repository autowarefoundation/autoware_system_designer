#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COLLECT_MANIFESTS_SCRIPT="${REPO_ROOT}/autoware_system_designer/script/collect_system_design_manifests.py"
DEPLOYMENT_PROCESS_SCRIPT="${REPO_ROOT}/autoware_system_designer/script/deployment_process.py"
DEPLOYMENT_FILE="${REPO_ROOT}/autoware_system_design_examples/deployment/vehicle_x.system.yaml"

if [[ ! -f "${COLLECT_MANIFESTS_SCRIPT}" ]]; then
  echo "[build-system-design-examples] Missing script: ${COLLECT_MANIFESTS_SCRIPT}"
  exit 1
fi

if [[ ! -f "${DEPLOYMENT_PROCESS_SCRIPT}" ]]; then
  echo "[build-system-design-examples] Missing script: ${DEPLOYMENT_PROCESS_SCRIPT}"
  exit 1
fi

if [[ ! -f "${DEPLOYMENT_FILE}" ]]; then
  echo "[build-system-design-examples] Missing deployment file: ${DEPLOYMENT_FILE}"
  exit 1
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/autoware-system-design-examples.XXXXXX")"
MANIFEST_DIR="${TMP_ROOT}/manifest"
OUTPUT_DIR="${TMP_ROOT}/output"
TEMP_INSTALL_PREFIX="${TMP_ROOT}/install"

cleanup() {
  rm -rf -- "${TMP_ROOT}"
}
trap cleanup EXIT INT TERM

mkdir -p "${MANIFEST_DIR}" "${OUTPUT_DIR}" "${TEMP_INSTALL_PREFIX}"

echo "[build-system-design-examples] Collecting manifests (source mode, no build required)..."
python3 "${COLLECT_MANIFESTS_SCRIPT}" \
  "${REPO_ROOT}" \
  "${MANIFEST_DIR}" \
  "${TEMP_INSTALL_PREFIX}" \
  --package-map-mode source

echo "[build-system-design-examples] Running deployment_process.py..."
PYTHONPATH="${REPO_ROOT}/autoware_system_designer:${PYTHONPATH:-}" \
  python3 "${DEPLOYMENT_PROCESS_SCRIPT}" \
    "${DEPLOYMENT_FILE}" \
    "${MANIFEST_DIR}" \
    "${OUTPUT_DIR}"

echo "[build-system-design-examples] Deploy generation completed successfully."
