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

# ubuntu-drivers may install the kernel/compute driver without
# the matching nvidia-smi userspace package on some GPU images.
# Detect the installed compute-driver branch and ensure its
# matching nvidia-utils package is installed before reboot.
compute_package="$(
    dpkg-query -W         -f='${Package}|${Status}\n'         'libnvidia-compute-*'         2>/dev/null |
    awk -F'|'         '$2 == "install ok installed" {print $1}' |
    sort -V |
    tail -n 1 ||
    true
)"

if [[ -n "$compute_package" ]]; then
    driver_series="${compute_package#libnvidia-compute-}"
    utils_package="nvidia-utils-${driver_series}"

    if apt-cache show "$utils_package" >/dev/null 2>&1; then
        if ! dpkg-query -W             -f='${Status}'             "$utils_package"             2>/dev/null |
            grep -Fx 'install ok installed' >/dev/null
        then
            log "Installing matching NVIDIA utilities: $utils_package"
            DEBIAN_FRONTEND=noninteractive apt-get install -y                 "$utils_package"
        fi
    else
        echo             "WARNING: Matching NVIDIA utility package $utils_package was not found."             >&2
    fi
fi

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
