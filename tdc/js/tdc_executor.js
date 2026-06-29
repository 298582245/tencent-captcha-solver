/**
 * tdc_executor.js — one-shot stdin/stdout wrapper around tdc_run.js
 */

"use strict";

const { runCollect } = require("./tdc_run");

async function main() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const result = await runCollect(JSON.parse(input));
  process.stdout.write(JSON.stringify(result));
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
