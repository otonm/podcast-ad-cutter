# Deployment

Fedora CoreOS VM running the podcast-ad-cutter pipeline on a systemd timer. Provisioned via [Ignition](https://coreos.github.io/ignition/) — the entire host configuration (users, files, systemd units) is declared once in `ignition.json` and applied atomically on first boot.

## Architecture

```
generate.sh
  ├── reads  deployment/env          (secrets + site-specific vars)
  ├── reads  config.yaml             (feed URLs, model settings)
  ├── renders *.template → .staging/
  └── calls  butane → ignition.json

ignition.json
  └── applied by CoreOS on first boot:
        ├── writes config.yaml, secrets.env, systemd units, first-boot.sh
        └── enables podcast-ad-cutter-first-boot.service

first-boot.sh (runs once, network-gated)
  ├── logs into ghcr.io (if GHCR_TOKEN set)
  ├── creates Podman secrets from secrets.env
  ├── pulls ghcr.io/<GITHUB_USERNAME>/podcast-ad-cutter:latest
  ├── systemctl daemon-reload  (Quadlet generates .service from .container)
  ├── systemctl enable --now tailscale.service
  ├── systemctl enable --now podcast-ad-cutter.timer
  └── shreds secrets.env
```

## Developer Machine Prerequisites

| Tool | Install |
|------|---------|
| [butane](https://github.com/coreos/butane/releases) | `dnf install butane` / download binary |
| `envsubst` | `gettext` package |
| `systemd-escape` | `systemd` package (Linux only) |

`generate.sh` will fail fast if any of these are absent.

## Configuration

### 1. `deployment/env`

```bash
cp deployment/env.example deployment/env
```

Fill in `deployment/env`:

```bash
# Required
GITHUB_USERNAME=your-github-username
SSH_PUBLIC_KEY="ssh-ed25519 AAAA... user@host"
FEED_CHECK_INTERVAL_HOURS=6

TAILSCALE_AUTH_KEY=tskey-auth-...
TAILSCALE_HOSTNAME=my-coreos-server

# Optional — leave blank for named Podman volume
GHCR_TOKEN=ghp_...
NFS_SHARE=192.168.1.10:/exports/podcasts   # or blank
NFS_MOUNT_PATH=/mnt/podcast-output         # default if omitted

# API keys — fill in only the providers you use
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
# ... see env.example for full list
```

`deployment/env` and `ignition.json` are gitignored. Do not commit them.

### 2. `config.yaml`

Ensure `config.yaml` exists at the project root and contains your feed URLs and model settings. `generate.sh` copies it into the ignition payload as-is.

### 3. Tailscale auth key

Obtain a reusable or ephemeral auth key from [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys). Reusable keys are convenient for reprovisioning; ephemeral keys expire after the node is inactive.

## Generating `ignition.json`

```bash
./deployment/generate.sh
```

This renders all templates into a temporary `.staging/` directory, then calls `butane --strict` to produce `deployment/ignition.json`. The staging directory is cleaned up on exit.

### What the script substitutes

| Template | Substitutions performed |
|----------|------------------------|
| `podcast-ad-cutter.container.template` | `{{GITHUB_USERNAME}}`, `{{OUTPUT_VOLUME_LINE}}`, `{{NFS_AFTER_LINE}}`, `{{NFS_REQUIRES_LINE}}` |
| `podcast-ad-cutter.timer.template` | `{{INTERVAL_HOURS}}` → integer, or literal `daily` for 24 |
| `tailscale.container.template` | `{{TAILSCALE_HOSTNAME}}` |
| `nfs-output.mount.template` | `{{NFS_SHARE}}`, `{{NFS_MOUNT_PATH}}` (only when `NFS_SHARE` is set) |
| `butane.yaml.template` | `${VAR}` env vars via `envsubst`; NFS directory and unit entries via `sed` markers |

### NFS output behaviour

- `NFS_SHARE` blank → output goes to the `podcast-ad-cutter-output` named Podman volume.
- `NFS_SHARE` set → `systemd-escape` derives the mount unit name from `NFS_MOUNT_PATH`, a `.mount` unit is generated and embedded, and `podcast-ad-cutter.container` declares `Requires=` and `After=` that unit. The container bind-mounts the host path with `:z` (shared SELinux label).

---

## Deployment

### Cloud (user-data)

Pass `deployment/ignition.json` as instance user-data. All major providers (AWS, GCP, Azure, Hetzner) accept Ignition v3 natively on Fedora CoreOS images.

### Bare metal

```bash
coreos-installer install /dev/sda \
    --ignition-file deployment/ignition.json
```

Replace `/dev/sda` with the target block device. The installer writes the Ignition config into the ESP and it is consumed on first boot.

### VM — generic QEMU/KVM

Pass the config via the firmware configuration device:

```bash
qemu-system-x86_64 \
    -fw_cfg name=opt/com.coreos/config,file=deployment/ignition.json \
    ...
```

---

## Proxmox VE

Proxmox does not have a native Ignition UI. The official method uses the cloud-init `cicustom` parameter to deliver the Ignition file as vendor data — CoreOS picks it up from the cloud-init drive on first boot.

All commands run as root on the Proxmox host.

### 1. Create a dedicated storage location

```bash
mkdir -p /var/coreos/images /var/coreos/snippets
pvesm add dir coreos --path /var/coreos --content images,snippets
```

`pvesm add` registers the directory with Proxmox and makes it available in the GUI and CLI as storage `coreos`.

### 2. Download the CoreOS image

Use the `proxmoxve` platform image (includes QEMU guest agent, virtio drivers):

```bash
# Using coreos-installer binary
STREAM=stable
coreos-installer download \
    -s ${STREAM} -p proxmoxve -f qcow2.xz --decompress \
    -C /var/coreos/images

# Or using the container (if coreos-installer is not installed on the host)
podman run --pull=always --rm \
    -v /var/coreos/images:/data -w /data \
    quay.io/coreos/coreos-installer:release \
    download -s ${STREAM} -p proxmoxve -f qcow2.xz --decompress
```

The decompressed filename will be `fedora-coreos-<version>-proxmoxve.x86_64.qcow2`.

### 3. Copy `ignition.json` to the snippets directory

```bash
scp deployment/ignition.json root@<proxmox-host>:/var/coreos/snippets/podcast-ad-cutter.ign
```

Or if generating directly on the Proxmox host:

```bash
cp deployment/ignition.json /var/coreos/snippets/podcast-ad-cutter.ign
```

### 4. Create and configure the VM

Adjust `VM_ID`, `STORAGE`, `CPU`, `MEMORY`, and `DISK_SIZE` to suit.

```bash
VM_ID=200
NAME=podcast-ad-cutter
QCOW=$(ls /var/coreos/images/fedora-coreos-*-proxmoxve.x86_64.qcow2 | tail -1)
IGN=podcast-ad-cutter.ign
STORAGE=local-lvm   # or your preferred storage pool
CPU=2
MEMORY=2048
DISK_SIZE=20G       # appended to the base image size (~5 GB)

# Create VM
qm create ${VM_ID} \
    --name ${NAME} \
    --cores ${CPU} \
    --memory ${MEMORY} \
    --net0 virtio,bridge=vmbr0 \
    --scsihw virtio-scsi-pci \
    --machine q35

# Import QCOW2 image as primary disk
qm set ${VM_ID} --scsi0 "${STORAGE}:0,import-from=${QCOW}"

# Expand disk
qm resize ${VM_ID} scsi0 +${DISK_SIZE}

# Add cloud-init drive (required for cicustom delivery)
qm set ${VM_ID} --ide2 ${STORAGE}:cloudinit

# Set boot order
qm set ${VM_ID} --boot order=scsi0

# Enable serial console (improves Proxmox console access for CoreOS)
qm set ${VM_ID} --serial0 socket --vga serial0

# Deliver Ignition config via vendor cloud-init data
qm set ${VM_ID} --cicustom vendor=coreos:snippets/${IGN}

# Disable Proxmox's own cloud-init upgrade step
qm set ${VM_ID} --ciupgrade 0
```

**Static IP** (optional — omit to use DHCP):

```bash
qm set ${VM_ID} --ipconfig0 ip=192.168.1.50/24,gw=192.168.1.1
```

### 5. Start the VM

```bash
qm start ${VM_ID}
```

Attach to the serial console to watch first-boot output:

```bash
qm terminal ${VM_ID}
```

Press `Ctrl-O` to detach from `qm terminal`.

### Notes on the cicustom delivery mechanism

Proxmox writes the file referenced by `--cicustom vendor=` onto a FAT32 cloud-init drive as `vendor_data`. Fedora CoreOS's cloud-init interop layer reads this file and passes it to Ignition as additional config. The `coreos:snippets/` prefix must match the storage name you registered with `pvesm add`.

The `--ciupgrade 0` flag prevents Proxmox from injecting a package upgrade command into the cloud-init user-data, which would conflict with CoreOS's immutable OS model.

### Reprovisioning

CoreOS applies Ignition only once. To reprovision a VM from scratch:

```bash
qm stop ${VM_ID}
qm destroy ${VM_ID}
# Re-run steps 4–5 with updated ignition.json
```

There is no incremental Ignition re-run mechanism. Config changes after provisioning go through `rpm-ostree` or manual unit management over SSH.

---

## Post-boot Verification

SSH in via the legacy port-22 path (until Tailscale is confirmed working):

```bash
ssh core@<host-ip>
```

Check the first-boot service:

```bash
systemctl status podcast-ad-cutter-first-boot.service
journalctl -u podcast-ad-cutter-first-boot.service
```

A successful run ends with `First-boot setup complete.` The sentinel file `/var/lib/podcast-ad-cutter/.setup-done` prevents re-execution on subsequent boots.

Check Tailscale:

```bash
systemctl status tailscale.service
journalctl -u tailscale.service -f
```

The container logs will show the node registering against the control plane. Once registered, the node appears in the [Tailscale admin console](https://login.tailscale.com/admin/machines).

Check the feed timer:

```bash
systemctl status podcast-ad-cutter.timer
systemctl list-timers podcast-ad-cutter.timer
```

---

## Tailscale SSH

After the node registers in your tailnet:

1. In the [Tailscale ACL editor](https://login.tailscale.com/admin/acls), add an SSH rule granting your user access to the node, for example:

    ```json
    "ssh": [
      {
        "action": "accept",
        "src":    ["autogroup:member"],
        "dst":    ["tag:servers"],
        "users":  ["autogroup:nonroot", "root"]
      }
    ]
    ```

2. SSH via the tailnet hostname:

    ```bash
    ssh core@<TAILSCALE_HOSTNAME>.<tailnet-name>.ts.net
    ```

Tailscale SSH uses tailnet identity for authentication; no SSH private key is required once connected via Tailscale. The key configured in `SSH_PUBLIC_KEY` remains active for direct (non-Tailscale) connections.

---

## NFS Output Mount

If `NFS_SHARE` was set during `generate.sh`, the mount unit is embedded in `ignition.json` and enabled on first boot. Verify after first-boot completes:

```bash
# The unit name is the systemd-escaped mount path, e.g. for /mnt/podcast-output:
systemctl status mnt-podcast\\x2doutput.mount
mount | grep nfs
```

The podcast-ad-cutter container declares `Requires=` and `After=` against the mount unit, so the job will not start if the NFS server is unreachable. With `soft,timeo=30`, an unreachable server produces I/O errors rather than hanging indefinitely.

---

## Ongoing Operations

### Logs

```bash
journalctl -u podcast-ad-cutter.service -f
journalctl -u podcast-ad-cutter.service --since today
```

### Manual trigger

```bash
systemctl start podcast-ad-cutter.service
```

### Container image updates

`podman-auto-update.timer` runs daily and restarts the container if a newer image digest is available at `ghcr.io/<GITHUB_USERNAME>/podcast-ad-cutter:latest`. The Tailscale container (`AutoUpdate=registry`) is also covered.

Force an immediate check:

```bash
podman auto-update
```

### OS updates

CoreOS applies rpm-ostree updates automatically and stages them for the next reboot. To reboot immediately:

```bash
systemctl reboot
```

### Reprovisioning with changed config

1. Edit `config.yaml` or `deployment/env` on the developer machine.
2. Re-run `./deployment/generate.sh` to produce a new `ignition.json`.
3. On the host, if only `config.yaml` changed, copy it directly:

    ```bash
    scp config.yaml core@<host>:/etc/podcast-ad-cutter/config.yaml
    ```

4. For structural changes (new secrets, new units), reprovision the VM as described under [Reprovisioning](#reprovisioning).
