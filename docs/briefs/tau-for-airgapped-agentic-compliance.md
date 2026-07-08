# Tau for Air-Gapped Agentic Compliance

## Problem

Local agents can accelerate compliance and engineering work, but their outputs
are untrusted claims. In controlled or air-gapped environments, the main risk is
not that an agent is slow. The risk is that an agent leaks data, invents
authority, mutates public systems, or turns unsupported text into a compliance
verdict.

## Tau Answer

Tau gates agent work with policy profiles, data boundaries, DAG contracts,
typed receipts, evidence manifests, validators, and human approvals. Agents may
propose. Tau decides what counts. Humans own high-risk approvals.

## Embry-OS Role

Embry-OS hosts the local operating environment: memory, scillm, local model
providers, APIs, monitors, evidence services, and operator infrastructure.

## Sparta Explorer Role

Sparta Explorer renders the human workbench: posture, QRA state, evidence
cases, blockers, monitor health, proof chains, and signoff readiness.

## Airgap Story

The model runs locally. External providers and public mutation are denied by
policy. Tau records provider, network, and evidence receipts so a reviewer can
inspect what happened and what remains blocked.

## Compliance Story

Agents perform tedious extraction and crosswalk work. They do not become legal
or export-control authorities. Tau routes final compliance and export-control
decisions to designated humans.

## Demo

The first review demo uses synthetic data only. One command should produce a
receipt-backed posture contract for Sparta Explorer with harness status PASS
and posture verdict NOT_SIGNOFF_READY because human export-control review is
required.

## Non-Claims

See [Tau Non-Claims](../non-claims.md). Tau does not prove ITAR compliance,
ATO readiness, SCIF readiness, model approval, or authorization to process real
controlled technical data.
