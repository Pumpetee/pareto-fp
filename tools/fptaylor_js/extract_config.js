// Turns FPTaylorJS's src/default_config.js (a JS template literal) into the plain text file
// the worker expects as `defaultcfg`. Run once after cloning FPTaylorJS.
//
//   node tools/fptaylor_js/extract_config.js path/to/FPTaylorJS/src/default_config.js
'use strict';

const fs = require('fs');
const path = require('path');

const src = process.argv[2];
if (!src) {
  console.error('usage: node extract_config.js <FPTaylorJS/src/default_config.js>');
  process.exit(2);
}

let s = fs.readFileSync(src, 'utf8');
s = s.replace(/^export const default_config = `/, '').replace(/`;?\s*$/, '');
s = s.replace(/\\`/g, '`');

const out = path.join(__dirname, 'default_config.txt');
fs.writeFileSync(out, s);
console.log(`${out}: ${s.length} bytes`);
