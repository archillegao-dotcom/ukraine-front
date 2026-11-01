"use strict";
// ---------------- 援助与政治 tab (Kiel Ukraine Support Tracker + aid/political headlines)
(function () {
  const A = { chart: null, series: [], data: null, newsCat: "", loadedFor: null, req: 0, active: false };
  const CAT = { military: "军事", financial: "经济/金融", humanitarian: "人道", political: "政治", other: "其他" };
  const MEAS = { Allocation: "已分配", Commitment: "承诺（未来）" };
  const ISO = { "Australia": "AU", "Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Canada": "CA", "China": "CN",
    "Croatia": "HR", "Cyprus": "CY", "Czechia": "CZ", "Denmark": "DK", "Estonia": "EE", "Finland": "FI", "France": "FR",
    "Germany": "DE", "Greece": "GR", "Hungary": "HU", "Iceland": "IS", "India": "IN", "Ireland": "IE", "Italy": "IT",
    "Japan": "JP", "Latvia": "LV", "Lithuania": "LT", "Luxembourg": "LU", "Malta": "MT", "Netherlands": "NL",
    "New Zealand": "NZ", "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO", "Slovakia": "SK",
    "Slovenia": "SI", "South Korea": "KR", "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "Taiwan": "TW",
    "Turkiye": "TR", "United Kingdom": "GB", "United States": "US", "EU (Commission and Council)": "EU",
    "European Investment Bank": "EU", "European Peace Facility": "EU" };
  // ISO badge instead of emoji flags (Windows does not render flag emoji)
  const flag = (n) => `<span class="iso" title="${esc(n)}">${ISO[n] || "··"}</span>`;
  const eur = (v) => v == null ? "金额未披露" : v >= 1e9 ? `€${(v / 1e9).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 十亿`
    : v >= 1e6 ? `€${(v / 1e6).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 百万` : `€${Math.round(v).toLocaleString("zh-CN")}`;
  const bn = (v) => v == null ? "–" : `€${(v / 1e9).toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} bn`;

  // tabs
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === b));
    $("tabFront").hidden = b.dataset.tab !== "tabFront";
    $("tabAid").hidden = b.dataset.tab !== "tabAid";
    A.active = b.dataset.tab === "tabAid";
    $("panel").classList.toggle("wide", A.active);
    setTimeout(() => map.invalidateSize(), 50);
    if (A.active) { if (!A.series.length) loadSeries(); const d = curDate(); if (d && A.loadedFor !== d + "|" + $("aidWin").value) load(d); }
  });

  function showMeta(m) {
    if (!m || !m.release) { $("aidMeta").innerHTML = "⚠️ 尚无 Kiel 援助数据（后台会尝试下载）"; return; }
    $("aidMeta").innerHTML = `📊 援助数据：<a href="${esc(m.page)}" target="_blank" rel="noopener">Kiel Institute Ukraine Support Tracker</a>
      <b>${esc(m.release)}</b>，覆盖至 <b>${esc(m.coverage_end)}</b>（较今天滞后 <b>${m.lag_days ?? "?"}</b> 天；Kiel 约每 1–2 个月发布一次新版本）<br>
      共 ${m.rows} 条记录 · 本机下载于 ${esc(m.downloaded_utc_paris || "–")} · 上次检查新版本 ${esc(m.checked_utc_paris || "–")}（巴黎时间）`;
  }

  async function loadSeries() {
    try {
      const r = await api("/api/aid/timeline");
      A.series = r.series; A.meta = A.meta || r.meta; showMeta(r.meta); draw();
    } catch (e) { $("aidMeta").textContent = "援助时间线加载失败：" + e.message; }
  }

  function draw() {
    const s = A.series; if (!s.length) return;
    const front = new Map((S.timeline || []).map(r => [r.date, r.occupied_km2]));
    // daily x-axis from the war start to today so both series line up
    const start = "2022-02-24", end = (S.days.length ? S.days[S.days.length - 1].date : s[s.length - 1].date);
    const labels = []; for (let d = new Date(start + "T12:00:00Z"); ; d.setUTCDate(d.getUTCDate() + 1)) { const k = d.toISOString().slice(0, 10); labels.push(k); if (k >= end) break; }
    let j = 0, cur = { military: 0, financial: 0, humanitarian: 0 };
    while (j < s.length && s[j].date < start) { cur = s[j]; j++; }     // Jan-Feb 2022 pre-invasion pledges
    const cov = (A.meta && A.meta.coverage_end) || s[s.length - 1].date;
    const rows = labels.map(k => { while (j < s.length && s[j].date <= k) { cur = s[j]; j++; } return k <= cov ? cur : null; });
    const ds = [
      { label: "军事", key: "military", c: "rgba(109,76,65,.75)" }, { label: "经济/金融", key: "financial", c: "rgba(21,101,192,.6)" },
      { label: "人道", key: "humanitarian", c: "rgba(46,125,50,.6)" }].map(x => ({
        type: "line", label: x.label + "（十亿€）", data: rows.map(r => r ? r[x.key] : null), backgroundColor: x.c, borderColor: x.c,
        fill: true, stack: "aid", pointRadius: 0, borderWidth: 1, yAxisID: "y" }));
    if ($("aidOverlay").checked)
      ds.push({ type: "line", label: "俄控面积 km²（右轴）", data: labels.map(k => front.get(k) ?? null), borderColor: "#9e9e9e",
        borderWidth: 1.2, pointRadius: 0, yAxisID: "y1", fill: false, stack: "front" });
    A.labels = labels;
    const cfg = {
      data: { labels, datasets: ds },
      options: { animation: false, responsive: true, interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { boxWidth: 10, font: { size: 10 } } },
          tooltip: { callbacks: { label: (c) => c.raw == null ? null : `${c.dataset.label}: ${Number(c.raw).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}` } } },
        scales: { x: { ticks: { maxTicksLimit: 8, font: { size: 10 }, callback: function (v) { return this.getLabelForValue(v).slice(0, 7); } } },
          y: { stacked: true, position: "left", title: { display: true, text: "十亿欧元", font: { size: 10 } }, ticks: { font: { size: 9 } } },
          y1: { display: $("aidOverlay").checked, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "俄控 km²", font: { size: 10 } }, ticks: { font: { size: 9 } } } },
        onClick: (e, els) => { if (!els.length) return; const d = A.labels[els[0].index]; const k = S.days.findIndex(x => x.date === d); if (k >= 0) { S.idx = k; selectDate(); } } },
      plugins: [{ id: "vline", afterDatasetsDraw: (ch) => {
        const d = curDate(); const i = A.labels.indexOf(d); if (i < 0) return;
        const x = ch.scales.x.getPixelForValue(i), y = ch.scales.y;
        const g = ch.ctx; g.save(); g.strokeStyle = "#ff9800"; g.lineWidth = 2; g.beginPath(); g.moveTo(x, y.top); g.lineTo(x, y.bottom); g.stroke(); g.restore(); } }],
    };
    if (A.chart) A.chart.destroy();
    A.chart = new Chart($("aidChart"), cfg);
  }

  async function load(d) {
    const my = ++A.req, w = $("aidWin").value;
    $("aidDateLbl").textContent = `所选日期 ${d}`;
    $("aidList").innerHTML = '<li class="muted">加载中…</li>'; $("pnews").innerHTML = '<li class="muted">加载中…</li>';
    let r;
    try { r = await api(`/api/aid?date=${d}&window=${w}`); }
    catch (e) { $("aidList").innerHTML = `<li>加载失败：${esc(e.message)}</li>`; $("pnews").innerHTML = ""; return; }
    if (my !== A.req) return;
    A.data = r; A.meta = r.meta; A.loadedFor = d + "|" + w; showMeta(r.meta);
    $("aidDateLbl").textContent = `所选日期 ${d}（窗口 ${r.from} 至 ${r.to}）`;
    $("aidNote").hidden = !r.note; $("aidNote").textContent = r.note ? "⚠️ " + r.note : "";
    const s = r.summary, c = s.by_category_eur;
    $("aTot").textContent = bn(s.total_eur);
    $("aMil").textContent = bn(c.military || 0); $("aFin").textContent = bn(c.financial || 0); $("aHum").textContent = bn(c.humanitarian || 0);
    $("aCnt").textContent = `${s.allocations.toLocaleString()}（${s.allocations_without_value.toLocaleString()}）`;
    const cm = s.commitments_eur || {}; const ct = (cm.military || 0) + (cm.financial || 0) + (cm.humanitarian || 0);
    $("aCommit").innerHTML = `另有 Kiel 记录的<b>未来承诺</b>（commitments，多年期预算等，未计入上面合计）条目金额约 ${bn(ct)}。` +
      `条目按 Kiel 子活动（sub-activity）计数；“无金额”指整个子活动没有任何金额（多为未公开价值的武器援助），未计入金额合计。`;
    const top = s.top_donors, mx = top.length ? top[0].eur : 1;
    $("aDonors").innerHTML = top.map(t => `<div class="d"><span class="n">${flag(t.donor)} ${esc(t.donor)}</span>
      <span class="bar" style="width:${Math.max(2, 200 * t.eur / mx)}px"></span><span>${bn(t.eur)}</span></div>`).join("") || '<span class="muted">无</span>';
    renderAid(); renderNews();
    if (A.chart) A.chart.update();
  }

  function renderAid() {
    const r = A.data; if (!r) return;
    const cats = [...document.querySelectorAll("#aidFilt input:checked")].map(x => x.value), m = $("aidMeasure").value;
    const ev = r.aid.filter(e => cats.includes(e.category) && (m === "all" || e.measure === m));
    const val = ev.reduce((a, e) => a + (e.value_eur || 0), 0);
    $("aidCount").textContent = `（${ev.length} 条，合计 ${eur(val)}；窗口 ${r.from} 至 ${r.to}）`;
    $("aidList").innerHTML = ev.length ? ev.slice(0, 300).map(e => `<li>
      <b>${esc(e.date)}</b> ${flag(e.donor)} <b>${esc(e.donor)}</b> <span class="cat ${e.category}">${CAT[e.category] || e.category}</span>
      <span class="muted">${esc(e.aid_type)} · ${MEAS[e.measure] || e.measure}</span><br>
      <span class="amt">${eur(e.value_eur)}</span> <span class="muted">${esc(e.value_basis)}${e.date_note ? " · " + esc(e.date_note) : ""}</span>
      ${e.items.length ? `<div class="muted">装备/物资：${esc(e.items.join("；"))}</div>` : ""}
      <div class="desc clip" title="点击展开/收起">${esc(e.description)}</div>
      ${e.source_url ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener">原始来源 ↗</a>` : '<span class="muted">Kiel 未给出链接</span>'}
      <span class="muted"> · Kiel ID ${esc(e.activity_id)}</span></li>`).join("")
      : `<li class="muted">该窗口内 Kiel 数据中没有${m === "Commitment" ? "承诺" : "援助"}记录${r.note ? "（数据尚未覆盖此日期）" : ""}。</li>`;
    $("aidList").querySelectorAll(".desc").forEach(x => x.onclick = () => x.classList.toggle("clip"));
  }

  function renderNews() {
    const r = A.data; if (!r) return;
    const all = r.news, items = A.newsCat ? all.filter(n => n.category === A.newsCat) : all;
    const cnt = {}; all.forEach(n => cnt[n.category] = (cnt[n.category] || 0) + 1);
    document.querySelectorAll(".nf").forEach(b => { const c = b.dataset.c; b.textContent = (c ? CAT[c] : "全部") + `（${c ? cnt[c] || 0 : all.length}）`; });
    $("newsCount").textContent = `（${r.from} 至 ${r.to}，${all.length} 条）`;
    const err = (r.news_status && r.news_status.errors || []).join("；");
    $("pnews").innerHTML = items.length ? items.slice(0, 200).map(n => `<li><span class="cat ${n.category}">${CAT[n.category] || n.category}</span>
      <a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>
      <span class="muted">— ${esc(n.outlet || "?")} · ${esc(n.date)}</span> <span class="auto">自动分类</span></li>`).join("")
      : `<li class="muted">该窗口/分类无新闻。${esc(err)}</li>`;
  }

  document.querySelectorAll(".nf").forEach(b => b.onclick = () => {
    document.querySelectorAll(".nf").forEach(x => x.classList.toggle("active", x === b)); A.newsCat = b.dataset.c; renderNews(); });
  document.querySelectorAll("#aidFilt input").forEach(x => x.onchange = renderAid);
  $("aidMeasure").onchange = renderAid;
  $("aidWin").onchange = () => { const d = curDate(); if (d) load(d); };
  $("aidOverlay").onchange = draw;

  window.aidOnDate = (d) => { if (A.active) load(d); else A.loadedFor = null; if (A.chart) A.chart.update(); };
  window.aidReload = async () => { A.series = []; if (A.active) { await loadSeries(); const d = curDate(); if (d) await load(d); } };
  if (location.hash === "#aid") setTimeout(() => document.querySelector('.tab[data-tab="tabAid"]').click(), 300);
})();
