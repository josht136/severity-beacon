/* Severity Beacon Ingress UI 0.8 — multi-light, mute, audio mute, lights mute */

const DEFAULT_BASE = [255, 255, 255];
const DEFAULT_RECOVERY = [0, 220, 160];
const LEGACY_NONE_GREEN = [0, 255, 0];

const TIERS = [
  { key: "unclassified", label: "0 — Not classified", sev: 0, defaultMode: "pattern", defaultPrimary: [255, 220, 0], defaultAlt: [0, 255, 0], defaultWindow: 3, defaultTransition: "snap" },
  { key: "info", label: "1 — Information", sev: 1, defaultMode: "pattern", defaultPrimary: [0, 120, 255], defaultAlt: [0, 255, 0], defaultWindow: 6, defaultTransition: "fade", infoTimings: true },
  { key: "warning", label: "2 — Warning", sev: 2, defaultMode: "steady", defaultPrimary: [255, 140, 0], defaultAlt: [255, 200, 0], defaultWindow: 0 },
  { key: "average", label: "3 — Average", sev: 3, defaultMode: "steady", defaultPrimary: [255, 80, 0], defaultAlt: [255, 140, 0], defaultWindow: 0 },
  { key: "high", label: "4 — High", sev: 4, defaultMode: "steady", defaultPrimary: [255, 0, 0], defaultAlt: [255, 80, 0], defaultWindow: 0 },
  { key: "disaster", label: "5 — Disaster", sev: 5, defaultMode: "pattern", defaultPrimary: [255, 0, 0], defaultAlt: [255, 140, 0], defaultWindow: 0, defaultTransition: "fade" },
];

const MODES = [
  { value: "steady", label: "Steady" },
  { value: "pattern", label: "Pattern" },
];
const AUDIO_MODES = [
  { value: "none", label: "None" },
  { value: "media", label: "Media (once)" },
  { value: "repeat_media", label: "Media (repeat)" },
  { value: "tts", label: "TTS" },
];
const LEGACY_MODE_MAP = { flash: "pattern", fade: "pattern", strobe: "pattern" };

const CONN_FIELDS = [
  "zabbix_url",
  "poll_interval_seconds",
  "host_group_id",
  "severities",
  "brightness",
  "mute_timeout_minutes",
  "audio_media_player",
  "audio_tts_entity",
  "audio_volume",
  "quiet_hours_start",
  "quiet_hours_end",
  "notify_service",
  "notify_on_severities",
];

const state = {
  options: {},
  patterns: {},
  windows: {},
  targets: {},
  lights: [],
  muteEntities: [],
  audioEntities: [],
  lightsMuteEntities: [],
  haEntities: { light: [], mute: [], media: [], tts: [] },
};

function setStatus(text, cls) {
  const el = document.getElementById("status");
  el.textContent = text || "";
  el.className = "status" + (cls ? " " + cls : "");
}

function rgbToHex(r, g, b) {
  const h = (n) => Number(n).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

function parseRgbString(value, fallback = [0, 0, 0]) {
  try {
    const parts = String(value || "").split(",").map((p) => parseInt(p.trim(), 10));
    if (parts.length !== 3 || parts.some((n) => Number.isNaN(n) || n < 0 || n > 255)) return fallback.slice();
    return parts;
  } catch {
    return fallback.slice();
  }
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function normalizeMode(value, fallback) {
  const mode = String(value || "").trim().toLowerCase();
  if (mode === "steady" || mode === "pattern") return mode;
  if (LEGACY_MODE_MAP[mode]) return LEGACY_MODE_MAP[mode];
  return fallback;
}

function parseEntityList(value) {
  if (Array.isArray(value)) return value.map(String).map((s) => s.trim()).filter(Boolean);
  const text = String(value || "").trim();
  if (!text) return [];
  if (text.startsWith("[")) {
    try {
      const data = JSON.parse(text);
      if (Array.isArray(data)) return data.map(String).map((s) => s.trim()).filter(Boolean);
    } catch { /* fall through */ }
  }
  return text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}

function windowOptKey(tierKey) { return `window_${tierKey}`; }
function patternKey(tierKey) { return `pattern_${tierKey}`; }
function targetsKey(tierKey) { return `targets_${tierKey}`; }

function resolveBaseRgb(opts) {
  if (opts.color_base) return parseRgbString(opts.color_base, DEFAULT_BASE);
  if (opts.color_none) {
    const none = parseRgbString(opts.color_none, DEFAULT_BASE);
    const isLegacyGreen = none[0] === LEGACY_NONE_GREEN[0] && none[1] === LEGACY_NONE_GREEN[1] && none[2] === LEGACY_NONE_GREEN[2];
    if (!isLegacyGreen) return none;
  }
  return DEFAULT_BASE.slice();
}

function resolveRecoveryRgb(opts) {
  return parseRgbString(opts.color_recovery, DEFAULT_RECOVERY);
}

function syncRecoveryTimingVisibility() {
  const transition = document.getElementById("recovery_transition")?.value || "fade";
  const fadeWrap = document.getElementById("recovery-fade-wrap");
  if (fadeWrap) fadeWrap.hidden = transition !== "fade";
}

function bindBaseRecoveryColors() {
  for (const [pickerId, textId] of [
    ["color_base_picker", "color_base"],
    ["color_recovery_picker", "color_recovery"],
  ]) {
    const colorInput = document.getElementById(pickerId);
    const textInput = document.getElementById(textId);
    if (!colorInput || !textInput) continue;
    colorInput.addEventListener("input", () => {
      const rgb = hexToRgb(colorInput.value);
      if (rgb) textInput.value = rgb.join(",");
    });
    textInput.addEventListener("change", () => {
      const rgb = parseRgbString(textInput.value);
      textInput.value = rgb.join(",");
      colorInput.value = rgbToHex(...rgb);
    });
  }
  document.getElementById("recovery_transition")?.addEventListener("change", syncRecoveryTimingVisibility);
}

function resolveWindow(tier, opts) {
  const key = windowOptKey(tier.key);
  if (opts[key] !== undefined && opts[key] !== null && opts[key] !== "") {
    const n = Number(opts[key]);
    if (!Number.isNaN(n) && n >= 0) return n;
  }
  return tier.defaultWindow;
}

function parsePattern(raw, tier, opts) {
  if (!raw || !String(raw).trim()) return defaultPatternForTier(tier, opts);
  try {
    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
    let stepsIn = data;
    let defaultTransition = tier.defaultTransition || "snap";
    let defaultFade = 1;
    if (data && typeof data === "object" && !Array.isArray(data)) {
      stepsIn = data.steps || [];
      if (data.transition === "fade" || data.transition === "snap") defaultTransition = data.transition;
      if (data.fade_s != null) defaultFade = Math.max(0.05, Number(data.fade_s) || 1);
    }
    if (!Array.isArray(stepsIn) || !stepsIn.length) return defaultPatternForTier(tier, opts);
    return stepsIn.map((step) => {
      const r = Number(step.r ?? step.rgb?.[0] ?? 0);
      const g = Number(step.g ?? step.rgb?.[1] ?? 0);
      const b = Number(step.b ?? step.rgb?.[2] ?? 0);
      const duration_s = Math.max(0, Number(step.duration_s ?? step.duration ?? 0.25));
      let transition = String(step.transition || defaultTransition).toLowerCase();
      if (transition !== "fade" && transition !== "snap") transition = defaultTransition;
      const fade_s = Math.max(0.05, Number(step.fade_s ?? step.fade_seconds ?? defaultFade) || defaultFade);
      return {
        r: Math.min(255, Math.max(0, Math.round(r))),
        g: Math.min(255, Math.max(0, Math.round(g))),
        b: Math.min(255, Math.max(0, Math.round(b))),
        duration_s,
        transition,
        fade_s,
        targets: parseEntityList(step.targets),
      };
    });
  } catch {
    return defaultPatternForTier(tier, opts);
  }
}

function defaultPatternForTier(tier, opts) {
  const primary = parseRgbString(opts[`color_${tier.key}`], tier.defaultPrimary);
  const alt = parseRgbString(opts[`color_${tier.key}_alt`], tier.defaultAlt);
  const hold = Number(opts.info_hold_seconds) || 3;
  const fade = Number(opts.info_fade_seconds) || 3;
  if (tier.key === "info") {
    return [
      { r: primary[0], g: primary[1], b: primary[2], duration_s: hold, transition: "snap", fade_s: 0.5, targets: [] },
      { r: alt[0], g: alt[1], b: alt[2], duration_s: 0.1, transition: "fade", fade_s: fade, targets: [] },
    ];
  }
  return [
    { r: primary[0], g: primary[1], b: primary[2], duration_s: 0.25, transition: tier.defaultTransition || "snap", fade_s: 0.5, targets: [] },
    { r: alt[0], g: alt[1], b: alt[2], duration_s: 0.25, transition: tier.defaultTransition || "snap", fade_s: 0.5, targets: [] },
  ];
}

function serializePattern(steps) {
  return JSON.stringify(
    steps.map((s) => ({
      r: s.r, g: s.g, b: s.b,
      duration_s: s.duration_s,
      transition: s.transition || "snap",
      fade_s: s.fade_s ?? 0.5,
      targets: s.targets || [],
    }))
  );
}

function modeHint(mode) {
  if (mode === "steady") return "Solid colour on selected lights while this severity is max. Black (0,0,0) turns them off.";
  return "Cycle colour steps. Per-step lights optional. Window 0 = loop until cleared / higher wins.";
}

function fillDatalist(id, entities) {
  const dl = document.getElementById(id);
  if (!dl) return;
  dl.innerHTML = entities.map((e) => `<option value="${e.entity_id}">${e.name || e.entity_id}</option>`).join("");
}

function renderPillBox(containerId, selected, candidates, onChange) {
  const box = document.getElementById(containerId);
  if (!box) return;
  box.innerHTML = "";
  const row = document.createElement("div");
  row.className = "pills";
  selected.forEach((eid) => {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.innerHTML = `<span>${eid}</span><button type="button" aria-label="Remove">×</button>`;
    pill.querySelector("button").addEventListener("click", () => {
      onChange(selected.filter((x) => x !== eid));
    });
    row.appendChild(pill);
  });
  box.appendChild(row);
  const addRow = document.createElement("div");
  addRow.className = "pill-add";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Type entity_id and Enter";
  input.setAttribute("list", box.dataset.list || "");
  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const v = input.value.trim();
    if (!v || selected.includes(v)) return;
    onChange([...selected, v]);
    input.value = "";
  });
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "btn";
  addBtn.textContent = "Add";
  addBtn.addEventListener("click", () => {
    const v = input.value.trim();
    if (!v || selected.includes(v)) return;
    onChange([...selected, v]);
    input.value = "";
  });
  addRow.appendChild(input);
  addRow.appendChild(addBtn);
  if (candidates?.length) {
    const sel = document.createElement("select");
    sel.innerHTML = `<option value="">Pick from HA…</option>` +
      candidates.map((c) => `<option value="${c.entity_id}">${c.name || c.entity_id}</option>`).join("");
    sel.addEventListener("change", () => {
      const v = sel.value;
      if (!v || selected.includes(v)) return;
      onChange([...selected, v]);
      sel.value = "";
    });
    addRow.appendChild(sel);
  }
  box.appendChild(addRow);
}

function bindColorPair(row) {
  const colorInput = row.querySelector('input[type="color"]');
  const textInput = row.querySelector('input[type="text"]');
  colorInput.addEventListener("input", () => {
    const rgb = hexToRgb(colorInput.value);
    if (rgb) textInput.value = rgb.join(",");
  });
  textInput.addEventListener("change", () => {
    const rgb = parseRgbString(textInput.value);
    textInput.value = rgb.join(",");
    colorInput.value = rgbToHex(...rgb);
  });
}

function syncTierVisibility(panel) {
  const mode = panel.querySelector(".mode-select")?.value || "steady";
  const steadyBlock = panel.querySelector(".steady-wrap");
  const patternBlock = panel.querySelector(".pattern-wrap");
  const hint = panel.querySelector(".mode-hint");
  if (hint) hint.textContent = modeHint(mode);
  if (steadyBlock) steadyBlock.hidden = mode !== "steady";
  if (patternBlock) patternBlock.hidden = mode !== "pattern";
  const audioMode = panel.querySelector(".audio-mode")?.value || "none";
  panel.querySelectorAll("[data-audio-show]").forEach((el) => {
    const need = el.dataset.audioShow;
    el.hidden = !(need === audioMode || (need === "media" && audioMode === "repeat_media"));
  });
}

function syncStepTransitionUi(row) {
  const transition = row.querySelector(".step-transition")?.value || "snap";
  const fadeWrap = row.querySelector(".fade-wrap");
  if (fadeWrap) fadeWrap.hidden = transition !== "fade";
}

function renderMiniTargets(container, selected, onChange) {
  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "mini-targets";
  selected.forEach((eid) => {
    const pill = document.createElement("span");
    pill.className = "pill tiny";
    pill.innerHTML = `<span>${eid.split(".").pop()}</span><button type="button">×</button>`;
    pill.title = eid;
    pill.querySelector("button").addEventListener("click", () => onChange(selected.filter((x) => x !== eid)));
    wrap.appendChild(pill);
  });
  const add = document.createElement("select");
  add.innerHTML = `<option value="">+ light</option>` +
    state.lights.map((e) => `<option value="${e}">${e}</option>`).join("");
  add.addEventListener("change", () => {
    if (!add.value || selected.includes(add.value)) return;
    onChange([...selected, add.value]);
    add.value = "";
  });
  wrap.appendChild(add);
  container.appendChild(wrap);
}

function renderPattern(tierKey) {
  const root = document.getElementById(`pattern_${tierKey}`);
  if (!root) return;
  const key = patternKey(tierKey);
  const steps = state.patterns[key] || [];
  root.innerHTML = "";

  steps.forEach((step, idx) => {
    const hex = rgbToHex(step.r, step.g, step.b);
    const row = document.createElement("div");
    row.className = "step";
    row.innerHTML = `
      <div class="step-main">
        <input type="color" value="${hex}" aria-label="Step ${idx + 1} color" />
        <input type="text" class="rgb" value="${step.r},${step.g},${step.b}" spellcheck="false" />
        <label class="mini">Hold (s)
          <input class="dur" type="number" min="0" max="60" step="0.05" value="${step.duration_s}" />
        </label>
        <label class="mini">Transition
          <select class="step-transition">
            <option value="snap" ${step.transition === "snap" ? "selected" : ""}>Snap</option>
            <option value="fade" ${step.transition === "fade" ? "selected" : ""}>Fade</option>
          </select>
        </label>
        <label class="mini fade-wrap">Fade (s)
          <input class="fade" type="number" min="0.05" max="60" step="0.05" value="${step.fade_s ?? 0.5}" />
        </label>
      </div>
      <div class="step-targets" data-step-targets></div>
      <div class="step-actions">
        <button type="button" class="btn icon" data-act="up" title="Move up">↑</button>
        <button type="button" class="btn icon" data-act="down" title="Move down">↓</button>
        <button type="button" class="btn icon danger" data-act="del" title="Remove" ${steps.length <= 1 ? "disabled" : ""}>✕</button>
      </div>
    `;
    const colorInput = row.querySelector('input[type="color"]');
    const textInput = row.querySelector("input.rgb");
    const durInput = row.querySelector(".dur");
    const fadeInput = row.querySelector(".fade");
    const transitionSelect = row.querySelector(".step-transition");
    colorInput.addEventListener("input", () => {
      const rgb = hexToRgb(colorInput.value);
      if (!rgb) return;
      textInput.value = rgb.join(",");
      Object.assign(state.patterns[key][idx], { r: rgb[0], g: rgb[1], b: rgb[2] });
      updatePreview(tierKey);
    });
    textInput.addEventListener("change", () => {
      const rgb = parseRgbString(textInput.value);
      textInput.value = rgb.join(",");
      colorInput.value = rgbToHex(...rgb);
      Object.assign(state.patterns[key][idx], { r: rgb[0], g: rgb[1], b: rgb[2] });
      updatePreview(tierKey);
    });
    durInput.addEventListener("change", () => {
      state.patterns[key][idx].duration_s = Math.max(0, Number(durInput.value) || 0);
      updatePreview(tierKey);
    });
    fadeInput.addEventListener("change", () => {
      state.patterns[key][idx].fade_s = Math.max(0.05, Number(fadeInput.value) || 0.5);
      updatePreview(tierKey);
    });
    transitionSelect.addEventListener("change", () => {
      state.patterns[key][idx].transition = transitionSelect.value;
      syncStepTransitionUi(row);
      updatePreview(tierKey);
    });
    row.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const act = btn.dataset.act;
        const arr = state.patterns[key];
        if (act === "del" && arr.length > 1) arr.splice(idx, 1);
        if (act === "up" && idx > 0) [arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
        if (act === "down" && idx < arr.length - 1) [arr[idx + 1], arr[idx]] = [arr[idx], arr[idx + 1]];
        renderPattern(tierKey);
      });
    });
    renderMiniTargets(row.querySelector("[data-step-targets]"), step.targets || [], (next) => {
      state.patterns[key][idx].targets = next;
      renderPattern(tierKey);
    });
    syncStepTransitionUi(row);
    root.appendChild(row);
  });

  const actions = document.createElement("div");
  actions.className = "pattern-actions";
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "btn";
  addBtn.textContent = "Add step";
  addBtn.addEventListener("click", () => {
    state.patterns[key].push({ r: 255, g: 255, b: 255, duration_s: 0.5, transition: "snap", fade_s: 0.5, targets: [] });
    renderPattern(tierKey);
  });
  actions.appendChild(addBtn);
  root.appendChild(actions);

  const preview = document.createElement("div");
  preview.className = "preview";
  preview.id = `preview_${tierKey}`;
  root.appendChild(preview);
  updatePreview(tierKey);
}

function updatePreview(tierKey) {
  const el = document.getElementById(`preview_${tierKey}`);
  if (!el) return;
  const steps = state.patterns[patternKey(tierKey)] || [];
  const weights = steps.map((x) => Math.max(0.05, x.duration_s || 0.05) + (x.transition === "fade" ? Math.max(0.05, x.fade_s || 0.5) : 0));
  const total = weights.reduce((s, x) => s + x, 0) || 1;
  el.innerHTML = "";
  steps.forEach((step, i) => {
    const span = document.createElement("span");
    span.style.background = rgbToHex(step.r, step.g, step.b);
    span.style.flex = String(Math.max(0.05, weights[i] / total));
    el.appendChild(span);
  });
}

function buildSeverityPanels() {
  const root = document.getElementById("severity-panels");
  root.innerHTML = "";
  for (const tier of TIERS) {
    const mode = normalizeMode(state.options[`mode_${tier.key}`], tier.defaultMode);
    const primary = parseRgbString(state.options[`color_${tier.key}`], tier.defaultPrimary);
    const windowVal = state.windows[tier.key] ?? resolveWindow(tier, state.options);
    const targets = state.targets[tier.key] || [];
    const audioMode = state.options[`audio_mode_${tier.key}`] || "none";
    const readBodyRaw = state.options[`audio_tts_read_body_${tier.key}`];
    const readBodyOn =
      readBodyRaw === undefined || readBodyRaw === null || readBodyRaw === ""
        ? tier.key !== "none"
        : !(readBodyRaw === false || readBodyRaw === "false" || readBodyRaw === 0 || readBodyRaw === "0");
    const panel = document.createElement("section");
    panel.className = "panel sev-panel";
    panel.dataset.key = tier.key;
    const modeOptions = MODES.map((m) => `<option value="${m.value}" ${m.value === mode ? "selected" : ""}>${m.label}</option>`).join("");
    const audioOptions = AUDIO_MODES.map((m) => `<option value="${m.value}" ${m.value === audioMode ? "selected" : ""}>${m.label}</option>`).join("");
    const testPath = String(tier.sev);
    panel.innerHTML = `
      <div class="panel-head">
        <h2>${tier.label}</h2>
        <div class="head-actions">
          <label class="mode-label">Mode
            <select class="mode-select">${modeOptions}</select>
          </label>
          <button type="button" class="btn test-btn" data-test="${testPath}">Test</button>
        </div>
      </div>
      <p class="hint mode-hint"></p>
      <label class="block-label">Lights for this severity</label>
      <div class="tier-targets" data-tier-targets></div>
      <div class="steady-wrap">
        <div class="color-row" data-role="primary">
          <div class="name">Colour</div>
          <input type="color" value="${rgbToHex(...primary)}" />
          <input type="text" value="${primary.join(",")}" spellcheck="false" />
        </div>
      </div>
      <div class="pattern-wrap">
        <label class="inline window-field">Window (s)
          <input type="number" data-window="${tier.key}" min="0" max="3600" step="0.1" value="${windowVal}" />
        </label>
        <p class="hint tight">0 = loop while this severity stays max.</p>
        <h3 class="pattern-title">Colour steps</h3>
        <div class="pattern" id="pattern_${tier.key}"></div>
      </div>
      <div class="audio-block">
        <h3 class="pattern-title">Audio</h3>
        <label class="inline">Mode
          <select class="audio-mode" data-audio-mode="${tier.key}">${audioOptions}</select>
        </label>
        <label class="inline" data-audio-show="media">Media content id / URL
          <input type="text" data-audio-media value="${state.options[`audio_media_${tier.key}`] || ""}" />
        </label>
        <label class="inline" data-audio-show="tts">TTS message
          <input type="text" data-audio-tts value="${state.options[`audio_tts_${tier.key}`] || ""}" placeholder="Message (prefix)" />
        </label>
        <label class="check" data-audio-show="tts">
          <input type="checkbox" data-audio-tts-body ${readBodyOn ? "checked" : ""} />
          Read Zabbix alert body
        </label>
        <p class="hint tight" data-audio-show="tts">Speaks: Message - Host - Problem; related alerts summarize as Host … along with N others (host drops trailing 6 hex MAC chars only).</p>
        <label class="inline" data-audio-show="repeat_media">Repeat every (s)
          <input type="number" data-audio-repeat min="0" step="1" value="${state.options[`audio_repeat_${tier.key}`] || 0}" />
        </label>
      </div>
    `;
    panel.querySelector(".mode-select").addEventListener("change", () => syncTierVisibility(panel));
    panel.querySelector(".audio-mode").addEventListener("change", () => syncTierVisibility(panel));
    panel.querySelectorAll(".color-row").forEach((row) => bindColorPair(row));
    panel.querySelector("[data-window]")?.addEventListener("change", (e) => {
      state.windows[tier.key] = Math.max(0, Number(e.target.value) || 0);
    });
    panel.querySelector(".test-btn")?.addEventListener("click", () => {
      testSeverity(testPath).catch((err) => setStatus(String(err.message || err), "err"));
    });
    const tgtHost = panel.querySelector("[data-tier-targets]");
    const bindTargets = () => {
      renderMiniTargets(tgtHost, state.targets[tier.key] || [], (next) => {
        state.targets[tier.key] = next;
        bindTargets();
      });
    };
    bindTargets();
    root.appendChild(panel);
    syncTierVisibility(panel);
    renderPattern(tier.key);
  }
}

async function testSeverity(sevPath) {
  setStatus(`Saving & testing ${sevPath}…`, "busy");
  await saveOptions();
  const res = await fetch(`api/test/${sevPath}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seconds: 8 }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `Test failed (${res.status})`);
  setStatus(data.message || `Testing ${sevPath}…`, "ok");
}

function collectFormOptions() {
  const out = { ...state.options };
  for (const id of CONN_FIELDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === "number") out[id] = Number(el.value);
    else out[id] = el.value;
  }
  out.escalate_only = !!document.getElementById("escalate_only")?.checked;
  out.audio_enabled = !!document.getElementById("audio_enabled")?.checked;
  out.audio_recovery_chime = !!document.getElementById("audio_recovery_chime")?.checked;

  out.lights = state.lights.join(",");
  out.light_entity_id = state.lights[0] || "";
  out.mute_entities = state.muteEntities.join(",");
  out.audio_toggle_entities = state.audioEntities.join(",");
  out.lights_toggle_entities = state.lightsMuteEntities.join(",");

  const baseRgb = parseRgbString(document.getElementById("color_base")?.value, DEFAULT_BASE);
  const recoveryRgb = parseRgbString(document.getElementById("color_recovery")?.value, DEFAULT_RECOVERY);
  out.color_base = baseRgb.join(",");
  out.color_none = out.color_base;
  out.color_recovery = recoveryRgb.join(",");
  out.recovery_hold_seconds = Math.max(0, Number(document.getElementById("recovery_hold_seconds")?.value) || 0);
  out.recovery_fade_seconds = Math.max(0, Number(document.getElementById("recovery_fade_seconds")?.value) || 0);
  out.recovery_transition = document.getElementById("recovery_transition")?.value || "fade";
  out.mode_none = "steady";
  out.window_none = 0;
  out.targets_none = out.lights;

  document.querySelectorAll(".sev-panel").forEach((panel) => {
    const key = panel.dataset.key;
    out[`mode_${key}`] = panel.querySelector(".mode-select")?.value || "steady";
    const primaryRow = panel.querySelector('.color-row[data-role="primary"] input[type="text"]');
    if (primaryRow) out[`color_${key}`] = parseRgbString(primaryRow.value).join(",");
    if (out[`color_${key}_alt`] == null) {
      const tier = TIERS.find((t) => t.key === key);
      out[`color_${key}_alt`] = (tier?.defaultAlt || [0, 0, 0]).join(",");
    }
    const winInput = panel.querySelector("[data-window]");
    if (winInput) {
      const w = Math.max(0, Number(winInput.value) || 0);
      out[windowOptKey(key)] = w;
      state.windows[key] = w;
    }
    out[patternKey(key)] = serializePattern(state.patterns[patternKey(key)] || []);
    out[targetsKey(key)] = (state.targets[key] || []).join(",");
    out[`audio_mode_${key}`] = panel.querySelector(".audio-mode")?.value || "none";
    out[`audio_media_${key}`] = panel.querySelector("[data-audio-media]")?.value || "";
    out[`audio_tts_${key}`] = panel.querySelector("[data-audio-tts]")?.value || "";
    out[`audio_tts_read_body_${key}`] = !!panel.querySelector("[data-audio-tts-body]")?.checked;
    out[`audio_repeat_${key}`] = Number(panel.querySelector("[data-audio-repeat]")?.value) || 0;
  });

  const token = (document.getElementById("zabbix_api_token")?.value || "").trim();
  if (token) out.zabbix_api_token = token;
  else delete out.zabbix_api_token;
  return out;
}

function fillBaseRecovery(opts) {
  const base = resolveBaseRgb(opts);
  const recovery = resolveRecoveryRgb(opts);
  const baseText = document.getElementById("color_base");
  const basePicker = document.getElementById("color_base_picker");
  const recText = document.getElementById("color_recovery");
  const recPicker = document.getElementById("color_recovery_picker");
  if (baseText) baseText.value = base.join(",");
  if (basePicker) basePicker.value = rgbToHex(...base);
  if (recText) recText.value = recovery.join(",");
  if (recPicker) recPicker.value = rgbToHex(...recovery);
  const hold = document.getElementById("recovery_hold_seconds");
  const fade = document.getElementById("recovery_fade_seconds");
  const transition = document.getElementById("recovery_transition");
  if (hold) hold.value = String(opts.recovery_hold_seconds ?? 3);
  if (fade) fade.value = String(opts.recovery_fade_seconds ?? 1);
  if (transition) transition.value = String(opts.recovery_transition || "fade").toLowerCase() === "snap" ? "snap" : "fade";
  syncRecoveryTimingVisibility();
}

function refreshPillBoxes() {
  document.getElementById("lights-pills").dataset.list = "dl-lights";
  document.getElementById("mute-pills").dataset.list = "dl-mute";
  const audioPills = document.getElementById("audio-pills");
  const lightsMutePills = document.getElementById("lights-mute-pills");
  if (audioPills) audioPills.dataset.list = "dl-mute";
  if (lightsMutePills) lightsMutePills.dataset.list = "dl-mute";
  renderPillBox("lights-pills", state.lights, state.haEntities.light, (next) => {
    state.lights = next;
    refreshPillBoxes();
  });
  renderPillBox("mute-pills", state.muteEntities, state.haEntities.mute, (next) => {
    state.muteEntities = next;
    refreshPillBoxes();
  });
  renderPillBox("audio-pills", state.audioEntities, state.haEntities.mute, (next) => {
    state.audioEntities = next;
    refreshPillBoxes();
  });
  renderPillBox("lights-mute-pills", state.lightsMuteEntities, state.haEntities.mute, (next) => {
    state.lightsMuteEntities = next;
    refreshPillBoxes();
  });
}

function fillForm(opts) {
  state.options = { ...opts };
  state.lights = parseEntityList(opts.lights || opts.light_entity_id);
  state.muteEntities = parseEntityList(opts.mute_entities);
  state.audioEntities = parseEntityList(opts.audio_toggle_entities);
  state.lightsMuteEntities = parseEntityList(opts.lights_toggle_entities);
  for (const id of CONN_FIELDS) {
    const el = document.getElementById(id);
    if (el && opts[id] !== undefined && opts[id] !== null) el.value = opts[id];
  }
  document.getElementById("zabbix_api_token").value = "";
  document.getElementById("escalate_only").checked = !!opts.escalate_only;
  document.getElementById("audio_enabled").checked = !!opts.audio_enabled;
  document.getElementById("audio_recovery_chime").checked = !!opts.audio_recovery_chime;
  fillBaseRecovery(opts);
  for (const tier of TIERS) {
    state.patterns[patternKey(tier.key)] = parsePattern(opts[patternKey(tier.key)], tier, opts);
    state.windows[tier.key] = resolveWindow(tier, opts);
    state.targets[tier.key] = parseEntityList(opts[targetsKey(tier.key)]);
    state.options[`mode_${tier.key}`] = normalizeMode(opts[`mode_${tier.key}`], tier.defaultMode);
  }
  refreshPillBoxes();
  buildSeverityPanels();
}

function renderLiveStatus(status) {
  const el = document.getElementById("live-status");
  if (!el || !status) return;
  const sev = status.severity == null ? "none" : status.severity;
  const full = status.muted ? `muted@${status.muted_at_severity}` : "live";
  const lightsHold = status.lights_suppressed ? "muted" : "live";
  const audioHold = status.audio_suppressed ? "muted" : "live";
  el.textContent = `Status: severity=${sev} · full=${full} · lights=${lightsHold} · audio=${audioHold}`;
}

async function loadHaEntities() {
  try {
    const res = await fetch("api/ha/entities?domain=light,button,input_button,event,media_player,tts");
    const data = await res.json();
    const ents = data.entities || [];
    state.haEntities.light = ents.filter((e) => e.domain === "light");
    state.haEntities.mute = ents.filter((e) => ["button", "input_button", "event"].includes(e.domain));
    state.haEntities.media = ents.filter((e) => e.domain === "media_player");
    state.haEntities.tts = ents.filter((e) => e.domain === "tts");
    fillDatalist("dl-lights", state.haEntities.light);
    fillDatalist("dl-mute", state.haEntities.mute);
    fillDatalist("dl-media", state.haEntities.media);
    fillDatalist("dl-tts", state.haEntities.tts);
    refreshPillBoxes();
  } catch (e) {
    console.warn("HA entities", e);
  }
}

async function loadOptions() {
  setStatus("Loading…", "busy");
  const res = await fetch("api/options");
  if (!res.ok) throw new Error(`Load failed (${res.status})`);
  const data = await res.json();
  fillForm(data.options || {});
  renderLiveStatus(data.status);
  setStatus("Loaded", "ok");
  loadHaEntities();
}

async function saveOptions() {
  setStatus("Saving…", "busy");
  const payload = { options: collectFormOptions() };
  const res = await fetch("api/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `Save failed (${res.status})`);
  state.options = { ...(data.options || payload.options) };
  renderLiveStatus(data.status);
  setStatus("Saved — poller will reload", "ok");
}

async function testRecovery() {
  setStatus("Saving & testing recovery…", "busy");
  await saveOptions();
  const res = await fetch("api/test/recovery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seconds: 8 }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `Test failed (${res.status})`);
  setStatus(data.message || "Testing recovery…", "ok");
}

async function postMute(path) {
  await saveOptions();
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || "Mute API failed");
  setStatus(data.message || "OK", "ok");
  const st = await fetch("api/status").then((r) => r.json()).catch(() => ({}));
  renderLiveStatus(st.status);
}

document.getElementById("btn-reload").addEventListener("click", () => {
  loadOptions().catch((e) => setStatus(String(e.message || e), "err"));
});
document.getElementById("btn-save").addEventListener("click", () => {
  saveOptions().catch((e) => setStatus(String(e.message || e), "err"));
});
document.getElementById("btn-test-recovery")?.addEventListener("click", () => {
  testRecovery().catch((err) => setStatus(String(err.message || err), "err"));
});
document.getElementById("btn-mute")?.addEventListener("click", () => {
  postMute("api/mute").catch((e) => setStatus(String(e.message || e), "err"));
});
document.getElementById("btn-unmute")?.addEventListener("click", () => {
  postMute("api/unmute").catch((e) => setStatus(String(e.message || e), "err"));
});
document.getElementById("btn-audio-toggle")?.addEventListener("click", () => {
  postMute("api/audio/toggle").catch((e) => setStatus(String(e.message || e), "err"));
});
document.getElementById("btn-lights-toggle")?.addEventListener("click", () => {
  postMute("api/lights/toggle").catch((e) => setStatus(String(e.message || e), "err"));
});

bindBaseRecoveryColors();
loadOptions().catch((e) => setStatus(String(e.message || e), "err"));
setInterval(() => {
  fetch("api/status").then((r) => r.json()).then((d) => renderLiveStatus(d.status)).catch(() => {});
}, 5000);
