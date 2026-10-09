# Dependency Security

## Overview

This project implements supply-chain security measures to protect CI/CD workflows from malicious or compromised Python packages.

## Security Measures

### 1. Pinned Dependencies

All Python dependencies used in CI/CD are pinned to exact versions in `requirements-dev.lock`. This prevents:
- Automatic installation of compromised package updates
- Dependency confusion attacks
- Transitive dependency vulnerabilities from new releases

### 2. Pinned GitHub Actions

All GitHub Actions are pinned to specific commit hashes (e.g., `actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1`) to prevent:
- Malicious updates to action repositories
- Tag/branch manipulation attacks
- Supply-chain attacks via CI tooling

### 3. Pinned pip Version

The `pip` package manager itself is pinned to a specific version to prevent attacks via compromised pip releases.

## Updating Dependencies

### Regular Updates

1. Review the current versions in `requirements-dev.lock`
2. Check for security advisories for each package
3. Update version numbers in `requirements-dev.lock`
4. Test in a clean environment:
   ```bash
   python -m venv test_env
   source test_env/bin/activate  # or test_env\Scripts\activate on Windows
   pip install pip==24.0
   pip install -r requirements-dev.lock
   python -m unittest discover tests/
   ```
5. Commit the updated lock file

### Adding Hash Verification (Recommended)

For enhanced security, add SHA256 hashes to `requirements-dev.lock`:

1. Install pip-tools:
   ```bash
   pip install pip-tools
   ```

2. Generate hashed requirements:
   ```bash
   pip-compile --generate-hashes --output-file=requirements-dev.lock requirements-dev.txt
   ```

3. Update the workflow to use `--require-hashes`:
   ```yaml
   pip install --no-cache-dir --require-hashes -r requirements-dev.lock
   ```

### Updating GitHub Actions

1. Check for new releases of actions used in workflows
2. Find the commit hash for the desired version:
   ```bash
   git ls-remote https://github.com/actions/checkout.git refs/tags/v4.1.1
   ```
3. Update the workflow file with the new hash and version comment

## Monitoring

Consider implementing:
- Dependabot alerts for security vulnerabilities
- Regular dependency audits using `pip-audit`
- Automated dependency update PRs with testing

## References

- [SLSA Supply Chain Security Framework](https://slsa.dev/)
- [pip-tools Documentation](https://pip-tools.readthedocs.io/)
- [GitHub Actions Security Hardening](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
