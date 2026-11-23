/* ragpoison web UI. Plain JS, no build step; every action is a call to the REST API. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => (v == null ? "–" : `${v}%`);
const store = {
  get(k, d) { try { const v = localStorage.getItem("ragpoison." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ragpoison." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const state = {
  status: null,
  target: store.get("target", { kind: "local", collection: "lab" }),
  measure: store.get("measure", {}),
  defense: "outlier",
};

// ------------------------------------------------------------------ API

async function api(method, path, body) {
  const headers = {};
  const token = sessionStorage.getItem("ragpoison.token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401) {
    const t = prompt("This server requires an API token:");
    if (t) { sessionStorage.setItem("ragpoison.token", t); return api(method, path, body); }
  }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "object" ? data.detail ?? JSON.stringify(data) : data);
  return data;
}

function toast(msg, ok = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (ok ? " ok" : ""); t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), ok ? 2500 : 7000);
}

async function busy(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message); throw e; } finally { if (btn) btn.disabled = false; }
}

/** Start a background job and stream its log into the job bar. Resolves with the job result. */
async function runJob(path, body, label) {
  const { job_id } = await api("POST", path, body);
  const bar = $("#jobbar"), log = $("#job-log"), spin = $("#job-spin");
  bar.hidden = false; log.textContent = ""; spin.className = "spinner";
  $("#job-kind").textContent = label; $("#job-status").textContent = "queued";
  let since = 0;
  for (;;) {
    const j = await api("GET", `/api/jobs/${job_id}?since=${since}`);
    if (j.log.length) { log.textContent += j.log.join("\n") + "\n"; log.scrollTop = log.scrollHeight; }
    since = j.log_len;
    const secs = j.started ? Math.round(((j.finished ?? Date.now() / 1000) - j.started)) : 0;
    $("#job-status").textContent = `${j.status}${secs ? ` · ${secs}s` : ""}`;
    if (j.status === "done") { spin.className = "spinner done"; return j.result; }
    if (j.status === "error") { spin.className = "spinner error"; log.hidden = false; throw new Error(j.error); }
    await new Promise((r) => setTimeout(r, 600));
  }
}

// ------------------------------------------------------------------ navigation

function go(page) {
  $$("section[data-page]").forEach((s) => (s.hidden = s.dataset.page !== page));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.go === page));
  store.set("page", page);
  ({ target: renderTarget, craft: loadCands, validate: renderPicks, measure: renderMeasure, api: renderApi, explore: renderExplore }[page] || (() => {}))();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (a) { e.preventDefault(); go(a.dataset.go); }
});
$("#job-toggle").onclick = () => ($("#job-log").hidden = !$("#job-log").hidden);

// ------------------------------------------------------------------ target

function targetSpec() { return { ...state.target }; }
function targetTag() {
  return state.target.kind === "local" ? `local_${state.target.collection}` : "remote";
}
function saveTarget() {
  store.set("target", state.target);
  const t = state.target;
  $("#target-pill").innerHTML = t.kind === "local"
    ? `target: <b>local</b> · ${esc(t.collection)}`
    : `target: <b>remote</b> · ${esc(t.base_url || "not set")}`;
}

function readRemoteForm() {
  let headers = {};
  const raw = $("#r-headers").value.trim();
  if (raw) { try { headers = JSON.parse(raw); } catch { throw new Error("Headers must be a JSON object"); } }
  state.target = {
    kind: "remote", base_url: $("#r-url").value.trim(), query_path: $("#r-qpath").value.trim() || "/query",
    ingest_path: $("#r-ipath").value.trim() || null, pool: +$("#r-pool").value || 10, headers,
    results_key: $("#r-results").value.trim(), id_key: $("#r-id").value.trim(),
    score_key: $("#r-score").value.trim(), text_key: $("#r-text").value.trim(),
  };
  saveTarget();
}

function fillRemoteForm() {
  const t = state.target.kind === "remote" ? state.target : store.get("remote", {});
  $("#r-url").value = t.base_url ?? ""; $("#r-qpath").value = t.query_path ?? "/query";
  $("#r-ipath").value = t.ingest_path ?? ""; $("#r-pool").value = t.pool ?? 10;
  $("#r-results").value = t.results_key ?? "results"; $("#r-id").value = t.id_key ?? "doc_id";
  $("#r-score").value = t.score_key ?? "score"; $("#r-text").value = t.text_key ?? "text";
  $("#r-headers").value = t.headers && Object.keys(t.headers).length ? JSON.stringify(t.headers) : "";
}

function setMode(kind) {
  $$("#mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === kind));
  $("#local-cfg").hidden = kind !== "local"; $("#remote-cfg").hidden = kind !== "remote";
  if (kind === "local") {
    if (state.target.kind === "remote") store.set("remote", state.target);
    state.target = { kind: "local", collection: $("#l-collection").value || "lab" };
    saveTarget(); refreshLocal();
  } else {
    fillRemoteForm();
    if ($("#r-url").value) readRemoteForm(); else { state.target = { kind: "remote", base_url: "" }; saveTarget(); }
  }
}
$$("#mode button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$("#l-collection").onchange = () => { state.target = { kind: "local", collection: $("#l-collection").value }; saveTarget(); refreshLocal(); };
$("#l-refresh").onclick = () => refreshLocal();
$("#l-build").onclick = (e) => busy(e.target, async () => {
  await runJob("/api/build", { collection: state.target.collection }, `build ${state.target.collection}`);
  toast(`Rebuilt '${state.target.collection}'`, true); refreshLocal();
});
["#r-url", "#r-qpath", "#r-ipath", "#r-pool", "#r-results", "#r-id", "#r-score", "#r-text", "#r-headers"]
  .forEach((id) => ($(id).onchange = () => { try { readRemoteForm(); } catch (e) { toast(e.message); } }));
$("#r-self").onclick = () => {
  $("#r-url").value = location.origin; $("#r-qpath").value = "/rag/query"; $("#r-ipath").value = "/rag/ingest";
  readRemoteForm(); toast("Remote target = this server's demo RAG endpoints over the 'lab' collection", true);
};
$("#r-test").onclick = (e) => busy(e.target, async () => {
  readRemoteForm();
  const r = await api("POST", "/api/query", { target: targetSpec(), query: "what are the risks of investing", k: 5 });
  $("#r-info").innerHTML = `<p class="hint">Connected. Sample query <i>what are the risks of investing</i>:</p>` + hitsCard(r.results);
});

async function refreshLocal() {
  const el = $("#l-info");
  try {
    const info = await api("POST", "/api/target/info", targetSpec());
    const opts = new Set(["lab", "clean", ...info.collections]);
    $("#l-collection").innerHTML = [...opts].map((c) => `<option ${c === info.collection ? "selected" : ""}>${esc(c)}</option>`).join("");
    const poison = info.docs.filter((d) => d.poison);
    el.innerHTML = info.chunks
      ? `<p class="stat"><b>${info.chunks}</b> chunks · <b>${info.docs.length}</b> documents · ` +
        (poison.length ? `<span class="badge psn">${poison.length} POISON</span> ${poison.map((d) => `<code>${esc(d.doc_id)}</code>`).join(" ")}` : `<span class="badge ok">clean</span>`) + `</p>`
      : `<p class="stat">Collection is empty. Rebuild it from the corpus to start.</p>`;
  } catch (e) { el.innerHTML = `<p class="stat">${esc(e.message)}</p>`; }
}

function renderTarget() {
  $$("#mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === state.target.kind));
  $("#local-cfg").hidden = state.target.kind !== "local"; $("#remote-cfg").hidden = state.target.kind !== "remote";
  if (state.target.kind === "local") refreshLocal(); else fillRemoteForm();
  const s = state.status; if (!s) return;
  $("#corpus").innerHTML = s.corpus.map((d) =>
    `<span class="chip ${d.target ? "tgt" : d.topic ? "topic" : ""}" data-doc="${esc(d.doc_id)}">${esc(d.doc_id)}${d.target ? " · target" : ""}</span>`).join("");
}
$("#corpus").onclick = async (e) => {
  const c = e.target.closest("[data-doc]"); if (!c) return;
  const d = await api("GET", `/api/corpus/${encodeURIComponent(c.dataset.doc)}`);
  const v = $("#doc-view"); v.hidden = false; v.innerHTML = `<h3>${esc(d.doc_id)}</h3>${esc(d.text)}`;
};

// ------------------------------------------------------------------ explore

let queries = null;
async function renderExplore() {
  queries = queries || await api("GET", "/api/queries");
  $("#q-canaries").innerHTML = `<span class="muted small">canaries:</span>` +
    queries.canary.map((q) => `<span class="chip" data-q="${esc(q)}">${esc(q)}</span>`).join("");
  $("#q-side-wrap").hidden = state.target.kind !== "local";
}
$("#q-canaries").onclick = (e) => { const c = e.target.closest("[data-q]"); if (c) { $("#q").value = c.dataset.q; $("#q-form").requestSubmit(); } };

function hitsCard(hits, title) {
  const max = Math.max(...hits.map((h) => Math.abs(h.score)), 1e-9);
  const tgt = state.status?.target_doc;
  return `<div class="card flush">${title ? `<div class="colhead">${title}</div>` : ""}` + (hits.length ? hits.map((h) => {
    const cls = h.poison ? "psn" : h.doc_id === tgt ? "tgt" : "";
    const w = Math.max(2, Math.round(120 * Math.abs(h.score) / max));
    return `<div class="hit ${cls}"><div class="rk">${h.rank}</div><div>
      <span class="id">${esc(h.doc_id)}</span> ${h.poison ? '<span class="badge psn">POISON</span>' : ""}${h.doc_id === tgt ? '<span class="badge tgt">TARGET</span>' : ""}
      <div class="scorebar"><i style="width:${w}px"></i>${h.score}${h.vector_score != null ? ` <span class="muted">(vector ${h.vector_score})</span>` : ""}</div></div>
      <div class="txt">${esc((h.text || "").slice(0, 260))}${(h.text || "").length > 260 ? "…" : ""}</div></div>`;
  }).join("") : `<div class="empty">no results</div>`) + `</div>`;
}

$("#q-form").onsubmit = (e) => {
  e.preventDefault();
  const q = $("#q").value.trim(); if (!q) return;
  busy($("#q-form button"), async () => {
    const body = { query: q, k: +$("#q-k").value || 5, rerank: $("#q-rerank").checked };
    const side = state.target.kind === "local" && $("#q-side").checked;
    const main = api("POST", "/api/query", { ...body, target: targetSpec() });
    const other = state.target.collection === "clean" ? "lab" : "clean";
    const cmp = side ? api("POST", "/api/query", { ...body, target: { kind: "local", collection: other } }) : null;
    const [a, b] = await Promise.all([main, cmp]);
    const label = state.target.kind === "local" ? `local · ${esc(state.target.collection)}` : `remote · ${esc(state.target.base_url)}`;
    $("#q-results").innerHTML = hitsCard(a.results, label) + (b ? hitsCard(b.results, `local · ${other}`) : "");
  });
};

// ------------------------------------------------------------------ craft

function sparkline(traj) {
  if (!traj || traj.length < 2) return "";
  const lo = Math.min(...traj), hi = Math.max(...traj), w = 300, h = 40, span = hi - lo || 1;
  const pts = traj.map((v, i) => `${(i / (traj.length - 1)) * w},${h - 4 - ((v - lo) / span) * (h - 8)}`);
  const ref = state.reference;
  const refY = ref != null && ref >= lo && ref <= hi ? h - 4 - ((ref - lo) / span) * (h - 8) : null;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="M${pts.join("L")}"/>` +
    (refY != null ? `<line x1="0" x2="${w}" y1="${refY}" y2="${refY}"/>` : "") + `</svg>`;
}

async function loadCands() {
  const [cands, s] = await Promise.all([api("GET", "/api/poison"), state.status ? null : refreshStatus()]);
  $("#n-attacker").textContent = state.status?.queries.attacker ?? "";
  const names = Object.keys(cands);
  $("#cands").innerHTML = names.length ? names.map((n) => {
    const c = cands[n], k = c.craft;
    const text = esc(c.text).replace(/(\[LAB-MARKER[^\]]*\])/g, '<span class="marker">$1</span>');
    return `<div class="card cand"><div class="head"><b>${esc(n)}</b><span class="spacer"></span>
      <button class="btn ghost small" data-del="${esc(n)}">delete</button></div>
      ${k ? `<div class="stat">mean cos to attacker cluster <b>${k.trajectory[0]} → ${k.mean_cos_to_cluster}</b> · ${k.iterations} iterations · ${k.chars} chars</div>${sparkline(k.trajectory)}`
          : `<div class="stat">hand-written · ${c.text.length} chars</div>`}
      <blockquote>${text}</blockquote></div>`;
  }).join("") : `<div class="empty">No candidates yet. Craft some, or add your own below.</div>`;
}
$("#cands").onclick = async (e) => {
  const b = e.target.closest("[data-del]"); if (!b) return;
  await busy(b, () => api("DELETE", `/api/poison/${encodeURIComponent(b.dataset.del)}`));
  loadCands(); refreshStatus();
};
$("#c-run").onclick = (e) => busy(e.target, async () => {
  const methods = $$(".methods input:checked").map((i) => i.value).join("");
  if (!methods) throw new Error("Pick at least one method");
  await runJob("/api/craft", { methods, seed: +$("#c-seed").value, max_chars: +$("#c-max").value, marker: $("#c-marker").value }, `craft ${methods}`);
  toast("Candidates crafted", true); loadCands(); refreshStatus();
});
$("#cu-save").onclick = (e) => busy(e.target, async () => {
  const name = $("#cu-name").value.trim(), text = $("#cu-text").value.trim();
  if (!name || !text) throw new Error("Name and text are both required");
  await api("PUT", `/api/poison/${encodeURIComponent(name)}`, { text });
  $("#cu-name").value = ""; $("#cu-text").value = ""; toast(`Saved ${name}`, true); loadCands(); refreshStatus();
});

// ------------------------------------------------------------------ candidate pickers

function pickerHTML(id) {
  const names = state.status?.poison ?? [];
  const off = new Set(store.get(`off.${id}`, []));
  return names.length ? names.map((n) => `<span class="chip ${off.has(n) ? "" : "on"}" data-pick="${esc(n)}">${esc(n)}</span>`).join("")
    : `<span class="muted small">no candidates: <a data-go="craft">craft some first</a></span>`;
}
function picked(id) { return $$(`#${id} .chip.on`).map((c) => c.dataset.pick); }
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-pick]"); if (!c) return;
  c.classList.toggle("on");
  const box = c.parentElement.id;
  store.set(`off.${box}`, $$(`#${box} [data-pick]`).filter((x) => !x.classList.contains("on")).map((x) => x.dataset.pick));
});
function renderPicks() { $("#v-pick").innerHTML = pickerHTML("v-pick"); $("#m-pick").innerHTML = pickerHTML("m-pick"); }

// ------------------------------------------------------------------ validate

function rankCell(r, good) {
  if (r == null) return `<td class="num muted">–</td>`;
  const hot = good ? r > 3 : r <= 3;
  return `<td class="num ${hot ? "down" : ""}">${r}</td>`;
}

$("#v-run").onclick = (e) => busy(e.target, async () => {
  const names = picked("v-pick"); if (!names.length) throw new Error("Pick at least one candidate");
  const out = await runJob("/api/validate", { names }, "validate (offline)");
  renderValidation(out);
});
function renderValidation(out) {
  const b = out.baseline, sets = Object.entries(out.candidates);
  const ref = Object.entries(out.trusted_topic_centrality || {});
  state.reference = ref.length ? Math.max(...ref.map(([, v]) => v)) : null;
  let html = `<div class="tablewrap"><table><thead><tr><th>set</th><th class="num">cos → cluster</th><th class="num">cos → target</th>
    <th class="num">poison top-3</th><th class="num">poison #1</th><th class="num">target top-3</th><th></th></tr></thead><tbody>
    <tr><td>baseline (no poison)</td><td></td><td></td><td class="num">–</td><td class="num">–</td><td class="num">${pct(b.target_in_top3_pct)}</td><td></td></tr>`;
  for (const [n, i] of sets) {
    html += `<tr><td><code>${esc(n)}</code></td><td class="num">${i.mean_cos_to_cluster ?? ""}</td><td class="num">${i.cos_to_target ?? ""}</td>
      <td class="num">${pct(i.poison_in_top3_pct)}</td><td class="num">${pct(i.poison_rank1_pct)}</td><td class="num">${pct(i.target_in_top3_pct)}</td>
      <td>${i.passes ? '<span class="badge bad">PASS</span>' : '<span class="badge ok">FAIL</span>'}</td></tr>`;
  }
  html += `</tbody></table></div>`;
  if (ref.length) html += `<p class="hint">Most central trusted topic doc: <b>${state.reference}</b> mean cosine (${ref.map(([d, v]) => `${esc(d)} ${v}`).join(", ")}). A candidate above that sits closer to the topic centre than any real document. PASS = poison in the top-3 for at least half the canaries.</p>`;
  html += `<h2>Per query: target rank / best poison rank</h2><div class="tablewrap"><table><thead><tr><th>canary</th>` +
    sets.map(([n]) => `<th class="num">${esc(n.replace(/^cand_/, ""))}</th>`).join("") + `</tr></thead><tbody>`;
  const qs = sets[0][1].per_query.map((r) => r.query);
  qs.forEach((q, qi) => {
    html += `<tr><td>${esc(q)}</td>` + sets.map(([, i]) => {
      const r = i.per_query[qi];
      return `<td class="num rk-cell"><span class="${r.target_rank > 3 ? "down" : ""}">${r.target_rank}</span> / <span class="${r.poison_rank <= 3 ? "down" : ""}">${r.poison_rank}</span></td>`;
    }).join("") + `</tr>`;
  });
  $("#v-out").innerHTML = html + `</tbody></table></div>`;
}

// ------------------------------------------------------------------ measure

function renderMeasure() {
  renderPicks();
  $("#n-canary").textContent = state.status?.queries.canary ?? "";
  $("#m-rebuild").parentElement.hidden = state.target.kind !== "local";
  const m = state.measure[targetTag()] || {};
  $("#m-base-s").textContent = m.base ? `saved ${m.base}` : "";
  $("#m-after-s").textContent = m.after ? `saved ${m.after}` : "";
  $("#m-inj-s").textContent = m.injected ? `injected ${m.injected.join(", ")}` : "";
  if (m.diff) renderDiff(m.diff); else $("#m-diff").innerHTML = "";
}
function saveMeasure(patch) {
  const tag = targetTag();
  state.measure[tag] = { ...(state.measure[tag] || {}), ...patch };
  store.set("measure", state.measure);
}
$("#m-base").onclick = (e) => busy(e.target, async () => {
  const tag = targetTag();
  if (state.target.kind === "local" && $("#m-rebuild").checked)
    await runJob("/api/build", { collection: state.target.collection }, `build ${state.target.collection}`);
  const name = `canary_${tag}_baseline.json`;
  const r = await runJob("/api/canary", { target: targetSpec(), rerank: false, save_as: name }, "baseline canary");
  saveMeasure({ base: name, after: null, diff: null, injected: null, baseSummary: r.summary });
  renderMeasure(); toast(`Baseline: target in top-3 on ${r.summary.target_in_top3_pct}% of canaries`, true);
});
$("#m-inject").onclick = (e) => busy(e.target, async () => {
  const names = picked("m-pick"); if (!names.length) throw new Error("Pick at least one candidate");
  const r = await api("POST", "/api/inject", { target: targetSpec(), names });
  saveMeasure({ injected: names }); renderMeasure();
  toast(`Injected ${names.length} document(s)${r.total_chunks ? `; store now holds ${r.total_chunks} chunks` : ""}`, true);
});
$("#m-after").onclick = (e) => busy(e.target, async () => {
  const tag = targetTag(), m = state.measure[tag] || {};
  if (!m.base) throw new Error("Measure the baseline first");
  const rerank = $("#m-rerank").checked;
  const name = `canary_${tag}_after${rerank ? "_rerank" : ""}.json`;
  await runJob("/api/canary", { target: targetSpec(), rerank, save_as: name }, `canary after${rerank ? " + rerank" : ""}`);
  const diff = await api("POST", "/api/compare", { before: m.base, after: name, label: `${tag}${rerank ? "_rerank" : ""}` });
  saveMeasure({ after: name, diff: { summary: diff.summary, rows: diff.rows, rerank } }); renderMeasure();
});

function tile(k, v, d, cls = "") { return `<div class="tile ${cls}"><div class="k">${k}</div><div class="v">${v}</div><div class="d muted">${d}</div></div>`; }
function renderDiff(d) {
  const s = d.summary;
  let html = `<h2>Result${d.rerank ? " (after: with cross-encoder rerank)" : ""}</h2><div class="tiles">` +
    tile("poison in top-3", pct(s.poison_in_top3_pct), "attacker text reaches the context", s.poison_in_top3_pct > 0 ? "bad" : "good") +
    tile("poison at rank 1", pct(s.poison_rank1_pct), "attacker text leads the context", s.poison_rank1_pct > 0 ? "bad" : "good") +
    tile("target in top-3", pct(s.target_in_top3_after_pct), `before ${pct(s.target_in_top3_before_pct)}`, s.target_in_top3_after_pct < s.target_in_top3_before_pct ? "bad" : "") +
    tile("target pushed out", pct(s.target_dropped_out_of_top3_pct), `${s.target_dropped_out_of_top3_of_previously_in} of those in the top-3 before`, s.target_dropped_out_of_top3_pct > 0 ? "bad" : "good") +
    `</div><div class="tablewrap"><table><thead><tr><th>canary</th><th class="num">target rank</th><th class="num">poison rank</th><th>top-3 after</th></tr></thead><tbody>`;
  const psn = new Set(state.status?.poison ?? []), tgt = state.status?.target_doc;
  for (const r of d.rows) {
    const [b, a] = r.target_rank;
    const mv = a == null || (b != null && a > b) ? "down" : a < b ? "up" : "";
    html += `<tr class="${r.dropped_out_of_top3 ? "flag" : ""}"><td>${esc(r.query)}</td>
      <td class="num rk-cell">${b ?? "–"} → <span class="${mv}">${a ?? "out"}</span></td>
      <td class="num">${r.poison_rank_after ?? "–"}</td>
      <td><div class="chips small">${r.top3_after.map((x) => `<span class="chip ${psn.has(x) ? "psn" : x === tgt ? "tgt" : ""}">${esc(x)}</span>`).join("")}</div></td></tr>`;
  }
  $("#m-diff").innerHTML = html + `</tbody></table></div>`;
}

// ------------------------------------------------------------------ defenses

const DEF_HINT = {
  outlier: "Mean similarity of each document to 50 queries across every topic, z-scored against the trusted corpus. Catches documents that are similar to everything.",
  topic: "Mean similarity of each incoming document to defender-side queries about the target topic, z-scored against the trusted documents of that topic (trusted docs scored leave-one-out).",
};
$$("#d-kind button").forEach((b) => (b.onclick = () => setDefense(b.dataset.d)));
function setDefense(d) {
  state.defense = d;
  $$("#d-kind button").forEach((b) => b.classList.toggle("on", b.dataset.d === d));
  $("#d-hint").textContent = DEF_HINT[d];
}
$("#d-run").onclick = (e) => busy(e.target, async () => {
  const z = +$("#d-z").value || 2;
  const out = await runJob("/api/defend", { defense: state.defense, z }, `defense: ${state.defense}`);
  renderDefense(out);
});
function renderDefense(out) {
  const s = out.summary, zkey = s.defense === "global_outlier" ? "z_vs_corpus" : "z_vs_topic_docs";
  const nIn = +s.incoming_flagged.split("/")[1], caught = +s.incoming_flagged.split("/")[0];
  const lo = -3, hi = 5, sc = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  let html = `<div class="tiles">` +
    tile("poison flagged", s.incoming_flagged, `at z > ${s.z_threshold}`, caught < nIn ? "bad" : "good") +
    tile("false positives", s.false_positives.length, s.false_positives.map(esc).join(", ") || "none", s.false_positives.length ? "bad" : "good") +
    tile("reference", s.reference_mean, `mean similarity, SD ${s.reference_sd}`) + `</div>`;
  html += `<div class="tablewrap"><table><thead><tr><th>document</th><th class="num">mean sim</th><th class="num">z</th><th>z (orange = threshold)</th><th></th></tr></thead><tbody>`;
  for (const [d, r] of Object.entries(out.docs)) {
    const z = r[zkey], x0 = sc(Math.min(0, z)), x1 = sc(Math.max(0, z));
    html += `<tr class="${r.flagged ? "flag" : ""}"><td><code>${esc(d)}</code> ${r.incoming ? '<span class="badge psn">INCOMING</span>' : ""}</td>
      <td class="num">${r.mean_sim}</td><td class="num">${z}</td>
      <td><div class="zbar"><i class="${r.flagged ? "hot" : ""}" style="left:${x0}%;width:${x1 - x0}%"></i><b style="left:${sc(s.z_threshold)}%"></b></div></td>
      <td>${r.flagged ? (r.incoming ? '<span class="badge ok">CAUGHT</span>' : '<span class="badge bad">FALSE POSITIVE</span>') : r.incoming ? '<span class="badge bad">MISSED</span>' : ""}</td></tr>`;
  }
  $("#d-out").innerHTML = html + `</tbody></table></div>`;
}

// ------------------------------------------------------------------ run everything

$("#run-all").onclick = (e) => busy(e.target, async () => {
  $("#job-log").hidden = false;
  const r = await runJob("/api/run", {}, "full pipeline");
  const v = r.vector_only, p = r.poisoned, rr = r.poisoned_rerank;
  $("#run-out").innerHTML = `<h2>Vector only</h2><div class="tiles">` +
    tile("poison in top-3", pct(p.poison_in_top3_pct), "", "bad") + tile("poison at rank 1", pct(p.poison_rank1_pct), "", "bad") +
    tile("target in top-3", pct(p.target_in_top3_pct), `baseline ${pct(r.baseline.target_in_top3_pct)}`) +
    tile("target pushed out", pct(v.target_dropped_out_of_top3_pct), v.target_dropped_out_of_top3_of_previously_in, "bad") +
    `</div><h2>With cross-encoder rerank</h2><div class="tiles">` +
    tile("poison in top-3", pct(rr.poison_in_top3_pct), "", "bad") + tile("poison at rank 1", pct(rr.poison_rank1_pct), "", "bad") +
    tile("target in top-3", pct(rr.target_in_top3_pct), "") +
    tile("target pushed out", pct(r.rerank_vs_vector.target_dropped_out_of_top3_pct), "vs the vector baseline") +
    `</div><h2>Detection</h2><div class="tiles">` +
    tile("global outlier: flagged", r.outlier.incoming_flagged, `false positives: ${r.outlier.false_positives.map(esc).join(", ") || "none"}`) +
    tile("in-topic centrality: flagged", r.topic.incoming_flagged, `false positives: ${r.topic.false_positives.map(esc).join(", ") || "none"}`) +
    `</div><h2>summary.md</h2><pre>${esc(r.markdown)}</pre>`;
  refreshStatus();
});

// ------------------------------------------------------------------ API page

async function renderApi() {
  const origin = location.origin;
  $("#api-ex").textContent = [
    `# query the local 'lab' collection`,
    `curl -s ${origin}/api/query -H 'Content-Type: application/json' \\`,
    `  -d '{"query": "could I lose money doing this", "k": 3}'`, ``,
    `# measure a remote RAG service on the canaries (returns a job id)`,
    `curl -s ${origin}/api/canary -H 'Content-Type: application/json' \\`,
    `  -d '{"target": {"kind": "remote", "base_url": "http://127.0.0.1:8766", "query_path": "/rag/query"}, "save_as": "remote_base.json"}'`,
    `curl -s ${origin}/api/jobs/<job_id>`, ``,
    `# this server as a toy RAG service`,
    `curl -s ${origin}/rag/query -H 'Content-Type: application/json' -d '{"query": "what are the risks here", "k": 3}'`,
  ].join("\n");
  const spec = await api("GET", "/openapi.json");
  $("#api-list").innerHTML = Object.entries(spec.paths).flatMap(([p, ms]) =>
    Object.entries(ms).map(([m, op]) => `<div class="endpoint"><span class="m">${m.toUpperCase()}</span><span class="p">${esc(p)}</span><span class="ds muted">${esc(op.summary || "")}</span></div>`)).join("");
}

// ------------------------------------------------------------------ boot

async function refreshStatus() {
  state.status = await api("GET", "/api/status");
  const s = state.status;
  $("#version").textContent = `v${s.version}`;
  $("#model-info").textContent = `${s.embed_model.split("/").pop()} · ${s.workdir}`;
  renderPicks();
  return s;
}

(async () => {
  saveTarget(); setDefense("outlier");
  try { await refreshStatus(); } catch (e) { toast(`Cannot reach the API: ${e.message}`); }
  try {
    const ref = Object.values((await api("GET", "/api/results/validation.json")).trusted_topic_centrality || {});
    if (ref.length) state.reference = Math.max(...ref);
  } catch { /* no validation run yet */ }
  go(store.get("page", "target"));
})();
