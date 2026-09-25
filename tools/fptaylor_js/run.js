// Headless driver for the js_of_ocaml build of FPTaylor.
//
// FPTaylor ships as OCaml sources; the authors also publish a js_of_ocaml build that powers
// https://monadius.github.io/FPTaylorJS. That build is a Web Worker script, which makes it the
// one way to run FPTaylor on a machine without an OCaml toolchain. This driver feeds the worker
// a task and prints its answer as JSON, so the benchmark harness can call it like a normal CLI.
//
// Setup (once):
//   git clone --depth 1 -b gh-pages https://github.com/monadius/FPTaylorJS fptaylor-js
//   cp fptaylor-js/fptaylor.js  tools/fptaylor_js/fptaylor.js
//   node tools/fptaylor_js/extract_config.js   # writes default_config.txt from the master branch
//
// Usage:
//   node tools/fptaylor_js/run.js <input.fptaylor> [config.cfg]
//
// Prints {"log": [...], "results": [...]} where results[i].errors[j].errorStr is the bound.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const inputFile = process.argv[2];
const configFile = process.argv[3];
if (!inputFile) {
  console.error('usage: node run.js <input.fptaylor> [config.cfg]');
  process.exit(2);
}

const here = __dirname;
const input = fs.readFileSync(inputFile, 'utf8');
const config = configFile ? fs.readFileSync(configFile, 'utf8') : '';
const defaultcfg = fs.readFileSync(path.join(here, 'default_config.txt'), 'utf8');
const bundle = fs.readFileSync(path.join(here, 'fptaylor.js'), 'utf8');

const log = [];
let results = null;

// The bundle refuses to start unless these worker globals already exist.
globalThis.onmessage = null;
globalThis.postMessage = function (data) {
  if (Array.isArray(data)) results = data;
  else log.push(data);
};
globalThis.self = globalThis;
globalThis.importScripts = function () {};

// Under Node the js_of_ocaml runtime switches to the real filesystem device and then fails to
// register its in-memory config files ("cannot register file"). Hiding `process` during init
// makes it take the browser path instead.
const realProcess = globalThis.process;
const out = realProcess.stdout;
const realRequire = globalThis.require;
delete globalThis.process;
delete globalThis.require;
try {
  vm.runInThisContext(bundle, { filename: 'fptaylor.js' });
  if (typeof globalThis.onmessage !== 'function') {
    throw new Error('the bundle did not install an onmessage handler');
  }
  globalThis.onmessage({ data: { input, config, defaultcfg } });
} finally {
  globalThis.process = realProcess;
  globalThis.require = realRequire;
}

out.write(JSON.stringify({ log, results }, null, 1));
