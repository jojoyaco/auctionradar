#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'USAGE'
Usage: install-lxc.sh --archive FILE --sha256 HEX

Install a verified local AuctionRadar product archive into a fresh Debian 13 LXC.
This script does not download application code, create users/passwords, or
replace an existing installation or its data.
USAGE
}

archive=
sha256=
while (($#)); do
    case "$1" in
        --archive)
            (($# >= 2)) || { usage >&2; exit 2; }
            archive=$2
            shift 2
            ;;
        --sha256)
            (($# >= 2)) || { usage >&2; exit 2; }
            sha256=$2
            shift 2
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

if [[ -z "$archive" || ! "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
    usage >&2
    exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
    printf 'Run this installer as root inside a new Debian 13 LXC.\n' >&2
    exit 2
fi
if [[ ! -f "$archive" || -L "$archive" ]]; then
    printf 'Archive must be a regular, non-symlink file.\n' >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for support_file in package.py auctionradar.env.example auctionradar-web.service \
    nginx-bootstrap.conf nginx-https.conf.example auctionradar-create-admin; do
    if [[ ! -f "$script_dir/$support_file" || -L "$script_dir/$support_file" ]]; then
        printf 'Required installer file missing or unsafe: %s\n' "$support_file" >&2
        exit 2
    fi
done

if ! python3 -c '
import sys
values = {}
for line in open("/etc/os-release", encoding="utf-8"):
    key, sep, value = line.rstrip("\n").partition("=")
    if sep:
        values[key] = value.strip().strip(chr(34)).strip(chr(39))
sys.exit(0 if values.get("ID") == "debian" and values.get("VERSION_ID") == "13" else 1)
'; then
    printf 'This installer supports a fresh Debian 13 container only.\n' >&2
    exit 2
fi

for path in /opt/auctionradar /etc/auctionradar /var/lib/auctionradar \
    /etc/systemd/system/auctionradar-web.service \
    /etc/nginx/sites-available/auctionradar-bootstrap.conf \
    /etc/nginx/sites-enabled/auctionradar-bootstrap.conf \
    /usr/local/sbin/auctionradar-create-admin; do
    if [[ -e "$path" || -L "$path" ]]; then
        printf 'Refusing non-fresh target; existing path: %s\n' "$path" >&2
        exit 2
    fi
done
if getent passwd auctionradar >/dev/null || getent group auctionradar >/dev/null; then
    printf 'Refusing non-fresh target; account or group auctionradar already exists.\n' >&2
    exit 2
fi
policy_guard=/usr/sbin/policy-rc.d
if [[ -e "$policy_guard" || -L "$policy_guard" ]]; then
    printf 'Refusing to replace an existing %s.\n' "$policy_guard" >&2
    exit 2
fi

temporary_root=
policy_created=0
cleanup() {
    if ((policy_created)) && [[ -f "$policy_guard" && ! -L "$policy_guard" ]]; then
        rm -f -- "$policy_guard"
    fi
    if [[ -n "$temporary_root" && "$temporary_root" == /var/tmp/auctionradar-product.* \
        && -d "$temporary_root" && ! -L "$temporary_root" ]]; then
        rm -rf -- "$temporary_root"
    fi
}
trap cleanup EXIT

temporary_root=$(mktemp -d /var/tmp/auctionradar-product.XXXXXXXX)
python3 "$script_dir/package.py" verify \
    --archive "$archive" --sha256 "$sha256" \
    --destination "$temporary_root/extracted"
release_root="$temporary_root/extracted/auctionradar-product"

# Prevent package post-install hooks from starting a network service before
# the loopback-only nginx site has replaced Debian's default site.
printf '#!/bin/sh\nexit 101\n' > "$policy_guard"
chmod 0755 "$policy_guard"
policy_created=1
DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl nginx openssl poppler-utils python3 python3-venv
rm -f -- "$policy_guard"
policy_created=0

adduser --system --group --home /var/lib/auctionradar --no-create-home \
    --shell /usr/sbin/nologin auctionradar
install -d -o root -g root -m 0755 /opt/auctionradar/app
cp -a -- "$release_root/." /opt/auctionradar/app/
chown -R root:root /opt/auctionradar/app
install -d -o auctionradar -g auctionradar -m 0750 /var/lib/auctionradar

python3 -m venv /opt/auctionradar/venv
/opt/auctionradar/venv/bin/pip install --disable-pip-version-check --no-cache-dir \
    --require-hashes --no-deps -r /opt/auctionradar/app/requirements.txt

install -d -o root -g auctionradar -m 0750 /etc/auctionradar
install -d -o root -g root -m 0750 /etc/auctionradar/tls
umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > /etc/auctionradar/session.key
python3 -c 'import secrets, sys; sys.stdout.buffer.write(secrets.token_bytes(32))' \
    > /etc/auctionradar/credential.key
chown root:auctionradar /etc/auctionradar/session.key
chmod 0640 /etc/auctionradar/session.key
chown auctionradar:auctionradar /etc/auctionradar/credential.key
chmod 0600 /etc/auctionradar/credential.key
install -o root -g auctionradar -m 0640 "$script_dir/auctionradar.env.example" \
    /etc/auctionradar/auctionradar.env

install -o root -g root -m 0644 "$script_dir/auctionradar-web.service" \
    /etc/systemd/system/auctionradar-web.service
install -d -o root -g root -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
default_site=/etc/nginx/sites-enabled/default
if [[ -L "$default_site" ]]; then
    if [[ "$(readlink -f -- "$default_site")" != /etc/nginx/sites-available/default ]]; then
        printf 'Refusing to replace an unexpected nginx default-site link.\n' >&2
        exit 2
    fi
    rm -- "$default_site"
elif [[ -e "$default_site" ]]; then
    printf 'Refusing to replace an unexpected nginx default site.\n' >&2
    exit 2
fi
install -o root -g root -m 0644 "$script_dir/nginx-bootstrap.conf" \
    /etc/nginx/sites-available/auctionradar-bootstrap.conf
ln -s /etc/nginx/sites-available/auctionradar-bootstrap.conf \
    /etc/nginx/sites-enabled/auctionradar-bootstrap.conf
install -o root -g root -m 0750 "$script_dir/auctionradar-create-admin" \
    /usr/local/sbin/auctionradar-create-admin

nginx -t
systemctl daemon-reload
systemctl enable --now auctionradar-web.service
systemctl enable --now nginx.service
python3 -c '
from urllib.request import urlopen
from urllib.error import URLError
import time
for attempt in range(50):
    try:
        with urlopen("http://127.0.0.1:8080/healthz", timeout=2) as response:
            if response.status == 200:
                print("healthz=ok")
                break
    except (URLError, TimeoutError):
        pass
    time.sleep(.2)
else:
    raise SystemExit("backend health check failed; inspect auctionradar-web.service")
'

printf '\nInstall complete. No initial account or password was created.\n'
printf 'Loopback-only bootstrap listener: http://127.0.0.1:8080/\n'
printf 'Configure and verify HTTPS before enabling any public registration.\n'
printf 'Create an administrator from the LXC console with: auctionradar-create-admin USERNAME\n'
