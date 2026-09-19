# ADR-0010 — Commercial Model: Apache-2.0 Pure Open Source

- **Status:** Accepted
- **Date:** 2026-04-24
- **Milestone:** v3.0 (Productization — Sigantry)
- **Deciders:** platform team + product owner
- **Context:** The HS2 internal toolkit is becoming Sigantry, an open-source product targeting any Microsoft Fabric organisation. The commercial model shapes every downstream decision — licensing, package structure, plugin ecosystem, contributor governance, pricing, and support. It must be locked before code moves.

## Decision

Sigantry ships under **Apache-2.0** as a pure open-source project.

- All three packages — `sigantry-core`, `sigantry-hs2`, `sigantry-jtoye` — are Apache-2.0.
- There is no "core" / "enterprise" split. Every capability built in v3.0 is in the OSS repo.
- Copyright is held jointly by contributors; no CLA is required at v3.0 open. (See the *Contributor licence* section below.)
- Any future commercial layer (support subscription, managed control plane, premium plugins) is explicitly **out of scope for v3.0** and would require revisiting this ADR.

## Alternatives considered

| Model | Description | Why rejected |
|-------|-------------|--------------|
| **A. MIT** | More permissive; no patent grant. | A toolkit that integrates multiple vendor APIs (Microsoft Fabric REST, ADO REST, GitHub REST, Purview, Azure Key Vault) needs an explicit patent grant for enterprise legal review. MIT does not provide one. Apache-2.0's patent grant is worth the extra line in headers. |
| **B. Apache-2.0 (chosen)** | Permissive + patent grant. Widely understood by enterprise legal. | **Chosen.** Best of the permissive options for this vendor-integration surface. |
| **C. Open Core / Dual-Licence** | OSS base + commercial/proprietary enterprise plugins. (e.g. Elastic/Logstash pattern, HashiCorp BSL) | Creates a three-way complexity at birth: OSS policy, commercial policy, and the boundary between them. Every PR becomes "does this belong in OSS or enterprise?" That overhead is unjustified until there's revenue paying for it. Revisit post-v3. |
| **D. AGPL** | Strong copyleft. Forces downstream to open-source modifications. | Sigantry will be embedded in private CI pipelines at customer orgs. AGPL's "network use" clause triggers in pipeline contexts and creates legal uncertainty for adopters. Counter to the goal of frictionless enterprise adoption. |
| **E. SSPL / BSL / variants** | Non-OSI "source available" licences. | Excluded from most enterprise procurement whitelists because they are not OSI-approved. Self-defeating for an adoption-first product. |
| **F. Managed-service-only, closed-source** | SaaS offering with no public source. | Alienates the ICP entirely. Platform leads at Fabric-running orgs expect to self-host their audit plane and their CI pipelines. A managed-only play has a 10x smaller addressable market. |

## Rationale

1. **The ICP wants OSS.** Platform leads in Fabric-running orgs overwhelmingly self-host their CI/CD stack. A commercial-only product is filtered out before evaluation. Apache-2.0 is the default enterprise-friendly permissive licence.
2. **The wedge (work-item traceability + audit plane) is a process differentiator, not a secret algorithm.** The value is in the end-to-end experience and the `WorkItemProvider` seam contract, not in any specific line of code. There is no lock-in moat to protect via closed source.
3. **The HS2 legacy is already effectively open.** v1.0 and v2.0 were built without revenue intent. Flipping to commercial now would betray the spirit of the work and break continuity with the HS2 → Sigantry migration.
4. **Apache-2.0's patent grant is load-bearing.** Sigantry integrates ADO REST, Fabric REST, GitHub REST, Purview, Key Vault, and five more vendor/OSS libraries. Enterprise legal teams flag MIT-only projects for lack of patent safety. Apache-2.0 is table-stakes for adoption at regulated shops.
5. **Open Core would fork the contributor experience.** Every PR reviewer would have to police the boundary. At v3.0 team size (small) this is a net drain. Revisit when revenue justifies the overhead.

## Consequences

- Sigantry contributes upstream to `fabric-cicd`, `ms-fabric-cli`, `msfabricpysdkcore` freely. No "enterprise-only extensions" to gate those upstream integrations.
- All Sigantry features — including traceability, audit plane, drift detection, and the JToye plugin — live in public GitHub under Apache-2.0.
- The `sigantry-core`, `sigantry-hs2`, and `sigantry-jtoye` package `LICENSE` files all carry Apache-2.0.
- Revenue, if any, comes from **services around** Sigantry (support, consulting, managed hosting), not from **restrictions on** Sigantry.
- Contributors outside the platform team contribute under Apache-2.0. See *Contributor licence* below.
- Future premium plugins (if ever) would ship as separate, non-core packages with their own licence, and would not touch `sigantry-core`. This ADR does not authorise any such package.

## Contributor licence

- v3.0 uses **Developer Certificate of Origin (DCO)** via `Signed-off-by` in commits. No CLA.
- Rationale: CLA friction slows contribution and DCO is sufficient for Apache-2.0 project governance. GitHub's DCO app enforces automatically.
- If a specific enterprise contributor requires a CLA for their internal process, handle per-contributor rather than project-wide.
- **Revisit trigger:** if a corporate contributor requires relicensing (unlikely under Apache-2.0) or if we adopt an Open-Core boundary in the future.

## Trademark

- The name "Sigantry" is **not yet cleared** for trademark / domain / package-name availability as of 2026-04-24. Per user decision (2026-04-24), build proceeds without blocking on clearance; ADR-0011 tracks the rename-if-necessary plan.
- The Apache-2.0 licence does not grant trademark rights; if clearance later blocks the name, the licence stays Apache-2.0 and only the brand rotates. No code relicensing occurs.

## Pricing posture (forward-looking, not binding)

Explicitly noted in the PRODUCT-BRIEF.md and repeated here for decision-record continuity: v3.0 defines no commercial levers. Any commercial consideration (support SLA, managed control plane, premium plugins) is deferred and would require a new ADR amending this one.

## Revisit triggers

This ADR should be revisited if **any** of the following occur:

1. A recurring paying-customer opportunity appears where the customer requires a commercial licence or support contract. Handle per-contract first; revisit the ADR only after the second such request.
2. A major contributor (>10% of commits over a quarter) requests relicensing.
3. Revenue from support/managed-hosting becomes material (say, >2 FTE-worth) and the economics of an open-core split become tangible.
4. A security-disclosure or legal advisory recommends moving off Apache-2.0. (Unlikely — Apache-2.0 is settled law.)
5. Microsoft changes Fabric's licensing or API access model in a way that materially changes the ecosystem.

## Supersedes / superseded by

- Supersedes: none.
- Superseded by: none.
- Related: [ADR-0011 — Rename to Sigantry](ADR-0011-rename-to-sigantry.md).

---

*Decision captured: 2026-04-24 (milestone v3.0 Phase 10, BRIEF-02).*
