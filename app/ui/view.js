// The CEO app's view: it draws what model.js returns and decides nothing. It reaches Rust only through
// window.__TAURI__.core.invoke("feed"), and reads the board again every 30 seconds and whenever the window comes
// back to the front.
import {age, ago, clock, day, model} from "./model.js";

const PULL = 30 * 1000;
const TOTALS = [["building", "building", "b"], ["judging", "judging", "b"], ["needs", "need you", "a"],
  ["stuck", "stuck", "r"], ["cut", "cut", ""]];
const ROW = [["building", "building", "b"], ["judging", "judging", "b"], ["needs", "need you", "a"],
  ["stuck", "stuck", "r"], ["cut", "cut", "k"]];
const WAVE = [["merged", "merged", "g"], ...TOTALS];
const SEGMENTS = ["live", "built", "needs", "stuck", "queued", "picked", "merged", "cut"];
const LIGHT = ["Build", "Judge", "Merge"];
let shown = "all", seen = null, asked = 0, nav = ["all"];
const opened = new Set();  // the CEOs whose stages stand open in the overview, kept across pulls

const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  "'": "&#39;"}[ch]));
const yes = flag => (flag ? "yes" : "no");
const tally = (counts, keys) => keys.map(([key, word, tone]) =>
  `<span class="${counts[key] ? tone : "z"}"><strong>${counts[key]}</strong>${word}</span>`).join("");
const bar = marks => marks.map(mark => `<i class="${mark}"></i>`).join("");
const grouped = orders => SEGMENTS.flatMap(mark => orders.filter(order => order.mark === mark).map(() => mark));
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
  const now = Date.now(), m = model(seen, now);
  if (!m.ceos.some(one => one.name === shown)) shown = "all";
  nav = ["all", ...m.ceos.map(one => one.name)];
  const scroller = $("scroll"), kept = scroller && scroller.dataset.view === shown ? scroller.scrollTop : 0;
  side(m, now);
  $("main").innerHTML = shown === "all" ? overview(m, now) : pane(m, m.ceos.find(one => one.name === shown), now);
  $("scroll").scrollTop = kept;
}

function side(m, now) {
  const t = m.totals;
  $("sum").innerHTML = `<h1>${t.online} of ${t.of} <span>online</span></h1><p>${t.working} working ·
    <span class="feed" data-feed data-state="${m.feed.stale ? "stale" : "fresh"}">${esc(feedWords(m, now))}</span></p>`;
  $("top").innerHTML = `<div class="item${shown === "all" ? " sel" : ""}" data-side="all"><span class="ovicon"><i></i><i></i><i></i><i></i></span>
    <div class="who"><b>Overview</b><small>all ${t.of} CEOs · ${t.merged} of ${t.orders} merged</small></div></div>`;
  $("list").innerHTML = m.ceos.map(one => {
    const sub = one.online ? `${one.host} · ${one.counts.merged} of ${one.counts.orders} merged`
      : `${one.host} · ${one.tick === null ? "never ticked" : `offline since ${day(one.tick)}`}`;
    return `<div class="item${one.name === shown ? " sel" : ""}${one.online ? "" : " offline"}" data-side="${esc(one.name)}">
      <div class="lamps"><i class="lamp${one.online ? " on" : ""}" title="online"></i><i class="lamp${one.working ? " work" : ""}" title="working"></i></div>
      <div class="who"><b>${esc(one.name)}</b><small>${esc(sub)}</small></div>
      ${one.counts.needs ? `<span class="badge" data-needs title="orders that need you">${one.counts.needs}</span>` : ""}</div>`;
  }).join("") + m.unreadable.map(one => `<div class="item unreadable" data-file="${esc(one.file)}">
      <div class="lamps"><i class="lamp bad" title="unreadable"></i><i class="lamp"></i></div>
      <div class="who"><b>${esc(one.name)}</b><small>${esc(one.file)} · unreadable</small></div></div>`).join("");
}

// A CEO's plan as the overview opens it under its row: its landed share, then each stage and every order in it.
const TONE = {"landed": "merged", "in review": "built", "building": "live", "queued": "queued", "held": "needs",
  "cut": "cut", "not filed": "queued"};
const percent = one => (one.map === null ? "<div></div>"
  : `<div><button class="pct${opened.has(one.name) ? " open" : ""}" data-percent="${one.map.percent}" title="the stages of its plan">${one.map.percent}<span>%</span></button></div>`);
function stages(one) {
  if (one.map === null || !opened.has(one.name)) return "";
  const many = one.map.intents.length > 1;
  return `<section class="cmap" data-map="${esc(one.name)}"><p data-landed>${one.map.landed} of ${one.map.all} orders landed</p>` +
    one.map.intents.map(intent => (many ? `<h3>${esc(intent.title)}</h3>` : "") + intent.stages.map(stage =>
      `<div class="stage" data-stage="${esc(stage.title)}" data-state="${esc(stage.state)}"><h4>${esc(stage.title)}<em>${esc(stage.state)}</em></h4>` +
      stage.orders.map(order => `<div class="mo" data-map-order="${esc(order.number)}" data-state="${esc(order.state)}">
        <span class="no">${esc(order.number || "—")}</span><span class="t" title="${esc(order.title)}">${esc(order.title)}</span>
        <span class="st ${TONE[order.state] || "queued"}"><i></i>${esc(order.state)}</span></div>`).join("") + "</div>").join("")).join("") +
    "</section>";
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

function overview(m, now) {
  const t = m.totals;
  const rows = m.ceos.map(one => {
    const state = one.online
      ? `<div><i class="lamp on"></i><span data-tick>Online · tick ${ago(one.tick, now)}</span></div>` + (one.working
        ? `<div class="work"><i class="lamp work"></i>Working · ${esc(one.doing.join(", "))}</div>`
        : `<div class="idle"><i class="lamp"></i>Idle · no agent runs</div>`)
      : `<div class="offl"><i class="lamp"></i><span data-tick>${offline(one)}</span></div>` +
        `<div class="offl"><i class="lamp"></i>${one.tick === null ? "its status file holds no tick" : `last tick ${ago(one.tick, now)}`}</div>`;
    const bad = one.bad ? ` · <span class="bad" data-bad-records>${one.bad} unreadable record${one.bad === 1 ? "" : "s"}</span>` : "";
    return `<div class="crow${one.online ? "" : " offline"}" data-ceo="${esc(one.name)}" data-online="${yes(one.online)}" data-working="${yes(one.working)}">
      <div class="nm"><b>${esc(one.name)}</b><small>${esc(one.host)}${bad}</small></div>
      ${percent(one)}
      <div class="ratio" data-merged><b class="m${one.counts.merged ? "" : " z"}">${one.counts.merged}</b><span>/${one.counts.orders}</span></div>
      <div class="bar">${bar(one.orders.map(order => order.mark))}</div>
      <div class="lines">${state}</div>
      <div class="counts">${tally(one.counts, ROW)}</div></div>${stages(one)}`;
  }).join("") + m.unreadable.map(one => `<div class="crow unreadable" data-ceo="${esc(one.name)}" data-unreadable data-online="no" data-working="no">
      <div class="nm"><b>${esc(one.name)}</b><small>${esc(one.file)}</small></div><div></div><div class="ratio"></div><div></div>
      <div class="lines"><div class="why"><i class="lamp bad"></i>Unreadable: ${esc(one.why)}</div>
      <div class="offl"><i class="lamp"></i>counted in the CEOs, never online</div></div><div></div></div>`).join("");
  return `<header class="tool" data-tauri-drag-region><h2>Overview</h2><span class="host">${t.of} CEOs</span><span class="sp" data-tauri-drag-region></span>
      <span class="chip${m.feed.stale ? " stale" : ""}"><i class="lamp${m.feed.stale ? "" : " on"}"></i>${esc(feedWords(m, now))}</span></header>
    ${trouble(m)}
    <section class="totals">
      <div class="big" data-total="online"><strong>${t.online}</strong><span>of ${t.of}</span><small>CEOs online</small></div>
      <div class="big" data-total="working"><strong>${t.working}</strong><small>working</small></div>
      <div class="big" data-total="merged"><strong${t.merged ? ' class="g"' : ""}>${t.merged}</strong><span>of ${t.orders}</span><small>orders merged</small></div>
      <div class="tally">${tally(t, TOTALS)}</div>
      <div class="bar">${bar(grouped(m.ceos.flatMap(one => one.orders)))}</div></section>
    <div class="ceos" id="scroll" data-view="all"><div class="chead"><span>CEO</span><span>Plan</span><span>Merged</span><span>Wave</span><span>Online · working</span><span>Orders</span></div>${rows}</div>`;
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
  return `<header class="tool" data-tauri-drag-region><h2>${esc(one.name)}</h2><span class="host">${esc(one.host)}</span><span class="sp" data-tauri-drag-region></span>
      <span class="chip${one.online ? "" : " off"}"><i class="lamp${one.online ? " on" : ""}"></i>${one.online ? `Online · tick ${ago(one.tick, now)}` : offline(one)}</span>
      ${one.online ? `<span class="chip${one.working ? " work" : ""}"><i class="lamp${one.working ? " work" : ""}"></i>${one.working
        ? `Working · ${esc(one.doing.join(", "))}` : "Idle · no agent runs"}</span>` : ""}</header>
    <section class="wave"><div class="plan">${plans}</div><div class="tally">${tally(one.counts, WAVE)}</div>
      <div class="bar">${bar(one.orders.map(order => order.mark))}</div></section>
    ${notes}
    <div class="tablewrap" id="scroll" data-view="${esc(one.name)}"><table>
      <colgroup><col class="c-no"><col><col class="c-r"><col class="c-lt"><col class="c-st"><col class="c-s"></colgroup>
      <thead><tr><th>No.</th><th>Order</th><th>Rnd</th><th><span class="three"><span>Build</span><span>Judge</span><span>Merge</span></span></th><th>Status</th><th>Since</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function open(name) {
  if (name === undefined || name === shown) return;
  shown = name;
  draw();
}

document.addEventListener("click", event => {
  const pct = event.target.closest(".crow[data-ceo] [data-percent]");
  if (pct) {
    const name = pct.closest(".crow").dataset.ceo;
    opened.delete(name) || opened.add(name);
    return draw();
  }
  const side = event.target.closest("[data-side]");
  const row = event.target.closest(".crow[data-ceo]:not([data-unreadable])");
  open(side ? side.dataset.side : row ? row.dataset.ceo : undefined);
});
document.addEventListener("keydown", event => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const next = nav[nav.indexOf(shown) + (event.key === "ArrowDown" ? 1 : -1)];
  if (next === undefined) return;
  event.preventDefault();
  open(next);
});
document.addEventListener("visibilitychange", () => document.hidden || load());
window.addEventListener("focus", load);
setInterval(load, PULL);
load();
