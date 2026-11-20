// KimiLM — desktop shell.
// Starts the local engine (Python) with a random token, opens the window on it, keeps running in the tray so
// chats, long tasks and schedules continue when the window is closed, and can start with Windows.
const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, shell, Notification, nativeImage, session, powerSaveBlocker, desktopCapturer } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { migrateUserData, moveLoginItem } = require("./migrate.cjs");

const isDev = !app.isPackaged;
const token = crypto.randomBytes(24).toString("base64url");
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
const logsDir = path.join(userData, "logs");   // created after the first-start data move (migrate.cjs)

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
    win.webContents.send("dva:zoom", z);
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

// Which Python runs the engine: DVA_PYTHON, then the installer's own embedded Python, then engine\.venv
// (development), then a Python 3.12 already installed on this computer that has the engine's packages.
function pythonPath() {
  if (process.env.DVA_PYTHON) return process.env.DVA_PYTHON;
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
  return fs.existsSync(path.join(bundled, "dva")) ? bundled : path.join(__dirname, "..", "..", "engine");
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
// kimilm.slice with its own limits; elsewhere systemd creates the slice on the fly. DVA_NO_SLICE=1 turns this off,
// and a scope that cannot be made (no systemd user session) falls back to a plain start once.
let sliceFailed = false;
function sliceWrap(cmd, args) {
  if (process.platform !== "linux" || process.env.DVA_NO_SLICE === "1" || sliceFailed) return null;
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
      DVA_TOKEN: token,
      DVA_UI_DIR: uiDir,
      DVA_NODE: process.execPath,
      DVA_NODE_IS_ELECTRON: "1",
      DVA_PLAYWRIGHT_MCP: playwrightCli(),
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONNOUSERSITE: "1",
      NO_PROXY: "127.0.0.1,localhost",
    };
    const port = process.env.DVA_ENGINE_PORT || "0";
    const args = ["-m", "dva", "serve", "--port", port, "--data-dir", userData, "--resources", resources()];
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
      const m = text.match(/DVA_READY (\d+)/);
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
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(appUrl())) { e.preventDefault(); shell.openExternal(url); }
  });
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

// ------------------------------------------------------------------ IPC for the window
ipcMain.on("dva:config", (e) => { e.returnValue = { token, port: enginePort, dev: isDev, version: app.getVersion(), platform: process.platform }; });
ipcMain.handle("dva:pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("dva:pick-file", async (_e, filters) => {
  const r = await dialog.showOpenDialog(win, { properties: ["openFile"], filters: filters || [] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("dva:open-path", (_e, p) => shell.openPath(p));
ipcMain.handle("dva:show-item", (_e, p) => shell.showItemInFolder(p));
ipcMain.handle("dva:open-external", (_e, url) => { if (/^https?:\/\//.test(url)) shell.openExternal(url); });
ipcMain.handle("dva:notify", (_e, { title, body }) => {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body, icon: iconPath() });
  n.on("click", showWindow);
  n.show();
});
ipcMain.handle("dva:get-shell-prefs", () => ({ ...prefs(), openAtLogin: app.getLoginItemSettings().openAtLogin }));
ipcMain.handle("dva:set-shell-prefs", (_e, p) => {
  savePrefs(p);
  if ("openAtLogin" in p) app.setLoginItemSettings({ openAtLogin: !!p.openAtLogin, args: ["--hidden"] });
  return prefs();
});
ipcMain.handle("dva:logs-folder", () => shell.openPath(logsDir));
ipcMain.handle("dva:get-zoom", () => zoomFactor());
ipcMain.handle("dva:set-zoom", (_e, z) => setZoom(z));
ipcMain.handle("dva:step-zoom", (_e, dir) => stepZoom(Number(dir) || 0));

// ------------------------------------------------------------------ lifecycle
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", showWindow);
  app.setAppUserModelId("com.kimilm.app");
  app.whenReady().then(async () => {
    const moved = migrateUserData({ app, dialog });   // before anything writes into the data folder
    fs.mkdirSync(logsDir, { recursive: true });
    if (moved.status !== "none") log(`data from the earlier app name: ${moved.status} ${moved.from || ""} -> ${moved.to || ""} ${moved.detail || ""}`);
    if (moved.status === "moved") {
      moveLoginItem(app, !!prefs().openAtLogin);
      if (Notification.isSupported()) {
        new Notification({ title: "Welcome to KimiLM", body: "Datavera Agent is now KimiLM. Your chats, models, memory and settings came with you." }).show();
      }
    } else if (moved.status === "failed") {
      dialog.showMessageBoxSync({ type: "warning", title: "KimiLM", message: "Your data from Datavera Agent could not be moved",
        detail: `${moved.detail}\n\nKimiLM starts without it. Nothing was deleted: it is still in ${moved.from}. Logs: ${logsDir}` });
    }
    try {
      await startEngine();
    } catch (err) {
      return showFatal(err);
    }
    if (!isDev) {
      // Content Security Policy for the window (production only: Vite's dev server needs inline scripts).
      session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
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
    const hidden = process.argv.includes("--hidden") || prefs().startHidden;
    createWindow(!hidden);
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
