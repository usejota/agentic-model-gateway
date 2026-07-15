# Cloud Infrastructure Domain Plan for free-claude-code GCP Deployment

> **Implementation note:** this infra is now defined declaratively as Crossplane
> Managed Resources in [`deploy/crossplane/`](../deploy/crossplane/README.md) (primary),
> mirroring the org's `octopus` setup. The `gcloud` commands below are the reference /
> fallback form (`deploy/provision.sh`).

## Overview
Focus on GCP-specific infrastructure components: VM configuration, disks, images, networking prerequisites (refer to networking domain), and service account management.

## Infrastructure Components from Plan
- **GCE VM**: e2-standard-2 (2 vCPU, 8 GB) in regional subnet with no external IP
- **Boot Disk**: Standard image (Debian/Ubuntu) with startup script for setup
- **Service Account**: Dedicated SA for VM with Secret Manager access
- **Startup Script**: Installs uv, Python 3.14, clones repo, configures proxy, creates systemd service
- **Systemd Service**: Runs proxy as user `fcc`
- **Secret Manager**: Single secret for provider API key
- **Cloud NAT**: Required for outbound traffic to provider API (requires Cloud Router)
- **Firewall Rules**: For IAP TCP forwarding (see networking domain)
- **IAP**: Configured on instance for tunnel access
- **OS Login**: For SSH access via IAP (refer to security domain)

## Review Findings Impacting Cloud Infrastructure

### From Security Review
1. **Service account scopes too broad** (`--scopes=cloud-platform`)
   - Grants VM's SA all Google Cloud API scopes
   - Only needs: `secretmanager.versions.access` for provider key
   - Potentially: `logging.logEntries.create` for Cloud Logging
   - **Solution:** Replace with minimal scope or omit with proper IAM role binding
   - Grant `roles/secretmanager.secretAccessor` on provider key secret
   - Optionally grant `roles/logging.logWriter` for Cloud Logging

2. **Secret written to disk in plaintext** (startup script)
   - Provider API key persisted to `/home/fcc/.fcc/.env`
   - Risks: readable by fcc user/sudo, VM snapshots/disk clones expose key
   - **Solution:** Modify startup script to NOT write secret to disk
   - Have proxy fetch key from Secret Manager at runtime (memory only)
   - Alternative: tmpfs mount for `/home/fcc/.fcc` (RAM-only)

3. **OS Login strategy not documented**
   - Plan assumes `gcloud compute ssh` works
   - For org deployment: OS Login with IAM group binding is correct approach
   - **Solution:** Enable OS Login on project; grant IAP group `roles/compute.osLogin` on instance

### From Networking Review
1. **Private Google Access must be enabled on subnet**
   - VM has no external IP; needs this to reach Google APIs
   - Without it: startup script fails at Secret Manager access
   - VM cannot send logs to Cloud Logging
   - **Solution:** Enable explicitly: `gcloud compute networks subnets update <SUBNET> --region=<REGION> --enable-private-ip-google-access`

2. **Cloud Router prerequisite for Cloud NAT**
   - Cloud NAT requires Cloud Router in same region
   - Plan mentions NAT but not router
   - **Solution:** Create Cloud Router, then Cloud NAT
   - `gcloud compute routers create fcc-router --network=<VPC> --region=<REGION>`
   - `gcloud compute routers nats create fcc-nat --router=fcc-router --region=<REGION> --nat-all-subnet-ip-ranges --auto-allocate-nat-external-ips`

3. **Firewall rules too broad** (see networking domain for details)
   - Affects VM via required network tags
   - **Solution:** Split into tag-scoped rules; apply tags to VM: `--tags=fcc-proxy,fcc-admin`

4. **Subnet CIDR not specified**
   - Risk of overlap with on-prem or shared-VPC networks
   - **Solution:** Choose and specify CIDR block explicitly (e.g., `10.128.0.0/20`)

### Architecture Considerations
- **VM size (e2-standard-2)** is appropriate for 10-50 users with 3 uvicorn workers
  - Proxy is async I/O-bound; handles many concurrent connections per worker
  - Monitor CPU/memory; scale vertically first (increase VM size)
  - Horizontal scaling: Managed Instance Group behind internal TCP LB (still IAP-tunneled)
- **Boot disk image**: Use standard Linux image (Debian 11, Ubuntu 22.04 LTS, or COS)
  - Current startup script assumes Debian-like environment (uses bash, useradd)
  - Container-Optimized OS (COS) would require containerization approach
  - Weigh trade-offs: COS offers better security/update model but requires script changes

## Implementation Tasks for Cloud Infrastructure

### Immediate (Critical/High Priority)
- [ ] Choose and specify subnet CIDR (e.g., `10.128.0.0/20`)
- [ ] Enable Private Google Access on subnet
- [ ] Create Cloud Router (prerequisite for NAT)
- [ ] Create VM with:
    - No external IP (`--no-address`)
    - Dedicated service account (with minimal IAM roles):
        - Secret accessor on provider key secret: `roles/secretmanager.secretAccessor`
        - Optional: logging writer: `roles/logging.logWriter`
    - Either omit `--scopes` or set to minimal scope:
        - `--scopes=https://www.googleapis.com/auth/cloud-platform.read-only`
        - Rely on IAM for further restriction
    - Network tags: `fcc-proxy,fcc-admin` (for tag-scoped firewall rules)
    - Enable OS Login (for SSH via IAP)
    - Boot disk: standard image (e.g., `debian-11` or `ubuntu-2204-lts`)
- [ ] Update startup script:
    - Do NOT write provider key to `.env` file
    - Modify proxy to fetch key from Secret Manager at runtime
    - Alternative: read key from Secret Manager at startup, keep in memory only
    - Ensure systemd service creation remains unchanged
- [ ] Create Secret Manager secret for provider key
- [ ] Set IAM binding: SA to secret accessor role on provider key secret
- [ ] Document OS Login requirement for SSH access (`gcloud compute ssh --tunnel-through-iap`)

### Verification Steps
After implementation, verify:
- [ ] VM has no external IP (`gcloud compute instances describe fcc-proxy --format="get(networkInterfaces[0].accessConfigs)"`)
- [ ] Subnet has Private Google Access enabled (`gcloud compute networks subnets describe <SUBNET> --region=<REGION> --format="get(privateIpGoogleAccess)"`)
- [ ] Cloud Router exists (`gcloud compute routers list --filter="name=fcc-router"`)
- [ ] Cloud NAT is configured (`gcloud compute routers nats list --router=fcc-router --region=<REGION>`)
- [ ] Firewall rules are tag-scoped:
    - Proxy rule: `--target-tags=fcc-proxy` for tcp:8082
    - Admin SSH rule: `--target-tags=fcc-admin` for tcp:22
- [ ] VM has network tags `fcc-proxy` and `fcc-admin`
- [ ] Service account has only required IAM roles (secret accessor, optional logging)
- [ ] Startup script does not contain `OPENROUTER_API_KEY=$KEY` write to disk
- [ ] Proxy process does not have API key in environment (`cat /proc/<pid>/environ | tr '\0' '\n' | grep -i key`)
- [ ] SSH access via IAP works with OS Login (test: `gcloud compute ssh fcc-proxy --zone=<ZONE> --tunnel-through-iap`)
- [ ] End-to-end: VM can reach Secret Manager (`gcloud secrets versions access latest --secret=fcc-provider-key` from VM)
- [ ] VM can send logs to Cloud Logging (check Cloud Logging for startup script output)

## References
- Original plan: free-claude-code-gcp-plan.md
- Security and networking review findings from teammates
- GCP Service Accounts: https://cloud.google.com/iam/docs/service-accounts
- GCP OS Login: https://cloud.google.com/compute/docs/oslogin
- GCP Private Google Access: https://cloud.google.com/vpc/docs/private-google-access
- GCP Cloud NAT: https://cloud.google.com/nat
- GCP Secret Manager: https://cloud.google.com/secret-manager