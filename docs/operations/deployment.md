# Deployment

Three recipes for running MultiLLM in production. Pick the one that matches your operational discipline; all three read the same `.env.example` inventory.

> **WARNING — bind exposure before `/setup` is complete.** The first-run wizard at `/setup` creates the initial admin user. Until that completes, **anyone who can reach the gateway can claim admin**. Do NOT bind to `0.0.0.0` until you have walked through the wizard on `127.0.0.1`. The recipes below default to localhost for that reason.

---

## Recipe 1 — Docker Compose (recommended for self-host)

The canonical bring-up path. Single service, SQLite-backed, host-volume mounted for durability.

```bash
git clone https://github.com/${OWNER}/multillm.git
cd multillm
cp .env.example .env
docker compose up -d
docker compose logs -f gateway   # follow startup
```

The compose file (`docker-compose.yml`) mounts `${MULTILLM_HOME:-./.multillm}` at `/data` inside the container. Override the host path via env:

```bash
MULTILLM_HOME=/var/lib/multillm docker compose up -d
```

The container's `HEALTHCHECK` probes `/health` every 10 s (mirrored in the compose file). `docker compose ps` shows the service as `healthy` once the gateway is serving.

### Updating

```bash
docker compose pull
docker compose up -d        # recreate with the new image; volume persists
```

The `multillm migrate up` invocation in the entrypoint runs every start; the runner is idempotent and writes an automatic pre-migration backup. See [backup-restore.md](backup-restore.md).

### Exposing beyond localhost

Compose binds `8080:8080` on the host. To restrict to localhost, change the publish to `"127.0.0.1:8080:8080"`. To expose to the LAN, **first** finish the `/setup` wizard locally, **then** put MultiLLM behind a TLS-terminating reverse proxy (Caddy / nginx / Traefik). Setting `MULTILLM_API_KEY` is recommended whenever the gateway is reachable off-host; the wizard will lead Phase 2b's per-tenant key issuance once that lands.

---

## Recipe 2 — Bare-metal with systemd

For operators who prefer a managed system service over Docker.

### Pre-install

```bash
sudo useradd --system --shell /sbin/nologin --home-dir /var/lib/multillm multillm
sudo mkdir -p /var/lib/multillm /etc/multillm
sudo chown multillm:multillm /var/lib/multillm

# pipx is recommended (venv-isolated). The `pipx install` route assumes a
# published release on PyPI (see docs/operations/release.md).
sudo apt install pipx
sudo -u multillm pipx install multillm-gateway

sudo cp /path/to/multillm/.env.example /etc/multillm/.env
sudo chmod 640 /etc/multillm/.env
sudo chown root:multillm /etc/multillm/.env
# Edit /etc/multillm/.env and fill in backend keys.
```

### Systemd unit

Create `/etc/systemd/system/multillm.service`:

```ini
[Unit]
Description=MultiLLM Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=multillm
Group=multillm
EnvironmentFile=/etc/multillm/.env
Environment=MULTILLM_HOME=/var/lib/multillm
ExecStartPre=/var/lib/multillm/.local/bin/multillm migrate up
ExecStart=/var/lib/multillm/.local/bin/multillm serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/multillm
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now multillm
sudo journalctl -u multillm -f
```

Bind to `127.0.0.1` in `/etc/multillm/.env` (`GATEWAY_HOST=127.0.0.1`) and reverse-proxy with TLS for external access.

---

## Recipe 3 — Kubernetes (template only)

A minimal Deployment + Service + PVC for clusters with existing operational discipline. A full Helm chart is on the v2 roadmap; the YAML below is a starting template, not a polished release.

> Single-replica only at v1.0. SQLite cannot be safely scaled horizontally. Postgres-backed routing and HPA land in v2.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: multillm-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: Secret
metadata:
  name: multillm-env
type: Opaque
stringData:
  GATEWAY_HOST: "0.0.0.0"
  MULTILLM_HOME: "/data"
  # Add backend keys as needed (OPENAI_API_KEY, GEMINI_API_KEY, ...).
  # Best practice: source these from your cluster secret store (External
  # Secrets Operator, Vault Secrets Operator, SOPS, etc.).
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: multillm
spec:
  replicas: 1
  strategy:
    type: Recreate           # SQLite: never two writers
  selector:
    matchLabels: { app: multillm }
  template:
    metadata:
      labels: { app: multillm }
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
      containers:
        - name: gateway
          image: ghcr.io/${OWNER}/multillm:latest
          ports:
            - containerPort: 8080
          envFrom:
            - secretRef: { name: multillm-env }
          volumeMounts:
            - mountPath: /data
              name: data
          readinessProbe:
            httpGet: { path: /health, port: 8080 }
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /health, port: 8080 }
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests: { cpu: 200m, memory: 256Mi }
            limits:   { cpu: 1000m, memory: 1Gi }
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: multillm-data
---
apiVersion: v1
kind: Service
metadata:
  name: multillm
spec:
  selector: { app: multillm }
  ports:
    - port: 80
      targetPort: 8080
```

The `runAsUser: 10001` matches the dedicated `multillm` user baked into the container image.

Apply with `kubectl apply -f multillm.yaml`, then port-forward the first run to localhost so you can complete `/setup` safely:

```bash
kubectl port-forward svc/multillm 8080:80
open http://localhost:8080/setup
```

After setup is complete, expose the service via Ingress with TLS.

---

## Choosing a deployment

| Situation                                              | Use                      |
| ------------------------------------------------------ | ------------------------ |
| Single machine, self-only                              | Docker Compose           |
| Team server with existing systemd discipline           | Bare-metal systemd       |
| Cluster with existing K8s ops                          | Kubernetes (template)    |
| Multi-replica / HA / SSO with enterprise IdP           | Wait for v2 (Helm + PG)  |

For the authoritative env-var list see [`.env.example`](../../.env.example). For backup/restore procedures see [backup-restore.md](backup-restore.md). For upgrade procedure see [upgrade.md](upgrade.md). For when things break see [troubleshooting.md](troubleshooting.md).
