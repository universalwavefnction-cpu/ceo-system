// The CEO app's rules, every one of them, by code alone. Pure: no DOM and no Tauri, so Node imports this module
// as the page does. model(feed, now) reads what feed.rs gives (the board folder's status files, as
// `ceo.py board --name` writes them) and returns all the view draws. Missing data is never shown green.

export const QUIET = 15 * 60 * 1000;  // a tick, or a pull, older than this is a stop (board.py QUIET)
// The owner's two lines for a standing: hours with nothing moved before a CEO is slow, and decisions owed before it waits.
export const RULES = {quietHours: 24, owedAtLeast: 1};
// The owner's choices, as one JSON shape: what a card shows, its size, his two lines and the look. Every key and default.
export const DEFAULTS = {card: {dial: true, owed: true, reason: true, last: true, flight: true}, size: "regular",
  rules: {...RULES}, look: {theme: "system", accent: "blue"}};
export const CHOICES = {size: ["compact", "regular", "large"], theme: ["system", "light", "dark"],
  accent: ["blue", "lilac", "sage", "amber", "graphite"]};
export const RANGES = {quietHours: [1, 336], owedAtLeast: [1, 99]};
const MINUTE = 60 * 1000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
// A live agent's lights and word, by its role (ceo_agents.logs_of); any other lock lights no order.
const LIVE = {worker: ["live", "off", "off"], reviewer: ["done", "live", "off"], door: ["done", "done", "live"]};
const DOING = {worker: "Building", reviewer: "Judging", door: "Merging"};
const AHEAD = ["worker", "reviewer", "door"];  // two live agents on one order: the furthest on shows
// The table's order, and each Status word's mark in the bars: live work first, settled last.
const RANK = ["Building", "Judging", "Merging", "Built", "Needs you", "Parked", "Stuck", "Failed", "Queued", "",
  "Unknown", "Picked", "Merged", "Cut"];
const MARK = {Building: "live", Judging: "live", Merging: "live", Built: "built", "Needs you": "needs",
  Parked: "stuck", Stuck: "stuck", Failed: "stuck", Picked: "picked", Merged: "merged", Cut: "cut"};

// A dial's marks, by an order's mark in the wave and by a row's state on a map; any other is still to go.
const WAVE_DIAL = {merged: "landed", live: "flight", built: "flight", needs: "pick", stuck: "held"};
const MAP_DIAL = {landed: "landed", "in review": "flight", building: "flight", held: "held"};
const MARKS = ["landed", "flight", "pick", "held", "togo"];
const ranked = marks => MARKS.flatMap(mark => marks.filter(one => one === mark));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const latest = (stamps, pick = Math.max) => (stamps.some(at => at !== null) ? pick(...stamps.filter(at => at !== null)) : null);

const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const list = value => (Array.isArray(value) ? value : []);
// A field as text: a string as it is, a number or a boolean by its digits or word, anything else (an object or an array
// from a hand-edited file, whose own toString may throw) empty. A malformed field never aborts the whole model.
const text = value => (typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean"
  ? String(value) : "");

// An ISO stamp as Python 3.10's datetime.fromisoformat reads it, after ceo_score.utc turns Z into +00:00.
const STAMP = /^(\d{4})-(\d{2})-(\d{2})(?:[^](\d{2})(?::(\d{2})(?::(\d{2})(?:\.(\d{3}|\d{6}))?)?)?(?:([+-])(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{6}))?)?)?)?$/;

// The time a stamp names, in milliseconds; one without a zone is UTC (ceo_score.utc); null when it names none.
export function utc(stamp) {
  const found = typeof stamp === "string" && STAMP.exec(stamp.replaceAll("Z", "+00:00"));
  if (!found) return null;
  const [y, mo, d, h, mi, s] = found.slice(1, 7).map(part => Number(part || 0));
  const at = new Date(0);
  at.setUTCFullYear(y, mo - 1, d);
  if (y < 1 || at.getUTCFullYear() !== y || at.getUTCMonth() !== mo - 1 || at.getUTCDate() !== d) return null;
  if (h > 23 || mi > 59 || s > 59) return null;
  let ms = at.getTime() + h * HOUR + mi * MINUTE + s * 1000 + Math.floor(Number(`0.${found[7] || 0}`) * 1000);
  if (found[8]) {
    const [oh, om, os] = [found[9], found[10], found[11]].map(part => Number(part || 0));
    if (om > 59 || os > 59 || oh * HOUR + om * MINUTE + os * 1000 >= DAY) return null;
    ms -= (found[8] === "-" ? -1 : 1) * (oh * HOUR + om * MINUTE + os * 1000 + Math.floor(Number(`0.${found[12] || 0}`) * 1000));
  }
  return ms;
}

// How long ago, as the page says it: "under 1 min ago", "3 min ago", "5 h ago", "15 days ago".
export function ago(at, now) {
  const minutes = Math.floor((now - at) / MINUTE);
  if (minutes < 1) return "under 1 min ago";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)} h ago`;
  return `${Math.floor(minutes / (24 * 60))} days ago`;
}

// An age in the table's short form: "14m", "5h", "16d".
export function age(at, now) {
  const minutes = Math.max(0, Math.floor((now - at) / MINUTE));
  return minutes < 60 ? `${minutes}m` : minutes < 48 * 60 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / (24 * 60))}d`;
}

export const day = at => new Date(at).toLocaleDateString("en-GB", {day: "numeric", month: "short"});
export const clock = at => new Date(at).toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit"});

// An order's three lights, Build · Judge · Merge, each off, live, done, failed or unknown: the first that holds.
export function lights(order, role, online) {
  if (order.merged === true) return ["done", "done", "done"];
  if (order.picked === true) return ["done", "off", "off"];
  if (order.cut === true) return ["off", "off", "off"];
  if (role) return [...LIVE[role]];
  if (typeof order.stage !== "string") return ["unknown", "unknown", "off"];  // an older kernel's file
  const live = online ? "live" : "unknown";  // an offline CEO's agents are no longer known to run
  return {building: [live, "off", "off"], "in review": ["done", live, "off"], done: ["done", "off", "off"],
    failed: ["failed", "off", "off"]}[order.stage] || ["off", "off", "off"];
}

// An order's Status word: the first that holds.
export function status(order, row, role) {
  const stage = typeof order.stage === "string" ? order.stage : "";
  if (order.merged === true) return "Merged";
  if (order.cut === true) return "Cut";
  if (row.state === "waiting") return "Needs you";
  if (order.picked === true) return "Picked";
  if (row.state === "needs attention") return "Parked";
  if (order.stalled === true) return "Stuck";
  if (role) return DOING[role];
  const word = {building: "Building", "in review": "Judging", failed: "Failed", done: "Built", queued: "Queued"}[stage];
  return word || (stage ? stage[0].toUpperCase() + stage.slice(1) : "Unknown");
}

// The board's own class of a wave order (board.placed), the first that holds.
export function placed(order, row) {
  return order.merged === true ? "merged" : order.cut === true ? "cut" : row.state === "waiting" ? "need-you"
    : row.state === "needs attention" ? "parked" : order.stalled === true ? "stuck" : "running";
}

// A status file's text as the board reads it (board.read): a status file, or why it is none.
function parsed(file) {
  const name = text(file.name);
  const named = {name: name.endsWith(".json") ? name.slice(0, -5) : name, file: name};
  if (typeof file.text !== "string") return {...named, why: text(file.error) || "it cannot be read"};
  let found;
  try {
    found = JSON.parse(file.text);
  } catch {
    return {...named, why: "it is not JSON"};
  }
  if (!(object(found) && typeof found.ceo === "string" && found.ceo && object(found.status) && object(found.wave)))
    return {...named, why: "it is not a status file, as board --name writes one"};
  return {...named, one: found};
}

// What a CEO's live agents do, in the kernel's words: "worker on 220 · 14 min".
function doing(agent, now) {
  const slug = text(agent.slug), started = utc(agent.started);
  return [`${text(agent.role) || "agent"}${slug ? ` on ${(/^\d+/.exec(slug) || [slug])[0]}` : ""}`,
    ...(started === null ? [] : [`${Math.max(0, Math.floor((now - started) / MINUTE))} min`])].join(" · ");
}

// The words the kernel writes for an order on a map (board.maps rows) and for a stage; any other reads "unknown".
const ORDER_STATES = ["landed", "in review", "building", "queued", "held", "cut", "not filed"];
const STAGE_STATES = ["done", "running", "ahead"];
const known = (state, words) => (words.includes(state) ? state : "unknown");

// A CEO's map: the share of the orders its open intents need that have landed, and every order by stage. Null when the
// file has no maps, no open intent, or no stage; with no order left to land (a cut order is no need) its share is null.
// Landed is state "landed" alone.
function mapOf(maps) {
  const intents = list(maps).filter(intent => object(intent) && intent.open === true).map(intent => ({
    title: text(intent.title) || text(intent.intent),
    stages: list(intent.stages).filter(object).map(stage => ({title: text(stage.title),
      state: known(stage.state, STAGE_STATES), orders: list(stage.orders).filter(object).map(row => ({
        number: text(row.number), title: text(row.title) || text(row.number), state: known(row.state, ORDER_STATES),
        plan: text(row.plan)}))}))}));
  // Every stage of every open intent is numbered from 1, and one is current: the first running, else the first ahead,
  // else the last. The dial counts that same stage, and the room marks it.
  const stages = intents.flatMap(intent => intent.stages);
  stages.forEach((stage, at) => Object.assign(stage, {no: at + 1, current: false}));
  const current = stages.find(stage => stage.state === "running") || stages.find(stage => stage.state === "ahead")
    || stages[stages.length - 1];
  if (current) current.current = true;
  const need = intents.flatMap(intent => intent.stages.flatMap(stage => stage.orders)).filter(row => row.state !== "cut");
  const landed = need.filter(row => row.state === "landed").length;
  return stages.length ? {percent: need.length ? Math.floor(100 * landed / need.length) : null, landed, all: need.length,
    intents} : null;
}

// One CEO from its status file alone, at now.
function ceo(one, file, now, rules) {
  const rows = new Map(list(one.status.rows).filter(object).map(row => [row.slug, row]));
  const tick = utc(one.last_tick), stamped = utc(one.time);
  // A long pass leaves last_tick old, but a Mac tick that holds the machine awake says so (ceo_tick.awake: true, never
  // merely truthy), and the board's own time, never the file's age, says it said so within QUIET of now.
  const awake = one.status.awake === true && stamped !== null && Math.abs(now - stamped) <= QUIET;
  const online = (tick !== null && now - tick <= QUIET) || awake;
  const agents = list(one.status.agents);
  const live = new Map();  // an online CEO's agents on an order's slug, the furthest on of each slug
  for (const agent of online ? agents.filter(object) : []) {
    const was = live.get(agent.slug);
    if (own(LIVE, text(agent.role)) && typeof agent.slug === "string"
        && (!was || AHEAD.indexOf(agent.role) > AHEAD.indexOf(was.role))) live.set(agent.slug, agent);
  }
  const orders = list(one.wave.orders).filter(object).map(order => {
    const latest = typeof order.latest === "string" ? order.latest : null;
    const row = (latest !== null && rows.get(latest)) || {};
    const agent = latest !== null ? live.get(latest) : undefined;
    const said = status(order, row, agent && agent.role);
    const since = utc(row.since) ?? (agent ? utc(agent.started) : null);
    return {number: text(order.number), title: text(order.title) || text(order.number) || text(latest),
      round: order.round, latest, kind: placed(order, row), lights: lights(order, agent && agent.role, online),
      status: said, mark: MARK[said] || "queued", question: said === "Needs you" ? text(row.question) : "",
      since, merged_at: utc(order.merged_at)};
  });
  orders.sort((a, b) => rank(a.status) - rank(b.status) || (Number(b.number) || 0) - (Number(a.number) || 0));
  const counting = words => orders.filter(order => words.includes(order.status)).length;
  const plans = list(one.wave.plans).filter(object).map(plan => ({title: text(plan.title), filed: utc(plan.filed)}));
  const map = mapOf(one.maps), all = list(one.status.rows).filter(object);
  const owed = all.filter(row => row.state === "waiting"), last = latest(orders.map(order => order.merged_at));
  const desk = {owed: owed.length, asked: owed.map(row => ({slug: text(row.slug), question: text(row.question),
      since: utc(row.since)})), oldest: latest(owed.map(row => utc(row.since)), Math.min),
    moved: latest([...all.map(row => utc(row.since)), ...orders.map(order => order.merged_at),
      ...(online ? agents.filter(object).map(agent => utc(agent.started)) : [])]),
    held: orders.filter(order => order.mark === "stuck").length,
    dial: dialOf(map, orders, plans),
    last: last === null ? null : {title: orders.find(order => order.merged_at === last).title, at: last},
    flight: orders.filter(order => order.mark === "live" || order.mark === "built")
      .map(({number, title, status}) => ({number, title, status}))};
  const made = {name: one.ceo, file, host: text(one.host), tick, online, working: online && agents.length > 0,
    doing: online ? agents.filter(object).map(agent => doing(agent, now)) : [], plans, orders, desk,
    bad: list(one.unreadable).length, map,
    counts: {orders: orders.length, merged: orders.filter(order => order.kind === "merged").length,
      needs: orders.filter(order => order.kind === "need-you").length, building: counting(["Building"]),
      judging: counting(["Judging", "Merging"]), stuck: counting(["Stuck"]), cut: counting(["Cut"])}};
  return {...made, standing: standing(made, rules, now)};
}

// A CEO's dial: with a map, every order not cut of its current stage (mapOf marks it); without one, every wave order
// not cut. Landed first, still to go last.
function dialOf(map, orders, plans) {
  if (map === null) {
    return {label: "Wave", marks: ranked(orders.filter(order => order.mark !== "cut").map(order => WAVE_DIAL[order.mark] || "togo")),
      line: plans.length ? `Wave · ${plans[0].title}${plans.length > 1 ? ` and ${plans.length - 1} more` : ""}` : "No filed plan, so no wave"};
  }
  const stages = map.intents.flatMap(intent => intent.stages), at = stages.find(stage => stage.current);
  return {label: `Stage ${at.no}`, line: `Stage ${at.no} of ${stages.length} · ${at.title}`,
    marks: ranked(at.orders.filter(row => row.state !== "cut").map(row => MAP_DIAL[row.state] || "togo"))};
}

// A CEO's standing, the first that holds: its colour, its word and why. Every place that shows a standing reads this.
export function standing(one, rules, now) {
  const {owed, held, moved} = one.desk;
  if (!one.online) return {colour: "red", word: "Stopped", why: one.tick === null ? "it has never run" : `no pass since ${day(one.tick)}`};
  if (held) return {colour: "red", word: "A check failed", why: `${plural(held, "order")} held`};
  if (owed >= rules.owedAtLeast) return {colour: "yellow", word: "Waiting on you", why: plural(owed, "decision")};
  if (moved === null) return {colour: "yellow", word: "Slow", why: "nothing has moved yet"};
  const quiet = Math.floor((now - moved) / HOUR);
  if (now - moved >= rules.quietHours * HOUR) return {colour: "yellow", word: "Slow", why: `nothing moved for ${quiet}h`};
  return {colour: "green", word: "Moving",
    why: owed ? `${plural(owed, "decision")}, under your line of ${rules.owedAtLeast}` : "nothing needs you"};
}

function rank(word) {
  const at = RANK.indexOf(word);
  return at < 0 ? RANK.indexOf("") : at;
}

// Everything the page shows, from feed (feed.rs's JSON, parsed) at now, in milliseconds.
export function model(feed, now, rules = RULES) {
  const files = object(feed) ? list(feed.files).filter(object) : [];
  const ceos = [], unreadable = [], taken = new Set();
  for (const [at, found] of files.map(parsed).entries()) {
    const name = found.one && found.one.ceo;
    if (!found.one) unreadable.push(found);
    else if (name.trim().toLowerCase() === "all") unreadable.push({...found, why: "no CEO can be named all"});
    else if (taken.has(name)) unreadable.push({...found, why: `a second file of ${name}, and a CEO has one file`});
    else {
      taken.add(name);
      ceos.push(ceo(found.one, files[at].name, now, rules));
    }
  }
  ceos.sort((a, b) => Number(b.online) - Number(a.online));  // offline sinks to the bottom, else by file name
  const times = files.map(file => file.modified).filter(Number.isFinite);
  const newest = times.length ? Math.max(...times) * 1000 : null;
  const sum = key => ceos.reduce((total, one) => total + one.counts[key], 0);
  return {dir: object(feed) ? text(feed.dir) : "", error: object(feed) ? text(feed.error) : "the feed is no JSON object",
    feed: {at: newest, stale: newest === null || now - newest > QUIET}, ceos, unreadable,
    totals: {online: ceos.filter(one => one.online).length, of: ceos.length + unreadable.length,
      working: ceos.filter(one => one.working).length, merged: sum("merged"), orders: sum("orders"),
      needs: sum("needs"), owed: ceos.reduce((total, one) => total + one.desk.owed, 0), building: sum("building"), judging: sum("judging"), stuck: sum("stuck"), cut: sum("cut")}};
}

// The whole shape of the owner's choices from anything kept: nothing or nonsense gives DEFAULTS, a wrong part takes its
// default, and a number is rounded and, out of its range, takes the nearest end.
export function settings(raw) {
  const part = key => (object(raw) && object(raw[key]) ? raw[key] : {});
  const card = part("card"), rules = part("rules"), look = part("look");
  const one = (value, key, fallback) => (CHOICES[key].includes(value) ? value : fallback);
  const number = (value, [low, high], fallback) => (typeof value === "number" && Number.isFinite(value)
    ? Math.min(high, Math.max(low, Math.round(value))) : fallback);
  return {card: Object.fromEntries(Object.entries(DEFAULTS.card).map(([key, on]) =>
      [key, typeof card[key] === "boolean" ? card[key] : on])),
    size: one(object(raw) ? raw.size : null, "size", DEFAULTS.size),
    rules: Object.fromEntries(Object.entries(RANGES).map(([key, range]) => [key, number(rules[key], range, DEFAULTS.rules[key])])),
    look: {theme: one(look.theme, "theme", DEFAULTS.look.theme), accent: one(look.accent, "accent", DEFAULTS.look.accent)}};
}
