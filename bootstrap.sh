#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "Bootstrap failed near line $LINENO." >&2' ERR

log() {
    echo
    echo "==> $*"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

if [[ "$EUID" -ne 0 ]]; then
    exec sudo -E bash "$0" "$@"
fi

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$PROJECT_ROOT/install.sh"

[[ -f "$INSTALLER" ]] || fail "install.sh was not found."
[[ -x "$INSTALLER" ]] || chmod +x "$INSTALLER"

if command -v nvidia-smi >/dev/null 2>&1 &&
    nvidia-smi >/dev/null 2>&1
then
    log "Working NVIDIA driver detected"
    exec bash "$INSTALLER"
fi

log "Preparing NVIDIA driver detection"

apt-get update

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    pciutils \
    ubuntu-drivers-common

if ! lspci 2>/dev/null | grep -qi NVIDIA; then
    fail "No NVIDIA GPU was detected. Use an NVIDIA GPU-enabled VM."
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    echo
    echo "NVIDIA_DRIVER_PRESENT_BUT_NOT_ACTIVE"
    echo "Reboot the VM, then run sudo bash bootstrap.sh again."
    exit 20
fi

log "Installing Ubuntu-recommended NVIDIA compute driver"

available_drivers="$(ubuntu-drivers list --gpgpu 2>/dev/null || true)"

[[ -n "$available_drivers" ]] || \
    fail "Ubuntu could not find a compatible NVIDIA compute driver."

DEBIAN_FRONTEND=noninteractive ubuntu-drivers install --gpgpu

if command -v nvidia-smi >/dev/null 2>&1 &&
    nvidia-smi >/dev/null 2>&1
then
    log "NVIDIA driver installed successfully"
    exec bash "$INSTALLER"
fi

echo
echo "NVIDIA_DRIVER_INSTALLED_REBOOT_REQUIRED"
echo "Reboot the VM, then run sudo bash bootstrap.sh again."
exit 20
