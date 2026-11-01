"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (x, sign) => (x == null || isNaN(x)) ? "–" :
  ((sign && x > 0 ? "+" : "") + Number(x).toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2}));
const S = { days: [], idx: 0, layers: {}, chart: null, timeline: [], req: 0, version: null, range: 0 };

// ---------------- map
const map = L.map("map", { zoomControl: true, preferCanvas: false }).setView([48.6, 35.5], 6);
const carto = L.tileLayer("https://{s}.basemap.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 18, subdomains: "abcd",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> 贡献者 &copy; <a href="https://carto.com/">CARTO</a> | 前线: <a href="https://deepstatemap.live">DeepStateMap</a>'
}).addTo(map);
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> 贡献者 | 前线: <a href="https://deepstatemap.live">DeepStateMap</a>'
});
// basemap fallback: if CARTO tiles fail (blocked network), switch to OSM automatically
let cartoOk = false, cartoErr = 0;
carto.on("tileload", () => { cartoOk = true; });
carto.on("tileerror", () => {
  if (!cartoOk && ++cartoErr === 4 && map.hasLayer(carto)) { map.removeLayer(carto); osm.addTo(map); osm.bringToBack(); }
});
// offline country borders (Natural Earth, served locally) so the map has context even with no tiles
const borders = L.geoJSON(null, { style: (f) => ({ color: f.properties.a3 === "UKR" ? "#222" : "#888",
  weight: f.properties.a3 === "UKR" ? 1.6 : 0.8, fill: false }), interactive: false }).addTo(map);
fetch("/api/borders").then(r => r.json()).then(g => borders.addData(g)).catch(() => {});
const occLayer = L.geoJSON(null, { style: { color: "#b71c1c", weight: 0.8, fillColor: "#e57373", fillOpacity: 0.32 }, interactive: false }).addTo(map);
const greyLayer = L.geoJSON(null, { style: { color: "#757575", weight: 0.5, fillColor: "#9e9e9e", fillOpacity: 0.45 }, interactive: false }).addTo(map);
const chgLayer = L.geoJSON(null, {
  style: (f) => f.properties.kind === "gain"
    ? { color: "#8b0000", weight: 1.5, fillColor: "#c0000b", fillOpacity: 0.8 }
    : { color: "#0d47a1", weight: 1.5, fillColor: "#1e88e5", fillOpacity: 0.8 },
  onEachFeature: (f, l) => l.bindPopup(() => popupHtml(f.properties), { maxWidth: 340 }),
}).addTo(map);
const markers = L.layerGroup().addTo(map);
L.control.layers({ "CARTO 浅色": carto, "OpenStreetMap": osm },
  { "国界（Natural Earth）": borders, "俄控区": occLayer, "灰色区": greyLayer, "变化区域": chgLayer, "变化标记": markers }, { collapsed: true }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const d = L.DomUtil.create("div", "legend");
  d.innerHTML = '<i style="background:#e57373;opacity:.6"></i>俄控区（DeepState）<br>' +
    '<i style="background:#9e9e9e"></i>灰色区 / 状态不明<br>' +
    '<i style="background:#c0000b"></i>俄方新增（对比期内）<br>' +
    '<i style="background:#1e88e5"></i>俄控减少 / 乌方收复<br>' +
    '<span class="muted">圆圈 = 变化位置（缩小时便于发现），点击查看详情</span>';
  return d;
};
legend.addTo(map);

function popupHtml(p) {
  const kind = p.kind === "gain" ? '<span class="gain">🔴 俄方新增</span>' : '<span class="loss">🔵 俄控减少 / 乌方收复</span>';
  let h = `<div class="popup"><h3>${kind} ${fmt(p.area_km2)} km²</h3>`;
  h += `<div><b>位置：</b>${esc(p.place)}</div>`;
  if (p.note) h += `<div><b>备注：</b>${esc(p.note)}</div>`;
  if (p.first_seen && p.first_seen.length)
    h += `<div><b>出现于（按日快照）：</b>${p.first_seen.map(s => `${esc(s.snapshot_time)}（${fmt(s.km2)} km²）`).join("；")}</div>`;
  else h += `<div class="muted">逐日时间：本地缓存不足，无法细分到具体日期</div>`;
  if (p.deepstate_notes && p.deepstate_notes.length) {
    h += `<div><b>DeepState 说明（附近 6 km 内）：</b><ul style="padding-left:16px;margin:2px 0">`;
    h += p.deepstate_notes.map(n => `<li>${esc(n.time)}：${esc(n.text)}</li>`).join("");
    h += `</ul></div>`;
  } else h += `<div class="muted">对比区间内没有位置匹配的 DeepState 说明</div>`;
  h += `<div><a href="${esc(p.deepstate_url)}" target="_blank" rel="noopener">在 DeepStateMap 中查看 ↗</a></div></div>`;
  return h;
}

// ---------------- data loading
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.json();
}
function busy(on) { $("loading").style.display = on ? "block" : "none"; }
function curDate() { return S.days.length ? S.days[S.idx].date : null; }

async function loadMeta(keepDate) {
  const m = await api("/api/meta");
  const prev = keepDate ? curDate() : null;
  S.days = m.days;
  const sl = $("dateSlider");
  sl.max = Math.max(0, S.days.length - 1);
  let i = S.days.length - 1;
  if (prev) { const j = S.days.findIndex(d => d.date === prev); if (j >= 0) i = j; }
  S.idx = i; sl.value = i;
  if (S.days.length) { $("datePick").min = S.days[0].date; $("datePick").max = S.days[S.days.length - 1].date; }
  showRefresh(m.refresh);
  return m;
}

function showRefresh(r) {
  const btn = $("refreshBtn");
  btn.disabled = !!r.running;
  let t = r.running ? `刷新中：${r.step}${r.total ? ` (${r.done}/${r.total})` : ""}` :
    (r.finished ? `上次刷新：${r.finished}` : "");
  $("refreshStatus").textContent = t;
  if (r.log && r.log.length) {
    $("sources").innerHTML = r.log.map(s => `<li class="lvl-${s.level}"><b>${esc(s.source)}</b>：${esc(s.msg)}</li>`).join("");
  }
}

async function selectDate() {
  const d = curDate(); if (!d) return;
  const my = ++S.req;
  $("datePick").value = d; $("dateSlider").value = S.idx;
  const info = S.days[S.idx];
  const isw = info.source === "isw";
  $("snapInfo").innerHTML = (isw
      ? `<span class="srcbadge isw">ISW</span> ISW/CTP 评估控制区（${esc(info.date)}）—— DeepState 公开历史从 2022-04-03 开始，此前使用 ISW 数据，无灰色区`
      : `<span class="srcbadge deepstate">DeepState</span> 快照 #${info.snapshot_id}，发布于 ${esc(info.time)}（巴黎）` + (info.updated ? "" : " —— 当日无新快照，沿用此前最近快照"));
  busy(true);
  try {
    const [lay] = await Promise.all([api(`/api/layers?date=${d}`), loadChanges(my)]);
    if (my !== S.req) return;
    occLayer.clearLayers(); greyLayer.clearLayers();
    if (lay.occupied) occLayer.addData(lay.occupied);
    if (lay.grey) greyLayer.addData(lay.grey);
    $("sOcc").textContent = fmt(lay.occupied_km2) + " km²";
    $("sGrey").textContent = fmt(lay.grey_km2) + " km²";
  } catch (e) { $("changesMsg").textContent = "加载失败：" + e.message; }
  finally { if (my === S.req) busy(false); }
  loadIsw(d, my); loadNews(d, my); markChart();
  if (window.aidOnDate) window.aidOnDate(d);
}

async function loadChanges(my) {
  const d = curDate(), c = $("compare").value;
  let url = `/api/changes?date=${d}&compare=${c}`;
  if (c === "custom") { if (!$("fromPick").value) { $("compareInfo").textContent = "请选择对比日期"; return; } url += `&from=${$("fromPick").value}`; }
  let r;
  try { r = await api(url); } catch (e) { $("changesMsg").textContent = "变化计算失败：" + e.message; chgLayer.clearLayers(); markers.clearLayers(); return; }
  if (my !== S.req) return;
  $("compareInfo").textContent = `对比快照 #${r.from.id}（${r.from.time}）→ #${r.to.id}（${r.to.time}）`;
  $("sGain").textContent = fmt(r.stats.gained_km2) + " km²";
  $("sLoss").textContent = fmt(r.stats.lost_km2) + " km²";
  $("sNet").textContent = fmt(r.stats.net_km2, true) + " km²" + `（灰色区 ${fmt(r.stats.grey_change_km2, true)} km²）`;
  $("changesMsg").textContent = r.message || (`${r.stats.n_changes} 个 ≥0.05 km² 的变化区块（净变化含全部差异）`
    + (r.source_switch ? "。⚠️ 对比跨越数据源切换（ISW→DeepState），口径不同，差异包含方法差别" : "")
    + (r.long_window ? "。区间超过 45 天，未逐日细分出现日期" : ""));
  chgLayer.clearLayers(); markers.clearLayers();
  chgLayer.addData(r.changes);
  for (const f of r.changes.features) {
    const p = f.properties, col = p.kind === "gain" ? "#c0000b" : "#1565c0";
    L.circleMarker([p.lat, p.lon], { radius: Math.min(26, 6 + 5 * Math.sqrt(p.area_km2)), color: col, weight: 2, fill: false })
      .bindPopup(() => popupHtml(p), { maxWidth: 340 }).addTo(markers);
  }
  $("sectors").innerHTML = r.sectors.length ? r.sectors.map((s, i) =>
    `<li data-i="${i}"><b>${esc(s.name)}</b>（${esc(s.oblast)}）：<span class="gain">+${fmt(s.gain)}</span> / <span class="loss">−${fmt(s.loss)}</span> km²，净 ${fmt(s.net, true)}（${s.n} 块）</li>`).join("")
    : '<li class="muted" style="list-style:none">无</li>';
  [...$("sectors").children].forEach((li) => {
    const s = r.sectors[+li.dataset.i]; if (!s) return;
    li.onclick = () => { const b = s.bounds; map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: [60, 60], maxZoom: 13 }); };
  });
  $("notes").innerHTML = r.notes.length ? r.notes.map(n =>
    `<li><b>${esc(n.time)}</b>：${esc(n.text)} ${n.places.map(p => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}↗</a>`).join(" ")}</li>`).join("")
    : '<li class="muted">区间内无 DeepState 更新</li>';
}

async function loadIsw(d, my) {
  $("isw").innerHTML = '<span class="muted">加载中…</span>';
  try {
    const r = await api(`/api/isw?date=${d}`);
    if (my !== S.req) return;
    if (!r.items || !r.items.length) { $("isw").innerHTML = `<span class="muted">未获取到 ISW 报告。${esc(r.note || "")}</span>`; return; }
    $("isw").innerHTML = (r.note ? `<div class="muted">${esc(r.note)}</div>` : "") + r.items.map(it =>
      `<div><a href="${esc(it.url)}" target="_blank" rel="noopener"><b>${esc(it.title)}</b> ↗</a>
       ${it.asof ? `<div class="muted">${esc(it.asof)}</div>` : ""}
       <ul class="list">${(it.takeaways || []).map(t => `<li>${esc(t)}</li>`).join("") || '<li class="muted">仅标题</li>'}</ul></div>`).join("") +
      `<div class="muted">来源：${esc(r.source || "")}</div>`;
  } catch (e) { $("isw").textContent = "ISW 获取失败：" + e.message; }
}

async function loadNews(d, my) {
  $("news").innerHTML = '<li class="muted">加载中…</li>';
  try {
    const r = await api(`/api/news?date=${d}`);
    if (my !== S.req) return;
    $("news").innerHTML = r.items.length ? r.items.map(n =>
      `<li><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="muted">— ${esc(n.source)} ${n.ts ? new Date(n.ts).toLocaleString("zh-CN", {timeZone: "Europe/Paris", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : ""}</span></li>`).join("")
      : `<li class="muted">未获取到新闻。${esc((r.sources || []).map(s => s.msg).join("；"))}</li>`;
  } catch (e) { $("news").innerHTML = `<li>新闻获取失败：${esc(e.message)}</li>`; }
}

// ---------------- chart
async function loadTimeline() {
  S.timeline = await api("/api/timeline");
  drawChart();
}
function chartRows() {
  if (!S.range) return S.timeline;
  return S.timeline.slice(Math.max(0, S.timeline.length - S.range));
}
function drawChart() {
  const rows = chartRows();
  S.chartRows = rows;
  const cfg = {
    data: {
      labels: rows.map(r => r.date),
      datasets: [
        { type: "line", label: "俄控面积 km²", data: rows.map(r => r.occupied_km2), yAxisID: "y", borderWidth: 1.6, pointRadius: 0,
          segment: { borderColor: (c) => rows[c.p1DataIndex].source === "isw" ? "#ef6c00" : "#c62828" }, borderColor: "#c62828" },
        { type: "line", label: "累计净变化 km²（自 2022-02-24）", data: rows.map(r => r.cum_net_km2), yAxisID: "y1", borderColor: "#111", borderWidth: 1.2, pointRadius: 0 },
        { type: "bar", label: "每日净变化 km²", data: rows.map(r => r.net_km2), yAxisID: "y2",
          backgroundColor: rows.map(r => (r.net_km2 || 0) >= 0 ? "rgba(192,0,11,.55)" : "rgba(30,136,229,.65)") },
      ],
    },
    options: {
      animation: false, responsive: true, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, font: { size: 10 } } },
        tooltip: { callbacks: {
          title: (it) => { const r = S.chartRows[it[0].dataIndex]; return r.date + (r.source === "isw" ? "（ISW 数据）" : "（DeepState）"); },
          afterBody: (it) => { const r = S.chartRows[it[0].dataIndex]; return r.note ? "注：" + r.note : ""; } } } },
      scales: { x: { ticks: { maxTicksLimit: 8, font: { size: 10 }, callback: function (v) { const l = this.getLabelForValue(v); return S.range && S.range <= 365 ? l.slice(5) : l.slice(0, 7); } } },
        y: { position: "left", title: { display: true, text: "俄控 km²", font: { size: 10 } }, ticks: { font: { size: 9 } } },
        y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "累计净变化 km²", font: { size: 10 } }, ticks: { font: { size: 9 } } },
        y2: { display: false } },
      onClick: (e, els) => { if (!els.length) return; const d = S.chartRows[els[0].index].date; const j = S.days.findIndex(x => x.date === d); if (j >= 0) { S.idx = j; selectDate(); } },
    },
  };
  if (S.chart) S.chart.destroy();
  S.chart = new Chart($("chart"), cfg);
  markChart();
}
function markChart() {
  if (!S.chart || !S.chartRows) return;
  const d = curDate(); const j = S.chartRows.findIndex(r => r.date === d);
  S.chart.data.datasets[0].pointRadius = S.chartRows.map((r, i) => i === j ? 5 : 0);
  S.chart.data.datasets[0].pointBackgroundColor = "#ff9800";
  S.chart.update();
}
document.querySelectorAll(".rng").forEach(b => b.onclick = () => {
  document.querySelectorAll(".rng").forEach(x => x.classList.remove("active")); b.classList.add("active");
  S.range = +b.dataset.days; drawChart();
});

// ---------------- controls
let sliderTimer = null;
$("dateSlider").oninput = (e) => { S.idx = +e.target.value; const i = S.days[S.idx]; $("datePick").value = i.date; clearTimeout(sliderTimer); sliderTimer = setTimeout(selectDate, 250); };
$("datePick").onchange = (e) => { const j = S.days.findIndex(x => x.date === e.target.value); if (j >= 0) { S.idx = j; selectDate(); } else alert("该日期不在已缓存的范围内"); };
$("prevDay").onclick = () => { if (S.idx > 0) { S.idx--; selectDate(); } };
$("nextDay").onclick = () => { if (S.idx < S.days.length - 1) { S.idx++; selectDate(); } };
$("latestBtn").onclick = () => { S.idx = S.days.length - 1; selectDate(); };
$("compare").onchange = () => { $("fromPick").style.display = $("compare").value === "custom" ? "" : "none"; S.req++; const my = S.req; busy(true); loadChanges(my).finally(() => busy(false)); };
$("fromPick").onchange = () => { const my = ++S.req; busy(true); loadChanges(my).finally(() => busy(false)); };

// ---------------- background auto-update (server-side scheduler + client polling)
const POLL_IDLE_MS = 60 * 1000, POLL_BUSY_MS = 3000;
let pollTimer = null;
function toast(msg) { const t = $("toast"); t.textContent = msg; t.style.display = "block"; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = "none", 6000); }
function showSchedule(sc) {
  if (!sc) return;
  $("lastChecked").textContent = sc.last_checked_paris || "尚未";
  $("lastNew").textContent = sc.last_new_data_paris || "–";
  $("nextRun").textContent = sc.interval_min > 0 ? (sc.next_run_paris || "即将") : "已关闭";
  const sel = $("intervalSel");
  if (![...sel.options].some(o => +o.value === sc.interval_min)) {
    const o = document.createElement("option"); o.value = sc.interval_min; o.textContent = sc.interval_min + " 分钟"; sel.appendChild(o);
  }
  sel.value = String(sc.interval_min);
  sel.title = sc.interval_source === "env" ? "当前由环境变量 UF_REFRESH_MINUTES 设定；在此修改仅对本次运行生效" : "后台自动更新间隔（保存在本机）";
}
async function applyNewData() {
  const atLatest = S.idx >= S.days.length - 1;      // user was viewing the latest date -> follow it
  await loadMeta(!atLatest);
  if (atLatest) S.idx = S.days.length - 1;
  await loadTimeline();
  if (window.aidReload) await window.aidReload();
  await selectDate();                                  // map zoom / compare selection are kept
  toast("已自动载入新数据" + (atLatest ? "，已切换到最新日期 " + curDate() : ""));
}
async function poll() {
  clearTimeout(pollTimer);
  let r;
  try { r = await api("/api/status"); } catch (e) { pollTimer = setTimeout(poll, POLL_IDLE_MS); return; }
  showRefresh(r); showSchedule(r.schedule);
  const v = r.schedule && r.schedule.data_version;
  if (!r.running && v && S.version && v !== S.version) { S.version = v; try { await applyNewData(); } catch (e) { console.error(e); } }
  else if (v && !r.running) S.version = v;
  pollTimer = setTimeout(poll, r.running ? POLL_BUSY_MS : POLL_IDLE_MS);
}
$("intervalSel").onchange = async (e) => {
  try { showSchedule(await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_minutes: +e.target.value }) })); toast("自动更新间隔已设为 " + (e.target.value === "0" ? "关闭" : e.target.value + " 分钟")); }
  catch (err) { alert("设置失败：" + err.message); }
};
$("refreshBtn").onclick = async () => { showRefresh(await api("/api/refresh", { method: "POST" })); setTimeout(poll, 800); };

(async function init() {
  busy(true);
  try {
    const m = await loadMeta(false);
    if (!S.days.length) { $("snapInfo").textContent = "尚无缓存数据，正在后台获取，请稍候…"; }
    await loadTimeline();
    await selectDate();
    S.version = m.schedule ? m.schedule.data_version : null;
    showSchedule(m.schedule);
    setTimeout(poll, 1500);
  } catch (e) { $("snapInfo").textContent = "初始化失败：" + e.message; }
  finally { busy(false); }
})();
