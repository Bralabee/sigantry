# Security Policy

## Supported Versions

Only the latest release of **Sigantry** receives active security updates and patches.

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Known accepted issues

Issues recorded here are known, assessed and deliberately not remediated. They
are listed so that a reporter does not spend effort on a finding the
maintainer has already ruled on.

### Client-branded slide deck in git history (accepted, 2026-09-20)

A client-branded commercial slide deck was committed to this repository in
its initial import and deleted in the very next commit, before any release.
Deleting a file at the tip does not remove its blob from history, so the
object (`1351735c`, ~583 KB) remains retrievable by anyone who can read the
repository.

**Assessment.** The deck is commercial positioning material: a scan of its
extracted text found no credentials, no tokens, no GUIDs, no email addresses
and no URLs. It is therefore an unintended disclosure of commercial material,
not a secret leak, and **no credential needs rotating.**

**Decision: accepted, not rewritten.** Removing it requires rewriting public
history and force-pushing, which breaks every existing clone and every open
branch, and which does not by itself evict cached object views. The
maintainer has weighed that cost against the content class and accepted the
exposure. This note exists so the decision is recorded rather than
rediscovered.

**Not an invitation to report.** Please do not file this as a vulnerability;
it is tracked here. Anything genuinely sensitive found in history - a
credential, token, connection string or customer data - is in scope and
should be reported through the channel below.

## Reporting a Vulnerability

If you discover a security vulnerability within Sigantry, please report it responsibly rather than opening a public issue on GitHub.

### Reporting Channel

Please email security reports to **bralabala@gmail.com** with the subject line:
`[SECURITY] Vulnerability in Sigantry`.

Please include in your report:
1. A clear description of the vulnerability and affected versions.
2. Steps to reproduce the vulnerability (proof-of-concept script, reproduction manifest, or minimal code snippet).
3. Any known mitigations or suggested fixes.

### Response Timeline

- **Initial Acknowledgement:** Within 48 hours of receipt.
- **Assessment & Confirmation:** Within 5 business days.
- **Patch & Release:** We aim to release a patched version within 14 days of confirmation, accompanied by a published Security Advisory.
