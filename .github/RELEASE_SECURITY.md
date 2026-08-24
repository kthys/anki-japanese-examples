# Release Security Configuration

This document describes the security controls required for the release workflow to function properly and securely.

## Overview

The release workflow (`.github/workflows/main.yml`) implements multiple security layers to prevent unauthorized releases:

1. **Environment Protection with Manual Approval** (Required)
2. **Branch Ancestry Verification** (Automated)
3. **Tag Protection Rules** (Recommended)

## Required Configuration

### 1. Environment Protection (REQUIRED)

The workflow uses a GitHub environment named `release` that **must be configured** with protection rules.

**To configure the release environment:**

1. Go to repository **Settings** → **Environments**
2. Create or edit the `release` environment
3. Enable **Required reviewers** and add trusted maintainers
4. Optionally, restrict deployment branches to `main` or your default branch

**Why this is critical:** Without environment protection, the workflow will run automatically on any `v*` tag push. Environment protection adds a manual approval gate, ensuring that only authorized maintainers can approve releases.

### 2. Branch Ancestry Verification (AUTOMATED)

The workflow automatically verifies that tagged commits exist on the default branch. This prevents releases from arbitrary commits or feature branches.

**How it works:**
- The workflow fetches full git history
- It checks that the tagged commit is reachable from the default branch
- If verification fails, the workflow exits with an error

**No configuration needed** - this is enforced automatically by the workflow.

### 3. Tag Protection Rules (RECOMMENDED)

While the workflow enforces authorization through environment protection and branch verification, repository-level tag protection provides defense-in-depth.

**To configure tag protection:**

1. Go to repository **Settings** → **Rules** → **Rulesets**
2. Create a new ruleset for tags
3. Target tags matching `v*`
4. Enable restrictions:
   - Require a pull request before merging (for the commit being tagged)
   - Restrict tag creation to specific roles or users
   - Require signed commits (optional but recommended)

**Why this helps:** Tag protection prevents unauthorized users from creating version tags in the first place, adding an additional layer of security.

## Security Model

The release workflow implements a defense-in-depth security model:

```
Tag Push (v*)
    ↓
Environment Protection → Manual approval required by trusted reviewers
    ↓
Branch Verification → Automated check that commit is on default branch
    ↓
Build & Release → Publish to GitHub Releases
```

**Attack scenarios prevented:**

1. **Compromised contributor account**: Cannot approve their own release (environment protection)
2. **Unauthorized tag on arbitrary commit**: Fails branch verification
3. **Tag on feature branch**: Fails branch verification
4. **Malicious fork tag**: Cannot trigger workflow in main repository

## Verification

To verify the security configuration is working:

1. **Check environment protection**: Try to push a tag - the workflow should wait for approval
2. **Check branch verification**: Create a tag on a non-default branch - the workflow should fail
3. **Check tag protection** (if configured): Try to create a tag without proper permissions - should be rejected

## Maintenance

- Review the list of authorized release approvers regularly
- Audit release history for unexpected or unauthorized releases
- Keep the workflow actions pinned to specific commit SHAs (already done)
- Monitor GitHub security advisories for the actions used in the workflow

## Questions or Issues

If you have questions about the release security configuration, please contact the repository maintainers.
