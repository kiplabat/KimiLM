// Safe bridge between the window and the desktop shell: only these functions are exposed.
const { contextBridge, ipcRenderer } = require("electron");

const config = ipcRenderer.sendSync("kimilm:config");

contextBridge.exposeInMainWorld("kimilm", {
  token: config.token,
  port: config.port,
  dev: config.dev,
  version: config.version,
  platform: config.platform,
  pickFolder: () => ipcRenderer.invoke("kimilm:pick-folder"),
  pickFile: (filters) => ipcRenderer.invoke("kimilm:pick-file", filters),
  openPath: (p) => ipcRenderer.invoke("kimilm:open-path", p),
  showItem: (p) => ipcRenderer.invoke("kimilm:show-item", p),
  openExternal: (url) => ipcRenderer.invoke("kimilm:open-external", url),
  notify: (title, body, extra) => ipcRenderer.invoke("kimilm:notify", { title, body, ...(extra || {}) }),
  // 2.29.0: a desktop notification was clicked: open what it is about
  onOpen: (fn) => { const h = (_e, where) => fn(where); ipcRenderer.on("kimilm:open", h); return () => ipcRenderer.removeListener("kimilm:open", h); },
  getShellPrefs: () => ipcRenderer.invoke("kimilm:get-shell-prefs"),
  setShellPrefs: (p) => ipcRenderer.invoke("kimilm:set-shell-prefs", p),
  openLogs: () => ipcRenderer.invoke("kimilm:logs-folder"),
  // 2.21.0: the size of everything in the window (1 = 100 %)
  getZoom: () => ipcRenderer.invoke("kimilm:get-zoom"),
  setZoom: (z) => ipcRenderer.invoke("kimilm:set-zoom", z),
  stepZoom: (dir) => ipcRenderer.invoke("kimilm:step-zoom", dir),
  onZoom: (fn) => { const h = (_e, z) => fn(z); ipcRenderer.on("kimilm:zoom", h); return () => ipcRenderer.removeListener("kimilm:zoom", h); },
  // 2.34.0: apps (device panels and games): open one in its own window, and put it in the computer's apps menu
  openApp: (id) => ipcRenderer.invoke("kimilm:open-app", id),
  appShortcut: (a) => ipcRenderer.invoke("kimilm:app-shortcut", a),
});
