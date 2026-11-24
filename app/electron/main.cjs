// KimiLM — desktop shell.
// Starts the local engine (Python) with a random token, opens the window on it, keeps running in the tray so
// chats, long tasks and schedules continue when the window is closed, and can start with Windows.
const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, shell, Notification, nativeImage, session, powerSaveBlocker, desktopCapturer } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

// 2.37.0: settings were called DVA_* before (DVA_PYTHON, DVA_NO_SLICE, …); a setting given by its old name still works
for (const [k, v] of Object.entries(process.env)) {
  if (k.startsWith("DVA_") && process.env["KIMILM_" + k.slice(4)] === undefined) process.env["KIMILM_" + k.slice(4)] = v;
}

const isDev = !app.isPackaged;
const token =crypto.randomBytes(24).toString("base64url");
let engine = null;
let enginePort = null;
let win = null;
let tray = null;
const kimiosSession = process.env.KIMIOS_SESSION === "1";   // started by KimiOS's session (kimios-session)
let quitting = false;
let restarts = 0;
let awake = null;     // id of the power-save blocker while tasks are running (2.9.0)

const userData = app.getPath("userData");
const prefsPath = path.join(userData, "shell-prefs.json");
const logsDir = path.join(userData, "logs");

function prefs() {
  try { return { keepInTray: true, openAtLogin: false, startHidden: false, keepAwake: true, ...JSON.parse(fs.readFileSync(prefsPath, "utf8")) }; }
  catch { return { keepInTray: true, openAtLogin: false, startHidden: false, keepAwake: true }; }
}
function savePrefs(p) { fs.writeFileSync(prefsPath, JSON.stringify({ ...prefs(), ...p }, null, 2)); }

// 2.21.0: the size of everything in the window, like a browser's zoom: text, spacing and layout grow together, so
// nothing breaks. Ctrl + / Ctrl - / Ctrl 0, Ctrl + the mouse wheel, or Settings. Kept in shell-prefs.json (the
// engine's address changes at each start, and Chromium keeps zoom per address).
const ZOOMS = [0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
function zoomFactor() {
  const z = Number(prefs().zoom);
  return Number.isFinite(z) && z > 0 ? Math.min(2, Math.max(0.5, z)) : 1;
}
function setZoom(z) {
  z = Math.round(Math.min(2, Math.max(0.5, Number(z) || 1)) * 100) / 100;
  savePrefs({ zoom: z });
  if (win && !win.isDestroyed()) {
    win.webContents.setZoomFactor(z);
    win.webContents.send("kimilm:zoom", z);
  }
  return z;
}
function stepZoom(dir) {
  if (!dir) return setZoom(1);
  const cur = zoomFactor();
  const next = dir > 0 ? ZOOMS.find((x) => x > cur + 0.001) : [...ZOOMS].reverse().find((x) => x < cur - 0.001);
  return setZoom(next || cur);
}

function resources() {
  return isDev ? path.join(__dirname, "..", "resources") : process.resourcesPath;
}

// Which Python runs the engine: KIMILM_PYTHON, then the installer's own embedded Python, then engine\.venv
// (development), then a Python 3.12 already installed on this computer that has the engine's packages.
function pythonPath() {
  if (process.env.KIMILM_PYTHON) return process.env.KIMILM_PYTHON;
  const bundled = path.join(resources(), "python", process.platform === "win32" ? "python.exe" : "bin/python3");
  if (fs.existsSync(bundled)) return bundled;
  const venv = path.join(__dirname, "..", "..", "engine", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (fs.existsSync(venv)) return venv;
  const { execFileSync } = require("child_process");
  const tries = process.platform === "win32" ? [["py", ["-3.12"]], ["python", []]] : [["python3.12", []], ["python3", []]];
  const check = "import sys, fastapi, uvicorn, fastmcp; assert sys.version_info[:2] == (3, 12); print(sys.executable)";
  for (const [cmd, pre] of tries) {
    try {
      const exe = execFileSync(cmd, [...pre, "-c", check], { windowsHide: true, timeout: 20000 }).toString().trim();
      if (exe) { log(`using installed Python: ${exe}`); return exe; }
    } catch {}
  }
  throw new Error("No Python found for the engine. Install the app with its installer, or for development run:\n" +
    "cd engine\npy -3.12 -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt");
}

function engineDir() {
  const bundled = path.join(resources(), "engine");
  return fs.existsSync(path.join(bundled, "kimilm")) ? bundled : path.join(__dirname, "..", "..", "engine");
}

function playwrightCli() {
  const candidates = [
    path.join(process.resourcesPath || "", "app.asar.unpacked", "node_modules", "@playwright", "mcp", "cli.js"),
    path.join(__dirname, "..", "node_modules", "@playwright", "mcp", "cli.js"),
  ];
  return candidates.find((p) => fs.existsSync(p)) || "";
}

function log(line) {
  try {
    fs.mkdirSync(logsDir, { recursive: true });
    fs.appendFileSync(path.join(logsDir, "shell.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {}
}

// Linux (2.22.0): the engine, and every model server it starts, runs in a systemd scope in kimilm.slice with memory
// limits. A model that runs away is then killed by the kernel instead of taking the computer down. KimiOS defines
// kimilm.slice with its own limits; elsewhere systemd creates the slice on the fly. KIMILM_NO_SLICE=1 turns this off,
// and a scope that cannot be made (no systemd user session) falls back to a plain start once.
let sliceFailed = false;
function sliceWrap(cmd, args) {
  if (process.platform !== "linux" || process.env.KIMILM_NO_SLICE === "1" || sliceFailed) return null;
  const rt = process.env.XDG_RUNTIME_DIR;
  if (!rt || !(fs.existsSync(path.join(rt, "systemd", "private")) || fs.existsSync(path.join(rt, "bus")))) return null;
  const systemdRun = ["/usr/bin/systemd-run", "/bin/systemd-run"].find((p) => fs.existsSync(p));
  if (!systemdRun) return null;
  return [systemdRun, ["--user", "--scope", "--quiet", "--collect", "--slice=kimilm.slice",
    "--description=KimiLM engine and models", "-p", "MemoryHigh=70%", "-p", "MemoryMax=85%", "--", cmd, ...args]];
}

function startEngine() {
  return new Promise((resolve, reject) => {
    const uiDir = isDev ? "" : path.join(process.resourcesPath, "ui");
    const env = {
      ...process.env,
      KIMILM_TOKEN: token,
      KIMILM_UI_DIR: uiDir,
      KIMILM_NODE: process.execPath,
      KIMILM_NODE_IS_ELECTRON: "1",
      KIMILM_ELECTRON_APP: isDev ? path.join(__dirname, "..") : "",    // 2.35.0: how to start this shell again (previews)
      KIMILM_PLAYWRIGHT_MCP: playwrightCli(),
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONNOUSERSITE: "1",
      NO_PROXY: "127.0.0.1,localhost",
    };
    const port = process.env.KIMILM_ENGINE_PORT || "0";
    const args = ["-m", "kimilm", "serve", "--port", port, "--data-dir", userData, "--resources", resources()];
    let python;
    try { python = pythonPath(); } catch (err) { reject(err); return; }
    const wrapped = sliceWrap(python, args);
    log(`starting engine: ${wrapped ? `${wrapped[0]} ${wrapped[1].join(" ")}` : `${python} ${args.join(" ")}`}`);
    engine = wrapped ? spawn(wrapped[0], wrapped[1], { cwd: engineDir(), env, windowsHide: true })
                     : spawn(python, args, { cwd: engineDir(), env, windowsHide: true });
    const out = fs.createWriteStream(path.join(logsDir, "engine-console.log"), { flags: "a" });
    let ready = false;
    const timer = setTimeout(() => { if (!ready) reject(new Error("The engine did not start within 90 seconds")); }, 90000);
    engine.stdout.on("data", (buf) => {
      const text = buf.toString();
      out.write(text);
      const m = text.match(/KIMILM_READY (\d+)/);
      if (m && !ready) { ready = true; enginePort = Number(m[1]); clearTimeout(timer); resolve(enginePort); }
    });
    engine.stderr.on("data", (buf) => out.write(buf));
    engine.on("exit", (code) => {
      log(`engine exited with ${code}`);
      engine = null;
      if (!ready && wrapped && !sliceFailed) {
        sliceFailed = true;
        clearTimeout(timer);
        log("the memory-limited scope could not start the engine; starting it without (see logs/engine-console.log)");
        startEngine().then(resolve, reject);
        return;
      }
      if (!ready) { clearTimeout(timer); reject(new Error(`The engine stopped while starting (code ${code}). See logs/engine-console.log`)); return; }
      if (!quitting && restarts < 3) {
        restarts += 1;
        setTimeout(() => startEngine().then(() => win && win.loadURL(appUrl())).catch(showFatal), 1500);
      }
    });
  });
}

function appUrl() {
  return isDev ? "http://localhost:5173/" : `http://127.0.0.1:${enginePort}/`;
}

function iconPath() {
  const p = path.join(__dirname, "..", "build", process.platform === "win32" ? "icon.ico" : "icon.png");
  return fs.existsSync(p) ? p : path.join(__dirname, "..", "build", "icon.png");
}

function createWindow(show = true) {
  win = new BrowserWindow({
    width: 1320, height: 860, minWidth: 900, minHeight: 600, show: false, title: "KimiLM",
    backgroundColor: "#F7F5F2", icon: iconPath(), autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.loadURL(appUrl());
  win.webContents.on("did-finish-load", () => win.webContents.setZoomFactor(zoomFactor()));
  win.webContents.on("zoom-changed", (_e, dir) => stepZoom(dir === "in" ? 1 : -1));     // Ctrl + the mouse wheel
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown" || !(input.control || input.meta) || input.alt) return;
    const k = input.key;
    const dir = k === "=" || k === "+" ? 1 : k === "-" || k === "_" ? -1 : k === "0" ? 0 : null;
    if (dir === null) return;
    e.preventDefault();
    stepZoom(dir);
  });
  win.once("ready-to-show", () => { if (show) win.show(); });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  // 2.26.0: a right click anywhere offers what Ctrl+C, Ctrl+X and Ctrl+V do: Copy for selected text (also in a
  // reply, a log or a table), Cut and Paste in a text box, Copy link for links, Copy image for pictures.
  win.webContents.on("context-menu", (_e, p) => {
    const items = [];
    const sel = (p.selectionText || "").trim().length > 0;
    if (p.isEditable) {
      items.push({ role: "cut", label: "Cut", enabled: sel && p.editFlags.canCut });
      items.push({ role: "copy", label: "Copy", enabled: sel && p.editFlags.canCopy });
      items.push({ role: "paste", label: "Paste", enabled: p.editFlags.canPaste });
      items.push({ type: "separator" }, { role: "selectAll", label: "Select all" });
    } else if (sel) {
      items.push({ role: "copy", label: "Copy" });
    }
    if (p.linkURL && /^https?:|^mailto:/.test(p.linkURL)) {
      if (items.length) items.push({ type: "separator" });
      items.push({ label: "Copy link", click: () => require("electron").clipboard.writeText(p.linkURL) });
      items.push({ label: "Open link", click: () => shell.openExternal(p.linkURL) });
    }
    if (p.mediaType === "image" && p.hasImageContents) {
      if (items.length) items.push({ type: "separator" });
      items.push({ label: "Copy image", click: () => win.webContents.copyImageAt(p.x, p.y) });
    }
    if (!p.isEditable && !items.length) items.push({ role: "selectAll", label: "Select all" });
    Menu.buildFromTemplate(items).popup({ window: win });
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(appUrl())) { e.preventDefault(); shell.openExternal(url); }
  });
  win.on("focus", () => { try { win.flashFrame(false); } catch {} });   // 2.29.0: seen
  win.on("close", (e) => {
    // In the KimiOS session (2.23.0) KimiLM is the whole desktop and there is no tray to come back from: closing
    // the window quits, and the session returns to the login screen.
    if (kimiosSession) { quitting = true; return; }
    if (!quitting && prefs().keepInTray) {
      e.preventDefault();
      win.hide();
      if (!prefs().trayHintShown && Notification.isSupported()) {
        new Notification({ title: "KimiLM is still running", body: "Chats, tasks and schedules continue in the background. Use the tray icon to open or quit." }).show();
        savePrefs({ trayHintShown: true });
      }
    }
  });
}

function showWindow() {
  if (!win) createWindow(true);
  else { win.show(); win.focus(); }
}

function createTray() {
  const image = nativeImage.createFromPath(iconPath()).resize({ width: 16, height: 16 });
  tray = new Tray(image);
  tray.setToolTip("KimiLM");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open KimiLM", click: showWindow },
    { type: "separator" },
    { label: "Quit (stops tasks and schedules)", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", showWindow);
}

// While anything is running (a reply, a planned run, a scheduled or long-running task) Windows is asked not to
// put the computer to sleep, so hours of work are not cut off halfway. The screen may still turn off, and closing
// a laptop's lid still does what Windows is set to do. The tray's tooltip says how many tasks are running.
function applyBusy(busy) {
  const active = Number(busy && busy.active) || 0;
  const want = active > 0 && prefs().keepAwake !== false;
  try {
    if (want && awake === null) { awake = powerSaveBlocker.start("prevent-app-suspension"); log("tasks are running: keeping the computer awake"); }
    if (!want && awake !== null) { powerSaveBlocker.stop(awake); awake = null; log("no task is running: the computer may sleep again"); }
    if (tray) tray.setToolTip(active ? `KimiLM — ${active} task${active === 1 ? "" : "s"} running${busy.waiting ? `, ${busy.waiting} waiting for you` : ""}` : "KimiLM");
  } catch (err) { log(`keeping awake: ${err && err.message || err}`); }
}

function watchWork() {
  setInterval(() => {
    if (!engine || !enginePort || quitting) return;
    const req = http.request({ host: "127.0.0.1", port: enginePort, path: "/api/busy", method: "GET",
      headers: { Authorization: `Bearer ${token}` }, timeout: 4000 }, (res) => {
      let data = "";
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => { try { applyBusy(JSON.parse(data)); } catch {} });
    });
    req.on("error", () => {});
    req.on("timeout", () => req.destroy());
    req.end();
  }, 15000);
}

function showFatal(err) {
  log(`fatal: ${err && err.stack || err}`);
  dialog.showErrorBox("KimiLM could not start", `${err.message || err}\n\nLogs: ${logsDir}`);
  quitting = true;
  app.quit();
}

function stopEngine() {
  return new Promise((resolve) => {
    if (!engine || !enginePort) return resolve();
    const req = http.request({ host: "127.0.0.1", port: enginePort, path: "/api/shutdown", method: "POST",
      headers: { Authorization: `Bearer ${token}` }, timeout: 4000 }, () => resolve());
    req.on("error", () => resolve());
    req.on("timeout", () => { req.destroy(); resolve(); });
    req.end();
    setTimeout(() => { if (engine) { try { engine.kill(); } catch {} } resolve(); }, 6000);
  });
}

// ------------------------------------------------------------------ apps in their own windows (2.34.0)
// `kimilm --open-app=<id>` (the computer's apps menu, KimiOS's launch_app) opens one app in a window of its own; a
// second start of KimiLM hands its arguments to this one.
const appWindows = new Map();
function isAppFrame(url) {
  try { const u = new URL(url); return u.pathname.startsWith("/appframe/") && (u.hostname === "127.0.0.1" || u.hostname === "localhost"); }
  catch { return false; }
}
function appFromArgs(argv) {
  const hit = (argv || []).find((a) => typeof a === "string" && a.startsWith("--open-app="));
  const id = hit ? hit.slice("--open-app=".length) : "";
  return /^[a-z0-9][a-z0-9-]{0,47}$/.test(id) ? id : "";
}
function openAppWindow(id) {
  if (!id || !enginePort) return;
  const open = appWindows.get(id);
  if (open && !open.isDestroyed()) { open.show(); open.focus(); return; }
  const w = new BrowserWindow({
    width: 1180, height: 800, minWidth: 480, minHeight: 360, show: false, title: "KimiLM app", backgroundColor: "#101318",
    icon: iconPath(), autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const base = appUrl();
  w.loadURL(`${base}?app=${encodeURIComponent(id)}`);
  w.webContents.on("did-finish-load", () => w.webContents.setZoomFactor(zoomFactor()));
  w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  w.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(base)) e.preventDefault(); });
  w.once("ready-to-show", () => w.show());
  w.on("closed", () => appWindows.delete(id));
  appWindows.set(id, w);
}

// The computer's own apps menu: a desktop entry (Linux, KimiOS: it also lands in KimiOS's software registry, so
// agents can open it with launch_app) or a Start menu shortcut (Windows).
function launcher() {
  if (process.env.APPIMAGE) return { exe: process.env.APPIMAGE, pre: [] };
  if (!app.isPackaged) return { exe: process.execPath, pre: [path.join(__dirname, "..")] };
  if (process.platform === "linux" && fs.existsSync("/usr/bin/kimilm") && process.execPath.startsWith("/usr/lib/kimilm")) return { exe: "/usr/bin/kimilm", pre: [] };
  return { exe: process.execPath, pre: [] };
}
function desktopEntry(a, exe, pre, icon) {
  const id = String(a.id);
  const line = (x) => String(x || "").replace(/[\r\n]+/g, " ");
  const q = (x) => (/[\s"'\\$`]/.test(x) ? `"${x.replace(/(["\\$`])/g, "\\$1")}"` : x);
  const about = a.about || (a.kind === "game" ? "A game made in KimiLM" : "A control panel made in KimiLM");
  return ["[Desktop Entry]", "Type=Application", `Name=${line(a.name || id).slice(0, 60)}`, `Comment=${line(about).slice(0, 200)}`,
    `Exec=${[exe, ...pre].map(q).join(" ")} --open-app=${id}`, `Icon=${icon}`, "Terminal=false",
    `Categories=${a.kind === "game" ? "Game;" : "Utility;"}`, "StartupWMClass=KimiLM", `X-KimiLM-App=${id}`,
    `X-KimiLM-Kind=${a.kind === "game" ? "game" : "device"}`, ""].join("\n");
}
function appShortcut(a) {
  const id = String((a && a.id) || "");
  if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(id)) return { ok: false, reason: "not an app" };
  const name = String(a.name || id).replace(/[\r\n]+/g, " ").slice(0, 60);
  const { exe, pre } = launcher();
  try {
    if (process.platform === "linux") {
      const data = process.env.XDG_DATA_HOME || path.join(app.getPath("home"), ".local", "share");
      const entry = path.join(data, "applications", `kimilm-app-${id}.desktop`);
      const icon = path.join(data, "icons", "hicolor", "scalable", "apps", `kimilm-app-${id}.svg`);
      if (!a.add) { for (const f of [entry, icon]) fs.rmSync(f, { force: true }); return { ok: true }; }
      fs.mkdirSync(path.dirname(entry), { recursive: true });
      fs.mkdirSync(path.dirname(icon), { recursive: true });
      if (a.icon && String(a.icon).startsWith("<svg")) fs.writeFileSync(icon, String(a.icon));
      fs.writeFileSync(entry, desktopEntry(a, exe, pre, fs.existsSync(icon) ? icon : "kimilm"));
      return { ok: true, path: entry };
    }
    if (process.platform === "win32") {
      const dir = path.join(app.getPath("appData"), "Microsoft", "Windows", "Start Menu", "Programs", "KimiLM apps");
      const link = path.join(dir, `${name.replace(/[<>:"/\\|?*]/g, "_")}.lnk`);
      if (!a.add) { fs.rmSync(link, { force: true }); return { ok: true }; }
      fs.mkdirSync(dir, { recursive: true });
      const ok = shell.writeShortcutLink(link, { target: exe, args: [...pre.map((x) => `"${x}"`), `--open-app=${id}`].join(" "),
        description: name, icon: process.execPath, iconIndex: 0, appUserModelId: "com.kimilm.app" });
      return ok ? { ok: true, path: link } : { ok: false, reason: "Windows did not take the shortcut" };
    }
    return { ok: false, reason: "On this system, open apps from KimiLM's Apps" };
  } catch (err) { log(`app shortcut: ${err.message}`); return { ok: false, reason: err.message }; }
}
ipcMain.handle("kimilm:open-app", (_e, id) => openAppWindow(String(id || "")));
ipcMain.handle("kimilm:app-shortcut", (_e, a) => appShortcut(a));

// ------------------------------------------------------------------ IPC for the window
ipcMain.on("kimilm:config", (e) => { e.returnValue = { token, port: enginePort, dev: isDev, version: app.getVersion(), platform: process.platform }; });
ipcMain.handle("kimilm:pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("kimilm:pick-file", async (_e, filters) => {
  const r = await dialog.showOpenDialog(win, { properties: ["openFile"], filters: filters || [] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("kimilm:open-path", (_e, p) => shell.openPath(p));
ipcMain.handle("kimilm:show-item", (_e, p) => shell.showItemInFolder(p));
ipcMain.handle("kimilm:open-external", (_e, url) => { if (/^https?:\/\//.test(url)) shell.openExternal(url); });
ipcMain.handle("kimilm:notify", (_e, { title, body, chatId, level }) => {
  // 2.29.0: something that needs you also makes the taskbar ask for attention; a click opens that chat
  if (level === "action" && win && !win.isFocused()) { try { win.flashFrame(true); } catch {} }
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body, icon: iconPath(), urgency: level === "action" ? "critical" : "normal" });
  n.on("click", () => { showWindow(); if (chatId && win) win.webContents.send("kimilm:open", { chatId }); });
  n.show();
});
ipcMain.handle("kimilm:get-shell-prefs", () => ({ ...prefs(), openAtLogin: app.getLoginItemSettings().openAtLogin }));
ipcMain.handle("kimilm:set-shell-prefs", (_e, p) => {
  savePrefs(p);
  if ("openAtLogin" in p) app.setLoginItemSettings({ openAtLogin: !!p.openAtLogin, args: ["--hidden"] });
  return prefs();
});
ipcMain.handle("kimilm:logs-folder", () => shell.openPath(logsDir));
ipcMain.handle("kimilm:get-zoom", () => zoomFactor());
ipcMain.handle("kimilm:set-zoom", (_e, z) => setZoom(z));
ipcMain.handle("kimilm:step-zoom", (_e, dir) => stepZoom(Number(dir) || 0));

// ------------------------------------------------------------------ an app tried off screen (2.35.0)
// `kimilm --kimilm-preview=<url> --kimilm-preview-out=<file> --kimilm-preview-shot=<png>`: the engine opens an app's preview page
// (engine/kimilm/app_preview.py) when the computer has no Chrome-like browser to run headless. This start is separate
// from the running KimiLM: its own temporary profile, no single-instance lock, no engine, no window shown.
function argValue(prefix) {
  const hit = process.argv.find((a) => typeof a === "string" && a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : "";
}
const previewUrl = argValue("--kimilm-preview=");
function runPreview(url, out) {
  app.setPath("userData", fs.mkdtempSync(path.join(require("os").tmpdir(), "kimilm-preview-")));
  app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
  app.commandLine.appendSwitch("use-angle", "swiftshader");          // WebGL off screen, with or without a GPU
  app.commandLine.appendSwitch("enable-unsafe-swiftshader");
  app.whenReady().then(() => {
    if (!/^http:\/\/127\.0\.0\.1:\d+\/appframe\/[\w.-]+\/_preview\.html$/.test(url)) return app.exit(2);
    // shown, but off screen: an off-screen window paints nothing on the screen, and a hidden page would have its
    // frames and the app's timers held back
    const w = new BrowserWindow({ show: true, width: 1024, height: 700, useContentSize: true, frame: false, skipTaskbar: true, focusable: false,
      webPreferences: { offscreen: true, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } });
    w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    let done = false;
    const finish = async () => {
      if (done) return;
      done = true;
      let text = "";
      try { text = await w.webContents.executeJavaScript('document.getElementById("kimi-preview-result").textContent'); } catch { /* none */ }
      try { if (out) fs.writeFileSync(out, text || ""); } catch { /* the engine reports no result */ }
      const shot = argValue("--kimilm-preview-shot=");           // 2.36.0: the app's picture
      try { if (shot) fs.writeFileSync(shot, (await w.webContents.capturePage()).toPNG()); } catch { /* no picture */ }
      app.exit(0);
    };
    w.webContents.on("page-title-updated", (_e, title) => { if (title === "KIMI_PREVIEW_DONE") finish(); });
    w.webContents.on("console-message", (e, level, message) => {            // to stderr: the engine shows its end
      process.stderr.write(`console: ${(e && e.message) || message}\n`);
    });
    setTimeout(finish, 20000);
    w.loadURL(url);
  });
}

// ------------------------------------------------------------------ lifecycle
if (previewUrl) {
  runPreview(previewUrl, argValue("--kimilm-preview-out="));
} else if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", (_e, argv) => {     // 2.34.0: `kimilm --open-app=<id>` while KimiLM runs
    const id = appFromArgs(argv);
    if (id) openAppWindow(id); else showWindow();
  });
  app.setAppUserModelId("com.kimilm.app");
  app.whenReady().then(async () => {
    fs.mkdirSync(logsDir, { recursive: true });
    try {
      await startEngine();
    } catch (err) {
      return showFatal(err);
    }
    if (!isDev) {
      // Content Security Policy for the window (production only: Vite's dev server needs inline scripts).
      session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
        // 2.34.0: an app's frame comes with its own, stricter policy (no internet; engine/kimilm/apps.py)
        if (isAppFrame(details.url)) return callback({ responseHeaders: details.responseHeaders });
        callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [
          "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'; script-src 'self'"] } });
      });
    }
    // Recording the screen in the Video window (2.14.0): the first screen, with the computer's own sound on Windows
    // (the only system where the shell can offer it). Without this, a window cannot be given the screen at all.
    session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
      desktopCapturer.getSources({ types: ["screen"] }).then((sources) => {
        if (!sources.length) return callback({});
        callback(request.audioRequested && process.platform === "win32" ? { video: sources[0], audio: "loopback" } : { video: sources[0] });
      }).catch((err) => { log(`screen recording: ${err.message}`); callback({}); });
    });
    if (!kimiosSession) createTray();
    watchWork();
    const solo = appFromArgs(process.argv);       // 2.34.0: started from an app's entry: that app, the main window hidden
    const hidden = process.argv.includes("--hidden") || prefs().startHidden || (!!solo && !kimiosSession);
    createWindow(!hidden);
    if (solo) openAppWindow(solo);
  });
  // Subscribing to this event keeps the app running when its window closes (the tray); it quits only when asked to
  app.on("window-all-closed", () => { if (quitting) app.quit(); });
  app.on("before-quit", async (e) => {
    quitting = true;
    if (engine) {
      e.preventDefault();
      await stopEngine();
      engine = null;
      app.quit();
    }
  });
}
