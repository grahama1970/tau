// Directory entry so `node --test workflows/test/` works on Node 22: the CJS
// loader resolves the positional directory to index.js, which hands off to the
// ESM harness (node:test collects the tests the import registers).
import('./dev-cycle-harness.test.mjs').catch(function (e) {
  setImmediate(function () { throw e; });
});
