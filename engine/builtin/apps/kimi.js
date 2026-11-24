/* KimiLM apps (2.34.0): the `kimi` object every app has. An app runs in a sandboxed frame with no origin of its
   own; each call goes to the KimiLM window around it (postMessage), which checks it, asks the person where needed
   and answers. All calls return promises.

     await kimi.ready                      -> {app: {id, name, kind, version}, device: {...} | null}
     kimi.device.info()                    -> the device: what it is, its actions (and for KimiOS devices the datasheet
                                              and status)
     kimi.device.act(action, args)         -> text (any device's actions; changes ask once per app)
     kimi.device.state()                   -> KimiOS devices: {readings, outputs, mode, controller, events}
     kimi.device.history({since})          -> KimiOS devices: [{t, <sensor>: value, <output>: value}]
     kimi.device.watch(fn, ms)             -> calls fn(state) every ms (at least 500); returns stop()
     kimi.device.check(spec)               -> KimiOS devices: {valid, problems}
     kimi.device.control(spec)             -> KimiOS devices: checks, then runs a controller spec
     kimi.device.release()                 -> KimiOS devices: the device's own controller takes over
     kimi.device.event(name, args)         -> simulated KimiOS devices: make something happen
     kimi.device.install()                 -> put this version's device/ part on the device (asks)
     kimi.storage.get(key) / set(key, v)   -> the app's own store (kept across versions; v is any JSON)
     kimi.ai.ask(prompt, {schema})         -> {text} or {json}: a question to the person's model (asks once)
     kimi.notify(text)                     -> a short message in the window
     kimi.on(event, fn)                    -> "visible" (true/false: the app was shown or hidden; a hidden app
                                              gets no animation frames)
*/
(function () {
  "use strict";
  if (window.kimi) return;
  const pending = new Map();
  const listeners = {};
  let seq = 0;
  const post = (msg) => { try { window.parent.postMessage(Object.assign({ kimi: 1 }, msg), "*"); } catch (e) { /* the window went */ } };
  const call = (method, args) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    post({ id, method, args: args === undefined ? null : args });
  });
  window.addEventListener("message", (e) => {
    if (e.source !== window.parent) return;
    const m = e.data || {};
    if (!m.kimi) return;
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.result);
    } else if (m.event) {
      (listeners[m.event] || []).forEach((fn) => { try { fn(m.data); } catch (err) { console.error(err); } });
    }
  });
  // Errors are shown in the window (with a button that asks the builder to fix them)
  window.addEventListener("error", (e) => post({ method: "error", args: { message: String(e.message || e.error || "error"),
    source: String(e.filename || "").split("/").pop(), line: e.lineno || 0, column: e.colno || 0 } }));
  window.addEventListener("unhandledrejection", (e) => post({ method: "error", args: {
    message: "Unhandled: " + String((e.reason && (e.reason.message || e.reason)) || "rejection"), source: "", line: 0 } }));
  // Browser storage is not there in an app: say what to use instead, rather than failing obscurely
  ["localStorage", "sessionStorage"].forEach((name) => {
    try { void window[name]; } catch (err) {
      try { Object.defineProperty(window, name, { configurable: true, get() { throw new Error(name + " is not available in KimiLM apps: use kimi.storage.get/set"); } }); } catch (e2) { /* fine */ }
    }
  });

  // A hidden app (another screen is in front) gets no animation frames until it is shown again, so a game left
  // open does not keep drawing; games that time their frames carry on where they were
  let visible = true, frames = 0;
  const held = [];
  const raf = window.requestAnimationFrame.bind(window);
  const counted = (cb) => (t) => { frames++; cb(t); };
  window.requestAnimationFrame = (cb) => { if (visible) return raf(counted(cb)); held.push(cb); return 0; };
  listeners.visible = [(v) => { visible = v !== false; if (visible) held.splice(0).forEach((cb) => raf(counted(cb))); }];

  // 2.35.0: tried before the person sees it (a preview): press a few keys, click the middle of the biggest canvas
  // (or the first button), and say what happened. Only the preview page sends this; it answers every call itself.
  listeners.preview = [() => {
    const canvases = Array.from(document.querySelectorAll("canvas"));
    const big = canvases.sort((x, y) => y.width * y.height - x.width * x.height)[0];
    const look = () => (document.body ? document.body.innerText : "").slice(0, 2000);
    const sample = () => {
      try {
        if (!big) return "";
        const g = big.getContext("2d");
        if (!g) return "";
        const d = g.getImageData(0, 0, big.width, big.height).data;
        let h = 0;
        for (let i = 0; i < d.length; i += 4093) h = (h * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) | 0;
        return String(h);
      } catch (e) { return ""; }
    };
    const before = { frames, text: look(), pixels: sample() };
    const target = () => document.activeElement && document.activeElement !== document.documentElement ? document.activeElement : document.body;
    const key = (k, code, type) => target().dispatchEvent(new KeyboardEvent(type, { key: k, code, bubbles: true, cancelable: true }));
    const keys = [["Enter", "Enter"], [" ", "Space"], ["ArrowUp", "ArrowUp"], ["ArrowLeft", "ArrowLeft"], ["w", "KeyW"]];
    keys.forEach(([k, c]) => key(k, c, "keydown"));
    const el = big || document.querySelector("button") || document.body;
    const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const at = document.elementFromPoint(x, y) || el;
    const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1, pointerType: "mouse", isPrimary: true };
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((t) =>
      at.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, opts) : new MouseEvent(t, opts)));
    setTimeout(() => keys.forEach(([k, c]) => key(k, c, "keyup")), 400);
    setTimeout(() => {
      const text = look();
      post({ method: "preview.report", args: {
        frames: before.frames, frames_after: frames,
        canvases: canvases.map((c) => [c.width, c.height, c.getContext("webgl2") || c.getContext("webgl") ? "webgl" : "2d"]),
        text: text.slice(0, 300), text_length: text.trim().length,
        buttons: document.querySelectorAll("button").length, inputs: document.querySelectorAll("input, select, textarea").length,
        changed: text !== before.text || (before.pixels !== "" && sample() !== before.pixels),
      } });
    }, 1800);
  }];

  const kimi = {
    ready: null,
    app: null,
    device: {
      info: () => call("device.info"),
      act: (action, args) => call("device.act", { action: String(action), args: args || {} }),
      state: () => call("device.state"),
      history: (opts) => call("device.history", opts || {}),
      check: (spec) => call("device.check", { spec }),
      control: (spec) => call("device.control", { spec }),
      release: () => call("device.release"),
      event: (name, args) => call("device.event", { name: String(name), args: args || {} }),
      install: () => call("device.install"),
      watch(fn, ms) {
        let stopped = false, timer = null;
        const every = Math.max(500, Number(ms) || 2000);
        const tick = async () => {
          if (stopped) return;
          try { fn(await kimi.device.state()); } catch (err) { console.warn(err); }
          if (!stopped) timer = setTimeout(tick, every);
        };
        tick();
        return () => { stopped = true; clearTimeout(timer); };
      },
    },
    storage: {
      get: (key) => call("storage.get", { key: String(key) }),
      set: (key, value) => call("storage.set", { key: String(key), value: value === undefined ? null : value }),
      all: () => call("storage.all"),
    },
    ai: { ask: (prompt, opts) => call("ai.ask", { prompt: String(prompt), schema: (opts && opts.schema) || null, max_tokens: (opts && opts.max_tokens) || 400 }) },
    notify: (text) => call("notify", { text: String(text) }),
    on(event, fn) { (listeners[event] = listeners[event] || []).push(fn); },
  };
  // Say hello until the window answers (it may still be setting up when the app's first script runs)
  kimi.ready = new Promise((resolve) => {
    let done = false, tries = 0;
    const hello = () => {
      if (done || tries++ > 40) return;
      call("hello", { title: document.title || "" }).then((info) => {
        if (done) return;
        done = true; kimi.app = info.app; kimi.device.current = info.device || null; resolve(info);
      }, () => {});
      setTimeout(hello, 500);
    };
    hello();
  });
  window.kimi = kimi;
})();
