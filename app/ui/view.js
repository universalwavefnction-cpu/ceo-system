// The CEO app's view: it draws what model.js returns and decides nothing. It reaches Rust only through
// window.__TAURI__.core.invoke("feed"), and reads the board again every 30 seconds and whenever the window comes
// back to the front. The owner's choices (settings) are kept in the window's own storage, once he makes one.
import {CHOICES, RANGES, age, ago, clock, day, model, settings} from "./model.js";

const PULL = 30 * 1000;
const TOTALS = [["building", "building", "b"], ["judging", "judging", "b"], ["needs", "need you", "a"],
  ["stuck", "stuck", "r"], ["cut", "cut", ""]];
const WAVE = [["merged", "merged", "g"], ...TOTALS];
const LIGHT = ["Build", "Judge", "Merge"];
let shown = "all", seen = null, asked = 0, panel = null;
const KEPT = "ceo.settings", dark = window.matchMedia("(prefers-color-scheme: dark)");
const kept = () => { try { return JSON.parse(localStorage.getItem(KEPT)); } catch { return null; } };
let prefs = settings(kept());

const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  "'": "&#39;"}[ch]));
const tally = (counts, keys) => keys.map(([key, word, tone]) =>
  `<span class="${counts[key] ? tone : "z"}"><strong>${counts[key]}</strong>${word}</span>`).join("");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const bar = marks => marks.map(mark => `<i class="${mark}"></i>`).join("");
const feedWords = (m, now) => (m.feed.at === null ? "no status file yet" : `feed updated ${ago(m.feed.at, now)}`);
const when = (at, now) => (now - at < 24 * 3600 * 1000 ? clock(at) : day(at));
const offline = one => (one.tick === null ? "Offline · never ticked" : `Offline since ${day(one.tick)}, ${clock(one.tick)}`);

async function load() {
  const mine = ++asked;
  let feed;
  try {
    const tauri = window.__TAURI__;
    if (!(tauri && tauri.core)) throw new Error("this page runs inside the CEO app, which hands it the board");
    feed = JSON.parse(await tauri.core.invoke("feed"));
  } catch (why) {
    feed = {dir: "", files: [], error: String((why && why.message) || why)};
  }
  if (mine !== asked) return;  // a later read is under way, and it draws
  seen = feed;
  draw();
}

function draw() {
  if (seen === null) return;
  const now = Date.now(), m = model(seen, now, prefs.rules);
  if (!m.ceos.some(one => one.name === shown)) shown = "all";
  const scroller = $("scroll"), same = scroller && scroller.dataset.view === shown, kept = same ? scroller.scrollTop : 0;
  const focused = same && document.activeElement?.closest("[data-ceo]");  // a card the keyboard is on stays on it
  $("main").innerHTML = shown === "all" ? desk(m, now) : pane(m, m.ceos.find(one => one.name === shown), now);
  $("scroll").scrollTop = kept;
  if (focused) document.querySelector(`[data-ceo="${CSS.escape(focused.dataset.ceo)}"]`)?.focus();
}

// A CEO's plan as its room shows it: its landed share, then every stage, the current one marked, and every order in it.
// Each order's state takes the desk's swatch for it (its legend); queued, not filed and cut are still to go.
const MAP_SW = {"landed": "landed", "in review": "flight", "building": "flight", "held": "held"};
const percent = one => (one.map === null || one.map.percent === null ? ""
  : `<span class="pct" data-percent="${one.map.percent}">${one.map.percent}<span>%</span></span>`);
function stages(one) {
  if (one.map === null) return `<p class="r-none" data-no-map>Its kernel reports no map of its plan yet.</p>`;
  const many = one.map.intents.length > 1;
  return `<section class="cmap" data-map="${esc(one.name)}"><p data-landed>${one.map.percent === null ? "" : `${one.map.percent}% · `}${one.map.landed} of ${one.map.all} orders landed</p>` +
    one.map.intents.map(intent => (many ? `<h3>${esc(intent.title)}</h3>` : "") + intent.stages.map(stage =>
      `<div class="stage${stage.current ? " now" : ""}" data-stage="${esc(stage.title)}" data-state="${esc(stage.state)}"${stage.current ? " data-current" : ""}>
      <h4>Stage ${stage.no} · ${esc(stage.title)}<em>${stage.current ? "current · " : ""}${esc(stage.state)}</em></h4>` +
      stage.orders.map(order => `<div class="mo" data-map-order="${esc(order.number)}" data-state="${esc(order.state)}">
        <span class="no">${esc(order.number || "—")}</span><span class="t" title="${esc(order.title)}">${esc(order.title)}</span>
        <span class="ms"><i class="sw ${MAP_SW[order.state] || "togo"}"></i>${esc(order.state)}</span></div>`).join("") + "</div>").join("")).join("") +
    "</section>";
}

// What waits for the owner, question by question, or one line that says nothing does.
function asks(one, now) {
  const asked = one.desk.asked;
  return `<section class="r-asks" data-asks="${asked.length}"><h3>${asked.length ? `${plural(asked.length, "decision")} owed to you`
    : "Nothing owed to you"}</h3>${asked.map(ask => `<div class="r-ask" data-ask="${esc(ask.slug)}"><b>${esc(ask.slug)}</b>
      <span class="q">${esc(ask.question || "no question written")}</span>
      <span class="since">${ask.since === null ? "" : `since ${ago(ask.since, now)}`}</span></div>`).join("")}</section>`;
}

// A notice above the totals when the board itself is missing or cannot be read.
function trouble(m) {
  if (m.error) return `<div class="note bad"><b>The board cannot be read.</b> ${esc(m.error)}</div>`;
  if (!m.ceos.length && !m.unreadable.length) {
    return `<div class="note"><b>No status file yet.</b> <code>${esc(m.dir)}</code> holds none; the board pull
      (<code>tools/fleet.py pull</code>) writes one for each CEO.</div>`;
  }
  return "";
}

// The dial: a car's gauge, 270 degrees open at the bottom, one mark an order with a small gap between marks.
const point = deg => [120 + 110 * Math.cos(deg * Math.PI / 180), 120 + 110 * Math.sin(deg * Math.PI / 180)].map(at => at.toFixed(2)).join(" ");
const arc = (from, to) => `M ${point(from)} A 110 110 0 ${to - from > 180 ? 1 : 0} 1 ${point(to)}`;
function dial(d) {
  const n = d.marks.length, gap = n > 1 ? Math.min(4, 60 / n) : 0, each = (270 - gap * (n - 1)) / Math.max(1, n);
  const marks = d.marks.map((mark, at) => `<path class="dial-seg seg-${mark}" data-mark="${mark}"${mark === "togo" ? ""
    : ` stroke="url(#dial-${mark})"`} d="${arc(135 + at * (each + gap), 135 + at * (each + gap) + each)}"/>`).join("");
  const landed = d.marks.filter(mark => mark === "landed").length;
  return `<div class="gauge gauge-orders" data-dial><svg class="ring" viewBox="0 0 240 240" aria-hidden="true">${n ? marks
    : `<path class="dial-seg seg-togo" d="${arc(135, 405)}"/>`}</svg><div class="og-face"><span class="og-l">${esc(d.label)}</span>
    <span class="og-n${landed ? "" : " nil"}">${landed}</span><span class="og-c">of ${n} landed</span></div></div>`;
}
// Each hue runs from its -a to its -b colour (theme.css); the dial's paths take them by id.
const HUES = {landed: "sage", flight: "blue", pick: "amber", held: "rose"};
const defs = `<svg class="defs" aria-hidden="true"><defs>${Object.entries(HUES).map(([mark, hue]) =>
  `<linearGradient id="dial-${mark}" x1="0%" y1="0%" x2="85%" y2="100%"><stop offset="0%" style="stop-color: var(--${hue}-a)"/>
  <stop offset="100%" style="stop-color: var(--${hue}-b)"/></linearGradient>`).join("")}</defs></svg>`;

function card(one, now) {
  const {desk: d, standing: s} = one, flight = d.flight.length, show = prefs.card;
  const flights = `<details class="in-flight" data-flight="${flight}">
    <summary>${flight ? `${flight} in flight` : "nothing in flight"}</summary><ul>${d.flight.map(order =>
      `<li>${esc(order.number)} ${esc(order.title)} · ${esc(order.status)}</li>`).join("")}</ul></details>`;
  return `<article class="number-tile house-row" data-ceo="${esc(one.name)}" data-standing="${s.colour}" data-zero="${!d.owed}" tabindex="0">
    <div class="house-title"><span class="house-link">${esc(one.name)}</span></div>
    <div class="fact activity"><p class="fact-value standing"><b data-word>${esc(s.word)}</b>${show.reason ? `<span data-why>${esc(s.why)}</span>` : ""}</p></div>
    ${show.dial || show.owed ? `<div class="hc-main${show.dial && show.owed ? "" : " one"}">${show.dial ? dial(d.dial) : ""}${show.owed ? `<div class="fact owed"><p class="fact-value" data-owed="${d.owed}">${d.owed}</p>
      <span class="fact-label">${d.owed === 1 ? "decision" : "decisions"} owed</span>
      ${d.oldest === null ? "" : `<p class="owed-since">the oldest since ${ago(d.oldest, now)}</p>`}</div>` : ""}</div>` : ""}
    ${show.dial ? `<p class="hc-stage" data-stage-line>${esc(d.dial.line)}</p>` : ""}
    ${show.last ? `<div class="fact publication" data-last><p class="pub-h"><span class="fact-label">Landed last</span>${d.last === null ? ""
      : `<span class="pub-age">${ago(d.last.at, now)}</span>`}</p><p class="fact-value">${d.last === null ? "nothing yet" : esc(d.last.title)}</p></div>` : ""}
    <div class="hc-foot">${show.flight ? flights : ""}${percent(one)}</div></article>`;
}

// The desk: what is owed over all CEOs, the legend, and one card a CEO; an unreadable file is a card with no numbers.
function desk(m, now) {
  const owed = m.totals.owed;
  const unreadable = m.unreadable.map(one => `<article class="number-tile house-row" data-ceo="${esc(one.name)}" data-unreadable data-state="unknown">
    <div class="house-title"><span class="house-link">${esc(one.name)}</span></div>
    <div class="fact activity"><p class="fact-value">Unreadable · ${esc(one.why)}</p></div><p class="hc-stage">${esc(one.file)}</p></article>`);
  return `<div class="desk">${defs}<header class="desk-bar" data-tauri-drag-region>
      <p class="quiet" data-feed data-state="${m.feed.stale ? "stale" : "fresh"}">${esc(feedWords(m, now))}</p><h1>CEOs</h1>
      <p class="word-line" data-owed-all="${owed}">${owed ? `${plural(owed, "decision")} owed to you, across all CEOs` : "nothing owed to you"}</p>
      <button class="cz-btn" data-customize aria-expanded="${panel !== null}">Customize</button></header>
    <div class="page" id="scroll" data-view="all">${trouble(m)}<div class="legends">
      <ul class="legend"><li data-standing="green"><i class="sd"></i><span><b>Moving</b> nothing needs you</span></li>
        <li data-standing="yellow" data-legend="yellow"><i class="sd"></i><span><b>Waiting on you</b> ${prefs.rules.owedAtLeast} or more ${prefs.rules.owedAtLeast === 1 ? "decision" : "decisions"} owed,
          or <b>Slow</b> nothing moved for ${plural(prefs.rules.quietHours, "hour")}</span></li>
        <li data-standing="red"><i class="sd"></i><span><b>Stopped</b> or <b>A check failed</b></span></li></ul>
      <ul class="legend"><li><i class="sw landed"></i>landed</li><li><i class="sw flight"></i>in flight</li>
        <li><i class="sw pick"></i>your pick</li><li><i class="sw held"></i>held</li><li><i class="sw togo"></i>to go</li></ul></div>
    <div class="number-grid" data-size="${prefs.size}">${m.ceos.map(one => card(one, now)).join("")}${unreadable.join("")}</div></div></div>`;
}
function pane(m, one, now) {
  const plans = one.plans.slice(0, 2).map(plan => `<div><b>WAVE · FILED ${plan.filed === null ? "—" : day(plan.filed)}</b>${esc(plan.title)}</div>`).join("")
    + (one.plans.length > 2 ? `<div><i>+ ${one.plans.length - 2} more plans in this wave</i></div>` : "")
    || "<div><b>NO WAVE</b>No filed plan, so no wave</div>";
  const rows = one.orders.map(order => {
    const [build, judge, merge] = order.lights;
    return `<tr class="${order.mark}" data-order="${esc(order.number)}" data-build="${build}" data-judge="${judge}" data-merge="${merge}">
      <td class="no">${esc(order.number)}</td>
      <td class="t" title="${esc(order.title)}">${esc(order.title)}${order.question ? `<span class="ask">${esc(order.question)}</span>` : ""}</td>
      <td class="r">${Number.isInteger(order.round) ? `r${order.round}` : ""}</td>
      <td class="lt"><div class="track${order.kind === "merged" ? " merged" : ""}">${order.lights.map((light, at) =>
        `<b class="${light}" title="${LIGHT[at]}: ${light}"></b>`).join("")}</div></td>
      <td><span class="st ${order.mark}" data-status><i></i>${esc(order.status)}</span></td>
      <td class="s" data-since>${order.since === null ? "—" : `${when(order.since, now)}<em>${age(order.since, now)}</em>`}</td></tr>`;
  }).join("");
  const notes = (one.online ? "" : `<div class="note"><b>${esc(one.name)} is offline.</b> ${one.tick === null ? "Its status file holds no tick."
      : `Its last tick was ${day(one.tick)}, ${clock(one.tick)} (${ago(one.tick, now)}).`} The orders below are as it left them.</div>`)
    + (one.bad ? `<div class="note bad"><b>${one.bad} of its run records cannot be read.</b> Its board names them; the kernel reads them as none.</div>` : "");
  const s = one.standing;
  return `<div class="desk room">${defs}<header class="tool" data-tauri-drag-region><button class="back" data-side="all">‹ All CEOs</button><h2>${esc(one.name)}</h2><span class="host">${esc(one.host)}</span>
      <p class="r-standing" data-room-standing="${s.colour}" data-standing="${s.colour}"><i class="sd"></i><b data-word>${esc(s.word)}</b><span>${esc(s.why)}</span></p><span class="sp" data-tauri-drag-region></span>
      <span class="chip${one.online ? "" : " off"}"><i class="lamp${one.online ? " on" : ""}"></i>${one.online ? `Online · tick ${ago(one.tick, now)}` : offline(one)}</span>
      ${one.online ? `<span class="chip${one.working ? " work" : ""}"><i class="lamp${one.working ? " work" : ""}"></i>${one.working
        ? `Working · ${esc(one.doing.join(", "))}` : "Idle · no agent runs"}</span>` : ""}</header>
    <div class="page" id="scroll" data-view="${esc(one.name)}">${notes}${asks(one, now)}<h3 class="r-h">The plan</h3>${stages(one)}
    <h3 class="r-h">The wave</h3><section class="wave"><div class="plan">${plans}</div><div class="tally">${tally(one.counts, WAVE)}</div>
      <div class="bar">${bar(one.orders.map(order => order.mark))}</div></section>
    <div class="tablewrap"><table>
      <colgroup><col class="c-no"><col><col class="c-r"><col class="c-lt"><col class="c-st"><col class="c-s"></colgroup>
      <thead><tr><th>No.</th><th>Order</th><th>Rnd</th><th><span class="three"><span>Build</span><span>Judge</span><span>Merge</span></span></th><th>Status</th><th>Since</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div></div>`;
}

// Customize: the inspector beside the page. Its controls stay put while the page is drawn again, so a pull of the board
// keeps every choice and the keyboard stays on the control it changed; each shows its choice from prefs (sync).
const PARTS = [["dial", "The dial", "orders landed of the current stage"], ["owed", "Decisions owed", "the number, set largest"],
  ["reason", "Why", "the reason beside the standing's word"], ["last", "Landed last", "the order that merged last"],
  ["flight", "In flight", "the orders being built or judged"]];
const word = value => value[0].toUpperCase() + value.slice(1);
const radios = (path, values, face = value => `<span>${word(value)}</span>`) => values.map(value =>
  `<label title="${word(value)}"><input type="radio" name="${path}" data-pref="${path}" value="${value}" aria-label="${word(value)}">${face(value)}</label>`).join("");
const number = (key, label, unit) => `<label class="cz-num"><span class="cz-t">${label}</span><span class="cz-in"><input type="number"
  data-pref="rules.${key}" min="${RANGES[key][0]}" max="${RANGES[key][1]}" step="1">${unit}</span></label>`;
const PANEL = `<div class="cz-head"><h2 id="cz-title" tabindex="-1">Customize</h2><button class="cz-done" data-done>Done</button></div>
  <p class="cz-lede">Each choice shows at once, and the app keeps it for the next time it opens.</p>
  <fieldset class="cz-sec"><legend>What a card shows</legend><div>${PARTS.map(([key, label, hint]) => `<label class="cz-sw">
    <span class="cz-t">${label}<small>${hint}</small></span><input type="checkbox" role="switch" data-pref="card.${key}"></label>`).join("")}
    <p class="cz-hint">The standing's word always stands.</p></div></fieldset>
  <fieldset class="cz-sec"><legend>Card size</legend><div class="cz-seg">${radios("size", CHOICES.size)}</div></fieldset>
  <fieldset class="cz-sec"><legend>When a standing turns yellow</legend><div>${number("quietHours", "Slow after nothing moved for", "hours")}
    ${number("owedAtLeast", "Waiting once decisions owed reach", "or more")}<p class="cz-hint">Red stays as it is: a CEO stopped, or a check failed.</p></div></fieldset>
  <fieldset class="cz-sec"><legend>Look</legend><div><div class="cz-seg">${radios("look.theme", CHOICES.theme)}</div>
    <div class="cz-acc">${radios("look.accent", CHOICES.accent, value => `<span class="cz-dot a-${value}"></span>`)}</div></div></fieldset>
  <div class="cz-sec cz-end"><div class="cz-row"><button class="cz-btn" data-copy>Copy settings</button>
    <button class="cz-btn cz-warn" data-reset>Reset</button></div><p class="cz-status" data-said></p>
    <textarea class="cz-json" data-settings rows="14" readonly hidden aria-label="Settings as JSON"></textarea></div>`;

function look() {
  const {theme, accent} = prefs.look, root = document.documentElement.dataset;
  root.theme = theme === "system" ? (dark.matches ? "dark" : "light") : theme;
  root.accent = accent;
}
function sync() {
  for (const input of panel ? panel.querySelectorAll("[data-pref]") : []) {
    const [group, key] = input.dataset.pref.split("."), value = key ? prefs[group][key] : prefs[group];
    if (input.type === "number") input.value = value;
    else input.checked = input.type === "checkbox" ? value : input.value === value;
  }
  const json = panel && panel.querySelector("[data-settings]");
  if (json && !json.hidden) json.value = JSON.stringify(prefs, null, 2);
}
// A choice made: kept (or, on reset, nothing kept), shown on the panel, the look and the page.
function choose(next, keep = true) {
  prefs = next;
  try {
    if (keep) localStorage.setItem(KEPT, JSON.stringify(prefs));
    else localStorage.removeItem(KEPT);
  } catch { /* a window without storage still shows the choice */ }
  sync();
  look();
  draw();
}
function customize() {
  if (panel) {
    panel.remove();
    panel = null;
  } else {
    panel = Object.assign(document.createElement("aside"), {className: "cz", innerHTML: PANEL});
    panel.dataset.panel = "";
    document.body.append(panel);
  }
  document.body.classList.toggle("customizing", panel !== null);
  sync();
  draw();
  (panel ? panel.querySelector("h2") : document.querySelector("[data-customize]"))?.focus();
}
function said(words) {
  if (panel) panel.querySelector("[data-said]").textContent = words;
}
function act(button) {
  const reset = panel.querySelector("[data-reset]");
  if (button.matches("[data-copy]")) {
    const json = panel.querySelector("[data-settings]"), text = JSON.stringify(prefs, null, 2);
    Object.assign(json, {hidden: false, value: text});
    said("The settings, as one JSON shape.");
    navigator.clipboard?.writeText(text).then(() => said("Copied, as one JSON shape."), () => {});
  } else if (!reset.hasAttribute("data-armed")) {
    reset.toggleAttribute("data-armed", true);
    reset.textContent = "Reset all: click again";
    said("This clears every choice: the defaults again, and nothing kept.");
  } else {
    reset.removeAttribute("data-armed");
    reset.textContent = "Reset";
    choose(settings(null), false);
    said("Every choice is cleared.");
  }
}

function open(name) {
  if (name === undefined || name === shown) return;
  shown = name;
  draw();
}

document.addEventListener("click", event => {
  const button = event.target.closest("[data-customize], [data-done], [data-copy], [data-reset]");
  if (button) return button.matches("[data-customize], [data-done]") ? customize() : act(button);
  // A click anywhere on a card, its In flight summary and list too, opens its room; nothing opens in place.
  if (event.target.closest("[data-ceo] [data-flight] summary")) event.preventDefault();
  const side = event.target.closest("[data-side]");
  const card = event.target.closest("[data-ceo]:not([data-unreadable])");
  open(side ? side.dataset.side : card ? card.dataset.ceo : undefined);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") open("all");
  else if (event.key === "Enter" && event.target.matches("[data-ceo]:not([data-unreadable])")) open(event.target.dataset.ceo);
});
document.addEventListener("change", event => {
  const input = event.target.closest("[data-panel] [data-pref]");
  if (!input) return;
  const [group, key] = input.dataset.pref.split("."), next = structuredClone(prefs);
  const value = input.type === "checkbox" ? input.checked : input.type !== "number" ? input.value
    : input.value === "" ? null : Number(input.value);
  if (key) next[group][key] = value;
  else next[group] = value;
  choose(settings(next));
});
dark.addEventListener("change", look);
look();
document.addEventListener("visibilitychange", () => document.hidden || load());
window.addEventListener("focus", load);
setInterval(load, PULL);
load();
