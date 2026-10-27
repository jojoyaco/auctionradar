#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'USAGE'
Usage: create-lxc.sh --ctid ID --storage STORAGE --bridge BRIDGE --ip dhcp|IPv4/CIDR \
  --template DEBIAN13_TEMPLATE --release ARCHIVE --sha256 HEX [options]

Required inputs are explicit. The script refuses an existing CT ID and never
downloads a moving application target, changes host firewall/routing, or
destroys a partially created container.

Options:
  --gateway IPv4       Required with a static --ip; must be inside its subnet
  --nameserver IPv4    Optional explicit DNS server (otherwise PVE host default)
  --hostname NAME      Default: auctionradar
  --disk-gb N          Root disk size, default: 12
  --memory-mb N        Memory, default: 2048
  --cores N            CPU cores, default: 2
  --dry-run            Print the plan; executes no pct or other Proxmox command
  --help               Show this help
USAGE
}

ctid=
storage=
bridge=
ip_address=
gateway=
nameserver=
template=
release=
sha256=
hostname=auctionradar
disk_gb=12
memory_mb=2048
cores=2
dry_run=0

while (($#)); do
    case "$1" in
        --ctid|--storage|--bridge|--ip|--gateway|--nameserver|--template|--release|--sha256|--hostname|--disk-gb|--memory-mb|--cores)
            (($# >= 2)) || { usage >&2; exit 2; }
            key=$1
            value=$2
            case "$key" in
                --ctid) ctid=$value ;;
                --storage) storage=$value ;;
                --bridge) bridge=$value ;;
                --ip) ip_address=$value ;;
                --gateway) gateway=$value ;;
                --nameserver) nameserver=$value ;;
                --template) template=$value ;;
                --release) release=$value ;;
                --sha256) sha256=$value ;;
                --hostname) hostname=$value ;;
                --disk-gb) disk_gb=$value ;;
                --memory-mb) memory_mb=$value ;;
                --cores) cores=$value ;;
            esac
            shift 2
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$ctid" || -z "$storage" || -z "$bridge" || -z "$ip_address" \
    || -z "$template" || -z "$release" || -z "$sha256" ]]; then
    usage >&2
    exit 2
fi
if [[ ! "$ctid" =~ ^[0-9]{1,9}$ ]]; then
    printf 'CTID must be an integer from 100 to 999999999.\n' >&2
    exit 2
fi
ctid=$((10#$ctid))
if ((ctid < 100 || ctid > 999999999)); then
    printf 'CTID must be an integer from 100 to 999999999.\n' >&2
    exit 2
fi
if [[ ! "$storage" =~ ^[A-Za-z0-9_.-]{1,64}$ ]]; then
    printf 'Storage ID contains unsupported characters.\n' >&2
    exit 2
fi
if [[ ! "$bridge" =~ ^[A-Za-z0-9_.:-]{1,15}$ ]]; then
    printf 'Bridge name contains unsupported characters.\n' >&2
    exit 2
fi
if [[ ! "$hostname" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
    printf 'Hostname must be a DNS label in lowercase.\n' >&2
    exit 2
fi
for numeric in disk_gb memory_mb cores; do
    value=${!numeric}
    if [[ ! "$value" =~ ^[1-9][0-9]{0,6}$ ]]; then
        printf '%s must be a positive integer.\n' "$numeric" >&2
        exit 2
    fi
done
if ((disk_gb < 8 || disk_gb > 2048 || memory_mb < 512 || memory_mb > 1048576 || cores > 128)); then
    printf 'Disk, memory, or core count is outside the supported range.\n' >&2
    exit 2
fi
if [[ ! "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'SHA-256 must be 64 lowercase hexadecimal characters.\n' >&2
    exit 2
fi
if [[ ! -f "$template" || -L "$template" || ! -r "$template" ]]; then
    printf 'Debian 13 template must be a readable local regular file.\n' >&2
    exit 2
fi
if [[ ! -f "$release" || -L "$release" || ! -r "$release" ]]; then
    printf 'Release archive must be a readable local regular file.\n' >&2
    exit 2
fi
for support_file in package.py install-lxc.sh auctionradar.env.example \
    auctionradar-web.service nginx-bootstrap.conf nginx-https.conf.example auctionradar-create-admin; do
    if [[ ! -f "$(dirname -- "${BASH_SOURCE[0]}")/$support_file" \
        || -L "$(dirname -- "${BASH_SOURCE[0]}")/$support_file" ]]; then
        printf 'Required support file missing or unsafe: %s\n' "$support_file" >&2
        exit 2
    fi
done

network_config="name=eth0,bridge=${bridge},ip=${ip_address}"
if [[ "$ip_address" == dhcp ]]; then
    if [[ -n "$gateway" ]]; then
        printf 'Do not set --gateway with DHCP.\n' >&2
        exit 2
    fi
else
    if [[ -z "$gateway" ]]; then
        printf 'Static --ip requires an explicit --gateway.\n' >&2
        exit 2
    fi
    if ! python3 - "$ip_address" "$gateway" <<'PY'
import ipaddress
import sys
try:
    interface = ipaddress.IPv4Interface(sys.argv[1])
    gateway = ipaddress.IPv4Address(sys.argv[2])
except ValueError:
    raise SystemExit(1)
unusable = {interface.network.network_address, interface.network.broadcast_address}
valid = gateway in interface.network and gateway != interface.ip
if interface.network.prefixlen < 31:
    valid = valid and interface.ip not in unusable and gateway not in unusable
raise SystemExit(0 if valid else 1)
PY
    then
        printf 'Static IP/gateway must be distinct usable IPv4 host addresses in one subnet.\n' >&2
        exit 2
    fi
    network_config+=",gw=${gateway}"
fi
if [[ -n "$nameserver" ]] && ! python3 - "$nameserver" <<'PY'
import ipaddress
import sys
try:
    ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
then
    printf 'Nameserver must be an IP address.\n' >&2
    exit 2
fi

archive_digest=$(sha256sum -- "$release")
archive_digest=${archive_digest%% *}
if [[ "$archive_digest" != "$sha256" ]]; then
    printf 'Release archive SHA-256 does not match --sha256.\n' >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
create_cmd=(pct create "$ctid" "$template" --ostype debian --unprivileged 1
    --hostname "$hostname" --storage "$storage" --rootfs "${storage}:${disk_gb}"
    --memory "$memory_mb" --cores "$cores" --net0 "$network_config" --onboot 1 --start 0)
if [[ -n "$nameserver" ]]; then
    create_cmd+=(--nameserver "$nameserver")
fi

if ((dry_run)); then
    printf 'DRY RUN: no Proxmox commands have been executed.\n'
    printf 'Planned: '
    printf '%q ' "${create_cmd[@]}"
    printf '\nPlanned: start CT %s, stage the local release/support files, run the Debian 13 installer.\n' "$ctid"
    printf 'Network: %s on bridge %s; no firewall, router, DNS, or port-forward changes.\n' "$ip_address" "$bridge"
    exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
    printf 'Run this host installer as root on a Proxmox node.\n' >&2
    exit 2
fi
for command_name in pct ip sha256sum install mktemp; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 2
    fi
done
container_list=$(pct list) || {
    printf 'Unable to list existing CTs; refusing to assume the CTID is free.\n' >&2
    exit 1
}
if [[ -z "$container_list" ]]; then
    printf 'Proxmox returned an empty CT list; refusing to assume the CTID is free.\n' >&2
    exit 1
fi
if awk -v id="$ctid" 'NR > 1 && $1 == id { found=1 } END { exit !found }' <<< "$container_list"; then
    printf 'Refusing to use existing CT ID %s.\n' "$ctid" >&2
    exit 2
fi
if ! ip link show dev "$bridge" >/dev/null 2>&1; then
    printf 'Bridge does not exist on this node: %s\n' "$bridge" >&2
    exit 2
fi

temporary_dir=$(mktemp -d /tmp/auctionradar-lxc.XXXXXXXX)
chmod 0700 "$temporary_dir"
cleanup() {
    if [[ -n "${temporary_dir:-}" && "$temporary_dir" == /tmp/auctionradar-lxc.* \
        && -d "$temporary_dir" && ! -L "$temporary_dir" ]]; then
        rm -f -- "$temporary_dir"/*
        rmdir -- "$temporary_dir"
    fi
}
trap cleanup EXIT
install -m 0600 -- "$release" "$temporary_dir/release.tar.gz"
actual_digest=$(sha256sum -- "$temporary_dir/release.tar.gz")
actual_digest=${actual_digest%% *}
if [[ "$actual_digest" != "$sha256" ]]; then
    printf 'Release changed while staging it; refusing to continue.\n' >&2
    exit 2
fi

"${create_cmd[@]}"
pct start "$ctid"
ready=0
for _attempt in $(seq 1 60); do
    if pct exec "$ctid" -- /usr/bin/test -d /root >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if (( ! ready )); then
    printf 'CT %s exists but did not become ready. It was left intact for inspection.\n' "$ctid" >&2
    exit 1
fi
pct exec "$ctid" -- /usr/bin/install -d -m 0700 /root/auctionradar-installer
pct push "$ctid" "$temporary_dir/release.tar.gz" /root/auctionradar-installer/release.tar.gz
for support_file in package.py install-lxc.sh auctionradar.env.example auctionradar-web.service \
    nginx-bootstrap.conf nginx-https.conf.example auctionradar-create-admin; do
    pct push "$ctid" "$script_dir/$support_file" "/root/auctionradar-installer/$support_file"
done
pct exec "$ctid" -- /usr/bin/env bash /root/auctionradar-installer/install-lxc.sh \
    --archive /root/auctionradar-installer/release.tar.gz --sha256 "$sha256"

for staged_file in release.tar.gz package.py install-lxc.sh auctionradar.env.example \
    auctionradar-web.service nginx-bootstrap.conf nginx-https.conf.example auctionradar-create-admin; do
    pct exec "$ctid" -- /usr/bin/rm -f -- "/root/auctionradar-installer/$staged_file"
done
pct exec "$ctid" -- /usr/bin/rmdir -- /root/auctionradar-installer

printf '\nCT %s is installed. It has no public HTTP listener; its bootstrap listener is loopback-only.\n' "$ctid"
printf 'Registration remains disabled. Configure your HTTPS certificate and trusted hostname before considering opt-in registration.\n'
printf 'If any step failed, the specified CT was deliberately left intact; inspect it before retrying.\n'
