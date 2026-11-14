"use strict";

// All server data is rendered with textContent: patches, logs and model output are untrusted.

const $ = (id) => document.getElementById(id);
let selectedRepair = null;
let refreshTimer = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function chip(state, label, sub) {
  return el("span", { class: "chip", "data-state": state },
    el("span", { class: "status-icon", "aria-hidden": "true" }),
    label,
    sub ? el("span", { class: "chip-sub", text: sub }) : null);
}

// --- formatting -----------------------------------------------------------

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(Date.now() / 1000 - seconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function duration(since) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - since));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

const pct = (v) => (v === null || v === undefined ? "–" : `${v.toFixed(v === 100 ? 0 : 2)}%`);
const ms = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)} ms`);
const isoSeconds = (iso) => (iso ? Date.parse(iso) / 1000 : null);
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const CI_LABEL = {
  passing: "Passing", failing: "Failing", running: "Running",
  no_runs: "No runs yet", unconfigured: "Not set up", unknown: "Unknown",
};
const UP_LABEL = { up: "Up", down: "Down", unknown: "No data" };
const OUTCOME_LABEL = { verified: "Verified", needs_review: "Needs review" };

// --- tooltip --------------------------------------------------------------

const tooltip = $("tooltip");
function showTip(target, lines) {
  tooltip.replaceChildren(...lines.map((line, i) => el("div", i === 0 ? { class: "tip-title" } : {}, line)));
  tooltip.hidden = false;
  const box = target.getBoundingClientRect();
  const tip = tooltip.getBoundingClientRect();
  const left = Math.min(window.innerWidth - tip.width - 8, Math.max(8, box.left + box.width / 2 - tip.width / 2));
  const top = box.top - tip.height - 8 < 8 ? box.bottom + 8 : box.top - tip.height - 8;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}
const hideTip = () => { tooltip.hidden = true; };

// --- apps -----------------------------------------------------------------

function uptimeStrip(recent) {
  const slots = 60;
  const ticks = [];
  for (let i = 0; i < slots - recent.length; i++) ticks.push(el("span", { class: "tick pending", "aria-hidden": "true" }));
  for (const s of recent) {
    const lines = [
      s.up ? "Up" : "Down",
      clock(s.ts),
      s.up ? `${ms(s.latency_ms)} · HTTP ${s.status_code}` : (s.error || `HTTP ${s.status_code}`),
    ];
    const tick = el("span", { class: s.up ? "tick" : "tick down", tabindex: "0", "aria-label": lines.join(", ") });
    tick.addEventListener("mouseenter", () => showTip(tick, lines));
    tick.addEventListener("focus", () => showTip(tick, lines));
    tick.addEventListener("mouseleave", hideTip);
    tick.addEventListener("blur", hideTip);
    ticks.push(tick);
  }
  const ups = recent.filter((s) => s.up).length;
  return el("div", { class: "strip-wrap" },
    el("div", { class: "strip", role: "group", "aria-label": `${ups} of ${recent.length} recent checks up` }, ticks),
    el("div", { class: "strip-caption" },
      el("span", { text: recent.length ? `Last ${recent.length} checks` : "Waiting for first check" }),
      el("span", { text: "now" })));
}

function ciBlock(ci) {
  const latest = ci.runs && ci.runs[0];
  const rows = [el("div", { class: "ci-row" }, el("span", { class: "ci-label", text: "CI" }), chip(ci.state, CI_LABEL[ci.state] || ci.state))];
  if (latest) {
    rows[0].append(el("span", { class: "ci-runs", "aria-hidden": "true" },
      ci.runs.slice().reverse().map((r) => el("span", {
        class: `ci-dot ${r.status !== "completed" ? "running" : r.conclusion === "success" ? "passing" : "failing"}`,
      }))));
    rows.push(el("div", { class: "ci-meta" },
      `${latest.title || "Run"} · ${latest.sha} on ${latest.branch} · ${ago(isoSeconds(latest.created_at))} · `,
      el("a", { href: latest.url, target: "_blank", rel: "noopener noreferrer", text: "View run ↗" })));
  } else if (ci.error) {
    rows.push(el("div", { class: "muted", text: ci.error }));
  }
  return el("div", { class: "ci" }, rows);
}

function appCard(app) {
  const u = app.uptime;
  const state = u.status;
  return el("article", { class: "card app", "aria-label": `${app.label}: ${UP_LABEL[state]}` },
    el("div", { class: "app-head" },
      el("span", { class: "app-name", text: app.label }),
      chip(state, UP_LABEL[state], u.since ? `for ${duration(u.since)}` : null)),
    el("div", { class: "stats" },
      stat("Uptime 1h", pct(u.uptime_1h)),
      stat("Uptime 24h", pct(u.uptime_24h)),
      stat("p95 latency", ms(u.latency_p95_1h)),
      stat("Last check", u.last_checked ? ago(u.last_checked) : "–")),
    uptimeStrip(u.recent || []),
    state === "down" && u.last_error ? el("div", { class: "muted", text: `Last error: ${u.last_error}` }) : null,
    el("div", { class: "url", text: u.url }),
    ciBlock(app.ci));
}

const stat = (label, value) => el("div", {}, el("div", { class: "stat-label", text: label }), el("div", { class: "stat-value", text: value }));

// --- repairs --------------------------------------------------------------

function repairRow(r) {
  const row = el("tr", { tabindex: "0", "aria-selected": String(r.id === selectedRepair) },
    el("td", { text: r.finished_at ? ago(isoSeconds(r.finished_at)) : "–" }),
    el("td", { text: r.app }),
    el("td", {}, el("code", { text: r.commit_sha })),
    el("td", {}, chip(r.outcome, OUTCOME_LABEL[r.outcome] || r.outcome, r.flaky ? "flaky" : null)),
    el("td", {},
      el("div", { class: "cause", text: r.likely_cause || "—" }),
      el("div", { class: "reason", text: r.reason })),
    el("td", { class: "num", text: r.candidates_tried ? `${r.candidates_tried} in ${r.rounds} round${r.rounds === 1 ? "" : "s"}` : "–" }));
  const open = () => selectRepair(r.id);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  return row;
}

function diffBlock(patch) {
  const lines = patch.split("\n").map((line) => {
    const cls = line.startsWith("+++") || line.startsWith("---") ? null
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : null;
    return cls ? el("span", { class: cls, text: line }) : document.createTextNode(line + "\n");
  });
  return el("pre", { class: "diff" }, lines);
}

function runBlock(title, run) {
  if (!run) return null;
  const state = run.passed ? "passing" : "failing";
  return el("div", { class: "run" },
    el("div", { class: "run-head" },
      el("span", { class: "run-title", text: title }),
      chip(state, run.timed_out ? "Timed out" : run.passed ? "Passed" : "Failed", `${run.duration_seconds}s`)),
    el("pre", { class: "out", text: `$ ${run.command}\n${run.output_tail}` }));
}

function renderDetail(r) {
  const d = r.detail;
  const sections = [
    el("div", { class: "detail-head" },
      el("span", { class: "detail-title", text: `${r.app} @ ${r.commit_sha}` }),
      chip(r.outcome, OUTCOME_LABEL[r.outcome] || r.outcome, r.confidence != null ? `confidence ${Math.round(r.confidence * 100)}%` : null)),
    el("div", {}, el("h3", { text: "Result" }), el("p", { class: "explain", text: r.reason })),
  ];
  if (r.likely_cause) {
    sections.push(el("div", {}, el("h3", { text: "Diagnosis" }),
      el("p", { class: "explain", text: r.likely_cause }),
      d.explanation ? el("p", { class: "explain muted", text: d.explanation }) : null));
  }
  if (d.evidence.length) {
    sections.push(el("div", {}, el("h3", { text: "Evidence" }), el("ul", { class: "evidence" },
      d.evidence.map((e) => el("li", {},
        el("span", { class: "tag", text: e.source.replace("_", " ") }),
        el("div", {}, el("code", { text: e.excerpt }), el("div", { class: "why", text: e.supports })))))));
  }
  if (d.patch) sections.push(el("div", {}, el("h3", { text: "Patch" }), diffBlock(d.patch)));
  const runs = [runBlock("Before: failing test on original code", d.baseline[0]),
    runBlock("After: same test with patch", d.after[0]), runBlock("After: full app suite", d.suite)].filter(Boolean);
  if (runs.length) sections.push(el("div", {}, el("h3", { text: "Test runs" }), el("div", { class: "runs" }, runs)));
  if (d.attempts.length) {
    sections.push(el("div", {}, el("h3", { text: "Candidates" }), el("ul", { class: "attempts" },
      d.attempts.map((a) => el("li", {},
        el("span", { class: "idx", text: `${a.round}.${a.index}` }),
        chip(a.passed ? "passing" : "failing", a.passed ? "Passed" : "Failed", a.changed_lines ? `${a.changed_lines} lines` : null),
        el("span", { text: a.summary || "—" }),
        el("span", { class: "muted", text: a.reason }))))));
  }
  $("detail").replaceChildren(el("div", { class: "card detail" }, sections));
}

async function selectRepair(id) {
  selectedRepair = id;
  for (const row of $("repairs").children) row.setAttribute("aria-selected", String(row.dataset.id === id));
  const res = await fetch(`/api/repairs/${encodeURIComponent(id)}`);
  if (res.ok) renderDetail(await res.json());
}

// --- page -----------------------------------------------------------------

function render(data) {
  $("demo-banner").hidden = !data.demo;
  const t = data.totals;
  const overall = t.apps_up === t.apps ? "up" : t.apps_up === 0 ? "down" : "degraded";
  $("overall").dataset.state = overall;
  $("overall-text").textContent = overall === "up" ? "All apps operational"
    : `${t.apps - t.apps_up} of ${t.apps} app${t.apps === 1 ? "" : "s"} down`;
  $("updated").textContent = `Updated ${clock(data.generated_at)}`;

  $("t-up").replaceChildren(String(t.apps_up), el("small", { text: ` / ${t.apps}` }));
  const day = data.apps.map((a) => a.uptime.uptime_24h).filter((v) => v !== null && v !== undefined);
  $("t-uptime").textContent = day.length ? pct(day.reduce((a, b) => a + b, 0) / day.length) : "–";
  $("t-verified").textContent = String(t.repairs_verified);
  $("t-review").textContent = String(t.repairs_needs_review);

  $("apps").replaceChildren(...data.apps.map(appCard));

  const rows = data.repairs.map((r) => { const row = repairRow(r); row.dataset.id = r.id; return row; });
  $("repairs").replaceChildren(...rows);
  $("repairs-empty").hidden = data.repairs.length > 0;
  if (!selectedRepair && data.repairs.length) selectRepair(data.repairs[0].id);
}

async function refresh() {
  let seconds = 10;
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    seconds = data.refresh_seconds || seconds;
    render(data);
  } catch (err) {
    $("overall").dataset.state = "unknown";
    $("overall-text").textContent = "Dashboard API unreachable";
  }
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, seconds * 1000);
}

refresh();
