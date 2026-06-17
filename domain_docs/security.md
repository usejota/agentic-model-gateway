# Security Domain Plan for free-claude-code GCP Deployment

## Overview
Address security hardening items identified in the security review from teammates.

## Critical Issues

### 1. Static Shared Proxy Token with No Per-User Audit Trail
**Problem:** The plan uses a static shared token (`ANTHROPIC_AUTH_TOKEN=freecc`) identical for all users.
- No per-user audit logging in proxy logs
- Leaked token is a universal bypass from any IAP-authorized account
- IAP is sole access control; workstation compromise with active tunnel gives unfettered access

**Solution:** 
- Generate unique tokens per user at onboarding
- Store tokens in Secret Manager or lightweight user registry
- Proxy validates token against this store
- Admin UI manages token lifecycle (create, revoke, audit)

**Implementation:**
```bash
# Onboarding script example
USER_TOKEN=$(openssl rand -hex 32)
gcloud secrets create "fcc-user-token-${USER_EMAIL}" --replication-policy="automatic"
echo -n "${USER_TOKEN}" | gcloud secrets versions add "fcc-user-token-${USER_EMAIL}" --data-file=-
```

### 2. Service Account Uses Overly Broad Scopes (`--scopes=cloud-platform`)
**Problem:** Grants VM's service account all Google Cloud API scopes, far beyond needs.
- Service account only needs: `secretmanager.versions.access` for provider key
- Potentially `logging.logEntries.create` for Cloud Logging

**Solution:**
- Replace with minimal scope or omit and rely on IAM role binding
- Grant `roles/secretmanager.secretAccessor` on provider key secret only
- Optionally grant `roles/logging.logWriter` for Cloud Logging

**Commands:**
```bash
# Create service account with minimal permissions
gcloud iam service-accounts create fcc-sa --display-name="Free Claude Code Proxy SA"

# Grant secret accessor role on specific secret
gcloud secrets add-iam-policy-binding fcc-provider-key \
  --member="serviceAccount:fcc-sa@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Or omit scopes and rely on IAM when creating VM
# gcloud compute instances create ... --service-account=fcc-sa@${PROJECT}.iam.gserviceaccount.com
```

## High Priority Issues

### 3. Secret Manager Secret Not Rotated; Uses `latest` Version
**Problem:** Startup script uses `gcloud secrets versions access latest --secret=fcc-provider-key`
- Key rotation creates new version; old version inaccessible on reboot if `.env` not regenerated
- Proxy continues using old key from disk if only systemd restart occurs

**Solution:**
- Have proxy fetch secret from Secret Manager at runtime (no disk persistence)
- Rotation takes effect immediately without VM reboot or service restart
- Secret never touches disk

### 4. Provider API Key Written to Disk in Plaintext
**Problem:** Secret fetched from Secret Manager written to `/home/fcc/.fcc/.env`
- Any process as `fcc` user (or sudo) can read it
- VM snapshots, disk clones, or disk reattachment expose key
- Key persists across VM rebuilds from same disk image

**Solution:**
- Do not write secret to disk
- Options (best to acceptable):
  1. Proxy imports Secret Manager client library, reads secret at startup (memory only)
  2. Use tmpfs mount for `/home/fcc/.fcc` (RAM-only, lost on reboot)
  3. At minimum: `chmod 600 /home/fcc/.fcc/.env` (current script has chown but no chmod)

### 5. Admin UI Accessible via Loopback Check (No-Op Through IAP Tunnel)
**Problem:** Admin UI "refuses non-127.0.0.1 requests" but IAP tunnel terminates on `localhost:8082` client-side
- Every user with active tunnel passes loopback check and reaches admin interface
- Zero protection; check provides false sense of security

**Solution:**
- Add real authentication to Admin UI:
  - Option 1: Admin password stored in Secret Manager (hashed)
  - Option 2: Serve Admin UI on separate port (e.g., 8083) firewalled to admin-only IAP tunnel
  - Option 3: Integrate with Google federated auth (OAuth)

**Implementation Example (Password):**
```python
# In admin_auth.py
import hashlib
import hmac
import os
from typing import Optional

def verify_admin_password(password: str) -> bool:
    stored_hash = os.getenv("ADMIN_PASSWORD_HASH")  # from Secret Manager
    if not stored_hash:
        return False
    # Constant-time comparison to avoid timing attacks
    return hmac.compare_digest(
        hashlib.sha256(password.encode()).hexdigest(),
        stored_hash
    )
```

## Medium Priority Issues

### 6. No TLS Between Client and Proxy (Plaintext HTTP Over Tunnel)
**Problem:** Uses `ANTHROPIC_BASE_URL="http://localhost:8082"`
- Data plaintext on client machine and inside GCE VM
- Every process on engineer's laptop or VM can read prompts, file contents, diffs

**Solution:**
- Document accepted risk for IAP TCP forwarding model
- For regulated environments: Consider HTTPS LB approach with IAP and automatic TLS certs
  - Adds token-refresh complexity but provides end-to-end encryption
  - Alternative: mutual TLS within tunnel (complex, may not be worth it)

### 7. No Network-Level Segmentation Between Admin Plane (SSH) and Data Plane (Proxy)
**Problem:** Single firewall rule allows IAP CIDR on both tcp:8082 and tcp:22
- Any user with `iap.tunnelResourceAccessor` can reach both proxy service and SSH
- No separation between admin and data paths

**Solution:**
- Split firewall rules with target tags:
  - Proxy rule: `tcp:8082` from IAP CIDR, target-tag `fcc-proxy`
  - Admin SSH rule: `tcp:22` from IAP CIDR, target-tag `fcc-admin`
- Apply tags to VM: `--tags=fcc-proxy,fcc-admin`
- Ensure SSH access further gated by OS Login + sudo group membership

**Commands:**
```bash
# Proxy firewall rule
gcloud compute firewall-rules create allow-iap-fcc-proxy \
  --network=<VPC> --direction=INGRESS --action=ALLOW \
  --rules=tcp:8082 --source-ranges=35.235.240.0/20 \
  --target-tags=fcc-proxy

# Admin SSH firewall rule
gcloud compute firewall-rules create allow-iap-fcc-ssh \
  --network=<VPC> --direction=INGRESS --action=ALLOW \
  --rules=tcp:22 --source-ranges=35.235.240.0/20 \
  --target-tags=fcc-admin

# Create VM with tags
gcloud compute instances create fcc-proxy \
  --tags=fcc-proxy,fcc-admin \
  # ... other params
```

### 8. Shared Provider API Key Creates Shared Rate Limit Ceiling
**Problem:** Single shared provider key means one user's burst can rate-limit all others
- Buggy client, runaway script, or malicious actor with tunnel access can DoS entire team

**Solution:**
- Monitor 429 response codes; alert on high rate
- If provider supports key hierarchies (e.g., OpenAI project keys), create per-user sub-keys
- Educate users on responsible usage; consider usage quotas per user in proxy

## Low Priority Issues

### 9. Service Account IAM Binding Not Shown/Scoped to Secret
**Problem:** Plan states "Grant Secret Manager accessor only" but doesn't show command
- Should be secret-level IAM binding, not project-level, for least privilege

**Solution:** Already covered in Critical #2 - ensure binding at secret level

### 10. No OS Login / SSH Key Management Strategy Documented
**Problem:** Assumes `gcloud compute ssh` works without specifying OS Login or project-wide SSH keys
- For org deployment, OS Login with IAM group binding is correct approach

**Solution:**
- Enable OS Login on project (if not already)
- Grant IAP group (e.g., `ai-gateway@jota.ai`) role `roles/compute.osLogin` on instance
- Document that SSH access requires OS Login and appropriate IAM role

## Summary of Security Improvements

| Priority | Issue | Solution |
|----------|-------|----------|
| Critical | Static shared token | Per-user tokens via Secret Manager |
| Critical | Over-privileged SA scopes | Minimal IAM roles + secret-level binding |
| High | Secret rotation broken | Fetch secret at runtime (no disk) |
| High | Key written to disk | Avoid persistence; use memory only |
| High | Admin UI loopback check | Real authentication (password/separate port/federated) |
| Medium | No TLS in tunnel | Document risk; consider HTTPS LB for regulated |
| Medium | No admin/data plane separation | Split firewall rules with target tags |
| Medium | Shared key rate limit | Monitor 429s; consider key hierarchies |
| Low | SA IAM binding scope | Secret-level binding only |
| Low | OS Login strategy | Enable OS Login + IAM group binding |

## Most Impactful Changes
1. Have proxy fetch provider key from Secret Manager at runtime (fixes #3 & #4)
2. Replace loopback-only Admin UI guard with real authentication (#5)
3. Change `--scopes=cloud-platform` to minimal scope or custom SA with secret accessor role (#2)
4. Implement per-user token strategy for auditability (#1)

## Implementation Tasks

### Immediate (Critical/High)
- [ ] Update startup script: fetch secret at runtime, no .env write
- [ ] Create IAM binding: SA to secret accessor role on provider key secret
- [ ] Add Admin UI authentication mechanism (password or port split)
- [ ] Update firewall rules: split into tag-scoped rules for proxy(8082) and admin(22)
- [ ] Apply network tags to VM: `fcc-proxy,fcc-admin`
- [ ] Document OS Login requirement for SSH access

### Short Term (Medium/Low)
- [ ] Specify subnet CIDR explicitly to avoid overlap
- [ ] Enable Private Google Access on subnet (networking blocker)
- [ ] Request IAP tunnel quota increase (networking blocker)
- [ ] Add Cloud Router as NAT prerequisite
- [ ] Fix wrapper script: ephemeral port, 60s wait, idle timeout flag
- [ ] Consider per-user token feature for audit (longer term)
- [ ] Enhance logging: structured format, audit trails
- [ ] Document plaintext HTTP risk within tunnel and shared key rate limit

## Verification Steps
After implementation, verify:
- [ ] VM has no external IP; subnet has Private Google Access enabled
- [ ] IAP tunnel quota sufficient (check via gcloud)
- [ ] Firewall rules tag-scoped; only VM has appropriate tags
- [ ] Cloud Router exists before NAT
- [ ] Wrapper script: tunnel establishes, ephemeral port, idle timeout disabled
- [ ] Proxy process: no API key in environment (/proc/<pid>/env) or on disk
- [ ] Admin UI authentication works (password challenge)
- [ ] SSH access via IAP requires OS Login + proper IAM role
- [ ] End-to-end: engineer runs fcc-connect, sends request, gets response, provider logs call

## References
- Original plan: free-claude-code-gcp-plan.md
- Security review findings from teammate
- GCP IAP documentation: https://cloud.google.com/iap/docs
- Secret Manager best practices: https://cloud.google.com/secret-manager/docs/best-practices