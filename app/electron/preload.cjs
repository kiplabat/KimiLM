// Safe bridge between the window and the desktop shell: only these functions are exposed.
const { contextBridge, ipcRenderer } = require("electron");

const config = ipcRenderer.sendSync("dva:config");

contextBridge.exposeInMainWorld("dva", {
  token: config.token,
  port: config.port,
  dev: config.dev,
  version: config.version,
  platform: config.platform,
  pickFolder: () => ipcRenderer.invoke("dva:pick-folder"),
  pickFile: (filters) => ipcRenderer.invoke("dva:pick-file", filters),
  openPath: (p) => ipcRenderer.invoke("dva:open-path", p),
  showItem: (p) => ipcRenderer.invoke("dva:show-item", p),
  openExternal: (url) => ipcRenderer.invoke("dva:open-external", url),
  notify: (title, body) => ipcRenderer.invoke("dva:notify", { title, body }),
  getShellPrefs: () => ipcRenderer.invoke("dva:get-shell-prefs"),
  setShellPrefs: (p) => ipcRenderer.invoke("dva:set-shell-prefs", p),
  openLogs: () => ipcRenderer.invoke("dva:logs-folder"),
  // 2.21.0: the size of everything in the window (1 = 100 %)
  getZoom: () => ipcRenderer.invoke("dva:get-zoom"),
  setZoom: (z) => ipcRenderer.invoke("dva:set-zoom", z),
  stepZoom: (dir) => ipcRenderer.invoke("dva:step-zoom", dir),
  onZoom: (fn) => { const h = (_e, z) => fn(z); ipcRenderer.on("dva:zoom", h); return () => ipcRenderer.removeListener("dva:zoom", h); },
});
