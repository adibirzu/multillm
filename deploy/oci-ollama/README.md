# Remote Ollama on OCI compute

Run open models on an OCI instance you control and route the MultiLLM gateway to
it. The gateway's Ollama adapter targets `OLLAMA_URL`, so "remote Ollama" is just
**provision a host running Ollama + point the gateway at it**.

## 1. Pick a shape

| Use case | Shape | Cost | Notes |
|----------|-------|------|-------|
| Small models (≤14B), cheap | `VM.Standard.A1.Flex` (Arm, 4 OCPU/24GB) | free-tier eligible | CPU inference, fine for 3B–14B |
| Large models, fast | `VM.GPU.A10.1` (1× A10 24GB) | ~$/hour (paid) | CUDA; 70B-class with quant |
| Very large | `VM.GPU.A10.2` / `BM.GPU.*` | $$$ | multi-GPU |

GPU shapes incur real hourly cost — stop the instance when idle.

## 2. Launch the instance (oci-cli)

Substitute your compartment, subnet, image, and SSH key. The `cap`
(pbncapgemini, eu-frankfurt-1) profile is full-control staging.

```bash
export OCI_PROFILE=cap
export COMPARTMENT_OCID=<your-compartment-ocid>
export SUBNET_OCID=<subnet-with-gateway-reachability>
export IMAGE_OCID=<oracle-linux-9-image>   # GPU: use an Oracle Linux GPU image

oci compute instance launch --profile $OCI_PROFILE \
  --availability-domain "$(oci iam availability-domain list --profile $OCI_PROFILE --compartment-id $COMPARTMENT_OCID --query 'data[0].name' --raw-output)" \
  --compartment-id $COMPARTMENT_OCID \
  --shape VM.Standard.A1.Flex \
  --shape-config '{"ocpus":4,"memoryInGBs":24}' \
  --image-id $IMAGE_OCID \
  --subnet-id $SUBNET_OCID \
  --assign-public-ip false \
  --user-data-file cloud-init.yaml \
  --metadata '{"ssh_authorized_keys":"'"$(cat ~/.ssh/id_rsa.pub)"'"}'
```

## 3. Lock down ingress

Add an NSG / security-list rule allowing **only the gateway host's IP** to reach
TCP **11434**. Never expose Ollama to the public internet (it has no auth).

## 4. Point the gateway at it

Two options:

**A. Replace the local Ollama target** (simplest) — set in `.env` and restart:
```
OLLAMA_URL=http://<oci-instance-private-ip>:11434
```

**B. Keep local + add OCI as separate routes** — add routes whose models exist
only on the OCI host; the gateway discovers installed models from `OLLAMA_URL`.
(To run *both* a local and an OCI Ollama simultaneously as distinct backends,
open an issue — it needs a second adapter instance keyed to a second URL.)

## 5. Verify

```bash
# from the gateway host
curl http://<oci-instance-private-ip>:11434/api/tags
# through the gateway
curl http://localhost:8080/v1/messages -d '{"model":"ollama/llama3.2:3b","messages":[{"role":"user","content":"hi"}],"max_tokens":20}'
```

The fusion panel / fallback chain can then include these OCI-hosted models.
