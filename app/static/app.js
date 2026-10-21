const tree = document.getElementById("tree");
const filter = document.getElementById("filter");
const jsonEl = document.getElementById("json");
const queryEl = document.getElementById("query");
const titleEl = document.getElementById("object-title");
const kindLabel = document.getElementById("kind-label");
const regionEl = document.getElementById("region");
const refreshBtn = document.getElementById("refresh");

let activeButton = null;
const loaded = new Map();

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || response.statusText);
  }
  return body;
}

function setStatus(title, query, message, isError) {
  titleEl.textContent = title;
  queryEl.textContent = query || "";
  jsonEl.textContent = message;
  jsonEl.classList.toggle("error", Boolean(isError));
}

function renderNav(nav) {
  tree.innerHTML = "";
  for (const group of nav.groups) {
    const groupEl = document.createElement("details");
    groupEl.open = true;
    const groupSummary = document.createElement("summary");
    groupSummary.textContent = group.label;
    groupEl.append(groupSummary);
    for (const kind of group.kinds) {
      const kindEl = document.createElement("details");
      kindEl.className = "kind";
      kindEl.dataset.kind = kind.id;
      const summary = document.createElement("summary");
      summary.textContent = kind.label;
      const body = document.createElement("div");
      body.className = "kind-body";
      body.innerHTML = '<p class="muted">Expand to load</p>';
      kindEl.append(summary, body);
      kindEl.addEventListener("toggle", () => {
        if (kindEl.open) loadKind(kind.id, kindEl);
      });
      groupEl.append(kindEl);
    }
    tree.append(groupEl);
  }
}

async function loadKind(kindId, kindEl, force) {
  if (loaded.has(kindId) && !force) {
    applyFilter();
    return;
  }
  const body = kindEl.querySelector(".kind-body");
  body.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api(`/api/kinds/${kindId}`);
    loaded.set(kindId, data);
    const summary = kindEl.querySelector("summary");
    summary.textContent = `${data.label} (${data.count})`;
    body.innerHTML = "";
    if (!data.items.length) {
      body.innerHTML = '<p class="muted">None</p>';
      return;
    }
    for (const item of data.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "leaf";
      button.dataset.label = item.label.toLowerCase();
      button.textContent = item.label;
      button.addEventListener("click", () => selectObject(kindId, item, button, data));
      body.append(button);
    }
    applyFilter();
  } catch (error) {
    body.innerHTML = `<p class="muted">${error.message}</p>`;
  }
}

async function selectObject(kindId, item, button, listData) {
  if (activeButton) activeButton.classList.remove("active");
  activeButton = button;
  button.classList.add("active");
  kindLabel.textContent = listData.label;
  setStatus(item.label, listData.query, "Loading JSON…");
  try {
    const data = await api(`/api/kinds/${kindId}/objects/${encodeURIComponent(item.id)}`);
    setStatus(data.label, data.query, JSON.stringify(data.object, null, 2));
  } catch (error) {
    setStatus(item.label, listData.query, error.message, true);
  }
}

function applyFilter() {
  const q = filter.value.trim().toLowerCase();
  for (const button of tree.querySelectorAll(".leaf")) {
    const show = !q || button.dataset.label.includes(q);
    button.hidden = !show;
  }
}

async function boot() {
  try {
    const nav = await api("/api/nav");
    regionEl.textContent = `${nav.region} · SELECT only`;
    renderNav(nav);
  } catch (error) {
    regionEl.textContent = "unavailable";
    setStatus("Cannot load nav", "", error.message, true);
  }
}

refreshBtn.addEventListener("click", async () => {
  loaded.clear();
  await api("/api/refresh", { method: "POST" });
  for (const kindEl of tree.querySelectorAll("details.kind")) {
    if (kindEl.open) await loadKind(kindEl.dataset.kind, kindEl, true);
  }
});

filter.addEventListener("input", applyFilter);
boot();
