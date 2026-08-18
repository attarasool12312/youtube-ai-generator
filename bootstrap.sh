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

# Use the main Ubuntu mirror instead of the sometimes-slow EC2 regional mirror.
for source_file in \
    /etc/apt/sources.list.d/ubuntu.sources \
    /etc/apt/sources.list
do
    [[ -f "$source_file" ]] || continue

    sed -Ei         's#https?://([a-z0-9-]+\.)?ec2\.archive\.ubuntu\.com/ubuntu/?#http://archive.ubuntu.com/ubuntu/#g'         "$source_file"
done

apt-get clean
apt-get update

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    pciutils

if ! lspci 2>/dev/null | grep -qi NVIDIA; then
    fail "No NVIDIA GPU was detected. Use an NVIDIA GPU-enabled VM."
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    echo
    echo "NVIDIA_DRIVER_PRESENT_BUT_NOT_ACTIVE"
    echo "Reboot the VM, then run sudo bash bootstrap.sh again."
    exit 20
fi

log "Installing pinned NVIDIA 595 Server driver"

NVIDIA_DRIVER_PACKAGE="nvidia-driver-595-server"
NVIDIA_UTILS_PACKAGE="nvidia-utils-595-server"

apt-cache show "$NVIDIA_DRIVER_PACKAGE" >/dev/null 2>&1 || \
    fail "Required NVIDIA package is unavailable: $NVIDIA_DRIVER_PACKAGE"

apt-cache show "$NVIDIA_UTILS_PACKAGE" >/dev/null 2>&1 || \
    fail "Required NVIDIA package is unavailable: $NVIDIA_UTILS_PACKAGE"

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "$NVIDIA_DRIVER_PACKAGE" \
    "$NVIDIA_UTILS_PACKAGE"

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
