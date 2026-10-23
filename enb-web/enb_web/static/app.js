"use strict";

const $ = id => document.getElementById(id);
const svgNamespace = "http://www.w3.org/2000/svg";
let objects = [];
let shipPosition = null;
let filter = "all";
let selected = null;
let sector = null;
let box = [-220, -220, 440, 440];
let initialBox = [...box];
let pendingOperation = false;
let noticeOverride = null;
let drag = null;
let connected = false;

function svgElement(name, attributes) {
  const element = document.createElementNS(svgNamespace, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function hex(id) {
  return "0x" + id.toString(16).padStart(8, "0");
}

function matching() {
  const query = $("search").value.trim().toLowerCase();
  return objects.filter(object =>
    (filter === "all" || object.kind === filter) &&
    (!query || (object.name + " " + hex(object.id) + " " + object.id).toLowerCase().includes(query))
  );
}

function setBox() {
  $("map").setAttribute("viewBox", box.join(" "));
}

function fitMap() {
  if (!objects.length) return;
  const xs = objects.map(object => object.position[0]);
  const ys = objects.map(object => -object.position[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const size = Math.max(maxX - minX, maxY - minY, 100) * 1.35;
  box = [(minX + maxX - size) / 2, (minY + maxY - size) / 2, size, size];
  initialBox = [...box];
  setBox();
}

function selectObject(id) {
  selected = id;
  const object = objects.find(item => item.id === id);
  if (!object) return;
  const title = document.createElement("strong");
  const detail = document.createElement("span");
  title.textContent = object.name;
  detail.textContent = `${hex(object.id)} · ${object.kind} · ${object.position.map(v => v.toFixed(2)).join(", ")} · ${object.visited ? "Visited" : "Not visited"}`;
  $("selection").replaceChildren(title, detail);
  render();
}

function render() {
  const visible = matching();
  const ids = new Set(visible.map(object => object.id));
  $("visible").textContent = visible.length;
  $("objects").replaceChildren();
  for (const object of visible) {
    const row = document.createElement("button");
    row.className = "object-row" + (selected === object.id ? " is-selected" : "");
    row.setAttribute("aria-pressed", String(selected === object.id));
    const icon = document.createElement("span");
    icon.className = "icon " + object.kind;
    icon.textContent = object.kind === "gate" ? "◈" : object.kind === "station" ? "◇" : "·";
    const text = document.createElement("span");
    const name = document.createElement("span");
    const sub = document.createElement("span");
    const arrow = document.createElement("span");
    name.className = "name";
    name.textContent = object.name;
    sub.className = "sub";
    sub.textContent = hex(object.id) + " · " + object.kind;
    arrow.className = "arrow";
    arrow.textContent = "›";
    text.append(name, sub);
    row.append(icon, text, arrow);
    row.addEventListener("click", () => selectObject(object.id));
    $("objects").append(row);
  }
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No matching navigation objects.";
    $("objects").append(empty);
  }
  $("points").replaceChildren();
  for (const object of objects) {
    const group = svgElement("g", {
      class: `point ${object.kind}${selected === object.id ? " is-selected" : ""}${ids.has(object.id) ? "" : " dim"}`,
      transform: `translate(${object.position[0]} ${-object.position[1]})`,
      tabindex: "0", role: "button", "aria-label": object.name
    });
    const circle = svgElement("circle", {r: object.kind === "navpoint" ? 2.2 : 4});
    const title = svgElement("title", {});
    title.textContent = object.name + " · " + hex(object.id);
    group.append(title, circle);
    if (object.kind !== "navpoint" || selected === object.id) {
      const label = svgElement("text", {x: 7, y: 2});
      label.textContent = object.name.replace("Sector Gate to ", "→ ");
      group.append(label);
    }
    group.addEventListener("click", () => selectObject(object.id));
    group.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectObject(object.id);
      }
    });
    $("points").append(group);
  }
  if (shipPosition) {
    const ship = svgElement("g", {class: "ship", transform: `translate(${shipPosition[0]} ${-shipPosition[1]})`});
    const title = svgElement("title", {});
    title.textContent = "Your ship";
    ship.append(title, svgElement("circle", {r: 7}), svgElement("path", {d: "M0,-5 L4,4 L0,2 L-4,4 Z"}));
    $("points").append(ship);
  }
  const target = objects.find(object => object.id === selected);
  $("warp").disabled = !connected || pendingOperation || !target;
  $("gate").disabled = !connected || pendingOperation || !target || target.kind !== "gate";
}

function zoom(factor) {
  const width = Math.min(2500, Math.max(25, box[2] * factor));
  const height = width * box[3] / box[2];
  box = [box[0] + (box[2] - width) / 2, box[1] + (box[3] - height) / 2, width, height];
  setBox();
}

$("zoom-in").addEventListener("click", () => zoom(.8));
$("zoom-out").addEventListener("click", () => zoom(1.25));
$("fit").addEventListener("click", fitMap);
$("map").addEventListener("wheel", event => {
  event.preventDefault();
  zoom(event.deltaY < 0 ? .9 : 1.1);
}, {passive: false});
$("map").addEventListener("pointerdown", event => {
  if (event.target.closest(".point")) return;
  drag = {x: event.clientX, y: event.clientY, box: [...box]};
  $("map").setPointerCapture(event.pointerId);
});
$("map").addEventListener("pointermove", event => {
  if (!drag) return;
  const bounds = $("map").getBoundingClientRect();
  const scale = Math.min(bounds.width / drag.box[2], bounds.height / drag.box[3]);
  box = [drag.box[0] - (event.clientX - drag.x) / scale, drag.box[1] - (event.clientY - drag.y) / scale, drag.box[2], drag.box[3]];
  setBox();
});
for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
  $("map").addEventListener(event, () => { drag = null; });
}
$("search").addEventListener("input", render);
for (const button of document.querySelectorAll("[data-filter]")) {
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    for (const sibling of document.querySelectorAll("[data-filter]")) {
      sibling.classList.toggle("selected", sibling === button);
    }
    render();
  });
}

function displayState(data) {
  const sectorChanged = data.sector !== sector;
  const firstObjects = objects.length === 0 && data.objects.length > 0;
  sector = data.sector;
  objects = data.objects;
  shipPosition = data.position;
  connected = data.character_connected;
  $("sector").textContent = sector || "Sector console";
  $("total").textContent = objects.length;
  $("gates").textContent = objects.filter(object => object.kind === "gate").length;
  $("stations").textContent = objects.filter(object => object.kind === "station").length;
  $("server").textContent = data.game_server;
  $("session-badge").textContent = data.character_connected ? `● ${data.character} · Live` : data.session_status.replaceAll("_", " ");
  $("character-title").textContent = data.character ? `${data.character} · ${data.character_connected ? "Live sector stream" : data.session_status.replaceAll("_", " ")}` : "Your sector, from the wire.";
  const characters = data.characters || [];
  if (JSON.stringify(characters) !== $("character").dataset.characters) {
    $("character").dataset.characters = JSON.stringify(characters);
    $("character").replaceChildren(...characters.map(character => {
      const option = document.createElement("option");
      option.value = character.slot;
      option.textContent = `${character.name} · ${character.location}`;
      return option;
    }));
  }
  const busy = pendingOperation || ["logging_in", "authenticating"].includes(data.session_status);
  $("account-panel").hidden = connected || data.session_status === "logging_in";
  $("account-form").hidden = data.session_status === "choose_character";
  $("character-selection").hidden = data.session_status !== "choose_character";
  $("authenticate").disabled = busy;
  $("username").disabled = busy;
  $("password").disabled = busy;
  $("login").disabled = busy || !characters.length;
  $("logout").disabled = busy || (!connected && !characters.length);
  $("character").disabled = busy;
  $("login").textContent = data.session_status === "logging_in" ? "Logging in…" : "Log in";
  const age = data.packet_age === null ? "—" : data.packet_age.toFixed(1) + "s";
  let message;
  if (data.source === "live") message = `${data.character} is connected directly · ${data.packets} packets received · latest packet ${age} ago`;
  else if (data.source === "capture") message = "Relogin snapshot · Log in to show your character’s current map.";
  else message = data.session_status === "logging_in" ? "Authenticating and receiving the character’s sector map…" : "Choose a character and log in.";
  $("notice").classList.toggle("error", Boolean(data.session_error));
  $("notice").textContent = data.session_error || noticeOverride || message;
  $("navigation-status").textContent = data.navigation_status || "Select a map object to travel.";
  $("source").textContent = data.source === "live" ? "Source: live server packets" : data.source === "capture" ? "Source: relogin capture" : "Source: " + data.session_status;
  if (sectorChanged) selected = null;
  if (sectorChanged || firstObjects) fitMap();
  render();
}

async function refresh() {
  const response = await fetch("/api/state");
  if (!response.ok) throw new Error("Could not load sector data");
  displayState(await response.json());
}

async function operation(path, payload = null) {
  if (pendingOperation) return;
  pendingOperation = true;
  noticeOverride = null;
  try {
    await refresh();
    const options = {method: "POST", headers: {"X-ENB-Request": "1"}};
    if (payload) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    if (data.message) noticeOverride = data.message;
  } catch (error) {
    noticeOverride = error.message;
    $("notice").classList.add("error");
    $("notice").textContent = error.message;
  } finally {
    pendingOperation = false;
    await refresh();
  }
}

$("account-form").addEventListener("submit", event => {
  event.preventDefault();
  const credentials = {username: $("username").value, password: $("password").value};
  $("password").value = "";
  operation("/api/authenticate", credentials);
});
$("login").addEventListener("click", () => operation("/api/login", {slot: Number($("character").value)}));
$("logout").addEventListener("click", () => operation("/api/logout"));
$("probe").addEventListener("click", () => operation("/api/probe"));
$("warp").addEventListener("click", () => operation("/api/navigate", {target: selected, sector, mode: "warp"}));
$("gate").addEventListener("click", () => operation("/api/navigate", {target: selected, sector, mode: "gate"}));
async function poll() {
  try { await refresh(); }
  catch (error) {
    $("notice").classList.add("error");
    $("notice").textContent = "Web connection lost: " + error.message;
  }
  setTimeout(poll, 1500);
}
poll();
