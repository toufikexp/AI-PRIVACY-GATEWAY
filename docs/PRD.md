# Product Requirements Document — LLM Privacy Gateway

## Problem

MENA enterprises (banks, telecoms, government, healthcare) want to use cloud LLMs (OpenAI, Anthropic, Google) but cannot send sensitive customer data to providers outside their country. Current options are bad:

- **Don't use cloud LLMs.** Lose competitive advantage in AI adoption.
- **Self-host open models.** Operationally expensive, quality below frontier models, ongoing maintenance burden.
- **Use cloud LLMs with manual masking.** Tedious, error-prone, doesn't scale.
- **Use existing PII detection tools (Private AI, Protecto, Skyflow).** Built for US/EU patterns; weak on Arabic, French, MENA-specific identifiers (Algerian NIN, Saudi Iqama, Emirates ID, etc.); no in-country deployment options.

## Solution

A drop-in OpenAI-compatible proxy that:

1. **Detects** sensitive entities in real time using a three-detector ensemble (structural validators + multilingual NER + contextual LLM).
2. **Substitutes** detected entities with synthetic, semantically equivalent values that preserve LLM reasoning quality.
3. **Forwards** the sanitized request to the upstream provider.
4. **Reverses** the substitution on the response so the calling application sees original values.

Original sensitive data never leaves the country boundary. The cloud LLM provider sees only sanitized text.

## Target users

**Primary:** Enterprise IT and compliance teams in MENA financial services, telecom, healthcare, and large enterprise. Decision-makers are CISOs, CTOs, and Chief Compliance Officers.

**Secondary:** Government agencies and defense organizations requiring strict data sovereignty.

**Not targeting at MVP:** Consumer/family users, SMBs without dedicated compliance, US/EU markets.

## Key differentiators

1. **MENA-native.** Country rule packs cover Algerian, Moroccan, Tunisian, Egyptian, Emirati, Saudi, Qatari, and other MENA-specific regulatory definitions and data formats.
2. **Three-tier rule authority.** Country rules immutable (regulatory baseline), industry rules configurable (banking/telecom/healthcare/etc.), customer rules fully editable. Customer-validated exceptions allow safe override of false positives without compromising regulatory integrity.
3. **95%+ detection accuracy.** Ensemble of three detectors with independent failure modes; per-tier accuracy targets (Tier 1 ≥99%, Tier 2 ≥93%, Tier 3 ≥90%).
4. **User-invisible latency.** Total proxy overhead bounded; ≤10% relative to upstream LLM response time at typical load.
5. **Data sovereignty.** Country-level deployment by default; optional dedicated single-customer deployment; air-gapped Sovereign tier with offline licensing.
6. **OpenAI-compatible.** Customer integration is a base URL change; no SDK lock-in, no application refactor.

## Success metrics (12 months post-launch)

- 5+ paying customers across at least 3 MENA countries
- Validated 95%+ detection accuracy on real customer data (per `docs/ARCHITECTURE.md` §4.6 evaluation methodology)
- P99 detection latency under 340ms at sustained load per the throughput model
- Zero confirmed cross-customer data leaks (multi-tenant isolation invariant)
- At least one design partner converts to a referenceable Enterprise or Sovereign tier customer

## Plans and pricing

| Plan | Target | Key Capabilities |
|------|--------|------------------|
| Starter | SMB / single-team pilots | 1 country, 1 industry pack, 25 Tier 3 rules, 10 exceptions, strict mode only |
| Professional | Mid-market enterprises | Up to 3 industry packs, 250 Tier 3 rules, 100 exceptions, Tier 2 customization |
| Enterprise | Large enterprises | Unlimited rules/exceptions, custom failure modes, optional dedicated deployment |
| Sovereign | Government, defense, regulated banking | Air-gapped operation, signed offline licenses, HSM-backed audit encryption |

## Out of scope (explicit)

- **Streaming responses** at MVP. Deferred to expansion phase. See `docs/ARCHITECTURE.md` §8.4.
- **Multi-turn conversation state.** Each request is independent at MVP. Multi-turn substitution mapping is a follow-on capability.
- **Document/PDF processing pipeline.** Text-only at MVP.
- **Image/multimodal sanitization.** Year 2+.
- **Broader LLM security** (jailbreak defense, output content moderation, hallucination detection). This is a privacy gateway, not an AI security platform. Customers requiring these can chain dedicated tools via the OpenAI API surface.
- **Cross-customer detection learning.** Privacy-preserving aggregation across tenants is Year 2+.
- **Self-serve onboarding.** MVP is white-glove deployment with vendor-led rule curation for the first 90 days.

## Validation

The 95% accuracy claim is the product's central value proposition. It must be validated on real MENA enterprise data before scaling. The reference corpus and evaluation methodology are defined in `docs/ARCHITECTURE.md` §4.6; `scripts/eval_corpus.py` runs the eval and is wired into CI on PRs that touch detection.

If measured precision falls below 93% or recall below 92% on the reference corpus, specific detectors must be improved before deployment to additional design partners.
