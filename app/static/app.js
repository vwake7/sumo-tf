const tree = document.getElementById("tree");
const filter = document.getElementById("filter");
const jsonEl = document.getElementById("json");
const queryEl = document.getElementById("query");
const titleEl = document.getElementById("object-title");
const kindLabel = document.getElementById("kind-label");
const regionEl = document.getElementById("region");
const refreshBtn = document.getElementById("refresh");

const MIN_SEARCH = 3;

let activeButton = null;
const loaded = new Map();
let searchSeq = 0;
let searchTimer = null;

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
    groupEl.className = "service";
    groupEl.dataset.label = group.label.toLowerCase();
    groupEl.dataset.title = (group.title || "").toLowerCase();
    const groupSummary = document.createElement("summary");
    groupSummary.textContent = group.label;
    groupEl.append(groupSummary);
    for (const kind of group.kinds) {
      const kindEl = document.createElement("details");
      kindEl.className = "kind";
      kindEl.dataset.kind = kind.id;
      kindEl.dataset.label = `${kind.id} ${kind.label}`.toLowerCase();
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
    paintSearch();
    return;
  }
  const body = kindEl.querySelector(".kind-body");
  body.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api(`/api/kinds/${encodeURIComponent(kindId)}`);
    loaded.set(kindId, data);
    const summary = kindEl.querySelector("summary");
    summary.textContent = `${data.label.split(".").pop()} (${data.count})`;
    body.innerHTML = "";
    if (!data.items.length) {
      body.innerHTML = '<p class="muted">None</p>';
      paintSearch();
      return;
    }
    for (const item of data.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "leaf";
      button.dataset.label = item.label.toLowerCase();
      button.dataset.id = String(item.id).toLowerCase();
      button.textContent = item.label;
      button.addEventListener("click", () => selectObject(kindId, item, button, data));
      body.append(button);
    }
    paintSearch();
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
    const data = await api(
      `/api/kinds/${encodeURIComponent(kindId)}/object?id=${encodeURIComponent(item.id)}`
    );
    setStatus(data.label, data.query, JSON.stringify(data.object, null, 2));
  } catch (error) {
    setStatus(item.label, listData.query, error.message, true);
  }
}

function clearHits() {
  for (const el of tree.querySelectorAll(".service, .kind, .leaf")) {
    el.hidden = false;
    el.classList.remove("hit");
  }
}

function paintSearch() {
  const q = filter.value.trim().toLowerCase();
  if (q.length < MIN_SEARCH) {
    clearHits();
    return;
  }
  let firstHit = null;
  for (const groupEl of tree.querySelectorAll("details.service")) {
    const serviceMatch =
      groupEl.dataset.label.includes(q) || groupEl.dataset.title.includes(q);
    let visibleKinds = 0;
    for (const kindEl of groupEl.querySelectorAll("details.kind")) {
      const kindMatch = kindEl.dataset.label.includes(q);
      let objectHits = 0;
      for (const button of kindEl.querySelectorAll(".leaf")) {
        const match = `${button.dataset.label || ""} ${button.dataset.id || ""}`.includes(q);
        button.classList.toggle("hit", match);
        button.hidden = Boolean(q) && !match && !kindMatch && !serviceMatch;
        if (match) {
          objectHits += 1;
          if (!firstHit) firstHit = button;
        }
      }
      const showKind = serviceMatch || kindMatch || objectHits > 0;
      kindEl.hidden = !showKind;
      kindEl.classList.toggle("hit", kindMatch);
      if (showKind) visibleKinds += 1;
      if (kindMatch || objectHits > 0) {
        kindEl.open = true;
        groupEl.open = true;
      }
    }
    groupEl.hidden = visibleKinds === 0;
    if (serviceMatch) groupEl.open = true;
  }
  firstHit?.scrollIntoView({ block: "nearest" });
}

async function runSearch() {
  const q = filter.value.trim().toLowerCase();
  const seq = ++searchSeq;
  if (q.length < MIN_SEARCH) {
    paintSearch();
    return;
  }
  const toLoad = [...tree.querySelectorAll("details.kind")].filter((el) =>
    el.dataset.label.includes(q)
  );
  await Promise.all(toLoad.map((el) => loadKind(el.dataset.kind, el)));
  if (seq !== searchSeq) return;
  paintSearch();
}

function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 200);
}

async function boot() {
  try {
    const nav = await api("/api/nav");
    const kinds = nav.groups.reduce((n, group) => n + group.kinds.length, 0);
    regionEl.textContent = `${nav.region} · ${nav.groups.length} services · ${kinds} resources`;
    renderNav(nav);
  } catch (error) {
    regionEl.textContent = "unavailable";
    setStatus("Cannot load nav", "", error.message, true);
  }
}

refreshBtn.addEventListener("click", async () => {
  loaded.clear();
  await api("/api/refresh", { method: "POST" });
  await boot();
});

filter.addEventListener("input", scheduleSearch);
filter.addEventListener("search", scheduleSearch);
boot();
