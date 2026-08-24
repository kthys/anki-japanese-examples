# Release Process Security Changes

## Summary

The release workflow has been updated to address a critical security vulnerability where any user with write access could automatically trigger a stable release by pushing a `v*` tag. The previous fix using `environment: release` was insufficient because it relied on external GitHub settings that could not be enforced within the workflow itself.

## Changes Made

### 1. Workflow Trigger Changed from Automatic to Manual

**Before:**
```yaml
on:
  push:
    tags:
      - 'v*'
```

**After:**
```yaml
on:
  workflow_dispatch:
    inputs:
      tag:
        description: 'Release tag (e.g., v1.0.0)'
        required: true
        type: string
```

**Impact:** Releases can no longer be triggered automatically by pushing a tag. They must be manually initiated through the GitHub Actions UI by a user with write access.

### 2. Enhanced Security Checks

The workflow now includes multiple verification steps:

1. **Tag Format Validation** - Validates the tag follows semantic versioning (vX.Y.Z)
2. **Tag Existence Check** - Verifies the tag actually exists in the repository
3. **Tag Signature Verification** - Checks for and verifies cryptographic signatures on tags (advisory)
4. **Branch Ancestry Verification** - Ensures the tagged commit is on the default branch
5. **Actor Authorization Logging** - Logs who triggered the release for audit purposes

### 3. Removed Unenforceable Controls

- Removed `environment: release` reference that depended on external configuration
- Removed reliance on out-of-band GitHub settings that couldn't be verified in the workflow

## Security Benefits

### Defense Against Attack Scenarios

1. **Compromised Account with Write Access:**
   - **Before:** Could push a tag and automatically trigger a release
   - **After:** Must manually trigger the workflow (explicit action), which is logged and auditable

2. **Unauthorized Tag on Arbitrary Commit:**
   - **Before:** Would be released if environment protection wasn't configured
   - **After:** Fails branch verification check

3. **Malicious Tag on Feature Branch:**
   - **Before:** Would be released if environment protection wasn't configured
   - **After:** Fails branch verification check

4. **Accidental Release:**
   - **Before:** Pushing a tag accidentally would trigger a release
   - **After:** Requires explicit manual action through the UI

### Authorization Model

The new authorization model is enforced at multiple levels:

```
User with Write Access
    ↓
Manual Workflow Trigger (workflow_dispatch)
    ↓
Tag Format Validation
    ↓
Tag Existence & Signature Check
    ↓
Branch Ancestry Verification
    ↓
Build & Publish Release
```

## Migration Guide

### For Release Managers

The release process has changed. To create a release:

1. **Create and push a tag** (as before):
   ```bash
   git checkout main
   git pull
   git tag -s v1.2.3 -m "Release version 1.2.3"
   git push origin v1.2.3
   ```

2. **Manually trigger the workflow** (NEW STEP):
   - Go to GitHub Actions → "Build & Release Anki Addon"
   - Click "Run workflow"
   - Enter the tag name (e.g., `v1.2.3`)
   - Click "Run workflow"

### For Repository Administrators

- The `release` environment is no longer used and can be removed from repository settings
- Consider configuring tag protection rules for defense-in-depth (see RELEASE_SECURITY.md)
- Review users with write access regularly, as they can trigger releases

## Why This Fix Works

The previous fix attempted to use `environment: release` with the assumption that it would be configured with required reviewers. However:

1. **The environment configuration is external** - It's not part of the repository code and can't be verified or enforced by the workflow
2. **The workflow still triggered automatically** - Any `v*` tag push would start the workflow, even if it waited for approval
3. **No in-workflow authorization** - The workflow itself didn't verify who created the tag or whether they were authorized

The new fix addresses these issues by:

1. **Requiring explicit manual action** - The workflow can only be triggered through the UI, not by pushing tags
2. **Enforcing authorization at the trigger level** - Only users with write access can trigger `workflow_dispatch` workflows
3. **Adding verifiable in-workflow checks** - Tag existence, signature, and branch ancestry are all verified within the workflow
4. **Creating an audit trail** - Every release is explicitly triggered by a named user, logged in the workflow run

## References

- See `.github/RELEASE_SECURITY.md` for complete security documentation
- See `.github/workflows/main.yml` for the updated workflow implementation
