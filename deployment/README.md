# deployment/

This folder contains infrastructure-as-code for running podcast-ad-cutter on **Fedora CoreOS** using **Podman/Quadlet** (rootless containers managed by systemd) and **Ignition** for automated provisioning. `generate.sh` renders the template files into a single `ignition.json` that CoreOS reads on first boot to configure the entire system.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| `butane` CLI | [coreos/butane releases](https://github.com/coreos/butane/releases) — or run via container |
| GitHub Container Registry token | Personal Access Token with `read:packages` scope. Create at [github.com/settings/tokens](https://github.com/settings/tokens). Only needed if your `ghcr.io` package is private. |
| Tailscale account + auth key | Reusable or one-time key from [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys) |
| At least one LLM API key | Groq, OpenAI, OpenRouter, or others (see `env.example`) |
| `config.yaml` at project root | Copy from `config.example.yaml` and fill in feed URLs and model settings |
| Optional: NFS share | For shared/persistent output storage across re-provisions |

---

## Quick Start

```bash
# 1. Fill in your values
cp deployment/env.example deployment/env
$EDITOR deployment/env

# 2. Generate ignition.json
./deployment/generate.sh

# 3. Provision a CoreOS VM — see the Proxmox example below
```

`ignition.json` and `env` are gitignored because they contain plaintext secrets.

---

## env File Reference

All variables come from `deployment/env.example`:

| Variable | Required | Description |
|---|---|---|
| `GITHUB_USERNAME` | Yes | Your GitHub username (owner of the `ghcr.io` package) |
| `GHCR_TOKEN` | No | GitHub PAT with `read:packages`. Leave blank for public images. |
| `SSH_PUBLIC_KEY` | Yes | Full public key string for the `core` user (e.g. `ssh-ed25519 AAAA... user@host`) |
| `TAILSCALE_AUTH_KEY` | Yes | Auth key from the Tailscale admin console |
| `TAILSCALE_HOSTNAME` | Yes | Hostname this node appears as in the Tailscale dashboard |
| `OPENAI_API_KEY` | No | Required if using OpenAI models |
| `GROQ_API_KEY` | No | Required if using Groq models |
| `OPENROUTER_API_KEY` | No | Required if using OpenRouter models |
| `ANTHROPIC_API_KEY` | No | Required if using Anthropic/Claude models |
| `GEMINI_API_KEY` | No | Required if using Google Gemini models |
| `AWS_ACCESS_KEY_ID` | No | Required for AWS Bedrock |
| `AWS_SECRET_ACCESS_KEY` | No | Required for AWS Bedrock |
| `AWS_REGION_NAME` | No | AWS region for Bedrock (default: `us-east-1`) |
| `NFS_SHARE` | No | NFS share in `server:/export/path` format. Leave blank to use a named Podman volume. |
| `NFS_MOUNT_PATH` | No | Mount point on the CoreOS host (default: `/mnt/podcast-output`). Only used when `NFS_SHARE` is set. |

Fill in at least the required variables and one or more LLM API keys.

---

## Proxmox VE Example

Full walkthrough using Proxmox VE 8+ with the `qm` CLI. Run commands prefixed with "on the Proxmox host" from a root shell on the Proxmox node (via SSH or the web console).

### 5.1 Download Fedora CoreOS (on the Proxmox host)

```bash
# Find the latest stable QEMU image URL
curl -s https://builds.coreos.fedoraproject.org/streams/stable.json | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['architectures']['x86_64']['artifacts']['qemu']['formats']['qcow2.xz']['disk']['location'])
"

# Download and decompress (substitute the actual URL from the command above)
curl -O <url-from-above>
xz -d fedora-coreos-*-qemu.x86_64.qcow2.xz
```

### 5.2 Create the VM

```bash
VMID=200

qm create $VMID \
  --name podcast-ad-cutter \
  --ostype l26 \
  --cpu host \
  --cores 2 \
  --memory 4096 \
  --machine q35 \
  --bios ovmf \
  --efidisk0 local-lvm:1,efitype=4m \
  --scsihw virtio-scsi-pci \
  --net0 virtio,bridge=vmbr0,firewall=1 \
  --serial0 socket \
  --vga serial0 \
  --onboot 1
```

> **Tip:** Adjust `--cores`, `--memory`, and the storage pool (`local-lvm`) to match your Proxmox setup.

### 5.3 Import the CoreOS disk

```bash
qm disk import $VMID fedora-coreos-*-qemu.x86_64.qcow2 local-lvm --format qcow2
qm set $VMID --scsi0 local-lvm:vm-${VMID}-disk-1,discard=on
qm set $VMID --boot order=scsi0
```

### 5.4 Attach the Ignition config

```bash
# Copy ignition.json from your local machine to the Proxmox host
scp deployment/ignition.json root@<proxmox-host>:/root/ignition-podcast-ad-cutter.json

# On the Proxmox host — pass ignition.json via QEMU fw_cfg
qm set $VMID --args "-fw_cfg name=opt/com.coreos/config,file=/root/ignition-podcast-ad-cutter.json"
```

CoreOS reads this file on first boot via the QEMU firmware configuration interface. It is the standard mechanism for passing Ignition configs to CoreOS VMs.

### 5.5 Boot and observe first boot

```bash
qm start $VMID

# Attach to the serial console to watch Ignition and first-boot.sh run
# Press Ctrl+O to detach
qm terminal $VMID
```

Ignition runs first (partition/file setup), then `podcast-ad-cutter-first-boot.service` runs after the network is online. First boot takes 1–3 minutes depending on image pull speed.

### 5.6 Access the service

```bash
# Via Tailscale (recommended — works across networks):
ssh core@<tailscale-hostname>.<tailnet-name>.ts.net
# Web UI: http://<tailscale-hostname>.<tailnet-name>.ts.net:8000

# Via local network (get VM IP from serial console or your DHCP server):
ssh core@<vm-ip>
curl http://<vm-ip>:8000
```

> **Note:** Tailscale SSH must be enabled in your tailnet ACLs before you can use `ssh core@<tailscale-hostname>`. Until then use the local IP.

---

## Optional: NFS Output Mount

By default, processed episodes are stored in a named Podman volume on the CoreOS host (`podcast-ad-cutter-output`). To use an NFS share instead:

```bash
# In deployment/env:
NFS_SHARE=192.168.1.10:/exports/podcasts
NFS_MOUNT_PATH=/mnt/podcast-output

# Regenerate ignition.json
./deployment/generate.sh
```

`generate.sh` will render an `.mount` systemd unit from `nfs-output.mount.template`, inject it into `butane.yaml`, and configure the container to use the NFS path as the output volume with the correct `After=`/`Requires=` dependencies.

---

## Auto-Updates

`podman-auto-update.timer` ships with Fedora CoreOS and is enabled by `generate.sh`. It checks `ghcr.io` daily for a newer image digest and restarts the container if one is found (because the `.container` file has `AutoUpdate=registry`).

```bash
# Check the timer status
systemctl status podman-auto-update.timer

# Trigger an update check immediately
sudo podman auto-update
```

---

## Viewing Logs

```bash
# Container application logs (follows live)
journalctl -u podcast-ad-cutter.service -f

# First-boot script output (last 50 lines)
journalctl -u podcast-ad-cutter-first-boot.service -n 50

# Tailscale
journalctl -u tailscale.service -f
```

---

## How It Works

```
generate.sh
    │
    ├── renders templates      podcast-ad-cutter.container
    │   (sed / envsubst)   →   tailscale.container
    │                          butane.yaml
    │                          first-boot.sh (copied as-is)
    │                          config.yaml   (copied from project root)
    │
    └── runs butane        →   ignition.json  (plaintext secrets — keep private)

CoreOS VM first boot
    │
    ├── Ignition reads ignition.json
    │   ├── writes /etc/podcast-ad-cutter/secrets.env  (mode 0600)
    │   ├── writes /etc/containers/systemd/*.container  (Quadlet units)
    │   ├── writes /usr/local/bin/podcast-ad-cutter-first-boot
    │   └── enables podcast-ad-cutter-first-boot.service
    │
    └── podcast-ad-cutter-first-boot.service  (oneshot, after network-online.target)
        ├── sources /etc/podcast-ad-cutter/secrets.env
        ├── logs in to ghcr.io (if GHCR_TOKEN is set)
        ├── creates Podman secrets (one per API key + tailscale_auth_key)
        ├── pulls ghcr.io/<GITHUB_USERNAME>/podcast-ad-cutter:latest
        ├── runs systemctl daemon-reload  →  Quadlet generates *.service units
        ├── starts tailscale.service and podcast-ad-cutter.service
        └── shreds /etc/podcast-ad-cutter/secrets.env  (secrets no longer on disk)

Subsequent reboots
    └── systemd starts podcast-ad-cutter.service and tailscale.service normally
        (Quadlet regenerates units on each daemon-reload; secrets live in Podman)
```

The sentinel file `/var/lib/podcast-ad-cutter/.setup-done` prevents `first-boot.sh` from running more than once per VM lifecycle.

---

## Re-provisioning

If you need a clean slate:

```bash
# 1. Destroy the VM
qm stop 200
qm destroy 200

# 2. Regenerate ignition.json if config changed
./deployment/generate.sh

# 3. Recreate the VM following the steps above (5.2 – 5.5)
```

`config.yaml` changes (feeds, model settings) do **not** require re-provisioning — edit the file on the host and the web UI picks it up immediately:

```bash
ssh core@<host>
sudo vi /etc/podcast-ad-cutter/config.yaml
```
