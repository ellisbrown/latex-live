// The viewer's ES modules, parsed and linked as a browser would link them, without running any: static.py runs this
// with Node (node --experimental-vm-modules link.mjs), sending on stdin {root, imports, roots, inline}: the checkout,
// the import map of viewer.html, the modules to start from (paths relative to root), and viewer.html's own module
// code, if any. A module that doesn't parse, an import that resolves to no file, and a name imported that its module
// doesn't export are problems, each said once, of the module that has it. It prints {modules, problems}: how many
// modules parsed, and what is wrong.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const { root, imports, roots, inline } = JSON.parse(fs.readFileSync(0, "utf8"));
const problems = [];
const loaded = new Map(); // (each module once, by its file: its SourceTextModule, or null if it can't be read)
const linked = new Map(); // (by file: whether it linked, or null while under way: a cycle links as one)
const context = vm.createContext({});
const rel = (file) => path.relative(root, file);

if (typeof vm.SourceTextModule !== "function") {
  console.log(JSON.stringify({ modules: 0, problems: [`node ${process.version} has no vm.SourceTextModule (run with --experimental-vm-modules)`] }));
  process.exit(0);
}

// A module's file from what imports it: relative to the importing file, or a bare name through the import map (an
// exact name, else the longest name ending in / that starts it), whose targets are relative to viewer.html (root).
function resolve(spec, from) {
  const target = (to) => path.resolve(root, to.split(/[?#]/)[0]);
  if (/^\.\.?\//.test(spec)) return path.resolve(path.dirname(from), spec.split(/[?#]/)[0]);
  if (Object.prototype.hasOwnProperty.call(imports, spec)) return target(imports[spec]);
  const prefix = Object.keys(imports).filter((k) => k.endsWith("/") && spec.startsWith(k)).sort((a, b) => b.length - a.length)[0];
  return prefix ? target(imports[prefix] + spec.slice(prefix.length)) : null;
}

// The module in file (parsed once), or null, with the problem said, if it can't be read or doesn't parse.
function load(file, code) {
  if (!loaded.has(file)) {
    let module = null, source;
    try {
      source = code ?? fs.readFileSync(file, "utf8");
      module = new vm.SourceTextModule(source, { identifier: rel(file), context });
    } catch (e) {
      if (source === undefined) problems.push(`${rel(file)} is not a file here`);
      else problems.push(`${rel(file)} does not parse: ${where(source) || e.message}`);
    }
    loaded.set(file, module);
  }
  return loaded.get(file);
}

// Where Node's own check of a module (node --check) finds a syntax error in source, and what it is, in a few lines.
function where(source) {
  const r = spawnSync(process.execPath, ["--input-type=module", "--check"], { input: source, encoding: "utf8" });
  return r.stderr.split("\n").filter((line) => line.trim()).slice(0, 4).join("\n").replace(/^\[stdin\]:/, "line ");
}

// Link file's module after the modules it imports, so that a failure is its own imports', unless one of those
// failed (said there): whether it linked.
async function link(file, code) {
  const module = load(file, code);
  if (!module) return false;
  if (linked.has(file)) return linked.get(file) ?? true;
  linked.set(file, null);
  let ok = true;
  for (const spec of module.dependencySpecifiers) {
    const dep = resolve(spec, file);
    if (!dep) problems.push(`${rel(file)} imports ${spec}, which the import map does not map`);
    if (!dep || !(await link(dep))) ok = false;
  }
  if (ok && module.status === "unlinked") {
    try {
      await module.link((spec) => load(resolve(spec, file)));
    } catch (e) {
      problems.push(`${rel(file)}: ${e.message}`);
      ok = false;
    }
  }
  linked.set(file, ok);
  return ok;
}

if (inline) await link(path.join(root, "viewer.html"), inline);
for (const name of roots) await link(path.resolve(root, name));
console.log(JSON.stringify({ modules: [...loaded.values()].filter(Boolean).length, problems }));
