// Gathers everything the installer ships besides the Electron app itself, into app/resources/:
//   engine/  the Python engine + built-in harnesses and skills (no plugin is built in since 2.15.0)
//   python/  Python 3.12 with the engine's packages pre-installed: the embeddable build on Windows, a
//            relocatable python-build-standalone build on Linux (2.22.0)
//   llama/cpu/  the llama.cpp CPU build (GPU builds are downloaded from inside the app when wanted)
// Usage: node scripts/prepare-resources.mjs [--platform win|linux] [--skip-python] [--skip-llama]
// The Linux Python is installed with its own pip, so --platform linux must run on Linux of the same architecture.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import extract from "extract-zip";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "..");
const repo = path.resolve(app, "..");
const engineSrc = path.join(repo, "engine");
const out = path.join(app, "resources");
const args = process.argv.slice(2);
const platform = args.includes("--platform") ? args[args.indexOf("--platform") + 1] : (process.platform === "win32" ? "win" : process.platform);
const versions = JSON.parse(fs.readFileSync(path.join(engineSrc, "dva", "versions.json"), "utf8"));
const cache = path.join(app, ".cache");
fs.mkdirSync(cache, { recursive: true });

function step(msg) { console.log(`\n== ${msg}`); }

function copyDir(src, dest, skip = (p) => false) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    if (skip(s, entry)) continue;
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDir(s, d, skip);
    else fs.copyFileSync(s, d);
  }
}

async function download(url, dest) {
  if (fs.existsSync(dest)) { console.log(`   cached ${path.basename(dest)}`); return dest; }
  console.log(`   downloading ${url}`);
  const r = await fetch(url, { headers: { "User-Agent": "kimilm-build" } });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  const buf = Buffer.from(await r.arrayBuffer());
  fs.writeFileSync(dest + ".part", buf);
  fs.renameSync(dest + ".part", dest);
  return dest;
}

function findPython() {
  const tries = process.platform === "win32" ? [["py", ["-3.12"]], ["python", []]] : [["python3.12", []], ["python3", []]];
  for (const [cmd, pre] of tries) {
    try {
      const v = execFileSync(cmd, [...pre, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"]).toString().trim();
      if (v === "3.12") return [cmd, pre];
    } catch {}
  }
  throw new Error("Python 3.12 is needed to prepare the installer (it installs the engine's packages). Install it from python.org or with: winget install Python.Python.3.12");
}

async function engine() {
  step("Engine");
  const dest = path.join(out, "engine");
  fs.rmSync(dest, { recursive: true, force: true });
  const skip = (p, e) => e.name === "__pycache__" || e.name === "tests" || e.name === ".venv" || e.name.endsWith(".pyc") || e.name === "pipeline";
  copyDir(path.join(engineSrc, "dva"), path.join(dest, "dva"), skip);
  copyDir(path.join(engineSrc, "builtin"), path.join(dest, "builtin"), skip);
  fs.copyFileSync(path.join(engineSrc, "requirements.txt"), path.join(dest, "requirements.txt"));
  console.log(`   engine copied to ${dest}`);
}

async function pythonLinux() {
  step("Python for Linux (python-build-standalone)");
  const dest = path.join(out, "python");
  if (process.platform !== "linux") throw new Error("The Linux Python must be prepared on Linux (its pip installs the engine's packages).");
  const arch = { x64: "x86_64", arm64: "aarch64" }[process.arch];
  const ps = versions.python_standalone;
  const sha = arch && ps.sha256[arch];
  if (!sha) throw new Error(`No pinned Python for ${process.arch}`);
  const name = `cpython-${ps.version}+${ps.tag}-${arch}-unknown-linux-gnu-install_only.tar.gz`;
  const archive = await download(`https://github.com/${ps.repo}/releases/download/${ps.tag}/${encodeURIComponent(name)}`, path.join(cache, name));
  const got = crypto.createHash("sha256").update(fs.readFileSync(archive)).digest("hex");
  if (got !== sha) { fs.rmSync(archive, { force: true }); throw new Error(`${name}: checksum ${got} does not match the pinned ${sha}`); }
  fs.rmSync(dest, { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });
  execFileSync("tar", ["-xzf", archive, "-C", dest, "--strip-components=1"], { stdio: "inherit" });   // the archive's top folder is python/
  const py = path.join(dest, "bin", "python3");
  console.log("   installing engine packages (a few minutes)…");
  execFileSync(py, ["-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--only-binary=:all:",
    "-r", path.join(engineSrc, "requirements.txt")], { stdio: "inherit", env: { ...process.env, PYTHONNOUSERSITE: "1" } });
  // The installed app is read-only (KimiOS puts it in /usr): compile now, or every start compiles in memory.
  execFileSync(py, ["-m", "compileall", "-q", "-j", "0", path.join(dest, "lib"), path.join(out, "engine")], { stdio: "inherit" });
  console.log(`   Python ${ps.version} ready in ${dest}`);
}

async function python() {
  if (platform === "linux") return pythonLinux();
  step("Embedded Python");
  const dest = path.join(out, "python");
  if (platform !== "win") { console.log("   skipped (only the Windows and Linux builds bundle Python; dev uses engine/.venv)"); return; }
  const v = versions.python_embed;
  const zip = await download(`https://www.python.org/ftp/python/${v}/python-${v}-embed-amd64.zip`, path.join(cache, `python-${v}-embed-amd64.zip`));
  fs.rmSync(dest, { recursive: true, force: true });
  await extract(zip, { dir: dest });
  const pth = fs.readdirSync(dest).find((f) => f.endsWith("._pth"));
  const zipName = fs.readdirSync(dest).find((f) => /^python\d+\.zip$/.test(f));
  fs.writeFileSync(path.join(dest, pth), [zipName, ".", "Lib\\site-packages", "..\\engine", "import site", ""].join("\r\n"));
  const site = path.join(dest, "Lib", "site-packages");
  fs.mkdirSync(site, { recursive: true });
  const [cmd, pre] = findPython();
  console.log("   installing engine packages for Windows / Python 3.12 (a few minutes)…");
  execFileSync(cmd, [...pre, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
    "--target", site, "--platform", "win_amd64", "--python-version", "3.12", "--implementation", "cp",
    "--only-binary=:all:", "-r", path.join(engineSrc, "requirements.txt")], { stdio: "inherit" });
  console.log(`   Python ready in ${dest}`);
}

async function llama() {
  step(`llama.cpp ${versions.llama_cpp_tag} (CPU build)`);
  const key = platform === "win" ? "windows" : (platform === "darwin" ? "darwin" : "linux");
  const backend = key === "darwin" ? "metal" : "cpu";
  const pats = versions.backend_assets[key][backend];
  const api = `https://api.github.com/repos/${versions.llama_cpp_repo}/releases`;
  const headers = { "User-Agent": "kimilm-build", Accept: "application/vnd.github+json",
    ...(process.env.GITHUB_TOKEN ? { Authorization: `Bearer ${process.env.GITHUB_TOKEN}` } : {}) };
  const get = async (url) => {
    const r = await fetch(url, { headers });
    const body = await r.json();
    if (!r.ok) throw new Error(`GitHub API ${r.status} for ${url}: ${body.message || ""}`);
    return body;
  };
  // Windows and macOS builds are .zip; Linux builds are .tar.gz.
  const ext = key === "linux" ? ".tar.gz" : ".zip";
  const match = (rel) => (rel.assets || []).find((a) => pats.every((p) => a.name.includes(p)) && a.name.endsWith(ext) && !a.name.startsWith("cudart"));
  // The archive of the pinned release, once downloaded, is used again without asking GitHub: its API allows 60
  // questions an hour per internet address without a token (addresses are often shared), and a build that stops
  // there after ten minutes of other work helps nobody. A token in GITHUB_TOKEN lifts the limit.
  const tag = versions.llama_cpp_tag;
  const fits = (name) => pats.every((p) => name.includes(p)) && name.endsWith(ext) && !name.startsWith("cudart");
  const kept = fs.readdirSync(cache).find((n) => n.includes(`-${tag}-`) && fits(n));
  let zip, name;
  if (kept) {
    console.log(`   cached ${kept} (GitHub was not asked)`);
    zip = path.join(cache, kept);
    name = kept;
  } else {
    let asset = null;
    try {
      const pinned = await get(`${api}/tags/${tag}`).catch((e) => { console.warn(`   ${e.message}`); return null; });
      asset = pinned ? match(pinned) : null;
      if (pinned && !asset) {
        console.warn(`   llama.cpp ${tag} has no file matching ${pats.join(" + ")} (it lists: ${(pinned.assets || []).map((a) => a.name).join(", ") || "nothing"}).`);
        for (const rel of await get(`${api}?per_page=30`)) {
          asset = match(rel);
          if (asset) { console.warn(`   using the newest release that has it: ${rel.tag_name}. Consider setting llama_cpp_tag to ${rel.tag_name} in engine/dva/versions.json.`); break; }
        }
      }
    } catch (e) { console.warn(`   ${e.message}`); }
    if (asset) {
      name = asset.name;
      zip = await download(asset.browser_download_url, path.join(cache, name));
    } else if (pats.length === 1) {
      // GitHub's API could not be asked. The file of a release has a fixed address, which needs no API.
      name = `llama-${tag}-${pats[0]}${ext}`;
      console.warn(`   GitHub's API gave no answer: fetching ${name} by its address instead.`);
      zip = await download(`https://github.com/${versions.llama_cpp_repo}/releases/download/${tag}/${name}`, path.join(cache, name));
    } else {
      throw new Error(`No llama.cpp release with a file matching ${pats.join(" + ")} could be found (GitHub's API may have refused: wait an hour, or set GITHUB_TOKEN).`);
    }
  }
  const dest = path.join(out, "llama", backend);
  fs.rmSync(dest, { recursive: true, force: true });
  const tmp = dest + ".tmp";
  fs.rmSync(tmp, { recursive: true, force: true });
  if (ext === ".tar.gz") { fs.mkdirSync(tmp, { recursive: true }); execFileSync("tar", ["-xzf", zip, "-C", tmp], { stdio: "inherit" }); }
  else await extract(zip, { dir: tmp });
  const exe = process.platform === "win32" || platform === "win" ? "llama-server.exe" : "llama-server";
  const find = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isFile() && e.name === exe) return dir;
      if (e.isDirectory()) { const f = find(p); if (f) return f; }
    }
    return null;
  };
  const folder = find(tmp);
  if (!folder) throw new Error("llama-server was not found in the download");
  fs.renameSync(folder, dest);
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`   ${name} → ${dest}`);
}

fs.mkdirSync(out, { recursive: true });
await engine();
if (!args.includes("--skip-python")) await python();
if (!args.includes("--skip-llama")) await llama();
for (const d of ["python", "llama"]) fs.mkdirSync(path.join(out, d), { recursive: true });
console.log("\nResources ready in app/resources");
