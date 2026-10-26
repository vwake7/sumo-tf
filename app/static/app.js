const tree = document.getElementById("tree");
const filter = document.getElementById("filter");
const jsonEl = document.getElementById("json");
const queryEl = document.getElementById("query");
const titleEl = document.getElementById("object-title");
const kindLabel = document.getElementById("kind-label");
const regionEl = document.getElementById("region");
const refreshBtn = document.getElementById("refresh");
const generateBtn = document.getElementById("generate");
const clearBtn = document.getElementById("clear-selection");
const selectCountEl = document.getElementById("select-count");

const MIN_SEARCH = 3;

let activeButton = null;
const loaded = new Map();
const selectedKinds = new Set();
const selectedObjects = new Map();
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

function objectSet(kindId) {
  if (!selectedObjects.has(kindId)) selectedObjects.set(kindId, new Set());
  return selectedObjects.get(kindId);
}

function kindTotal(kindEl) {
  const n = Number(kindEl.dataset.total);
  return Number.isFinite(n) ? n : 0;
}

function kindSelectedCount(kindEl) {
  const kindId = kindEl.dataset.kind;
  const total = kindTotal(kindEl);
  if (selectedKinds.has(kindId)) return total;
  return objectSet(kindId).size;
}

function setTally(el, selected, total) {
  if (!el) return;
  el.textContent = selected > 0 ? `${selected} / ${total}` : String(total);
}

function paintSelection() {
  let selectedAll = 0;
  let totalAll = 0;
  for (const groupEl of tree.querySelectorAll("details.service")) {
    let selected = 0;
    let total = 0;
    for (const kindEl of groupEl.querySelectorAll("details.kind")) {
      const kindSel = kindSelectedCount(kindEl);
      const kindTot = kindTotal(kindEl);
      selected += kindSel;
      total += kindTot;
      setTally(kindEl.querySelector(".tally"), kindSel, kindTot);
      const kindBox = kindEl.querySelector('summary input[type="checkbox"]');
      if (kindBox) {
        kindBox.checked = kindTot > 0 && kindSel === kindTot;
        kindBox.indeterminate = kindSel > 0 && kindSel < kindTot;
      }
    }
    selectedAll += selected;
    totalAll += total;
    setTally(groupEl.querySelector(":scope > summary .tally"), selected, total);
  }
  if (selectedAll > 0) {
    selectCountEl.textContent = `Selected ${selectedAll} of ${totalAll}`;
  } else {
    selectCountEl.textContent = `0 of ${totalAll} selected`;
  }
  clearBtn.disabled = selectedAll === 0;
}

function clearSelection() {
  selectedKinds.clear();
  selectedObjects.clear();
  for (const box of tree.querySelectorAll('input[type="checkbox"]')) {
    box.checked = false;
    box.indeterminate = false;
  }
  paintSelection();
}

function stopPick(event) {
  event.stopPropagation();
}

function renderNav(nav) {
  tree.innerHTML = "";
  for (const group of nav.groups) {
    const groupEl = document.createElement("details");
    groupEl.className = "service";
    groupEl.dataset.label = group.label.toLowerCase();
    groupEl.dataset.title = (group.title || "").toLowerCase();
    const groupSummary = document.createElement("summary");
    const groupName = document.createElement("span");
    groupName.className = "label-text";
    groupName.textContent = group.label;
    const groupTally = document.createElement("span");
    groupTally.className = "tally";
    const groupTotal = group.kinds.reduce((n, kind) => n + (Number(kind.count) || 0), 0);
    groupEl.dataset.total = String(groupTotal);
    groupSummary.append(groupName, groupTally);
    groupEl.append(groupSummary);
    for (const kind of group.kinds) {
      const kindEl = document.createElement("details");
      kindEl.className = "kind";
      kindEl.dataset.kind = kind.id;
      kindEl.dataset.total = String(Number(kind.count) || 0);
      kindEl.dataset.label = `${kind.id} ${kind.label}`.toLowerCase();
      const summary = document.createElement("summary");
      const pick = document.createElement("label");
      pick.className = "pick";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.kind = kind.id;
      box.checked = selectedKinds.has(kind.id);
      box.addEventListener("click", stopPick);
      box.addEventListener("change", () => {
        if (box.checked) selectedKinds.add(kind.id);
        else selectedKinds.delete(kind.id);
        for (const child of kindEl.querySelectorAll('input[type="checkbox"][data-id]')) {
          child.checked = box.checked;
          const ids = objectSet(kind.id);
          if (box.checked) ids.add(child.dataset.id);
          else ids.delete(child.dataset.id);
        }
        paintSelection();
      });
      pick.append(box);
      pick.addEventListener("click", stopPick);
      const name = document.createElement("span");
      name.className = "label-text";
      name.textContent = kind.label;
      const tally = document.createElement("span");
      tally.className = "tally";
      summary.append(pick, name, tally);
      summary.addEventListener("click", (event) => {
        if (event.target.closest(".pick")) event.preventDefault();
      });
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
  paintSelection();
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
    kindEl.dataset.total = String(data.count || 0);
    const parent = kindEl.closest("details.service");
    if (parent) {
      const sum = [...parent.querySelectorAll("details.kind")].reduce(
        (n, el) => n + kindTotal(el),
        0
      );
      parent.dataset.total = String(sum);
    }
    body.innerHTML = "";
    if (!data.items.length) {
      body.innerHTML = '<p class="muted">None</p>';
      paintSearch();
      return;
    }
    const kindChecked = selectedKinds.has(kindId);
    for (const item of data.items) {
      const row = document.createElement("div");
      row.className = "leaf-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.kind = kindId;
      box.dataset.id = String(item.id);
      box.checked = kindChecked || objectSet(kindId).has(String(item.id));
      if (box.checked) objectSet(kindId).add(String(item.id));
      box.addEventListener("change", () => {
        const ids = objectSet(kindId);
        if (box.checked) ids.add(String(item.id));
        else {
          ids.delete(String(item.id));
          selectedKinds.delete(kindId);
          const kindBox = kindEl.querySelector('summary input[type="checkbox"]');
          if (kindBox) kindBox.checked = false;
        }
        paintSelection();
      });
      const button = document.createElement("button");
      button.type = "button";
      button.className = "leaf";
      button.dataset.label = item.label.toLowerCase();
      button.dataset.id = String(item.id).toLowerCase();
      button.textContent = item.label;
      button.addEventListener("click", () => selectObject(kindId, item, button, data));
      row.append(box, button);
      body.append(row);
    }
    paintSearch();
    paintSelection();
  } catch (error) {
    body.innerHTML = `<p class="muted">${error.message}</p>`;
  }
}

async function selectObject(kindId, item, button, listData) {
  if (activeButton) activeButton.classList.remove("active");
  activeButton = button;
  button.classList.add("active");
  kindLabel.textContent = listData.label;
  if (item.object) {
    const origin = item.source === "get" ? "GET" : "list";
    setStatus(item.label, `${origin}: ${item.query || listData.query}`, JSON.stringify(item.object, null, 2));
    return;
  }
  setStatus(item.label, listData.query, "Loading JSON…");
  try {
    const data = await api(
      `/api/kinds/${encodeURIComponent(kindId)}/object?id=${encodeURIComponent(item.id)}`
    );
    const origin = data.source === "get" ? "GET" : "list";
    setStatus(data.label, `${origin}: ${data.query}`, JSON.stringify(data.object, null, 2));
  } catch (error) {
    setStatus(item.label, listData.query, error.message, true);
  }
}

function clearHits() {
  for (const el of tree.querySelectorAll(".service, .kind, .leaf-row, .leaf")) {
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
      for (const row of kindEl.querySelectorAll(".leaf-row")) {
        const button = row.querySelector(".leaf");
        const match = `${button?.dataset.label || ""} ${button?.dataset.id || ""}`.includes(q);
        button?.classList.toggle("hit", match);
        row.hidden = Boolean(q) && !match && !kindMatch && !serviceMatch;
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

function selectionPayload() {
  const kinds = [...selectedKinds];
  const objects = [];
  for (const [kindId, ids] of selectedObjects) {
    if (selectedKinds.has(kindId)) continue;
    for (const id of ids) objects.push({ kind: kindId, id });
  }
  return { kinds, objects };
}

async function boot() {
  try {
    const nav = await api("/api/nav");
    const kinds = nav.groups.reduce((n, group) => n + group.kinds.length, 0);
    const ready = nav.preloaded ? "preloaded" : "live";
    regionEl.textContent = `${nav.region} · ${nav.groups.length} services · ${kinds} resources · ${ready}`;
    renderNav(nav);
  } catch (error) {
    regionEl.textContent = "unavailable";
    setStatus("Cannot load nav", "", error.message, true);
  }
}

refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Refreshing from Sumo…";
  loaded.clear();
  selectedKinds.clear();
  selectedObjects.clear();
  setStatus(
    "Refreshing from Sumo",
    "",
    "Listing every resource and GET-by-id from the live Sumo account. This can take several minutes."
  );
  try {
    await api("/api/refresh", { method: "POST" });
    await boot();
    setStatus(
      "Account refreshed",
      "",
      "Resources were reloaded from the Sumo account. Select a resource in the left nav."
    );
  } catch (error) {
    setStatus("Refresh failed", "", error.message, true);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh from Sumo";
  }
});

generateBtn.addEventListener("click", async () => {
  const payload = selectionPayload();
  if (!payload.kinds.length && !payload.objects.length) {
    setStatus("Nothing selected", "", "Check one or more resources, then generate Terraform.", true);
    return;
  }
  generateBtn.disabled = true;
  generateBtn.textContent = "Generating Terraform…";
  setStatus(
    "Generating Terraform",
    "",
    "Expanding upstream and downstream dependencies, then writing HCL from the StackQL snapshot and the Terraform provider schema."
  );
  try {
    const result = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setStatus(
      "Terraform written",
      result.output,
      JSON.stringify(
        {
          output: result.output,
          selected: result.selected,
          expanded: result.expanded,
          generated: result.generated,
          skipped: result.skipped,
          files: (result.report.generated || []).map((row) => row.file),
          skipped_detail: result.report.skipped,
        },
        null,
        2
      )
    );
  } catch (error) {
    setStatus("Generate failed", "", error.message, true);
  } finally {
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate Terraform";
  }
});

clearBtn.addEventListener("click", clearSelection);

filter.addEventListener("input", scheduleSearch);
filter.addEventListener("search", scheduleSearch);
boot();
