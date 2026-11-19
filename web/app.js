"use strict";
const byId = id => document.getElementById(id);
const route = location.pathname === "/" ? "session" : location.pathname.slice(1);
byId(route).hidden = false;
document.querySelector(`nav a[href="${location.pathname}"]`).setAttribute("aria-current", "page");
document.title = byId(route).querySelector("h1").textContent + " · FWC";

async function api(path, options) {
  const response = await fetch("/api/" + path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}
function report(error) { byId("notice").textContent = error.message; }
function list(id, values, empty) {
  byId(id).replaceChildren(...(values.length ? values : [empty]).map(value => {
    const item = document.createElement("li");
    item.textContent = value;
    return item;
  }));
}

async function session() {
  const [status, plugins, peers] = await Promise.all([api("status"), api("plugins"), api("peers")]);
  byId("committed").textContent = status.committed;
  byId("clients").textContent = status.clients;
  byId("running").textContent = status.services.filter(service => service.pid !== null).length;
  list("services", status.services.map(s => s.name + " · " + (s.pid === null ? "waiting" : "running")), "No services configured.");
  list("plugins", plugins.items.map(p => p.name + " · " + p.state + (p.detail ? " · " + p.detail : "")), "No plugins reported.");
  list("peers", peers.items.map(p => p.name + " · " + p.peer), "No nearby companions recorded.");
}
if (route === "session") {
  session().catch(report);
  let refreshing = false;
  setInterval(async () => {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try { await session(); } catch (error) { report(error); } finally { refreshing = false; }
  }, 5000);
  const stream = new EventSource("/api/events");
  stream.onopen = () => { byId("live").textContent = "Live"; };
  stream.onerror = () => { byId("live").textContent = "Reconnecting"; };
  const activity = [];
  stream.addEventListener("activity", e => {
    const event = JSON.parse(e.data);
    const data = event.data || {};
    const label = data.ssid || data.name || data.peer || "";
    activity.unshift("#" + event.seq + " · " + event.topic + (label ? " · " + label : ""));
    activity.length = Math.min(20, activity.length);
    list("activity", activity, "");
  });
}
if (route === "records") {
  const history = [""];
  let next = null;
  async function render() {
    byId("next").disabled = byId("previous").disabled = true;
    const page = await api("networks?limit=25&after=" + encodeURIComponent(history[history.length - 1]));
    const rows = page.items.map(record => {
      const row = document.createElement("tr");
      const date = new Date(record.time_ns / 1e6);
      for (const value of [(record.ssid || "Unnamed network") + (record.owned ? " · owned" : ""), record.network, record.channel, date.toLocaleString()]) {
        const cell = row.insertCell(); cell.textContent = value;
      }
      return row;
    });
    byId("networks").replaceChildren(...rows);
    byId("empty").hidden = rows.length !== 0;
    next = page.next;
    byId("page").textContent = "Page " + history.length;
    byId("next").disabled = !next;
    byId("previous").disabled = history.length === 1;
  }
  byId("next").onclick = () => { history.push(next); render().catch(report); };
  byId("previous").onclick = () => { history.pop(); render().catch(report); };
  render().catch(report);
}
if (route === "settings") {
  const form = byId("editor");
  async function load() {
    const values = await api("config");
    for (const [key, value] of Object.entries(values)) {
      form.elements[key].value = Array.isArray(value) ? value.join(key === "channels" ? ", " : "\n") : value;
    }
    form.querySelectorAll("fieldset, button").forEach(el => { el.disabled = false; });
  }
  form.onsubmit = async e => {
    e.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    values.channels = values.channels.split(",").map(channel => Number(channel.trim()));
    for (const key of ["dwell_ms", "file_count", "max_bytes"]) values[key] = Number(values[key]);
    for (const key of ["allow_ssids", "deny_ssids"]) values[key] = values[key].split("\n").filter(s => s.length);
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await api("config", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(values)});
      byId("notice").textContent = "Settings saved for the next worker start.";
    } catch (error) { report(error); } finally { button.disabled = false; }
  };
  load().catch(report);
}
