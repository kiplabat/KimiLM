/* 2.35.0: the host side of an app's preview (see preview.html). Stand-in answers for every kimi call, a poke at
   1.5 s (the app's kimi.js presses keys and clicks), and the result once the app has reported (at most 9 s):
   {hello, errors: [..], calls: {method: n}, report: {frames, frames_after, canvases, text, buttons, changed}} */
(function () {
  "use strict";
  const P = window.__PREVIEW || {};
  // this script runs before the frame exists (so the app's first call is never missed): find it when needed
  const frame = { get contentWindow() { const f = document.getElementById("kimi-preview-app"); return f && f.contentWindow; } };
  const out = { hello: false, errors: [], calls: {}, report: null, sent: [] };
  const store = {};
  const answer = (m, result, error) => frame.contentWindow.postMessage({ kimi: 1, id: m.id, result: result, error: error }, "*");
  window.addEventListener("message", (e) => {
    if (e.source !== frame.contentWindow) return;
    const m = e.data || {};
    if (!m.kimi || !m.method) return;
    out.calls[m.method] = (out.calls[m.method] || 0) + 1;
    const a = m.args || {};
    switch (m.method) {
      case "hello": out.hello = true; return answer(m, { app: P.app || {}, device: P.device || null });
      case "error": if (out.errors.length < 20) out.errors.push(a); return;
      case "preview.report": out.report = a; out.finish(); return;
      case "notify": return answer(m, true);
      case "storage.get": return answer(m, Object.prototype.hasOwnProperty.call(store, a.key) ? store[a.key] : null);
      case "storage.set": store[a.key] = a.value; return answer(m, { ok: true });
      case "storage.all": return answer(m, store);
      case "device.info": case "device.state":
        return P.info ? answer(m, P.info) : answer(m, null, "This app has no device");
      case "device.history": return answer(m, P.history || []);
      case "device.check": return answer(m, { valid: true, problems: [] });
      case "device.control": out.sent.push("control"); return answer(m, { valid: true, problems: [], started: false, preview: true });
      case "device.act":
        out.sent.push(String(a.action));
        return P.info ? answer(m, "(a preview: " + a.action + " was not sent to the device)") : answer(m, null, "This app has no device");
      case "device.release": case "device.event": case "device.install":
        out.sent.push(m.method); return answer(m, "(a preview: not sent)");
      case "ai.ask": return answer(m, null, "(a preview: no model is asked while the app is tried)");
      default: return answer(m, null, "kimi has no " + m.method);
    }
  });
  // Poke at 1.5 s; send the result when the app has reported (its timers may run late in a hidden browser), or at 9 s
  let sent = false;
  const finish = () => {
    if (sent) return;
    sent = true;
    const text = JSON.stringify(out);
    document.getElementById("kimi-preview-result").textContent = text;
    // 2.36.0: to the engine, which waits for it; the browser's screenshot follows (./_wait answers after this)
    fetch("./_result", { method: "POST", headers: { "Content-Type": "application/json" }, body: text })
      .catch(() => {}).finally(() => { document.title = "KIMI_PREVIEW_DONE"; });
  };
  out.finish = () => setTimeout(finish, 250);
  setTimeout(() => frame.contentWindow && frame.contentWindow.postMessage({ kimi: 1, event: "preview" }, "*"), 1500);
  setTimeout(finish, 9000);
})();
