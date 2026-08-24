# Release Security Configuration

This document describes the security controls implemented in the release workflow to prevent unauthorized releases.

## Overview

The release workflow (`.github/workflows/main.yml`) implements multiple security layers to prevent unauthorized releases:

1. **Manual Workflow Dispatch** (Enforced) - Releases must be manually triggered
2. **Branch Ancestry Verification** (Automated) - Tagged commits must be on the default branch
3. **Tag Signature Verification** (Automated) - Checks for cryptographic signatures on tags
4. **Actor Authorization** (Enforced) - Only users with write access can trigger releases
5. **Tag Protection Rules** (Recommended) - Additional repository-level protection

## Security Model

The release workflow implements a defense-in-depth security model:

```
Manual Trigger (workflow_dispatch)
    ↓
Actor Authorization → Only users with write access can trigger
    ↓
Tag Format Validation → Prevents command injection
    ↓
Tag Existence Check → Verifies tag exists in repository
    ↓
Tag Signature Check → Verifies cryptographic signature (if available)
    ↓
Branch Verification → Automated check that commit is on default branch
    ↓
Build & Release → Publish to GitHub Releases
```

## How to Create a Release

### Prerequisites

- You must have **write access** or higher to the repository
- The commit you want to release must be on the default branch (typically `main`)
- You must create a git tag following semantic versioning (e.g., `v1.2.3`)

### Release Process

1. **Create and push a tag** on the default branch:
   ```bash
   git checkout main
   git pull
   git tag -s v1.2.3 -m "Release version 1.2.3"  # -s creates a signed tag (recommended)
   git push origin v1.2.3
   ```

2. **Manually trigger the release workflow**:
   - Go to the repository on GitHub
   - Navigate to **Actions** → **Build & Release Anki Addon**
   - Click **Run workflow**
   - Enter the tag name (e.g., `v1.2.3`)
   - Click **Run workflow** to start the release

3. **Monitor the workflow**:
   - The workflow will validate the tag format
   - It will verify the tag exists and check for signatures
   - It will verify the tagged commit is on the default branch
   - It will build the addon and create a GitHub release

### Tag Signing (Recommended)

For enhanced security, sign your tags with GPG or SSH:

**GPG signing:**
```bash
git tag -s v1.2.3 -m "Release version 1.2.3"
```

**SSH signing:**
```bash
git tag -s v1.2.3 -m "Release version 1.2.3" --sign-with=ssh
```

The workflow will verify signatures and log the verification status.

## Security Controls

### 1. Manual Workflow Dispatch (ENFORCED)

**What it does:** The workflow can only be triggered manually through the GitHub Actions UI, not automatically by pushing tags.

**Why it matters:** This prevents an attacker from automatically publishing a release by simply pushing a tag. Every release requires explicit manual action by an authorized user.

**How it works:** The workflow uses `workflow_dispatch` trigger instead of `push: tags:`. Only users with write access to the repository can trigger workflow_dispatch workflows.

### 2. Branch Ancestry Verification (AUTOMATED)

**What it does:** The workflow automatically verifies that tagged commits exist on the default branch.

**Why it matters:** This prevents releases from arbitrary commits or feature branches that haven't been reviewed and merged.

**How it works:**
- The workflow fetches full git history
- It checks that the tagged commit is reachable from the default branch
- If verification fails, the workflow exits with an error

**No configuration needed** - this is enforced automatically by the workflow.

### 3. Tag Signature Verification (AUTOMATED)

**What it does:** The workflow checks if tags are cryptographically signed with GPG or SSH keys.

**Why it matters:** Signed tags prove that the tag was created by someone with access to the signing key, providing non-repudiation and authenticity.

**How it works:**
- The workflow runs `git verify-tag` on the specified tag
- If the tag is signed and the signature is valid, it logs success
- If the tag is not signed or the signature cannot be verified, it logs a warning but continues
- This is currently advisory; for strict enforcement, modify the workflow to fail on unsigned tags

### 4. Actor Authorization (ENFORCED)

**What it does:** Logs the GitHub user who triggered the release and verifies they have write access.

**Why it matters:** Provides an audit trail and ensures only authorized users can trigger releases.

**How it works:**
- GitHub's `workflow_dispatch` trigger inherently requires write access
- The workflow logs the actor and workflow run URL for audit purposes
- Only repository collaborators with write access or higher can trigger the workflow

### 5. Tag Protection Rules (RECOMMENDED)

While the workflow enforces authorization through manual dispatch and verification checks, repository-level tag protection provides defense-in-depth.

**To configure tag protection:**

1. Go to repository **Settings** → **Rules** → **Rulesets**
2. Create a new ruleset for tags
3. Target tags matching `v*`
4. Enable restrictions:
   - Restrict tag creation to specific roles or users
   - Require signed commits (optional but recommended)

**Why this helps:** Tag protection prevents unauthorized users from creating version tags in the first place, adding an additional layer of security.

## Attack Scenarios Prevented

1. **Compromised contributor account with write access**: 
   - Must manually trigger the workflow (explicit action required)
   - Action is logged with actor identity for audit
   - Tag must point to a commit on the default branch (which requires PR review if branch protection is enabled)

2. **Unauthorized tag on arbitrary commit**: 
   - Fails branch verification
   - Cannot be released even if tag is created

3. **Tag on feature branch**: 
   - Fails branch verification
   - Cannot be released

4. **Malicious fork tag**: 
   - Cannot trigger workflow in main repository
   - Workflow_dispatch requires write access to the repository

5. **Automatic release on tag push**:
   - Prevented by workflow_dispatch trigger
   - No automatic execution possible

## Verification

To verify the security configuration is working:

1. **Check manual trigger requirement**: Push a tag - the workflow should NOT run automatically
2. **Check workflow_dispatch access**: Try to trigger the workflow without write access - should be denied
3. **Check branch verification**: Trigger the workflow with a tag on a non-default branch - should fail
4. **Check tag existence**: Trigger the workflow with a non-existent tag - should fail
5. **Check tag protection** (if configured): Try to create a tag without proper permissions - should be rejected

## Maintenance

- Review the list of users with write access regularly
- Audit release history for unexpected or unauthorized releases
- Keep the workflow actions pinned to specific commit SHAs (already done)
- Monitor GitHub security advisories for the actions used in the workflow
- Consider requiring signed tags by modifying the workflow to fail on unsigned tags

## Additional Recommendations

For maximum security, also configure:

1. **Branch protection rules** on the default branch:
   - Require pull request reviews before merging
   - Require status checks to pass
   - Restrict who can push to the branch

2. **Required reviewers**: 
   - Ensure all commits to the default branch are reviewed
   - This ensures that any commit that gets tagged has been reviewed

3. **Signed commits**:
   - Require all commits to be signed
   - This provides end-to-end verification of code authorship

## Questions or Issues

If you have questions about the release security configuration, please contact the repository maintainers.
