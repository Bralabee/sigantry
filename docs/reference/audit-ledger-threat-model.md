# Audit ledger — threat model

Sigantry's release and bootstrap ledgers are **integrity-checked, not tamper-proof**.
This page states exactly which adversary the shipped mechanism resists, which it does
not, and what an operator has to add before the ledger can be treated as evidence
against a motivated insider. It is the authoritative description; where any other page
in this repository describes the ledger, it defers to this one.

## What ships

Every record (`DeployRecord`, `BootstrapRecord`) carries an `audit_hash`: an **unkeyed
SHA-256** over the canonical JSON of its remaining fields
(`sigantry_core/release/record.py`). Records are appended to JSONL ledgers and each
record's `prev_hash` names its predecessor's `audit_hash`, so the file is a hash chain.
`sigantry release verify` walks the file in append order and checks both halves: each
record's own hash against its contents, and each `prev_hash` against the record before
it.

There is no keyed MAC, no signature and no external anchor anywhere in the package.
The hash inputs are all public fields and the algorithm ships in the wheel, so the seal
is reproducible by anyone holding the record.

## What the mechanism does resist

- **Accidental corruption.** A truncated write, an encoding mangle or a stray editor
  save flips `verify_hash()` to `False` on the affected record.
- **A naive edit.** Changing `approver`, `fabric_items_changed` or any other field
  without recomputing the seal is detected, and the whole chain is reported invalid
  from that record onward.
- **Deleting a record from the middle.** The next record's `prev_hash` no longer
  matches its new predecessor, so the chain breaks at a named index.
- **An empty ledger passed off as a verified one.** `release verify` reports
  `NOTHING TO VERIFY` with `vacuous: true` rather than a bare success.

That is a real and useful control. It is the control an auditor wants against a
filesystem, a bad deploy script, or a colleague who edited a line by hand.

## What the mechanism does not resist

- **Anyone who can write the ledger file.** The chain is unkeyed, so an actor with
  write access can edit any record and re-seal the entire file using nothing but the
  shipped public API (`record.model_copy(update=...).with_hash()`, walking `prev_hash`
  forward). `release verify` then reports the rewritten chain as valid. That actor is
  precisely the party the ledger exists to constrain — by default the ledger lives in
  the deploying identity's own `$HOME` (`~/.sigantry/audit/`), i.e. inside the trust
  domain being audited.
- **Truncating the tail.** The chain commits each record to its predecessor, but
  nothing commits the chain to its own **length or tip**. Dropping the last N records
  leaves a shorter, perfectly valid chain, and a verifier with no independent record of
  the expected tip cannot tell.
- **A chain migration used as laundering.** `scripts/audit_chain_migrate.py` legitimately
  re-seals every record when it back-fills `prev_hash`. Re-sealing is inherent to any
  rewrite of an unkeyed chain, so the same operation restores validity to an edited
  ledger.
- **Loss.** An unkeyed chain has no durability property at all. A ledger written to an
  ephemeral runner and never uploaded leaves no evidence of anything; each run also
  starts from an empty file, so every record is a first record with `prev_hash = null`
  and the chain property is vacuous.

## What an operator must add

The chain is only as strong as the place its tip is written. To make the ledger
evidence rather than a convenience:

1. **Give the ledger a durable home the deploying identity cannot rewrite.** Pass
   `--audit-dir` explicitly in CI and ship the directory somewhere append-only —
   an artifact store, an immutable-blob container, a log sink with a retention lock.
   Do not rely on the `~/.sigantry/audit/` default in an automated pipeline: it is a
   per-machine developer convenience.
2. **Anchor the tip where the deploying identity cannot write it.** Record the latest
   `audit_hash` and the record count somewhere outside the ledger — a protected git
   branch, a CI log with retention, an external notary — and compare on verify. Without
   an anchor there is no defence against tail truncation, and no defence against a full
   re-seal.
3. **Verify against the anchor, not just against the file.** `sigantry release verify`
   answers "is this file internally consistent?". Only the anchor answers "is this the
   file that was written?".

Sigantry does **not** currently provide (1) or (2) itself. Keying the chain, committing
to its tip, and shipping a durable sink are tracked as product work; until they land,
the accurate description of the ledger is *integrity-checked and independently
verifiable*, not *tamper-evident against an insider*.

## See also

- [Tutorial 04 — Read the audit trail](../tutorials/04-audit-trail.md) — the hands-on
  walkthrough, including the re-seal demonstration.
- `sigantry release verify --json` — `{valid, records, vacuous, first_bad_index, reason}`.
