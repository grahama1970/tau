#!/usr/bin/env node
// Deterministic controller harness for workflows/dev-cycle.workflow.js.
//
// Loads the REAL committed script text from the repo (no copy, no mock of the
// controller), executes it inside a node:vm sandbox with a stub `runs` runtime
// whose run() dispatches on child key to scripted scenario fixtures from
// workflows/test/scenarios/*.json. Plain node:test; zero new dependencies.
//
// Each scenario proves one controller property (evidence chain, lease fencing,
// contamination guard, audit veto, post-round re-gate, provider rebind, restart
// journal, convergence, gap synthesis) by asserting on the terminal result and
// the exact child-call log the script produced.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const scriptPath = join(here, '..', 'dev-cycle.workflow.js');
const scriptText = readFileSync(scriptPath, 'utf8');
const scenariosDir = join(here, 'scenarios');

// The script is written for the pi runtime, which wraps it in an async function
// with an injected `runs` global. vm.compileFunction reproduces that wrapper:
// top-level return is legal, `runs` arrives as a parameter, and each call gets
// a fresh scope.
const execute = vm.compileFunction(scriptText, ['runs']);

function loadScenarios() {
  return readdirSync(scenariosDir)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((f) => ({ file: f, scenario: JSON.parse(readFileSync(join(scenariosDir, f), 'utf8')) }));
}

async function runCase(fixture) {
  const calls = [];
  const runs = {
    run: function (key, spec) {
      const entry = fixture.find((e) => e.key === key);
      calls.push({ key, model: spec ? spec.model : undefined });
      if (!entry) {
        return Promise.reject(new Error('unscripted child key: ' + key + ' — add it to the scenario fixture'));
      }
      return Promise.resolve({ structuredOutput: entry.output });
    },
  };
  const result = await execute(runs);
  return { result, calls };
}

function applyExpect(expect, outcome, label) {
  const { result, calls } = outcome;
  const keys = calls.map((c) => c.key);
  if (expect.status !== undefined) assert.equal(result.status, expect.status, label + ': status');
  if (expect.status_not !== undefined) assert.notEqual(result.status, expect.status_not, label + ': status_not');
  if (expect.veto_reason_contains !== undefined) {
    assert.ok(String(result.vetoReason || '').includes(expect.veto_reason_contains), label + ': vetoReason');
  }
  for (const k of expect.calls_include || []) {
    assert.ok(keys.includes(k), label + ': expected call ' + k + ' in ' + JSON.stringify(keys));
  }
  for (const k of expect.calls_exclude || []) {
    assert.ok(!keys.includes(k), label + ': unexpected call ' + k + ' in ' + JSON.stringify(keys));
  }
  for (const [prefix, n] of Object.entries(expect.prefix_counts || {})) {
    assert.equal(keys.filter((k) => k.startsWith(prefix)).length, n, label + ': prefix count ' + prefix);
  }
  for (const [role, model] of Object.entries(expect.models_used || {})) {
    assert.equal(result.modelsUsed[role], model, label + ': modelsUsed.' + role);
  }
  for (const cm of expect.call_models || []) {
    const call = calls.find((c) => c.key === cm.key);
    assert.ok(call, label + ': call_models key ' + cm.key + ' not called');
    assert.equal(call.model, cm.model, label + ': model binding for ' + cm.key);
  }
  const tickets = result.iterations.flatMap((it) => it.tickets || []);
  if (expect.any_ticket_state !== undefined) {
    assert.ok(
      tickets.some((t) => t.state === expect.any_ticket_state),
      label + ': expected a ticket in state ' + expect.any_ticket_state + ', got ' + JSON.stringify(tickets.map((t) => t.state))
    );
  }
  if (expect.no_ticket_state !== undefined) {
    assert.ok(!tickets.some((t) => t.state === expect.no_ticket_state), label + ': no ticket may be ' + expect.no_ticket_state);
  }
  if (expect.unreleased_leases !== undefined) {
    assert.equal(result.unreleasedLeases.length, expect.unreleased_leases, label + ': unreleasedLeases');
  }
  for (const [idx, subset] of Object.entries(expect.iteration || {})) {
    const it = result.iterations[Number(idx)];
    assert.ok(it, label + ': iteration ' + idx + ' missing');
    for (const [k, v] of Object.entries(subset)) assert.deepEqual(it[k], v, label + ': iterations[' + idx + '].' + k);
  }
  for (const rb of expect.rebinds || []) {
    const it = result.iterations[rb.iteration_index];
    assert.ok(it, label + ': iteration ' + rb.iteration_index + ' missing for rebind');
    assert.ok(
      it.rebinds.some((r) => r.role === rb.role && r.to === rb.to),
      label + ': expected rebind ' + rb.role + ' -> ' + rb.to + ' in ' + JSON.stringify(it.rebinds)
    );
  }
}

for (const { file, scenario } of loadScenarios()) {
  test('scenario ' + file + ': ' + scenario.name, async () => {
    const allCalls = [];
    const cases = scenario.cases || [{ runs: scenario.runs, expect: scenario.expect }];
    for (const [i, c] of cases.entries()) {
      const outcome = await runCase(c.runs);
      allCalls.push(...outcome.calls);
      applyExpect(c.expect || {}, outcome, file + ' case ' + i);
    }
    const cross = scenario.cross_expect || {};
    for (const [prefix, n] of Object.entries(cross.prefix_counts || {})) {
      assert.equal(
        allCalls.map((c) => c.key).filter((k) => k.startsWith(prefix)).length,
        n,
        file + ': cross-case prefix count ' + prefix
      );
    }
  });
}
