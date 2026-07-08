# Lockheed-Style Review Angle

Tau is positioned as an air-gapped, ITAR/CUI-aware agent control plane for
synthetic review only.

What to inspect:

- local-provider path is separated from cloud-provider use;
- no-egress evidence is recorded as bounded probe evidence, not certification;
- synthetic contract clauses route to human export-control review;
- Sparta Explorer posture remains NOT_SIGNOFF_READY until a human acts;
- non-claims are explicit and visible.

The first review branch must not contain real controlled technical data,
customer data, supplier records, or copied proprietary contract language. The
demo is designed to fail closed with `approval_required`.
