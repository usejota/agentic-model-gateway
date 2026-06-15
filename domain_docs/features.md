# Features Domain Plan for free-claude-code GCP Deployment

## Overview
Plan for feature-related aspects of the free-claude-code proxy that are not purely infrastructure, security, or networking. This includes the Admin UI, model discovery, compact window, logging, and user-facing features.

## Existing Features (from codebase)
- **Admin UI**: Loopback-only by default for configuring provider settings (API key, model mappings) without redeploy
- **Model Discovery**: Enabled via `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` to show /model picker in Claude Code
- **Compact Window**: Configurable via `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to manage context window size
- **Static Proxy Token**: Uses `ANTHROPIC_AUTH_TOKEN=freecc` for authentication to the proxy
- **Provider Backends**: Supports multiple providers (NVIDIA NIM, OpenRouter, DeepSeek, etc.) via providers/ directory
- **Logging**: Uses loguru, outputs to journald -> Cloud Logging

## Review Findings Impacting Features
- **Admin UI authentication** (security review): Loopback-only check ineffective because IAP tunnel terminates on localhost:8082 client-side
  - Every user with active tunnel passes loopback check and can reach admin interface
  - Need real authentication for Admin UI
- **Model discovery** (from plan): Wrapper script exports `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`
  - Should verify this works with proxy; ensure proxy exposes /v1/models endpoint correctly
- **Compact window** (from plan): Wrapper script exports `CLAUDE_CODE_AUTO_COMPACT_WINDOW=190000`
  - Client-side setting; ensure appropriate for provider models being used
- **Shared proxy token** (security review): Limits per-user audit and is single point of compromise
  - Feature consideration: Per-user tokens would require modifying proxy to validate against user token store

## Feature Improvements and Tasks

### 1. Admin UI Authentication
**Problem:** Current loopback-only check provides no real security through IAP tunnel
**Solutions:**
- **Option A (Simple):** Add admin password prompt (store hash in Secret Manager)
- **Option B (Port Split):** Serve Admin UI on separate port (e.g., 8083) firewalled to admin-only IAP tunnel
- **Option C (Federated):** Integrate with Google OAuth (more complex)

**Recommended:** Option A for simplicity, or Option B if already separating admin tunneling (as in plan's SSH admin step)

**Implementation Tasks:**
- [ ] Add password hashing/verification to Admin UI endpoints
- [ ] Store admin password hash in Secret Manager
- [ ] Modify admin routes to require authentication
- [ ] Update startup script to fetch admin password hash from Secret Manager
- [ ] Document admin credentials distribution process

### 2. Per-User Proxy Tokens (Optional Feature)
**Problem:** Shared token limits audit and creates single point of compromise
**Solution (if auditability required):**
- Generate unique token per user at onboarding
- Store tokens in Secret Manager or user registry
- Modify proxy to validate token against store
- Admin UI manages token lifecycle (create, revoke, audit)

**Implementation Tasks:**
- [ ] Design user token storage (Secret Manager secrets or simple config)
- [ ] Modify proxy auth middleware to validate user tokens
- [ ] Create onboarding script to generate and store user tokens
- [ ] Add Admin UI views for token management (list, create, revoke)
- [ ] Log user identity with requests (if token validation succeeds)

### 3. Model Discovery Verification
**Problem:** Need to ensure /model picker works correctly with proxy
**Solution:**
- Verify proxy's /v1/models endpoint returns configured models
- Ensure Admin UI can update model mappings
- Test that Claude Code shows model picker and allows selection

**Implementation Tasks:**
- [ ] Verify /v1/models endpoint exists and returns correct format
- [ ] Test Admin UI model configuration updates
- [ ] Validate model picker appears in Claude Code with correct models
- [ ] Ensure selected model is used for requests

### 4. Compact Window Tuning
**Problem:** Value 190000 may need adjustment based on provider model limits
**Solution:**
- Review typical context window sizes for providers (NVIDIA NIM, OpenRouter, etc.)
- Consider making compact window configurable per user/project via Admin UI
- Provide guidance on appropriate values for different use cases

**Implementation Tasks:**
- [ ] Research provider model context limits
- [ ] Determine appropriate default value for CLAUDE_CODE_AUTO_COMPACT_WINDOW
- [ ] Consider adding Admin UI setting for compact window
- [ ] Document recommendations for different providers/use cases

### 5. Logging and Observability Enhancements
**Problem:** Basic loguru -> journald -> Cloud Logging may lack structure
**Solution:**
- Add structured logging (JSON) for easier querying in Cloud Logging
- Add logs for authentication attempts, model requests, errors
- Consider adding access logs with user identification (if per-user tokens)

**Implementation Tasks:**
- [ ] Configure loguru to output JSON format
- [ ] Add structured fields: timestamp, level, message, request_id, user_id (if available)
- [ ] Log authentication successes/failures
- [ ] Log provider requests/responses with timing
- [ ] Add error logging with stack traces
- [ ] Consider integrating with Cloud Monitoring for metrics

### 6. User Onboarding Features
**Problem:** Wrapper script (`fcc-connect`) could be enhanced
**Solution:**
- Add version check or self-update mechanism
- Add help flag or documentation link
- Consider adding connectivity diagnostics
- Add cleanup function for orphaned tunnel processes

**Implementation Tasks:**
- [ ] Add version reporting to fcc-connect
- [ ] Add `--help` flag with usage information
- [ ] Add `--diagnostics` flag to test tunnel and proxy connectivity
- [ ] Add cleanup function to kill orphaned tunnel processes on startup
- [ ] Improve error messages and troubleshooting guidance

## Summary of Feature Improvements

| Priority | Feature | Improvement |
|----------|---------|-------------|
| High | Admin UI | Add real authentication (password/separate port/federated) |
| Medium | Per-user tokens | Optional auditability feature (store in Secret Manager, validate in proxy) |
| Medium | Model discovery | Verify /v1/models endpoint works; test model picker in Claude Code |
| Medium | Compact window | Tune default value; consider making configurable via Admin UI |
| Medium | Logging | Enhance to structured JSON; add auth/request/error logging |
| Low | Onboarding | Enhance fcc-connect: version check, help, diagnostics, cleanup |

## Most Impactful Changes
1. Add real authentication to Admin UI (password or port split)
2. Verify model discovery works correctly with proxy
3. Enhance logging to structured format for better observability
4. Consider per-user token feature if auditability is required

## Implementation Tasks

### Immediate (High/Medium Priority)
- [ ] Add authentication to Admin UI (password hash from Secret Manager)
- [ ] Verify model discovery: check /v1/models endpoint and test with Claude Code
- [ ] Enhance logging: configure loguru for JSON output, add structured fields
- [ ] Research and tune compact window default value
- [ ] Add basic enhancements to fcc-connect: version flag, help text

### Short Term
- [ ] Consider per-user token feature (design storage, modify proxy validation)
- [ ] Add Admin UI settings for compact window (if making configurable)
- [ ] Enhance fcc-connect with diagnostics and cleanup functions
- [ ] Add structured logging for authentication and provider requests
- [ ] Document feature usage and recommendations

## Verification Steps
After implementation, verify:
- [ ] Admin UI requires authentication (password challenge)
- [ ] Model picker appears in Claude Code with correct model list
- [ ] Selected model is used for requests (check provider logs)
- [ ] Logs are structured JSON with useful fields
- [ ] Compact window setting appears to be respected (token usage)
- [ ] fcc-connect provides version info and help text
- [ ] Admin UI allows configuration changes and persists them
- [ ] End-to-end: engineer can configure provider via Admin UI, send request, get response

## References
- Original plan: free-claude-code-gcp-plan.md
- Codebase: server.py, api/admin_routes.py, api/admin_config.py, api/admin_urls.py
- Security review findings (Admin UI auth)
- Nuggets from features discussion in plan