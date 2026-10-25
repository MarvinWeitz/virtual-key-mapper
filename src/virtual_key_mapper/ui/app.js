// The Virtual Key Mapper setup page: talks only to the vkm server that served it, with the key vkm ui opened it with.
"use strict";

const TOOL_NAMES = { claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };
const SOURCES = {
  explicit: "this request", env: "environment variable", local: "your saved labels", file: ".vkm file", rules: "central rules",
};
const STATES = {
  ready: ["Signed in", "ok"],
  "logged-out": ["Not signed in", "warn"],
  expired: ["Sign-in expired, renews on use", "ok"],
  "re-login-required": ["Sign in again", "warn"],
};

const state = { token: null, profile: null, profiles: [], definitions: [], folder: null, models: null };
const $ = (selector) => document.querySelector(selector);

function element(tag, properties = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, properties);
  for (const child of [].concat(children)) node.append(child);
  return node;
}

// Talking to the server

function readToken() {
  const match = location.hash.match(/token=([A-Za-z0-9_-]+)/);
  if (match) {
    history.replaceState(null, "", location.pathname);  // Keep the key out of the address bar and bookmarks.
    try { sessionStorage.setItem("vkm-token", match[1]); } catch (error) { /* Private windows may refuse. */ }
    return match[1];
  }
  try { return sessionStorage.getItem("vkm-token"); } catch (error) { return null; }
}

async function api(path, { method = "GET", body, query = {}, profile = true } = {}) {
  const params = new URLSearchParams();
  if (profile && state.profile) params.set("profile", state.profile);
  for (const [name, value] of Object.entries(query)) {
    for (const item of [].concat(value)) if (item !== undefined && item !== null) params.append(name, item);
  }
  const headers = { Authorization: `Bearer ${state.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const search = params.toString();
  const response = await fetch(`/api${path}${search ? `?${search}` : ""}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); } catch (error) { data = { error: `The server answered ${response.status}.` }; }
  for (const text of data.messages || []) show(text, "note");
  if (!response.ok) throw new Error(data.error || `The server answered ${response.status}.`);
  return data.result;
}

function show(text, kind = "") {
  const box = element("div", { className: `message ${kind}`.trim(), textContent: text });
  $("#messages").append(box);
  if (kind !== "error") setTimeout(() => box.remove(), 12000);
}

function clearMessages() { $("#messages").replaceChildren(); }

// Runs an action with its button disabled and shows a failure as a message.
async function busy(button, action) {
  clearMessages();
  if (button) button.disabled = true;
  try { return await action(); } catch (error) { show(error.message, "error"); } finally { if (button) button.disabled = false; }
}

// Sections

function selectTab(id) {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.getAttribute("aria-controls") === id;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(`#${tab.getAttribute("aria-controls")}`).hidden = !selected;
  }
  if (id === "tools") loadTools();
  if (id === "repositories") loadRepositories();
}

async function loadProfiles() {
  const { default: active, profiles } = await api("/profiles", { profile: false });
  state.profiles = profiles;
  if (!profiles.some((profile) => profile.name === state.profile)) state.profile = active || profiles[0]?.name || null;
  const picker = $("#profile");
  picker.replaceChildren(...profiles.map((profile) => element("option", {
    value: profile.name, textContent: profile.name + (profile.name === active ? " (default)" : ""),
    selected: profile.name === state.profile,
  })));
  $(".profile-picker").hidden = profiles.length === 0;
  $(".tabs").hidden = profiles.length === 0;
  $("#add-profile").open = profiles.length === 0;
  $("#make-default").hidden = !state.profile || state.profile === active;
  if (profiles.length === 0) {
    $("#status-card").hidden = true;
    return;
  }
  await loadStatus();
}

async function loadStatus() {
  const profile = state.profiles.find((item) => item.name === state.profile);
  $("#status-card").hidden = !profile;
  if (!profile) return;
  $("#status-gateway").textContent = profile.gateway.url;
  let status;
  try {
    status = await api("/status");
  } catch (error) {
    show(error.message, "error");
    status = { state: "unknown" };
  }
  const [text, kind] = STATES[status.state] || ["Unknown", "warn"];
  const pill = $("#status-state");
  pill.textContent = text;
  pill.className = `pill ${kind}`;
  $("#status-user").textContent = status.user_id || "–";
  const signedIn = status.state === "ready" || status.state === "expired";
  $("#login").hidden = signedIn;
  $("#logout").hidden = !signedIn;
  state.models = null;
}

function addLabelRow() {
  const row = $("#label-row").content.firstElementChild.cloneNode(true);
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  $("#label-rows").append(row);
}

async function addProfile(event) {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const labels = [...document.querySelectorAll("#label-rows .label-row")].map((row) => {
    const label = { key: row.querySelector('[name="key"]').value.trim() };
    const description = row.querySelector('[name="description"]').value.trim();
    if (description) label.description = description;
    if (row.querySelector('[name="required"]').checked) label.required = true;
    return label;
  });
  const body = { name: data.get("name").trim(), gateway: data.get("gateway").trim().replace(/\/+$/, "") };
  for (const name of ["team_id", "rules"]) if (data.get(name).trim()) body[name] = data.get(name).trim();
  if (labels.length) body.definitions = { labels };
  await busy(form.querySelector('[type="submit"]'), async () => {
    const profile = await api("/profiles", { method: "POST", body, profile: false });
    if (state.profiles.length === 0) await api("/profiles/default", { method: "POST", body: { name: profile.name }, profile: false });
    state.profile = profile.name;
    form.reset();
    $("#label-rows").replaceChildren();
    show(`Added profile ${profile.name}. Sign in next.`);
    await loadProfiles();
  });
}

async function login(event) {
  await busy(event.target, async () => {
    $("#login-hint").hidden = false;
    try {
      const result = await api("/login", { method: "POST", body: {} });
      show(`Signed in as ${result.user}.`);
    } finally {
      $("#login-hint").hidden = true;
    }
    await loadStatus();
  });
}

async function logout(event) {
  await busy(event.target, async () => {
    await api("/logout", { method: "POST", body: {} });
    show("Signed out. Keys already issued expire on their own.");
    await loadStatus();
  });
}

async function makeDefault(event) {
  await busy(event.target, async () => {
    await api("/profiles/default", { method: "POST", body: { name: state.profile }, profile: false });
    await loadProfiles();
  });
}

// Tools

async function models() {
  if (state.models === null) {
    try { state.models = (await api("/models")).map((model) => model.id); } catch (error) { state.models = []; }
  }
  return state.models;
}

async function loadTools() {
  await busy(null, async () => {
    const [tools, available] = await Promise.all([api("/tools", { profile: false }), models()]);
    $("#tool-list").replaceChildren(...tools.map((tool) => toolCard(tool, available)));
    if (!available.length) show("Sign in to choose a model; the gateway lists the models you may use.", "note");
  });
}

function toolCard(tool, available) {
  const model = element("select", {}, [
    element("option", { value: "", textContent: "The tool's default" }),
    ...available.map((id) => element("option", { value: id, textContent: id })),
  ]);
  const replace = element("input", { type: "checkbox" });
  const notes = element("ul", { className: "notes" });
  const connect = element("button", { className: "primary", textContent: "Connect" });
  const remove = element("button", { textContent: "Disconnect" });
  const report = (lines) => notes.replaceChildren(...lines.map((line) => element("li", { textContent: line })));
  connect.addEventListener("click", () => busy(connect, async () => {
    const body = { replace: replace.checked };
    if (model.value) body.model = model.value;
    report((await api(`/setup/${tool}`, { method: "POST", body })).notes);
  }));
  remove.addEventListener("click", () => busy(remove, async () => {
    report((await api(`/setup/${tool}/remove`, { method: "POST", body: {} })).notes);
  }));
  return element("div", { className: "panel" }, [
    element("h2", { textContent: TOOL_NAMES[tool] || tool }),
    element("label", { textContent: "Model" }, [model]),
    element("label", { className: "check" }, [replace, "Replace another credential setup"]),
    element("div", { className: "actions" }, [connect, remove]),
    notes,
  ]);
}

// Repositories

async function loadRepositories() {
  await busy(null, async () => {
    state.definitions = await api("/labels");
    await openFolder(state.folder);
    await loadBindings();
  });
}

async function openFolder(path) {
  const listing = await api("/folders", { query: { directory: path }, profile: false });
  state.folder = listing.path;
  state.listing = listing;
  $("#path").value = listing.path;
  $("#up").disabled = !listing.parent;
  $("#folders").replaceChildren(...listing.folders.map((folder) => {
    const button = element("button", { type: "button" }, [
      element("span", { textContent: folder.name }), element("span", { className: "tag", textContent: folder.git ? "Git" : "" }),
    ]);
    button.addEventListener("click", () => busy(null, () => openFolder(folder.path)));
    return element("li", {}, [button]);
  }));
  if (!listing.folders.length) $("#folders").append(element("li", { className: "hint", textContent: "No subfolders." }));
  await loadLabels();
}

async function loadLabels() {
  const document_ = await api("/repository", { query: { directory: state.folder } });
  $("#repository-name").textContent = document_.repository ? `Repository ${document_.repository}` : "Not a Git repository with an origin: only a .vkm file can label it.";
  const personal = document.querySelector('[name="kind"][value="personal"]');
  personal.disabled = !document_.repository;
  if (!document_.repository) document.querySelector('[name="kind"][value="shared"]').checked = true;
  $("#unbind").disabled = !document_.repository;
  const definitions = Object.fromEntries(state.definitions.map((definition) => [definition.key, definition]));
  $("#label-values").replaceChildren(...document_.labels.map((label) => labelRow(label, definitions[label.key] || {})));
  if (!document_.labels.length) {
    $("#label-values").append(element("tr", {}, [element("td", { colSpan: 3, className: "hint", textContent: "No labels are defined for this profile." })]));
  }
  $("#repository-state").textContent = document_.complete
    ? "Requests from this folder carry these labels."
    : document_.message || "Requests from this folder are blocked until the required labels have values.";
}

function labelRow(label, definition) {
  let input;
  if (definition.values) {
    input = element("select", { name: label.key }, [
      element("option", { value: "", textContent: "No value" }),
      ...definition.values.map((choice) => element("option", {
        value: choice.id, textContent: choice.name ? `${choice.name} (${choice.id})` : choice.id, selected: choice.id === label.value,
      })),
    ]);
  } else {
    input = element("input", { name: label.key, value: label.value || "", placeholder: label.description || "No value" });
  }
  input.dataset.original = label.value || "";
  input.setAttribute("aria-label", label.key);
  const name = element("td", {}, [element("span", { textContent: label.key })]);
  if (label.required) name.append(element("span", { className: "required", textContent: "required" }));
  if (label.description) name.title = label.description;
  return element("tr", {}, [name, element("td", {}, [input]), element("td", { className: "tag", textContent: label.source ? SOURCES[label.source] || label.source : "–", title: label.detail || "" })]);
}

async function saveLabels(event) {
  event.preventDefault();
  const form = event.target;
  const shared = form.elements.kind.value === "shared";
  const labels = {};
  for (const input of form.querySelectorAll("#label-values [name]")) {
    const value = input.value.trim();
    if (value !== input.dataset.original && (value || shared)) labels[input.name] = value;
  }
  await busy(form.querySelector('[type="submit"]'), async () => {
    if (!Object.keys(labels).length) throw new Error("Change a value first.");
    const binding = await api("/repository/bind", {
      method: "POST", body: { directory: state.folder, labels, shared, replace: form.elements.replace.checked },
    });
    show(binding.shared ? `Wrote ${binding.target}. Commit it to share it with your team.` : `Saved the labels of ${binding.target} for you.`);
    await loadLabels();
    await loadBindings();
  });
}

async function unbind(event) {
  await busy(event.target, async () => {
    const result = await api("/repository/unbind", { method: "POST", body: { directory: state.folder } });
    show(`Removed your labels for ${result.repository}.`);
    await loadLabels();
    await loadBindings();
  });
}

async function loadBindings() {
  const bindings = await api("/repositories");
  $("#bindings").replaceChildren(...bindings.map((binding) => element("li", {}, [
    element("strong", { textContent: binding.repository }),
    element("div", { className: "tag", textContent: Object.entries(binding.labels).map(([key, value]) => `${key} = ${value}`).join(", ") }),
  ])));
  if (!bindings.length) $("#bindings").append(element("li", { className: "hint", textContent: "None yet." }));
}

// Start

function start() {
  state.token = readToken();
  if (!state.token) {
    $("#no-token").hidden = false;
    return;
  }
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    tab.addEventListener("click", () => selectTab(tab.getAttribute("aria-controls")));
    tab.addEventListener("keydown", (event) => {
      const tabs = [...document.querySelectorAll('[role="tab"]')];
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
      next.focus();
      selectTab(next.getAttribute("aria-controls"));
    });
  }
  $("#profile").addEventListener("change", (event) => busy(null, async () => {
    state.profile = event.target.value;
    await loadProfiles();
    const open = document.querySelector('[role="tab"][aria-selected="true"]').getAttribute("aria-controls");
    if (open !== "account") selectTab(open);
  }));
  $("#add-label").addEventListener("click", addLabelRow);
  $("#profile-form").addEventListener("submit", addProfile);
  $("#login").addEventListener("click", login);
  $("#logout").addEventListener("click", logout);
  $("#make-default").addEventListener("click", makeDefault);
  $("#path-form").addEventListener("submit", (event) => { event.preventDefault(); busy(null, () => openFolder($("#path").value)); });
  $("#up").addEventListener("click", () => busy(null, () => openFolder(state.listing.parent)));
  $("#home").addEventListener("click", () => busy(null, () => openFolder(state.listing.home)));
  $("#labels-form").addEventListener("submit", saveLabels);
  $("#unbind").addEventListener("click", unbind);
  selectTab("account");
  busy(null, loadProfiles);
}

start();
