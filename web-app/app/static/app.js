/* Dashboard glue: Leaflet map + ECharts chart islands, store-selection sync across
 * panels, and EventSource-streamed chat. Numbers come from the backend JSON/HTMX
 * endpoints; this file only wires interaction. */
"use strict";

const state = { store: null, selectedTarget: null, chart: null, map: null, markers: {}, chatOpen: null, chatHistory: [] };

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initChart();
  loadStoreLayer();
  loadEventLayer();

  document.getElementById("store-select").addEventListener("change", (e) => {
    if (e.target.value) selectStore(e.target.value);
  });

  document.getElementById("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = document.getElementById("chat-input");
    const msg = input.value.trim();
    if (msg) { sendChat(msg); input.value = ""; }
  });

  // Delegated: quick-prompt buttons (data-q), the per-day "explain" button (HTMX-swapped),
  // and the SHAP-decomposition button (data-action="shap", rendered as a chart in chat).
  document.addEventListener("click", (e) => {
    const q = e.target.closest("[data-q]");
    if (q) { sendChat(q.dataset.q); return; }
    const act = e.target.closest("[data-action]");
    if (act && act.dataset.action === "shap") { decomposeInChat(); return; }
    // Highlight the clicked forecast day (the breakdown loads via HTMX separately);
    // the rest of the centre panel is left as-is.
    const row = e.target.closest(".fc-row");
    if (row) {
      document.querySelectorAll(".fc-row.selected").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      state.selectedTarget = row.dataset.target;
    }
  });

  window.addEventListener("resize", () => {
    if (state.chart) state.chart.resize();
    if (state.map) state.map.invalidateSize();
  });
});

/* ----------------------------------------------------------------- map island */
function initMap() {
  state.map = L.map("map").setView([35.68, 139.76], 9);
  // GSI 淡色地図 (地理院タイル "pale"): Japan's official light/muted basemap. Chosen over
  // CARTO Positron because its labels are in Japanese (CARTO romanizes place names).
  L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "出典: <a href='https://maps.gsi.go.jp/development/ichiran.html'>国土地理院</a>",
  }).addTo(state.map);
}

function colorFor(v, lo, hi) {
  const t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
  if (t < 0.34) return "#f4a9ba";      // 少 — ピンク
  if (t < 0.67) return "#e05d6f";      // 中 — ライトレッド
  return "#8c1c2b";                    // 多 — ダークレッド
}

function loadStoreLayer() {
  fetch("/api/stores").then((r) => r.json()).then((stores) => {
    Object.values(state.markers).forEach((m) => state.map.removeLayer(m));
    state.markers = {};
    const vals = stores.map((s) => s.mean_calibrated);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    stores.forEach((s) => {
      const m = L.circleMarker([s.latitude, s.longitude], {
        radius: 7, weight: s.event_count > 0 ? 2 : 1,
        color: s.event_count > 0 ? "#333" : "#fff",
        fillColor: colorFor(s.mean_calibrated, lo, hi), fillOpacity: 0.9,
      }).addTo(state.map);
      m.bindTooltip(`${s.store_name}<br>平均補正後 ${s.mean_calibrated} 杯`);
      m.on("click", () => {
        document.getElementById("store-select").value = s.store_name;
        selectStore(s.store_name);
      });
      state.markers[s.store_name] = m;
    });
  });
}

function loadEventLayer() {
  fetch("/api/events").then((r) => r.json()).then((events) => {
    events.forEach((e) => {
      L.marker([e.latitude, e.longitude], { icon: starIcon() }).addTo(state.map)
        .bindTooltip(`★ ${e.event_name}<br>${e.venue || ""}<br>想定 ${e.attendance_point || "?"}人`);
    });
  });
}

function starIcon() {
  return L.divIcon({ className: "star-icon", html: "★", iconSize: [18, 18] });
}

/* ----------------------------------------------------------------- chart island */
function initChart() { state.chart = echarts.init(document.getElementById("chart")); }

function renderChart(rows) {
  const x = rows.map((r) => `${r.target_date.slice(5)}(${r.weekday})`);
  state.chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["予測", "天候補正後", "補正後売上"], bottom: 0 },
    grid: { left: 40, right: 12, top: 24, bottom: 40 },
    xAxis: { type: "category", data: x },
    yAxis: { type: "value", name: "杯" },
    series: [
      { name: "予測", type: "bar", data: rows.map((r) => r.predicted_sales), itemStyle: { color: "#9aa7b5" } },
      { name: "天候補正後", type: "bar", data: rows.map((r) => r.weather_calibrated_sales), itemStyle: { color: "#6f9ecf" } },
      { name: "補正後売上", type: "line", smooth: true, symbolSize: 8,
        data: rows.map((r) => r.calibrated_sales), itemStyle: { color: "#d1495b" }, lineStyle: { width: 3 } },
    ],
  }, true);
}

/* ----------------------------------------------------------------- selection sync */
function selectStore(name) {
  state.store = name;
  state.selectedTarget = null;   // new store: no day highlighted until one is clicked
  document.getElementById("store-title").textContent = name;
  htmx.ajax("GET", `/ui/store/${encodeURIComponent(name)}/forecast`,
    { target: "#forecast", swap: "innerHTML" });
  document.getElementById("breakdown").innerHTML = "";
  fetch(`/api/store/${encodeURIComponent(name)}/series`)
    .then((r) => r.json()).then((d) => renderChart(d.rows));
  const m = state.markers[name];
  if (m) state.map.panTo(m.getLatLng());
}

/* ----------------------------------------------------------------- chat (SSE) */
function appendMsg(cls, text) {
  const log = document.getElementById("chat-log");
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// Minimal, SAFE Markdown -> HTML for assistant messages. HTML is escaped first (no
// injection from model output), then a small subset is formatted: **bold**, `code`,
// bullet lists (* / -), and blank-line-separated paragraphs.
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inlineMd(s) {
  return escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}
// The model sometimes emits LaTeX math (we don't render it); turn it into readable plain
// text: drop the $…$ / \(…\) / \[…\] delimiters, unwrap \text{…}, and map common macros.
function stripLatexInner(s) {
  return s
    .replace(/\\text\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "$1/$2")
    .replace(/\\times/g, "×").replace(/\\cdot/g, "·").replace(/\\div/g, "÷")
    .replace(/\\pm/g, "±").replace(/\\leq/g, "≤").replace(/\\geq/g, "≥")
    .replace(/\\neq/g, "≠").replace(/\\approx/g, "≈").replace(/\\%/g, "%")
    .replace(/\\left|\\right/g, "").replace(/\\[,;!]/g, " ")
    .trim();
}
function stripLatex(text) {
  return text
    .replace(/\$\$([\s\S]*?)\$\$/g, (_, m) => stripLatexInner(m))
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => stripLatexInner(m))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, m) => stripLatexInner(m))
    .replace(/\$([^$\n]+)\$/g, (_, m) => stripLatexInner(m));
}
function mdToHtml(text) {
  text = stripLatex(text);
  let html = "", listOpen = false;
  const closeList = () => { if (listOpen) { html += "</ul>"; listOpen = false; } };
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const m = line.match(/^\s*[*-]\s+(.*)$/);
    if (m) {
      if (!listOpen) { html += "<ul>"; listOpen = true; }
      html += "<li>" + inlineMd(m[1]) + "</li>";
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html += "<p>" + inlineMd(line) + "</p>";
    }
  }
  closeList();
  return html;
}

// Open an SSE connection and stream {delta} chunks into `bubble` until 'done'/'error'.
function streamAssistant(url, bubble) {
  let acc = "";
  bubble.classList.add("md");
  if (state.chatOpen) state.chatOpen.close();
  const es = new EventSource(url);
  state.chatOpen = es;
  es.onmessage = (ev) => {
    try {
      acc += JSON.parse(ev.data).delta || "";
      bubble.innerHTML = mdToHtml(acc);
      document.getElementById("chat-log").scrollTop = 1e9;
    } catch (_) { /* ignore keep-alives */ }
  };
  es.addEventListener("done", () => { es.close(); state.chatOpen = null; bubble.classList.remove("streaming"); });
  es.onerror = () => {
    es.close(); state.chatOpen = null; bubble.classList.remove("streaming");
    if (!acc) bubble.textContent = "⚠️ AIサービスに接続できませんでした。";
  };
  return es;
}

// Main chat is the agentic analyst: non-streaming (a multi-step tool loop has no token
// stream). Show a "thinking…" bubble, then render the final answer + tool-step transcript.
function sendChat(message) {
  appendMsg("user", message);
  const bubble = appendMsg("assistant thinking", "考え中…（データを分析しています）");
  document.getElementById("chat-log").scrollTop = 1e9;
  const params = new URLSearchParams({ message });
  if (state.store) params.set("store", state.store);
  // Recent conversation for memory. Capped to keep the prompt small (the model has a
  // large system prompt already); only user/assistant text turns, no tool internals.
  const hist = state.chatHistory.slice(-10);
  if (hist.length) params.set("history", JSON.stringify(hist));
  fetch("/ui/chat", { method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: params })
    .then((r) => r.json())
    .then((d) => {
      renderAnswer(bubble, d);
      state.chatHistory.push({ role: "user", text: message },
                             { role: "assistant", text: d.message || "" });
    })
    .catch(() => {
      bubble.classList.remove("thinking");
      bubble.textContent = "⚠️ AIサービスに接続できませんでした。";
    });
}

// Render the agent's final answer (Markdown) plus a collapsible tool-step transcript
// (the SQL it ran and each result) — the "show steps" pattern for the non-streaming agent.
function renderAnswer(bubble, d) {
  bubble.classList.remove("thinking");
  bubble.classList.add("md");
  bubble.innerHTML = mdToHtml(d.message || "");
  if (d.steps && d.steps.length) {
    const det = document.createElement("details");
    det.className = "steps";
    const sum = document.createElement("summary");
    sum.textContent = `実行ステップ (${d.tool_calls || d.steps.length})`;
    det.appendChild(sum);
    d.steps.forEach((s) => {
      const inp = (s.input && (s.input.query || s.input.command)) || JSON.stringify(s.input);
      const div = document.createElement("div");
      div.className = "step";
      div.innerHTML =
        `<div class="step-tool">${escapeHtml(s.tool)}</div>`
        + `<pre class="step-in">${escapeHtml(inp)}</pre>`
        + `<pre class="step-out">${escapeHtml(s.output || "")}</pre>`;
      det.appendChild(div);
    });
    bubble.appendChild(det);
  }
  document.getElementById("chat-log").scrollTop = 1e9;
}

/* ---------------------------------------- SHAP decomposition (waterfall in chat) */
function decomposeInChat() {
  if (!state.store) { appendMsg("assistant", "先に店舗を選択してください。"); return; }
  appendMsg("user", "予測数量の分解（SHAP）");
  const bubble = appendMsg("assistant chart-msg", "");
  const title = document.createElement("div");
  title.className = "chart-title";
  title.textContent = "読み込み中…";
  const chartDiv = document.createElement("div");
  chartDiv.className = "chat-chart";
  bubble.appendChild(title);
  bubble.appendChild(chartDiv);

  // Target the highlighted day if one is selected; otherwise the server defaults to tomorrow.
  const q = state.selectedTarget ? `?target=${encodeURIComponent(state.selectedTarget)}` : "";
  fetch(`/api/store/${encodeURIComponent(state.store)}/shap${q}`)
    .then((r) => r.json())
    .then((d) => {
      if (!d.items) { bubble.classList.remove("chart-msg"); bubble.textContent = "SHAPデータがありません。"; return; }
      title.innerHTML = `<strong>${d.target_date}（${d.weekday}）</strong> の予測 `
        + `<strong>${Math.round(d.predicted)}杯</strong> の要因分解（SHAP）`;
      renderShapWaterfall(chartDiv, d);
      document.getElementById("chat-log").scrollTop = 1e9;
    })
    .catch(() => { bubble.classList.remove("chart-msg"); bubble.textContent = "取得に失敗しました。"; });

  // AI, plain-language explanation of the top drivers (streamed below the chart).
  const explainBubble = appendMsg("assistant streaming", "…");
  streamAssistant(`/ui/store/${encodeURIComponent(state.store)}/shap/explain${q}`, explainBubble);
}

function renderShapWaterfall(el, d) {
  // Waterfall: 基準値 -> each top contribution (+/-) -> その他 -> 予測.
  const cats = ["基準値"];
  const floor = [0];                                   // transparent offset per bar
  const bars = [{ value: d.base_value, itemStyle: { color: "#9aa7b5" } }];
  let running = d.base_value;
  const step = (label, delta) => {
    cats.push(label);
    floor.push(Math.min(running, running + delta));
    bars.push({ value: Math.abs(delta), _delta: delta,
                itemStyle: { color: delta >= 0 ? "#5b8f3f" : "#c0504d" } });
    running += delta;
  };
  d.items.forEach((it) => step(it.feature, it.shap));
  if (Math.round(d.other)) step("その他", d.other);
  cats.push("予測");
  floor.push(0);
  bars.push({ value: running, itemStyle: { color: "#3f6fb0" } });

  const chart = echarts.init(el);
  chart.setOption({
    grid: { left: 4, right: 8, top: 12, bottom: 78, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (p) => {
        if (p.seriesIndex === 0) return "";            // transparent offset series
        const dl = p.data._delta;
        const v = dl != null ? (dl > 0 ? "+" : "") + Math.round(dl) : Math.round(p.value);
        return `${p.name}<br/>${v} 杯`;
      },
    },
    xAxis: { type: "category", data: cats,
             axisLabel: { interval: 0, rotate: 45, fontSize: 9 } },
    yAxis: { type: "value", name: "杯" },
    series: [
      { type: "bar", stack: "wf", silent: true, itemStyle: { color: "transparent" }, data: floor },
      { type: "bar", stack: "wf", data: bars,
        label: { show: true, position: "top", fontSize: 9,
          formatter: (p) => {
            const dl = p.data._delta;
            return dl != null ? (dl > 0 ? "+" : "") + Math.round(dl) : Math.round(p.value);
          } } },
    ],
  });
}
