// One path each way between a phone and a TV: on the TV every key (its own remote's, a media key, a phone's) goes
// through tvDo, and on the phone every command is posted by tvSend.   node tests/test_tv_paths.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const js = (f) => fs.readFileSync(path.join(__dirname, "..", "app", "static", "js", f), "utf8");
const tv = js("tv.js"), remote = js("remote.js");
const body = (src, start) => { // the text of the block that opens after `start`
  const from = src.indexOf(start);
  assert.ok(from >= 0, start);
  let depth = 0, i = src.indexOf("{", from + start.length - 1);
  for (const open = i; i < src.length; i++) { if (src[i] === "{") depth++; else if (src[i] === "}" && !--depth) return src.slice(open, i + 1); }
};
const keys = body(tv, 'document.addEventListener("keydown", (e) => {');
assert.ok((keys.match(/tvDo\(/g) || []).length === 3, "arrows, Back and the media keys each call tvDo");
assert.ok(!/tvMove\(|tvBack\(|\.pause\(|tvPlay\(/.test(keys), "the key handler acts only through tvDo");
const calls = (src, re) => [...src.matchAll(re)].length;
assert.strictEqual(calls(tv.replace(body(tv, "function tvDo(m) {"), ""), /\btvBack\(\)/g), 1, "tvBack is called only inside tvDo (its own definition is the one left)");
const sends = [...remote.matchAll(/post\(`\/api\/tv\/\$\{[^}]+\}\/do/g)];
assert.strictEqual(sends.length, 1, "one place posts a command to a TV");
assert.ok(body(remote, "function tvSend(id, body, all = \"\") {").includes(sends[0][0]), "and it is tvSend");
for (const f of fs.readdirSync(path.join(__dirname, "..", "app", "static", "js"))) if (f !== "remote.js") assert.ok(!/\/api\/tv\/\$\{[^}]+\}\/do/.test(js(f)), `${f} sends through tvSend`);
console.log("tv paths ok");
