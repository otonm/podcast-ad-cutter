# Deployment — Fedora CoreOS on Proxmox

## Architecture

```
CoreOS VM (Proxmox KVM)
└── Podman Quadlet
    └── podcast-ad-cutter container  (webui.py — persistent web service)
        ├── In-process scheduler     (periodic feed runs)
        └── Tailscale sidecar        (remote access via tailnet)
```

Secrets (API keys) are stored as **Podman secrets** — they are never written to
unit files or `config.yaml`. `first-boot.sh` loads them once from a temporary file
written by Ignition, creates the Podman secrets, then shreds the plaintext file.

`podman-auto-update.timer` checks `ghcr.io` daily and restarts the container when a
new image digest is available.

---

## Prerequisites

**Developer machine:**
- [`butane`](https://github.com/coreos/butane/releases) ≥ v0.19
- `systemd-escape` (from `systemd`, used for NFS unit name computation)
- `scp` / `ssh`

**Proxmox host:**
- Any recent Proxmox VE with KVM support
- Internet access from VMs (to pull the container image from `ghcr.io`)

**Container registry:**
- A `ghcr.io` package published under your GitHub username (public or private)
- If private: a `GHCR_TOKEN` with `read:packages` scope

---

## Step 1 — Configure `deployment/env`

```bash
cp deployment/env.example deployment/env
```

Edit `deployment/env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `GITHUB_USERNAME` | yes | GitHub username that owns the `ghcr.io` package |
| `SSH_PUBLIC_KEY` | yes | Public key for SSH access to the `core` user |
| `TAILSCALE_AUTH_KEY` | yes | Tailscale auth key (reusable, ephemeral) |
| `TAILSCALE_HOSTNAME` | yes | Tailscale machine name for the VM |
| `OPENAI_API_KEY` | at least one | API key for your chosen provider |
| `GROQ_API_KEY` | at least one | API key for your chosen provider |
| `OPENROUTER_API_KEY` | at least one | API key for your chosen provider |
| `GHCR_TOKEN` | if private | GitHub PAT with `read:packages` scope |
| `NFS_SHARE` | no | NFS share for output storage (e.g. `192.168.1.10:/podcasts`) |
| `NFS_MOUNT_PATH` | no | Mount point on the CoreOS host (default: `/mnt/podcast-output`) |

---

## Step 2 — Configure `config.yaml`

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- Add your podcast feed URLs under `feeds`
- Set `paths.output_dir` (or leave as default `/app/output`)
- Set `transcription.provider` / `transcription.model`
- Set `interpretation.provider` / `interpretation.model`
- Configure `scheduler.interval_minutes` for how often to check feeds

---

## Step 3 — Generate `ignition.json`

```bash
./deployment/generate.sh
```

This renders all templates, substitutes your variables, and runs `butane` to produce
`deployment/ignition.json`.

> **WARNING:** `ignition.json` contains plaintext secrets. Keep it private.
> It is `.gitignore`d but treat it like a credentials file.

---

## Step 4 — Create the CoreOS VM on Proxmox

### 4a. Download Fedora CoreOS

Go to <https://fedoraproject.org/coreos/download?stream=stable> and download the
**QCOW2** image for the `stable` stream (x86_64).

### 4b. Create the VM and import the disk

```bash
# On the Proxmox host — adjust storage name and resource sizes as needed
qm create <vmid> \
    --name podcast-ad-cutter \
    --memory 4096 \
    --cores 2 \
    --net0 virtio,bridge=vmbr0 \
    --ostype l26

qm importdisk <vmid> fedora-coreos-*.qcow2 <storage>
qm set <vmid> \
    --scsihw virtio-scsi-pci \
    --scsi0 <storage>:vm-<vmid>-disk-0 \
    --boot order=scsi0 \
    --serial0 socket \
    --vga serial0

# Resize disk to at least 20 GB (CoreOS base is ~6 GB; episodes need space)
qm resize <vmid> scsi0 20G
```

Recommended sizes: **2 vCPU, 4 GB RAM, 20 GB disk**.

### 4c. Copy `ignition.json` to the Proxmox host

```bash
scp deployment/ignition.json root@proxmox:/root/
```

### 4d. Attach the Ignition config via `fw_cfg`

```bash
qm set <vmid> --args "-fw_cfg name=opt/com.coreos/config,file=/root/ignition.json"
```

### 4e. Start the VM

```bash
qm start <vmid>
```

---

## Step 5 — Verify first boot

SSH into the VM (use the IP shown in Proxmox, or wait for Tailscale to come up):

```bash
ssh core@<vm-ip>
```

Check first-boot progress:

```bash
systemctl status podcast-ad-cutter-first-boot.service
journalctl -u podcast-ad-cutter-first-boot.service
```

Once first-boot completes, verify the web service is running:

```bash
systemctl status podcast-ad-cutter.service
```

---

## Accessing the Web UI

| Method | URL |
|---|---|
| Direct (before Tailscale) | `http://<vm-ip>:8000` |
| Via Tailscale | `http://<TAILSCALE_HOSTNAME>.<tailnet>.ts.net:8000` |

---

## Management

```bash
# Stream live logs
journalctl -u podcast-ad-cutter.service -f

# Restart the service
systemctl restart podcast-ad-cutter.service

# Force an image update and restart
podman pull ghcr.io/<GITHUB_USERNAME>/podcast-ad-cutter:latest
systemctl restart podcast-ad-cutter.service
```

Auto-update runs daily via `podman-auto-update.timer` — it checks `ghcr.io` and
restarts the container whenever a new image digest is detected.

---

## Optional: NFS Output Storage

To mount an NFS share for episode output instead of a named Podman volume, set these
in `deployment/env` **before** running `generate.sh`:

```bash
NFS_SHARE=192.168.1.10:/podcasts          # your NFS server and export path
NFS_MOUNT_PATH=/mnt/podcast-output        # mount point on the CoreOS host
```

`generate.sh` will create a systemd `.mount` unit and add the necessary
`After=` / `Requires=` dependencies to the container unit automatically.

---

## Troubleshooting

| Symptom | Command |
|---|---|
| First-boot did not complete | `journalctl -u podcast-ad-cutter-first-boot.service` |
| Container won't start | `journalctl -u podcast-ad-cutter.service` |
| Quadlet not generating the service | `systemctl daemon-reload; systemctl status podcast-ad-cutter.service` |
| Tailscale not connecting | `systemctl status tailscale.service; journalctl -u tailscale.service` |
| Image pull failing | Check `GITHUB_USERNAME` and `GHCR_TOKEN` in `deployment/env` |
