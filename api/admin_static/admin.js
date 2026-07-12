const state = {
  config: null,
  fields: new Map(),
  localStatus: new Map(),
  modelOptions: [],
  activeView: "providers",
};

const MASKED_SECRET = "********";
const VIEW_GROUPS = [
  {
    id: "providers",
    label: "Providers",
    title: "Providers",
    sections: ["providers", "runtime"],
    containerId: "providersSections",
  },
  {
    id: "model_config",
    label: "Model Config",
    title: "Model Config",
    sections: ["models", "thinking", "web_tools"],
    containerId: "modelConfigSections",
  },
  {
    id: "messaging",
    label: "Messaging",
    title: "Messaging",
    sections: ["messaging", "voice"],
    containerId: "messagingSections",
  },
];

const byId = (id) => document.getElementById(id);

function sourceLabel(source) {
  const labels = {
    default: "default",
    template: "template",
    repo_env: "repo .env",
    managed_env: "",
    explicit_env_file: "FCC_ENV_FILE",
    process: "process env",
  };
  return Object.prototype.hasOwnProperty.call(labels, source) ? labels[source] : source;
}

function sourceText(field) {
  const parts = [];
  const label = sourceLabel(field.source);
  if (label) {
    parts.push(label);
  }
  return parts.join(" ");
}

function providerName(providerId) {
  const names = {
    nvidia_nim: "NVIDIA NIM",
    open_router: "OpenRouter",
    mistral_codestral: "Mistral Codestral",
    deepseek: "DeepSeek",
    lmstudio: "LM Studio",
    llamacpp: "llama.cpp",
    ollama: "Ollama",
    kimi: "Kimi",
    wafer: "Wafer",
    opencode: "OpenCode Zen",
    opencode_go: "OpenCode Go",
    zai: "Z.ai",
  };
  if (names[providerId]) return names[providerId];
  return providerId
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function statusClass(status) {
  if (["configured", "reachable", "running"].includes(status)) return "ok";
  if (["missing_key", "missing_url", "unknown"].includes(status)) return "warn";
  if (["offline", "error"].includes(status)) return "error";
  return "neutral";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function load() {
  showMessage("Loading admin config");
  const config = await api("/admin/api/config");
  state.config = config;
  state.fields = new Map(config.fields.map((field) => [field.key, field]));
  renderNav();
  renderProviders(config.provider_status);
  renderSections(config.sections, config.fields);
  byId("configPath").textContent = config.paths.managed;
  await validate(false);
  await refreshLocalStatus();
  updateDirtyState();
  showMessage("");
  // Fire-and-forget: populate model suggestions without blocking page load.
  loadModelOptions();
}

async function loadModelOptions() {
  try {
    const result = await api("/admin/api/models/refresh", {
      method: "POST",
      body: "{}",
    });
    const cached = result.cached_models || {};
    const models = Object.entries(cached).flatMap(([providerId, ids]) =>
      ids.map((model) => `${providerId}/${model}`),
    );
    state.modelOptions = Array.from(
      new Set([...state.modelOptions, ...models]),
    ).sort();
    populateModelSelects();
  } catch (error) {
    // Non-fatal: suggestions stay empty until a provider is tested manually.
    console.warn("Model options refresh failed", error);
  }
}

function renderNav() {
  const nav = byId("sectionNav");
  nav.innerHTML = "";
  VIEW_GROUPS.forEach((view, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `nav-link${index === 0 ? " active" : ""}`;
    button.dataset.view = view.id;
    button.textContent = view.label;
    if (index === 0) {
      button.setAttribute("aria-current", "page");
    }
    button.addEventListener("click", () => {
      setActiveView(view.id, { scroll: true });
    });
    nav.appendChild(button);
  });
  setActiveView(state.activeView, { scroll: false });
}

function setActiveView(viewId, { scroll = false } = {}) {
  const activeView =
    VIEW_GROUPS.find((view) => view.id === viewId) || VIEW_GROUPS[0];
  state.activeView = activeView.id;
  byId("pageTitle").textContent = activeView.title;

  document.querySelectorAll(".nav-link").forEach((link) => {
    const selected = link.dataset.view === activeView.id;
    link.classList.toggle("active", selected);
    if (selected) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });

  document.querySelectorAll(".admin-view").forEach((view) => {
    const selected = view.dataset.view === activeView.id;
    view.classList.toggle("active", selected);
    view.hidden = !selected;
  });

  if (scroll) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function renderProviders(providerStatus) {
  const grid = byId("providerGrid");
  grid.innerHTML = "";
  providerStatus.forEach((provider) => {
    const card = document.createElement("article");
    card.className = "provider-card";
    card.dataset.provider = provider.provider_id;

    const title = document.createElement("div");
    title.className = "provider-title";
    title.innerHTML = `<strong>${providerName(provider.provider_id)}</strong>`;

    const pill = document.createElement("span");
    pill.className = `status-pill ${statusClass(provider.status)}`;
    pill.textContent = provider.label;
    title.appendChild(pill);

    const meta = document.createElement("div");
    meta.className = "provider-meta";
    meta.textContent =
      provider.kind === "local"
        ? provider.base_url || "No local URL configured"
        : provider.credential_env;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "test-button";
    button.textContent = provider.kind === "local" ? "Test" : "Refresh models";
    button.addEventListener("click", () => testProvider(provider.provider_id, button));

    card.append(title, meta, button);
    grid.appendChild(card);
  });
}

function updateProviderCard(providerId, status, label, metaText) {
  const card = document.querySelector(`[data-provider="${providerId}"]`);
  if (!card) return;
  const pill = card.querySelector(".status-pill");
  pill.className = `status-pill ${statusClass(status)}`;
  pill.textContent = label;
  if (metaText) {
    card.querySelector(".provider-meta").textContent = metaText;
  }
}

function renderSections(sections, fields) {
  VIEW_GROUPS.forEach((view) => {
    byId(view.containerId).innerHTML = "";
  });

  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const bySection = new Map();
  sections.forEach((section) => bySection.set(section.id, []));
  fields.forEach((field) => {
    if (!bySection.has(field.section)) bySection.set(field.section, []);
    bySection.get(field.section).push(field);
  });

  VIEW_GROUPS.forEach((view) => {
    const container = byId(view.containerId);
    view.sections.forEach((sectionId) => {
      const section = sectionById.get(sectionId);
      const sectionFields = bySection.get(sectionId) || [];
      if (!section || sectionFields.length === 0) return;

      const sectionEl = document.createElement("section");
      sectionEl.className = "settings-section";
      sectionEl.id = `section-${section.id}`;

      const heading = document.createElement("div");
      heading.className = "section-heading";
      heading.innerHTML = `<div><h3>${section.label}</h3><p>${section.description}</p></div>`;
      sectionEl.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "field-grid";
      sectionFields.forEach((field) => {
        grid.appendChild(renderField(field));
      });
      sectionEl.appendChild(grid);

      if (sectionFields.some((field) => field.advanced)) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "ghost-button advanced-toggle";
        toggle.textContent = "Show advanced";
        toggle.addEventListener("click", () => {
          const showing = sectionEl.classList.toggle("show-advanced");
          toggle.textContent = showing ? "Hide advanced" : "Show advanced";
        });
        sectionEl.appendChild(toggle);
      }

      container.appendChild(sectionEl);
    });
  });
}

function renderField(field) {
  const wrapper = document.createElement("div");
  wrapper.className = `field${field.advanced ? " advanced-field" : ""}`;
  wrapper.dataset.key = field.key;

  const label = document.createElement("label");
  label.htmlFor = `field-${field.key}`;
  const labelText = document.createElement("span");
  labelText.textContent = field.label;
  label.appendChild(labelText);

  const source = sourceText(field);
  if (source) {
    const sourceEl = document.createElement("span");
    sourceEl.className = "field-source";
    sourceEl.textContent = source;
    label.appendChild(sourceEl);
  }

  const element = inputForField(field);
  // Composite widgets (e.g. the model picker) carry their value in an
  // inner hidden input; plain fields ARE the input.
  const input = element.dataset.modelPicker === "true"
    ? element.querySelector("input[data-model-picker-value]")
    : element;
  input.id = `field-${field.key}`;
  input.dataset.key = field.key;
  // Original must mirror what readFieldValue() will report: checkboxes map to
  // "true"/"false" (their .value is the meaningless default "on"), the model
  // picker's hidden input holds a normalized sorted list, and plain fields
  // report their raw value.
  input.dataset.original =
    input.type === "checkbox"
      ? input.checked
        ? "true"
        : "false"
      : input.dataset.modelPickerValue === "true"
        ? input.value
        : field.value || "";
  input.dataset.secret = field.secret ? "true" : "false";
  input.dataset.configured = field.configured ? "true" : "false";
  // No input.disabled = field.locked: every field is always editable. The
  // precedence swap in config/settings.py (_env_files) makes the managed env
  // win over FCC_ENV_FILE, so admin edits STICK over the bootstrap .env
  // instead of being silently overwritten on reload (which is what "locked"
  // was papering over).
  input.addEventListener("input", updateDirtyState);
  input.addEventListener("change", updateDirtyState);

  wrapper.append(label, element);
  if (field.description) {
    const description = document.createElement("div");
    description.className = "field-description";
    description.textContent = field.description;
    wrapper.appendChild(description);
  }
  return wrapper;
}

function inputForField(field) {
  if (field.type === "boolean") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = String(field.value).toLowerCase() === "true";
    input.dataset.original = input.checked ? "true" : "false";
    return input;
  }

  if (field.type === "tri_boolean") {
    const select = document.createElement("select");
    [
      ["", "Inherit"],
      ["true", "Enabled"],
      ["false", "Disabled"],
    ].forEach(([value, label]) => select.appendChild(option(value, label)));
    select.value = field.value || "";
    return select;
  }

  if (field.type === "select") {
    const select = document.createElement("select");
    field.options.forEach((value) => select.appendChild(option(value, value)));
    select.value = field.value || field.options[0] || "";
    return select;
  }

  if (field.key === "MODEL_DELEGATE_ALLOWLIST" || field.key === "MODEL_DELEGATE_APPROVAL") {
    // Multi-model picker with search, vendor grouping, and chips. Stored as a
    // comma-separated list in a hidden input; custom fnmatch globs typed via
    // the env file round-trip as checked entries.
    return buildModelMultiPicker(field.value || "");
  }

  if (field.key.startsWith("MODEL")) {
    const select = document.createElement("select");
    select.dataset.modelSelect = "true";
    fillModelSelect(select, field.value || "");
    return select;
  }

  if (field.type === "textarea") {
    const textarea = document.createElement("textarea");
    textarea.value = field.value || "";
    return textarea;
  }

  const input = document.createElement("input");
  input.type = field.type === "number" ? "number" : "text";
  if (field.type === "secret") {
    input.type = "password";
    input.placeholder = field.configured
      ? "Configured - enter a new value to replace"
      : "Not configured";
    input.value = "";
    input.autocomplete = "off";
  } else {
    input.value = field.value || "";
  }
  return input;
}

function option(value, label) {
  const optionEl = document.createElement("option");
  optionEl.value = value;
  optionEl.textContent = label;
  return optionEl;
}

function readFieldValue(input) {
  if (input.type === "checkbox") return input.checked ? "true" : "false";
  if (input.dataset.secret === "true" && input.dataset.configured === "true") {
    return input.value ? input.value : MASKED_SECRET;
  }
  return input.value;
}

function changedValues() {
  const values = {};
  document.querySelectorAll("[data-key]").forEach((input) => {
    if (input.disabled || !input.matches("input, select, textarea")) return;
    const value = readFieldValue(input);
    if (value !== input.dataset.original) {
      values[input.dataset.key] = value;
    }
  });
  return values;
}

function updateDirtyState() {
  const count = Object.keys(changedValues()).length;
  byId("dirtyState").textContent =
    count === 0 ? "No changes" : `${count} unsaved change${count === 1 ? "" : "s"}`;
  byId("applyButton").disabled = count === 0;
}

async function validate(showResult = true) {
  const result = await api("/admin/api/config/validate", {
    method: "POST",
    body: JSON.stringify({ values: changedValues() }),
  });
  if (showResult) {
    showValidationResult(result);
  }
  return result;
}

function showValidationResult(result) {
  if (result.valid) {
    showMessage("Config shape is valid", "ok");
  } else {
    showMessage(result.errors.join("; "), "error");
  }
}

async function apply() {
  const result = await api("/admin/api/config/apply", {
    method: "POST",
    body: JSON.stringify({ values: changedValues() }),
  });
  if (!result.applied) {
    showValidationResult(result);
    return;
  }
  const restart = result.restart || {};
  if (restart.required && restart.automatic) {
    showMessage("Applied. Restarting server...", "ok");
    byId("applyButton").disabled = true;
    setTimeout(() => {
      window.location.href = restart.admin_url || "/admin";
    }, 1600);
    return;
  }
  const pending = restart.required ? restart.fields || [] : result.pending_fields || [];
  await load();
  showMessage(
    pending.length
      ? `Applied. Restart fcc-server to use: ${pending.join(", ")}`
      : "Applied",
    "ok",
  );
}

async function refreshLocalStatus() {
  const result = await api("/admin/api/providers/local-status");
  result.providers.forEach((provider) => {
    state.localStatus.set(provider.provider_id, provider);
    const meta = provider.status_code
      ? `${provider.base_url} returned HTTP ${provider.status_code}`
      : provider.base_url;
    updateProviderCard(provider.provider_id, provider.status, provider.label, meta);
  });
}

async function testProvider(providerId, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Testing";
  try {
    const result = await api(`/admin/api/providers/${providerId}/test`, {
      method: "POST",
      body: "{}",
    });
    if (result.ok) {
      updateProviderCard(
        providerId,
        "reachable",
        `${result.models.length} models`,
        result.models.slice(0, 3).join(", ") || "No models returned",
      );
      state.modelOptions = Array.from(
        new Set([
          ...state.modelOptions,
          ...result.models.map((model) => `${providerId}/${model}`),
        ]),
      ).sort();
      populateModelSelects();
    } else {
      updateProviderCard(providerId, "offline", result.error_type, result.error_type);
    }
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function fillModelSelect(select, currentValue) {
  const previous = currentValue != null ? currentValue : select.value;
  select.innerHTML = "";
  select.appendChild(option("", "— use default —"));
  const values = [...state.modelOptions];
  if (previous && !values.includes(previous)) {
    values.unshift(previous);
  }
  values.forEach((model) => select.appendChild(option(model, model)));
  select.value = previous || "";
}

function buildModelMultiPicker(currentValue) {
  // Simple multi-select: one search box + one flat scrollable checkbox list.
  // Selected models sort to the top so the current value is always visible
  // without scrolling; custom fnmatch globs typed via the env file round-trip
  // as selected rows.
  const container = document.createElement("div");
  container.className = "model-picker";
  container.dataset.modelPicker = "true";

  // Selection lives in a Set; the hidden input holds the serialized value the
  // apply flow reads via readFieldValue/changedValues.
  const selected = new Set(
    (currentValue || "")
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean),
  );

  const hidden = document.createElement("input");
  hidden.type = "hidden";
  hidden.dataset.modelPickerValue = "true";
  hidden.value = Array.from(selected).sort().join(",");

  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = "Filter models (e.g. qwen, deepseek)…";
  search.className = "model-picker-search";

  const toolbar = document.createElement("div");
  toolbar.className = "model-picker-toolbar";
  const counter = document.createElement("span");
  counter.className = "model-picker-count";
  const selectShown = document.createElement("button");
  selectShown.type = "button";
  selectShown.textContent = "Select shown";
  const clearAll = document.createElement("button");
  clearAll.type = "button";
  clearAll.textContent = "Clear all";
  toolbar.append(counter, selectShown, clearAll);

  const list = document.createElement("div");
  list.className = "model-picker-list";

  function sync() {
    hidden.value = Array.from(selected).sort().join(",");
    counter.textContent = `${selected.size} selected`;
  }

  function visibleRefs() {
    const query = search.value.trim().toLowerCase();
    const values = Array.from(new Set([...selected, ...state.modelOptions]));
    const filtered = query
      ? values.filter((ref) => ref.toLowerCase().includes(query))
      : values;
    // Selected first (each block alphabetical) so the current value reads at
    // a glance without hunting through the list.
    return filtered.sort((a, b) => {
      const diff = Number(selected.has(b)) - Number(selected.has(a));
      return diff !== 0 ? diff : a.localeCompare(b);
    });
  }

  function render() {
    list.replaceChildren();
    visibleRefs().forEach((ref) => {
      const row = document.createElement("label");
      row.className = "model-picker-row";
      if (selected.has(ref)) row.classList.add("is-selected");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(ref);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(ref);
        else selected.delete(ref);
        row.classList.toggle("is-selected", checkbox.checked);
        sync();
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
      });
      const text = document.createElement("span");
      text.className = "model-picker-ref";
      text.textContent = ref;
      row.append(checkbox, text);
      list.appendChild(row);
    });
  }

  search.addEventListener("input", render);
  selectShown.addEventListener("click", () => {
    visibleRefs().forEach((ref) => selected.add(ref));
    sync();
    render();
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
  });
  clearAll.addEventListener("click", () => {
    selected.clear();
    sync();
    render();
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
  });

  container.append(hidden, search, toolbar, list);
  container.dataset.render = "true";
  container.refreshOptions = render;
  sync();
  render();
  return container;
}

function populateModelSelects() {
  document
    .querySelectorAll('select[data-model-select="true"]')
    .forEach((select) => fillModelSelect(select, select.value));
  document
    .querySelectorAll('[data-model-picker="true"]')
    .forEach((picker) => picker.refreshOptions && picker.refreshOptions());
}

function showMessage(message, kind = "") {
  const area = byId("messageArea");
  area.textContent = message;
  area.className = `message-area ${kind}`.trim();
}

byId("validateButton").addEventListener("click", () => validate(true));
byId("applyButton").addEventListener("click", apply);

load().catch((error) => {
  showMessage(error.message, "error");
});
