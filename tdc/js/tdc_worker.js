"use strict";

const readline = require("readline");
const { runCollect } = require("./tdc_run");

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
  terminal: false,
});

process.stderr.write("[tdc_worker] ready\n");

rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }
  Promise.resolve()
    .then(() => runCollect(JSON.parse(trimmed)))
    .then((result) => {
      process.stdout.write(JSON.stringify(result) + "\n");
    })
    .catch((err) => {
      process.stdout.write(
        JSON.stringify({
          error: String(err && err.message ? err.message : err),
        }) + "\n"
      );
    });
});
