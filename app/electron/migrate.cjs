// KimiLM: one-time move of the user's data from the app's earlier name ("Datavera Agent").
//
// Electron keeps the app's data in %APPDATA%\<product name>. The engine's database, chats' workspaces, models
// (unless moved to another folder in Settings), skills, harnesses, plugins and logs all live there, so the rename
// would otherwise start everyone with an empty app. On first start KimiLM moves that folder to its own name.
// The move is a rename on the same drive: instant, even with many GB of models, and nothing is copied twice.
//
// Rules
// - Runs before anything writes into KimiLM's own data folder (the shell creates logs only afterwards).
// - If KimiLM's folder does not exist yet, the whole old folder is renamed in one step (all or nothing).
// - If it exists (Chromium may create it early, e.g. its lock file), each item is moved that KimiLM's folder does
//   not have yet. If one item can't be moved, the items already moved go back, so nothing is left half-done.
// - If KimiLM already has its own database, nothing is moved: two databases are never merged. (The old database,
//   still under its old file name, in KimiLM's folder means an earlier move was interrupted: it is resumed.)
// - A note is written for the engine (migrated-from.json): it rewrites the old folder's path inside the database
//   (model files, attachments, downloads) on its next start. A short note is left in the old place.
// - Datavera Agent still running (files in use): the person can quit it, let KimiLM close it, or start without
//   the old data (it stays where it is).
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const OLD_NAMES = ["Datavera Agent"];
const OLD_EXE = "Datavera Agent.exe";
const OLD_LOGIN_ITEM = "io.datavera.agent";
const MARKER = "migrated-from.json";
const OWN_DB = "kimilm.sqlite3";
const DB_FILES = [OWN_DB, "datavera-agent.sqlite3"];
const BUSY = new Set(["EBUSY", "EPERM", "EACCES"]);

function hasDb(dir) {
  return DB_FILES.some((f) => fs.existsSync(path.join(dir, f)));
}

function moveItems(oldDir, newDir) {
  if (!fs.existsSync(newDir)) {
    fs.renameSync(oldDir, newDir);
    return { whole: true, items: [] };
  }
  const moved = [];
  try {
    for (const name of fs.readdirSync(oldDir)) {
      const target = path.join(newDir, name);
      if (fs.existsSync(target)) continue;          // KimiLM already has it (e.g. Chromium's own files)
      fs.renameSync(path.join(oldDir, name), target);
      moved.push(name);
    }
  } catch (err) {
    for (const name of moved.reverse()) {
      try { fs.renameSync(path.join(newDir, name), path.join(oldDir, name)); } catch {}
    }
    throw err;
  }
  return { whole: false, items: moved };
}

function closeOldApp() {
  if (process.platform !== "win32") return;
  try {
    // /T also stops what it started (the engine's Python and llama.cpp), so no file stays open.
    execFileSync("taskkill", ["/IM", OLD_EXE, "/T", "/F"], { windowsHide: true, timeout: 20000, stdio: "ignore" });
  } catch {}
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 3000);   // let Windows release the files
}

function leaveNote(oldDir, newDir) {
  try {
    fs.mkdirSync(oldDir, { recursive: true });
    fs.writeFileSync(path.join(oldDir, "MOVED-TO-KimiLM.txt"),
      "Datavera Agent is now called KimiLM.\r\n" +
      `Your chats, models, memory, settings and saved keys were moved to:\r\n${newDir}\r\n\r\n` +
      "Anything still in this folder was not needed by KimiLM. You can delete this folder.\r\n");
  } catch {}
}

// Returns {status, from?, to?, detail?}. status: none | moved | skipped | kept-old | failed.
function migrateUserData({ app, dialog }) {
  const newDir = app.getPath("userData");
  const parent = path.dirname(newDir);
  for (const oldName of OLD_NAMES) {
    const oldDir = path.join(parent, oldName);
    if (path.resolve(oldDir).toLowerCase() === path.resolve(newDir).toLowerCase()) continue;
    if (!fs.existsSync(oldDir)) continue;
    if (fs.existsSync(path.join(newDir, MARKER))) return { status: "none" };
    const resuming = fs.existsSync(path.join(newDir, "datavera-agent.sqlite3"));   // an interrupted earlier move
    if (!hasDb(oldDir) && !resuming) continue;
    if (fs.existsSync(path.join(newDir, OWN_DB))) {
      return { status: "skipped", from: oldDir, to: newDir,
        detail: "KimiLM already has its own data, so the old data was left where it is" };
    }
    for (;;) {
      try {
        const res = moveItems(oldDir, newDir);
        fs.writeFileSync(path.join(newDir, MARKER), JSON.stringify({
          from: oldDir, to: newDir, moved_at: new Date().toISOString(), whole_folder: res.whole,
          items: res.items, paths_rewritten: false,
        }, null, 2));
        leaveNote(oldDir, newDir);
        return { status: "moved", from: oldDir, to: newDir };
      } catch (err) {
        if (!BUSY.has(err.code)) {
          return { status: "failed", from: oldDir, to: newDir, detail: `${err.code || ""} ${err.message}`.trim() };
        }
        const choice = dialog.showMessageBoxSync({
          type: "warning", title: "KimiLM", noLink: true,
          message: "Datavera Agent is still running",
          detail: "KimiLM is the new name of Datavera Agent. It is moving your chats, models, memory and settings " +
            "into KimiLM, but Datavera Agent is still using them.\n\n" +
            "Quit Datavera Agent (right-click its icon near the clock, then Quit) and choose Try again, " +
            "or let KimiLM close it for you. Any task it is running stops.",
          buttons: ["Try again", "Close Datavera Agent for me", "Start without my old data"],
          defaultId: 1, cancelId: 2,
        });
        if (choice === 2) {
          return { status: "kept-old", from: oldDir, to: newDir,
            detail: "Started without the old data; it is still in the Datavera Agent folder" };
        }
        if (choice === 1) closeOldApp();
      }
    }
  }
  return { status: "none" };
}

// Start-with-Windows: the old app's entry would start the old app. Move the setting to KimiLM.
function moveLoginItem(app, wanted) {
  if (process.platform !== "win32") return;
  try { app.setLoginItemSettings({ openAtLogin: false, name: OLD_LOGIN_ITEM }); } catch {}
  if (wanted) {
    try { app.setLoginItemSettings({ openAtLogin: true, args: ["--hidden"] }); } catch {}
  }
}

module.exports = { migrateUserData, moveLoginItem, MARKER };
