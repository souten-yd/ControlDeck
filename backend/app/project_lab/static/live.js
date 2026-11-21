/* ControlDeck Project Lab: ライブ確認（見張りモード）。preview の HTML へ ControlDeck が 1 本だけ差し込む。
 * - `__cd/live` の WebSocket で、再読み込み・ビルドの状態・AI の知らせを受ける
 * - 記録（切り替え式）: クリック・入力（長さだけ）・送信・画面の移り変わり・エラー・fetch の失敗・気づき
 * アプリ自身のスクリプトや Vite の HMR を壊さないよう、グローバルは汚さず、fetch と console.error は元を呼ぶ。 */
(function () {
  "use strict";
  var root = document.documentElement;
  if (!root || root.getAttribute("data-cd-live")) return;
  var script = document.currentScript;
  var src = script && script.src;
  if (!src) {
    var all = document.getElementsByTagName("script");
    for (var i = 0; i < all.length; i++) {
      if (/__cd\/live\.js(\?|$)/.test(all[i].src)) { src = all[i].src; break; }
    }
  }
  if (!src) return;
  root.setAttribute("data-cd-live", "1");
  var wsUrl = src.replace(/live\.js(\?.*)?$/, "live").replace(/^http/, "ws");

  var MAX_PENDING = 200;
  var RECONNECT_MS = 3000;
  var ws = null, everOpened = false, reconnectTimer = null;
  var recording = false;
  var pending = [];
  var lastTarget = null;
  var pressTimer = null, pressXY = null, suppressClick = false;
  var STORE_KEY = "__cd_live_pending";

  try {
    var saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || "[]");
    if (Array.isArray(saved)) pending = saved.slice(-MAX_PENDING);
    sessionStorage.removeItem(STORE_KEY);
  } catch (e) { /* sandbox の iframe では使えない */ }

  // ---- 要素の示し方（ScrapeViewer の cssPath と同じ考え方） ----
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    var ident = /^[A-Za-z_][\w-]*$/;
    if (el.id && ident.test(el.id)) return "#" + el.id;
    var parts = [], depth = 0;
    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== "html" && depth < 4) {
      var seg = el.tagName.toLowerCase();
      var cls = (el.className && typeof el.className === "string")
        ? el.className.trim().split(/\s+/).filter(function (c) { return ident.test(c); }) : [];
      if (el.id && ident.test(el.id)) { parts.unshift("#" + el.id); break; }
      if (cls.length) { seg += "." + cls.slice(0, 2).join("."); }
      else if (el.parentNode) {
        var same = Array.prototype.filter.call(el.parentNode.children, function (c) { return c.tagName === el.tagName; });
        if (same.length > 1) seg += ":nth-of-type(" + (same.indexOf(el) + 1) + ")";
      }
      parts.unshift(seg);
      el = el.parentNode;
      depth++;
    }
    return parts.join(" > ");
  }
  function describe(el) {
    var tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    var secret = tag === "input" || tag === "textarea" || tag === "select";
    var text = secret ? "" : ((el && el.textContent) || "").replace(/\s+/g, " ").trim().slice(0, 80);
    return { selector: cssPath(el), tag: tag, text: text };
  }
  function isOurs(el) { return el === host || (host && host.contains(el)); }

  // ---- 送る ----
  function send(record) {
    record.path = location.pathname;
    var text;
    try { text = JSON.stringify(record); } catch (e) { return; }
    if (ws && ws.readyState === 1) {
      try { ws.send(text); return; } catch (e) { /* ためる */ }
    }
    pending.push(text);
    if (pending.length > MAX_PENDING) pending.shift();
  }
  function flush() {
    while (pending.length && ws && ws.readyState === 1) {
      try { ws.send(pending[0]); pending.shift(); } catch (e) { return; }
    }
  }
  window.addEventListener("pagehide", function () {
    if (!pending.length) return;
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(pending.slice(-MAX_PENDING))); } catch (e) { /* 無視 */ }
  });

  // ---- 表示（Shadow DOM。アプリの CSS と混ざらない） ----
  var host = document.createElement("div");
  host.setAttribute("data-cd-live-ui", "1");
  host.style.cssText = "position:fixed;right:8px;bottom:8px;z-index:2147483647;";
  var shadow = host.attachShadow ? host.attachShadow({ mode: "closed" }) : host;
  shadow.innerHTML =
    '<style>' +
    ':host,*{box-sizing:border-box;font:12px/1.4 system-ui,sans-serif}' +
    '.bar{display:flex;gap:6px;align-items:center;background:rgba(24,24,27,.88);color:#fff;padding:4px 8px;border-radius:14px;max-width:92vw}' +
    '.st{max-width:60vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.st.fail{color:#fca5a5}' +
    'button{all:unset;cursor:pointer;padding:2px 8px;border-radius:10px;background:rgba(255,255,255,.16);color:#fff;font:12px system-ui,sans-serif}' +
    '.dot{display:none;width:8px;height:8px;border-radius:50%;background:#ef4444}.rec .dot{display:inline-block}' +
    '.toast{display:none;margin-bottom:6px;background:#1d4ed8;color:#fff;padding:6px 10px;border-radius:10px;max-width:85vw}' +
    '.panel{display:none;margin-bottom:6px;background:#fff;color:#111;padding:8px;border-radius:10px;width:min(320px,86vw);box-shadow:0 2px 12px rgba(0,0,0,.3)}' +
    '.panel textarea{width:100%;height:70px;font:13px system-ui,sans-serif;color:#111;background:#fff;border:1px solid #999;border-radius:6px}' +
    '.panel .row{display:flex;gap:6px;justify-content:flex-end;margin-top:6px}.panel button{background:#e5e7eb;color:#111}.panel .tg{font-size:11px;color:#555;word-break:break-all}' +
    '</style>' +
    '<div class="toast"></div>' +
    '<div class="panel"><div class="tg"></div><textarea placeholder="気づいたこと"></textarea>' +
    '<div class="row"><button class="cancel">やめる</button><button class="ok">送る</button></div></div>' +
    '<div class="bar"><span class="st" style="display:none"></span>' +
    '<button class="rec" title="操作と気づきを記録する"><span class="dot"></span> 記録</button>' +
    '<button class="note">気づき</button></div>';
  var $ = function (sel) { return shadow.querySelector(sel); };
  var stEl = $(".st"), toast = $(".toast"), panel = $(".panel"), bar = $(".bar");
  var panelTarget = null, toastTimer = null;

  function showStatus(msg) {
    var text = "", fail = false;
    var phase = msg.phase, code = msg.exit_code;
    if (phase === "building") text = "ビルド中";
    else if (phase === "running") text = "実行中";
    else if (phase === "idle") text = "待機中";
    else if (phase === "failed" || (phase === "exited" && code)) {
      fail = true;
      var first = String(msg.error_tail || "").split("\n").filter(function (l) { return l.trim(); }).slice(-1)[0] || "";
      text = "失敗: " + (first || (code != null ? "終了コード " + code : "")).slice(0, 120);
    } else if (phase === "exited") text = "完了（終了コード " + (code == null ? 0 : code) + "）";
    stEl.textContent = text;
    stEl.className = "st" + (fail ? " fail" : "");
    stEl.style.display = text ? "" : "none";
  }
  function showNote(text) {
    toast.textContent = String(text || "").slice(0, 300);
    toast.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.style.display = "none"; }, 6000);
  }
  function openPanel(target) {
    panelTarget = target || lastTarget;
    $(".tg").textContent = panelTarget ? "対象: " + panelTarget.selector : "";
    panel.style.display = "block";
    var ta = $("textarea"); ta.value = ""; ta.focus();
  }
  $(".cancel").addEventListener("click", function () { panel.style.display = "none"; });
  $(".ok").addEventListener("click", function () {
    var text = $("textarea").value.trim();
    if (text) send({ kind: "note", text: text.slice(0, 4000), target: panelTarget || null });
    panel.style.display = "none";
  });
  $(".note").addEventListener("click", function () { openPanel(lastTarget); });
  $(".rec").addEventListener("click", function () {
    recording = !recording;
    bar.className = "bar" + (recording ? " rec" : "");
    $(".rec").setAttribute("aria-pressed", recording ? "true" : "false");
    send({ kind: "recording", on: recording });
  });
  function mount() { (document.body || root).appendChild(host); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  // ---- 接続 ----
  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () { reconnectTimer = null; connect(); }, RECONNECT_MS);
  }
  function connect() {
    if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
    try { ws = new WebSocket(wsUrl); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = function () { everOpened = true; flush(); };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "reload") location.reload();
      else if (msg.type === "status") showStatus(msg);
      else if (msg.type === "note") showNote(msg.text);
    };
    ws.onclose = function () { scheduleReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (e) { /* 無視 */ } };
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    // iOS Safari は背景で WebSocket を閉じ、繋ぎ直せないことがある。見ていない間に直っているので読み込み直す
    if (everOpened && (!ws || ws.readyState !== 1)) { location.reload(); return; }
    connect();
  });
  connect();

  // ---- 記録 ----
  function track(el) { if (el && el.nodeType === 1 && !isOurs(el)) lastTarget = describe(el); }
  document.addEventListener("click", function (e) {
    if (suppressClick) { suppressClick = false; e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (isOurs(e.target)) return;
    lastTarget = describe(e.target);   // 「気づき」を直前に触った要素へひも付ける
    if (!recording) return;
    send({ kind: "click", target: lastTarget, x: Math.round(e.clientX), y: Math.round(e.clientY),
           w: window.innerWidth, h: window.innerHeight });
  }, true);
  var inputTimer = null;
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!recording || !el || isOurs(el) || el.type === "password") return;
    track(el);
    clearTimeout(inputTimer);
    inputTimer = setTimeout(function () {
      // 値は送らない。長さだけ
      send({ kind: "input", target: describe(el), type: el.type || "", length: String(el.value == null ? "" : el.value).length });
    }, 500);
  }, true);
  document.addEventListener("submit", function (e) {
    if (!recording) return;
    send({ kind: "submit", target: describe(e.target) });
  }, true);
  var lastPath = location.pathname;
  setInterval(function () {
    if (location.pathname === lastPath) return;
    var from = lastPath; lastPath = location.pathname;
    if (recording) send({ kind: "navigate", from: from, to: lastPath });
  }, 500);
  window.addEventListener("error", function (e) {
    if (!recording) return;
    send({ kind: "error", message: String(e.message || "").slice(0, 500), line: e.lineno || 0 });
  });
  window.addEventListener("unhandledrejection", function (e) {
    if (!recording) return;
    var r = e.reason;
    send({ kind: "rejection", message: String(r && r.stack ? r.stack : r).slice(0, 500) });
  });
  var origError = console.error;
  console.error = function () {
    if (recording) {
      try {
        send({ kind: "console", message: Array.prototype.map.call(arguments, function (a) {
          return a && a.stack ? a.stack : (typeof a === "string" ? a : (function () { try { return JSON.stringify(a); } catch (x) { return String(a); } })());
        }).join(" ").slice(0, 500) });
      } catch (e) { /* 記録の失敗でアプリの console.error を止めない */ }
    }
    return origError.apply(this, arguments);
  };
  var origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = function (input, init) {
      var path = "";
      try { path = new URL(typeof input === "string" ? input : input.url, location.href).pathname; } catch (e) { /* 無視 */ }
      var p = origFetch.apply(this, arguments);
      return p.then(function (res) {
        if (recording && res && res.ok === false) send({ kind: "fetch", status: res.status, fetchPath: path });
        return res;
      }, function (err) {
        if (recording) send({ kind: "fetch", status: 0, fetchPath: path, message: String(err).slice(0, 200) });
        throw err;
      });
    };
  }
  // スマホ: 記録中に要素を長押しすると、その要素についての気づきを書ける
  document.addEventListener("touchstart", function (e) {
    if (!recording || isOurs(e.target) || !e.touches || e.touches.length !== 1) return;
    var t = e.target;
    pressXY = [e.touches[0].clientX, e.touches[0].clientY];
    clearTimeout(pressTimer);
    pressTimer = setTimeout(function () {
      suppressClick = true;
      setTimeout(function () { suppressClick = false; }, 1000);
      openPanel(describe(t));
    }, 650);
  }, { capture: true, passive: true });
  document.addEventListener("touchmove", function (e) {
    if (!pressTimer || !pressXY || !e.touches[0]) return;
    if (Math.abs(e.touches[0].clientX - pressXY[0]) > 10 || Math.abs(e.touches[0].clientY - pressXY[1]) > 10) {
      clearTimeout(pressTimer); pressTimer = null;
    }
  }, { capture: true, passive: true });
  function endPress() { clearTimeout(pressTimer); pressTimer = null; }
  document.addEventListener("touchend", endPress, true);
  document.addEventListener("touchcancel", endPress, true);
})();
