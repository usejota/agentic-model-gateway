# Networking Domain Plan for free-claude-code GCP Deployment

## Overview
Address networking hardening items identified in the networking review from teammates.

## Critical Blockers

### 1. Private Google Access Must Be Enabled on Subnet
**Problem:** VM has no external IP; needs Private Google Access to reach Google APIs (Secret Manager, Cloud Logging).
- Without it, startup script fails at `gcloud secrets versions access latest`
- VM cannot send logs to Cloud Logging via loguru->journald

**Solution:**
- Enable Private Google Access explicitly on the subnet after creation

**Command:**
```bash
gcloud compute networks subnets update <SUBNET> \
  --region=<REGION> \
  --enable-private-ip-google-access
```

### 2. IAP TCP Forwarding Quota Default (25 Tunnels/Project) Insufficient
**Problem:** Default quota is 25 simultaneous tunnels per project
- For 50 users, each may have proxy tunnel + SSH tunnel = 100 tunnels needed
- Quota exceeded causes tunnel establishment failures

**Solution:**
- Request quota increase via Cloud Console or gcloud before rollout
- Verify current quota: `gcloud compute project-info describe --project <PROJECT> | grep -A5 "iap_tunnel"`
- Request increase to at least 100 (to allow proxy + SSH tunnels per user)

## High Priority

### 3. Firewall Rule Overly Broad
**Problem:** Single rule allows tcp:8082 and tcp:22 from IAP CIDR (35.235.240.0/20) with no target tags
- Applies to ALL instances in VPC, not just proxy VM
- Security risk: any VM in VPC accepts IAP-originated traffic on 8082/22

**Solution:**
- Split into two tag-scoped rules:
  - Proxy rule: tcp:8082 from IAP CIDR, target-tag `fcc-proxy`
  - Admin SSH rule: tcp:22 from IAP CIDR, target-tag `fcc-admin`
- Apply tags to VM at creation: `--tags=fcc-proxy,fcc-admin`

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

# Create VM with both tags
gcloud compute instances create fcc-proxy \
  --tags=fcc-proxy,fcc-admin \
  # ... other params (no external IP, service account, etc.)
```

### 4. Missing Cloud Router Prerequisite for Cloud NAT
**Problem:** Cloud NAT requires a Cloud Router in the same region
- Plan mentions Cloud NAT but not Cloud Router
- Without router, NAT creation fails

**Solution:**
- Create Cloud Router, then Cloud NAT

**Commands:**
```bash
# Create Cloud Router
gcloud compute routers create fcc-router \
  --network=<VPC> --region=<REGION>

# Create Cloud NAT
gcloud compute routers nats create fcc-nat \
  --router=fcc-router --region=<REGION> \
  --nat-all-subnet-ip-ranges --auto-allocate-nat-external-ips
```

**Note:** `--nat-all-subnet-ip-ranges` sends all subnet traffic via NAT. 
If only specific traffic (e.g., to provider API) needs NAT, can be more specific.

### 5. Wrapper Script Issues
**Problems in `fcc-connect` wrapper script:**
- **Hardcoded port 8082:** Port conflict if another service uses that port on client
- **Race condition in tunnel wait:** 10s max wait may be too short for OAuth redirect
- **No idle timeout disable:** Tunnel drops after ~1h idle (gcloud default)
- **No tunnel health monitoring:** No indication if tunnel dies mid-session

**Solution:**
- Use ephemeral port assignment (`--local-host-port=localhost:0`)
- Increase tunnel wait timeout to 60s with failure gate
- Add `--iap-tunnel-disable-connection-timeout` flag for long-lived sessions
- Document recovery: `killall gcloud compute start-iap-tunnel` then rerun wrapper
- Optional: Add basic tunnel death detection and auto-restart

**Implementation Example (Improved Wrapper):**
```bash
#!/usr/bin/env bash
set -euo pipefail

ZONE="<ZONE>"
PROJECT="<PROJECT>"

# Start tunnel in background if port is not already listening
# Use ephemeral port to avoid conflicts
if ! nc -z localhost 0 2>/dev/null; then  # Placeholder - actual logic needs to get assigned port
  # Start tunnel and capture the actual local port
  TUNNEL_OUTPUT=$(gcloud compute start-iap-tunnel fcc-proxy 0 \
    --local-host-port=localhost:0 \
    --zone="$ZONE" --project="$PROJECT" \
    --iap-tunnel-disable-connection-timeout 2>&1) &
  
  # Extract assigned port from output (needs parsing)
  # For now, we'll use a fixed approach with retry on conflict
  LOCAL_PORT=8082
  ATTEMPTS=0
  MAX_ATTEMPTS=5
  
  while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    if ! nc -z localhost $LOCAL_PORT 2>/dev/null; then
      break
    fi
    LOCAL_PORT=$((LOCAL_PORT + 1))
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 1
  done
  
  if [ $ATTEMPTS -eq $MAX_ATTEMPTS ]; then
    echo "Failed to find available port after $MAX_ATTEMPTS attempts"
    exit 1
  fi
  
  # Restart tunnel with specific port (simplified - real implementation would parse)
  gcloud compute start-iap-tunnel fcc-proxy $LOCAL_PORT \
    --local-host-port=localhost:$LOCAL_PORT \
    --zone="$ZONE" --project="$PROJECT" \
    --iap-tunnel-disable-connection-timeout &
  
  # Wait for tunnel to be ready (60s timeout)
  for i in {1..120}; do
    if nc -z localhost $LOCAL_PORT; then
      break
    fi
    sleep 0.5
  done
  
  if ! nc -z localhost $LOCAL_PORT; then
    echo "Tunnel failed to become ready within 60s"
    exit 1
  fi
fi

export ANTHROPIC_BASE_URL="http://localhost:$LOCAL_PORT"
export ANTHROPIC_AUTH_TOKEN="freecc"
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=190000
exec claude "$@"
```

## Medium Priority

### 6. Specify Subnet CIDR Explicitly
**Problem:** No subnet CIDR specified in plan
- Risk of overlap with existing on-prem or shared-VPC networks
- Common in bank GCP organizations with peering or shared VPC

**Solution:**
- Choose and specify CIDR block explicitly (e.g., `10.128.0.0/20`)
- Adjust based on existing network allocations

**Command:**
```bash
gcloud compute networks subnets create fcc-subnet \
  --network=<VPC> \
  --region=<REGION> \
  --range=10.128.0.0/20 \
  --enable-private-ip-google-access
```

### 7. Scope Down VM Service Account to Minimal IAM Roles
**Problem:** `--scopes=cloud-platform` grants excessive permissions
- Also a security item, but affects networking via required scopes for metadata access

**Solution:**
- Either:
  - Omit `--scopes` and use custom SA with only `roles/secretmanager.secretAccessor` (and `roles/logging.logWriter`)
  - Or use minimal scope: `--scopes=https://www.googleapis.com/auth/cloud-platform.read-only` and rely on IAM for further restriction

**Note:** With Private Google Access enabled, the VM can access Google APIs without public IP.

## Low Priority

### 8. Document Tunnel Recovery Procedure
**Problem:** No guidance for users if tunnel dies mid-session
- Engineer may not know how to restore connectivity

**Solution:**
- Document recovery steps:
  1. `killall gcloud compute start-iap-tunnel` (kills any hanging tunnel processes)
  2. Rerun `fcc-connect` wrapper script
- Consider adding health check or auto-restart in wrapper (optional enhancement)

## Summary of Networking Improvements

| Priority | Issue | Solution |
|----------|-------|----------|
| Blocker | Private Google Access missing | Enable on subnet |
| Blocker | IAP tunnel quota (25) too low | Request increase to 100+ |
| High | Firewall rule too broad | Split into tag-scoped rules (proxy:8082, admin:22) |
| High | Missing Cloud Router | Create router before NAT |
| High | Wrapper script flaws | Ephemeral port, 60s wait, disable idle timeout |
| Medium | Subnet CIDR unspecified | Specify explicitly (e.g., 10.128.0.0/20) |
| Medium | SA scopes too broad | Omit scopes or use minimal scope + proper IAM |
| Low | Tunnel recovery not documented | Provide `killall` + rerun steps |

## Most Impactful Changes
1. Enable Private Google Access on subnet (blocker for VM to reach Google APIs)
2. Request IAP tunnel quota increase (blocker for 50+ users)
3. Split firewall rules into tag-scoped rules with VM tags (high security)
4. Add Cloud Router as NAT prerequisite (high dependency)
5. Fix wrapper script: ephemeral port, longer wait, idle timeout flag (high usability)

## Implementation Tasks

### Immediate (Blockers/High)
- [ ] Enable Private Google Access on subnet
- [ ] Request and configure IAP tunnel quota increase
- [ ] Create two tag-scoped firewall rules (proxy and admin SSH)
- [ ] Create Cloud Router and Cloud NAT
- [ ] Update wrapper script:
   - Use ephemeral local port (or port retry logic)
   - Increase tunnel wait timeout to 60s
   - Add `--iap-tunnel-disable-connection-timeout`
   - Add basic tunnel death detection (optional)
   - Document recovery steps
- [ ] Specify subnet CIDR in plan
- [ ] Scope VM SA to minimal roles or remove `--scopes` flag with proper IAM

### Verification Steps
After implementation, verify:
- [ ] Subnet has Private Google Access enabled
- [ ] IAP tunnel quota sufficient (check via gcloud)
- [ ] Firewall rules tag-scoped; only VM with tags `fcc-proxy`/`fcc-admin` accepts traffic
- [ ] Cloud Router exists and NAT is created successfully
- [ ] Wrapper script establishes tunnel with ephemeral port
- [ ] Tunnel remains active for >1h idle (disabled timeout)
- [ ] Recovery procedure works: kill tunnel processes, rerun wrapper
- [ ] VM accessible via IAP tunnel on correct port
- [ ] Admin SSH access works (if needed) with OS Login
- [ ] End-to-end: engineer runs fcc-connect, sends request, gets response

## References
- Original plan: free-claude-code-gcp-plan.md
- Networking review findings from teammate
- GCP VPC documentation: https://cloud.google.com/vpc
- GCP IAP TCP forwarding: https://cloud.google.com/iap/docs/tcp-forwarding-overview
- GCP Cloud NAT: https://cloud.google.com/nat