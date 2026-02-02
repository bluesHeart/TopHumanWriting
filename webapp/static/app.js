(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const ACCENTS = {
    teal: { base: "#14b8a6", hover: "#0ea5a0", pressed: "#0b837f" },
    ocean: { base: "#2f80ed", hover: "#2970d0", pressed: "#2159a7" },
    violet: { base: "#7c3aed", hover: "#6d28d9", pressed: "#5b21b6" },
    slate: { base: "#475569", hover: "#334155", pressed: "#1f2937" },
    crimson: { base: "#e53935", hover: "#d32f2f", pressed: "#b71c1c" },
  };

  const state = {
    libraries: [],
    library: localStorage.getItem("aiw.library") || "",
    lastScan: null,
    lastCite: null,
    lastCitecheck: null,
    lastCitecheckMissing: null,
    lastAudit: null,
    llm: null,
    llmApi: null,
    llmTest: null,
    libraryStatus: null,
    buildTaskId: localStorage.getItem("aiw.buildTaskId") || "",
    buildPollTimer: null,
    citeTaskId: localStorage.getItem("aiw.citeTaskId") || "",
    citePollTimer: null,
    citecheckTaskId: localStorage.getItem("aiw.citecheckTaskId") || "",
    citecheckPollTimer: null,
    citecheckMissingTaskId: localStorage.getItem("aiw.citecheckMissingTaskId") || "",
    citecheckMissingPollTimer: null,
    auditPaperId: localStorage.getItem("aiw.auditPaperId") || "",
    auditTaskId: localStorage.getItem("aiw.auditTaskId") || "",
    auditPollTimer: null,
    pdfFolder: localStorage.getItem("aiw.pdfFolder") || "",
    scanDraft: localStorage.getItem("aiw.scanDraft") || "",
    polishDraft: localStorage.getItem("aiw.polishDraft") || "",
    citecheckDraftId: localStorage.getItem("aiw.citecheckDraftId") || "",
    clientId: sessionStorage.getItem("aiw.clientId") || "",
    clientHeartbeatTimer: null,
    modalOnClose: null,
  };

  let renderSeq = 0;

  function hexToRgba(hex, a) {
    const h = String(hex || "").replace("#", "");
    if (h.length !== 6) return `rgba(20,184,166,${a})`;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  function setAccent(name) {
    const a = ACCENTS[name] || ACCENTS.teal;
    document.documentElement.style.setProperty("--accent", a.base);
    document.documentElement.style.setProperty("--accent-hover", a.hover);
    document.documentElement.style.setProperty("--accent-pressed", a.pressed);
    document.documentElement.style.setProperty("--focus", `0 0 0 3px ${hexToRgba(a.base, 0.25)}`);
    localStorage.setItem("aiw.accent", name);
    $("#accentSelect").value = name;
  }

  function setTheme(theme) {
    const t = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("aiw.theme", t);
  }

  function toast(msg, kind = "good", ms = 2600) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.remove("good", "bad", "show");
    el.classList.add(kind === "bad" ? "bad" : "good");
    el.classList.add("show");
    window.clearTimeout(toast._t);
    toast._t = window.setTimeout(() => el.classList.remove("show"), ms);
  }

  async function api(method, url, body, opts) {
    const init = { method, headers: {} };
    if (opts && opts.signal) init.signal = opts.signal;
    if (!init.signal) {
      // Default request timeout (keeps the UI from "hanging forever").
      // Long-running endpoints can pass their own AbortController.
      const timeoutMs = Number.isFinite(Number(opts && opts.timeout_ms)) ? Number(opts.timeout_ms) : 120000;
      if (timeoutMs > 0) {
        try {
          const ctl = new AbortController();
          init.signal = ctl.signal;
          window.setTimeout(() => {
            try {
              ctl.abort();
            } catch {}
          }, Math.max(1000, Math.min(600000, timeoutMs)));
        } catch {}
      }
    }
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const resp = await fetch(url, init);
    let data = null;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (!resp.ok) {
      const detail =
        data && typeof data === "object" && data.detail ? String(data.detail) : `${resp.status} ${resp.statusText}`;
      throw new Error(detail);
    }
    return data;
  }

  const apiGet = (url, opts) => api("GET", url, undefined, opts);
  const apiPost = (url, body, opts) => api("POST", url, body, opts);

  function maybeOpenIndexModalForError(msg) {
    const m = String(msg || "").toLowerCase();
    if (m.includes("rag index missing") || m.includes("build library first")) {
      openPrepWizard({ need: "rag" });
      return true;
    }
    if (m.includes("citation bank missing") || m.includes("cite index missing") || m.includes("build it first")) {
      openPrepWizard({ need: "cite" });
      return true;
    }
    return false;
  }

  async function apiFormPost(url, form) {
    const resp = await fetch(url, { method: "POST", body: form });
    let data = null;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (!resp.ok) {
      const detail =
        data && typeof data === "object" && data.detail ? String(data.detail) : `${resp.status} ${resp.statusText}`;
      throw new Error(detail);
    }
    return data;
  }

  function newClientId() {
    try {
      if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    } catch {}
    return `c_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
  }

  async function registerClient() {
    if (!state.clientId) {
      state.clientId = newClientId();
      sessionStorage.setItem("aiw.clientId", state.clientId);
    }
    try {
      const r = await apiPost("/api/client/register", { client_id: state.clientId });
      if (r && r.client_id && typeof r.client_id === "string") {
        state.clientId = r.client_id;
        sessionStorage.setItem("aiw.clientId", state.clientId);
      }
    } catch {
      // ignore: app can still work without auto-exit binding
    }
  }

  function startClientHeartbeat() {
    if (state.clientHeartbeatTimer) return;
    const ping = async () => {
      try {
        await apiPost("/api/client/heartbeat", { client_id: state.clientId });
      } catch {}
    };
    ping();
    state.clientHeartbeatTimer = window.setInterval(ping, 5000);
  }

  function bindClientLifecycle() {
    registerClient().finally(() => startClientHeartbeat());
    window.addEventListener("beforeunload", () => {
      try {
        const payload = JSON.stringify({ client_id: state.clientId });
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon("/api/client/unregister", blob);
      } catch {}
    });
  }

  function route() {
    const h = (location.hash || "").replace("#", "").trim();
    return h || "home";
  }

  function setRoute(name) {
    // Route changes should close any blocking modal; otherwise the UI feels "stuck".
    try {
      const back = $("#modalBackdrop");
      if (back && !back.classList.contains("hidden")) closeModal();
    } catch {}
    location.hash = name;
  }

  function libraryKeyForRoute(r) {
    const rr = String(r || "").trim().toLowerCase();
    return rr === "norms" ? "aiw.normsLibrary" : "aiw.library";
  }

  function getSavedLibraryForRoute(r) {
    const rr = String(r || "").trim().toLowerCase();
    const key = libraryKeyForRoute(rr);
    const v = String(localStorage.getItem(key) || "").trim();
    if (v) return v;
    return "";
  }

  function setSavedLibraryForRoute(r, name) {
    const rr = String(r || "").trim().toLowerCase();
    const key = libraryKeyForRoute(rr);
    localStorage.setItem(key, String(name || "").trim());
  }

  function navActive(name) {
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.route === name));
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
        else if (v === true) node.setAttribute(k, "");
        else if (v !== false && v != null) node.setAttribute(k, String(v));
      }
    }
    for (const c of children.flat()) {
      if (c == null) continue;
      if (c instanceof Node) node.appendChild(c);
      else node.appendChild(document.createTextNode(String(c)));
    }
    return node;
  }

  function normalizeLibraries(libs) {
    const out = [];
    for (const it of libs || []) {
      if (it == null) continue;
      if (typeof it === "string") out.push({ name: it });
      else if (typeof it === "object") {
        const name = String(it.name || "").trim();
        if (!name) continue;
        // Safety: ignore internal artifacts even if server forgets to filter.
        if (name.endsWith(".sentences")) continue;
        out.push({ ...it, name });
      } else {
        const name = String(it).trim();
        if (!name) continue;
        if (name.endsWith(".sentences")) continue;
        out.push({ name });
      }
    }
    return out;
  }

  function normalizeLibraryKind(raw) {
    const k = String(raw || "")
      .trim()
      .toLowerCase();
    if (!k) return "";
    if (k === "references" || k === "reference" || k === "refs" || k === "ref") return "references";
    if (k === "exemplar" || k === "topic") return "exemplar";
    return "";
  }

  function inferLibraryKindFromName(name) {
    const n = String(name || "")
      .trim()
      .toLowerCase();
    if (!n) return "exemplar";
    if (n.startsWith("refs_") || n.startsWith("refs-") || n.startsWith("ref_") || n.startsWith("ref-")) return "references";
    if (n.startsWith("citecheck") || n.startsWith("references") || n.endsWith("_refs") || n.endsWith("-refs")) return "references";
    return "exemplar";
  }

  function libraryKind(libOrName) {
    if (typeof libOrName === "string") return inferLibraryKindFromName(libOrName);
    const lib = libOrName && typeof libOrName === "object" ? libOrName : null;
    const name = lib ? String(lib.name || "").trim() : "";
    const k = normalizeLibraryKind(lib && lib.kind);
    return k || inferLibraryKindFromName(name);
  }

  function isReferenceLibrary(libOrName) {
    return libraryKind(libOrName) === "references";
  }

  function librariesForRoute(routeName, libs) {
    const r = String(routeName || "").trim();
    const arr = Array.isArray(libs) ? libs : [];
    if (r === "norms") return arr.filter((x) => x && !String(x.name || "").endsWith(".sentences") && isReferenceLibrary(x));
    return arr.filter((x) => x && !String(x.name || "").endsWith(".sentences") && !isReferenceLibrary(x));
  }

  function libraryNames() {
    return (state.libraries || []).map((x) => x && x.name).filter(Boolean);
  }

  function libraryByName(name) {
    const n = String(name || "").trim();
    if (!n) return null;
    return (state.libraries || []).find((x) => x && x.name === n) || null;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function updateGlobalLibraryUI() {
    const sel = $("#librarySelect");
    clear(sel);
    const r = route();
    sel.appendChild(el("option", { value: "" }, r === "norms" ? "— 选择参考文献库 —" : "— 选择范文专题库 —"));
    const libs = librariesForRoute(r, state.libraries || []);
    for (const lib of libs) {
      const name = (lib && lib.name) || "";
      if (!name) continue;
      sel.appendChild(el("option", { value: name }, name));
    }
    sel.value = state.library || "";
    setSavedLibraryForRoute(route(), state.library || "");
  }

  async function refreshLibraries() {
    // Keep library selection route-scoped:
    // - Most pages use "aiw.library" (exemplar library)
    // - Writing Norms uses "aiw.normsLibrary" (reference PDF library)
    const r0 = route();
    const saved = getSavedLibraryForRoute(r0);
    const hasExplicit = !!saved;
    if (saved) state.library = saved;
    else if (r0 === "norms") state.library = "";

    let data = null;
    try {
      data = await apiGet("/api/libraries/summary");
    } catch {
      data = await apiGet("/api/libraries");
    }
    state.libraries = normalizeLibraries((data && data.libraries) || []);

    // Prefer "ready" libraries near the top (metaso-style topic modules).
    try {
      state.libraries.sort((a, b) => {
        const ar = a && a.rag_index ? 1 : 0;
        const br = b && b.rag_index ? 1 : 0;
        if (ar !== br) return br - ar;
        const an = String((a && a.name) || "");
        const bn = String((b && b.name) || "");
        return an.localeCompare(bn);
      });
    } catch {}

    const allowed = librariesForRoute(r0, state.libraries || []);
    const allowedNames = allowed.map((x) => x && x.name).filter(Boolean);
    if (state.library && !allowedNames.includes(state.library)) state.library = "";

    // If user hasn't picked one yet, auto-select a ready demo library.
    // (But NOT for Writing Norms: avoid accidentally using an exemplar library as the reference library.)
    if (!hasExplicit && r0 !== "norms") {
      const ready = (allowed || []).find((x) => x && x.rag_index);
      if (ready && ready.name) state.library = ready.name;
    }
    if (!state.library && allowedNames.length && r0 !== "norms") state.library = allowedNames[0];
    updateGlobalLibraryUI();
  }

  function openIndexModal(kind, status) {
    const st = status || {};
    const k = String(kind || "").toLowerCase();
    const ok = k === "semantic" ? !!st.semantic_index : k === "rag" ? !!st.rag_index : k === "cite" ? !!st.cite_index : false;

    let title = "准备状态";
    let desc = "";
    let need = "";
    let nextRoute = "";
    let nextBtn = "";

    if (k === "semantic") {
      title = "写作特征库（提升相似度）";
      desc = "用于提升“相似段落检索”的准确度。通常在准备范文库时自动生成。";
      need = "不是所有功能都强依赖，但建议保持开启。";
      nextRoute = "library";
      nextBtn = "去范文库";
    } else if (k === "rag") {
      title = "范文片段库（用于对照）";
      desc = "用于“找差距/对齐润色”的范文片段检索（离线），给你可追溯的范文证据。";
      need = "找差距/对齐润色都需要它。";
      nextRoute = "library";
      nextBtn = "去准备";
    } else if (k === "cite") {
      title = "引用句式库（引用写法）";
      desc = "从范文中抽取“引用句子 + 参考文献”，用于检索可借鉴的引用写法。";
      need = "引用写法需要它；通常在准备范文库后再构建。";
      nextRoute = "cite";
      nextBtn = "去引用写法";
    }

    const badge = el("span", { class: "badge " + (ok ? "good" : "bad") }, ok ? "已就绪" : "未就绪");
    const body = el(
      "div",
      { class: "grid", style: "gap:10px" },
      el("div", { class: "row" }, badge, el("span", { class: "muted" }, state.library ? `当前范文专题库：${state.library}` : "未选择范文专题库")),
      el("div", null, desc),
      el("div", { class: "muted" }, need),
      el(
        "div",
        { class: "row" },
        el(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            onclick: () => {
              closeModal();
              if (nextRoute) setRoute(nextRoute);
            },
          },
          nextBtn || "去操作"
        ),
        el("button", { class: "btn", type: "button", onclick: closeModal }, "关闭")
      )
    );
    openModal(title, body);
  }

  function openPrepWizard(opts = {}) {
    const need = String(opts.need || "rag").trim().toLowerCase() || "rag"; // "rag" | "cite" | "import"
    const isImportOnly = need === "import";
    const resume = opts.resume && typeof opts.resume === "object" ? opts.resume : null;
    const presetLibrary = String(opts.library || "").trim();
    const lockLibrary = !!opts.lockLibrary;

    const title = isImportOnly ? "导入参考文献原文 PDF（用于引用核查）" : "准备范文库（第一次使用）";

    let selectedFiles = [];
    let importing = false;
    let importCanceled = false;
    let currentTaskId = "";
    let pollTimer = null;

    const pdfInput = el("input", {
      id: isImportOnly ? "aiwFilesPickerImport" : "aiwFilesPicker",
      type: "file",
      multiple: true,
      accept: ".pdf,application/pdf",
      "data-testid": isImportOnly ? "files-picker-import" : "files-picker",
      style: "display:none",
      tabindex: "-1",
      "aria-hidden": "true",
    });

    function fmtCount(n) {
      const x = Number(n || 0);
      if (!Number.isFinite(x)) return "0";
      return String(Math.max(0, Math.round(x)));
    }

    function prettyStage(stage) {
      const s = String(stage || "")
        .trim()
        .toLowerCase();
      const map = {
        starting: "准备中",
        pdf_extract: "读取 PDF",
        semantic_embed: "提取写作特征",
        rag_extract: "切分范文片段",
        rag_embed: "构建范文对照证据",
        rag_done: "范文对照证据完成",
        materials_scan: "扫描范文（素材库）",
        materials_doc: "结构化范文（素材库）",
        materials_done: "素材库完成",
        cite_extract: "抽取引用信息",
        cite_embed: "构建引用证据",
        cite_index: "整理引用证据",
        cite_done: "引用证据完成",
        llm_sentence: "LLM 逐句对齐诊断",
        llm_outline: "LLM 章节结构诊断",
        llm_cite: "LLM 引用写法诊断",
        citecheck_embed: "构建核查向量",
        citecheck_papers_index: "索引参考文献原文 PDF",
        citecheck_para: "抽取原文证据段落",
        citecheck_checking: "核查引用",
        citecheck_llm: "大模型判定",
        citecheck_missing_scan: "扫描缺失原文",
        citecheck_missing_oa: "联网查 DOI / OA",
        citecheck_missing_download: "下载 OA PDF",
      };
      return map[s] || humanTaskStage(stage);
    }

    function stopPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function pollTaskOnce(updateUI) {
      if (!currentTaskId) return null;
      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(currentTaskId)}`);
        if (typeof updateUI === "function") updateUI(t);
        return t;
      } catch (e) {
        stopPolling();
        throw e;
      }
    }

    // Step 0: select/create library
    const libSel = el("select", { class: "select", style: "flex:1; min-width:220px" });
    const libName = el("input", { class: "input", placeholder: isImportOnly ? "给参考文献库起个名字（例如：refs_finance）" : "给范文库起个名字（例如：finance_2026）", style: "flex:1; min-width:220px; display:none" });
    const libCreateBtn = el("button", { class: "btn btn-primary", type: "button", style: "display:none" }, "创建");
    const libNewBtn = el("button", { class: "btn", type: "button" }, isImportOnly ? "新建库" : "新建范文库");
    const libHint = el(
      "div",
      { class: "muted" },
      isImportOnly
        ? "参考文献库 = 你论文里引用到的“原文论文 PDF”存放库（离线保存在本地）。只需要导入 PDF，不需要生成范文片段。"
        : "范文库就是：你收集的同领域顶级 PDF。只要准备一次，之后找差距/润色都会直接有“范文片段（证据）”。"
    );

    if (presetLibrary) {
      state.library = presetLibrary;
      setSavedLibraryForRoute(route(), state.library);
      updateGlobalLibraryUI();
    }

    function syncLibSelOptions() {
      clear(libSel);
      libSel.appendChild(el("option", { value: "" }, isImportOnly ? "— 选择参考文献库 —" : "— 选择范文专题库 —"));
      const libs = isImportOnly ? librariesForRoute("norms", state.libraries || []) : librariesForRoute("library", state.libraries || []);
      for (const lib of libs) {
        const name = (lib && lib.name) || "";
        if (!name) continue;
        libSel.appendChild(el("option", { value: name }, name));
      }
      libSel.value = state.library || "";
    }

    function showNewLibrary(show) {
      libName.style.display = show ? "" : "none";
      libCreateBtn.style.display = show ? "" : "none";
      libNewBtn.style.display = show ? "none" : "";
      if (show) {
        libName.value = "";
        try {
          libName.focus();
        } catch {}
      }
    }

    libNewBtn.onclick = () => showNewLibrary(true);

    libCreateBtn.onclick = async () => {
      const name = String(libName.value || "").trim();
      if (!name) return toast(isImportOnly ? "请输入参考文献库名字。" : "请输入范文库名字。", "bad");
      libCreateBtn.disabled = true;
      libCreateBtn.textContent = "创建中…";
      try {
        const r = await apiPost("/api/libraries", { name, kind: isImportOnly ? "references" : "exemplar" });
        let safe = name;
        try {
          const p = String((r && r.path) || "");
          const base = p.split(/[\\/]/).pop() || "";
          if (base.toLowerCase().endsWith(".json")) safe = base.slice(0, -5) || safe;
        } catch {}
        state.library = safe;
        setSavedLibraryForRoute(route(), state.library);
        await refreshLibraries();
        await refreshLibraryStatus();
        updateGlobalLibraryUI();
        syncLibSelOptions();
        await syncImportedCount();
        toast(isImportOnly ? "已创建参考文献库。" : "已创建范文库。");
        showNewLibrary(false);
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      } finally {
        libCreateBtn.disabled = false;
        libCreateBtn.textContent = "创建";
      }
    };

    libSel.addEventListener("change", async () => {
      state.library = libSel.value || "";
      setSavedLibraryForRoute(route(), state.library);
      updateGlobalLibraryUI();
      await refreshLibraryStatus().catch(() => {});
      await syncImportedCount().catch(() => {});
    });

    // Step 1: pick folder (optional when evidence already exists)
    const selectedInfo = el(
      "div",
      { class: "muted mono" },
      isImportOnly
        ? "导入参考文献原文（可选）：拖拽 PDF / 选择多个 PDF（推荐）。"
        : "新增范文（可选）：拖拽 PDF / 选择多个 PDF（推荐）。"
    );
    const pickFilesBtn = el("button", { class: "btn btn-primary", type: "button", title: "推荐：兼容性最好（可多选）" }, "选择多个 PDF…");
    pickFilesBtn.onclick = () => {
      try {
        if (typeof pdfInput.showPicker === "function") pdfInput.showPicker();
        else pdfInput.click();
      } catch {
        pdfInput.click();
      }
    };

    const dropZone = el(
      "div",
      { class: "dropzone", tabindex: "0", title: "把 PDF 文件拖进来（支持多个）" },
      el("div", null, el("strong", null, "拖拽 PDF 到这里"), "（支持多个）"),
      el("div", { class: "muted" }, isImportOnly ? "只离线保存在本地参考文献库，不会上传。" : "只离线保存在本地范文库，不会上传。")
    );

    function setDropActive(on) {
      dropZone.classList.toggle("dragover", !!on);
    }

    dropZone.addEventListener("dragover", (e) => {
      try {
        e.preventDefault();
      } catch {}
      setDropActive(true);
    });
    dropZone.addEventListener("dragleave", () => setDropActive(false));
    dropZone.addEventListener("drop", (e) => {
      try {
        e.preventDefault();
      } catch {}
      setDropActive(false);
      const dt = e && e.dataTransfer ? e.dataTransfer : null;
      const files = dt && dt.files ? Array.from(dt.files) : [];
      const pdfs = files.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) {
        toast("未检测到 PDF 文件（请拖拽 .pdf）。", "bad", 4500);
        return;
      }
      selectedFiles = pdfs;
      updateSelectedInfo();
      toast(`已选择 ${fmtCount(pdfs.length)} 个 PDF。`);
    });

    function updateSelectedInfo() {
      const pdfs = selectedFiles.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) {
        selectedInfo.textContent = isImportOnly
          ? "导入参考文献原文（可选）：拖拽 PDF / 选择多个 PDF（推荐）。"
          : "新增范文（可选）：拖拽 PDF / 选择多个 PDF（推荐）。";
        return;
      }
      selectedInfo.textContent = `已选择：${fmtCount(pdfs.length)} 个 PDF`;
    }

    pdfInput.addEventListener("change", () => {
      selectedFiles = Array.from(pdfInput.files || []);
      updateSelectedInfo();
      if (selectedFiles.length) toast("已选择 PDF。");
    });

    // Step 2: import + build
    const importedInfo = el("div", { class: "muted mono" }, "—");
    const importBar = el("div", { class: "progress" }, el("div"));
    const importText = el("div", { class: "muted mono" }, "—");
    const buildBar = el("div", { class: "progress" }, el("div"));
    const buildText = el("div", { class: "muted mono" }, "—");

    const includeCite = el("input", { type: "checkbox" });
    includeCite.checked = need === "cite";

    async function syncImportedCount() {
      if (!state.library) {
        importedInfo.textContent = "请先选择范文库。";
        return { pdf_count: 0, pdf_root: "" };
      }

      // Prefer showing evidence-source when the RAG index already exists (avoids "已导入 0" confusion).
      try {
        await refreshLibraries();
      } catch {}

      const lib = (state.libraries || []).find((x) => x && String(x.name || "").trim() === state.library) || null;
      const ragOk = !!(lib && lib.rag_index);
      const ragN = lib && lib.rag_pdf_count != null ? Number(lib.rag_pdf_count) : 0;
      const ragChunks = lib && lib.rag_node_count != null ? Number(lib.rag_node_count) : 0;
      const ragRoot = lib && lib.rag_pdf_root ? String(lib.rag_pdf_root) : "";
      const importN = lib && lib.pdf_import_count != null ? Number(lib.pdf_import_count) : null;
      const importRoot = lib && lib.pdf_import_root ? String(lib.pdf_import_root) : "";

      if (ragOk) {
        const n = Number.isFinite(ragN) ? ragN : 0;
        const c = Number.isFinite(ragChunks) ? ragChunks : 0;
        importedInfo.textContent = `已准备：范文 ${fmtCount(n)} 篇 · 范文片段 ${fmtCount(c)} 段${ragRoot ? ` · 来源：${ragRoot}` : ""}`;
        return { pdf_count: n, pdf_root: ragRoot || importRoot || "" };
      }

      if (importN != null) {
        const n = Number.isFinite(importN) ? importN : 0;
        importedInfo.textContent = `已导入：${fmtCount(n)} 个 PDF（离线保存在本地）${importRoot ? ` · 存储：${importRoot}` : ""}`;
        return { pdf_count: n, pdf_root: importRoot || "" };
      }

      // Fallback for older server versions.
      const r = await apiGet(`/api/library/pdf_root?library=${encodeURIComponent(state.library)}`);
      const n = r && r.pdf_count != null ? Number(r.pdf_count) : 0;
      importedInfo.textContent = `已导入：${Number.isFinite(n) ? fmtCount(n) : "—"} 个 PDF（离线保存在本地）`;
      return r || { pdf_count: n || 0, pdf_root: "" };
    }

    const readyHint = el("div", { class: "muted" }, "—");
    const goBtn = el("button", { class: "btn btn-primary", type: "button", style: "display:none" }, "直接开始写作");
    const startBtn = el("button", { class: "btn btn-primary", type: "button" }, isImportOnly ? "开始导入" : "一键准备（导入 + 生成证据）");
    const cancelBtn = el("button", { class: "btn btn-danger btn-small", type: "button" }, "取消");
    cancelBtn.style.display = "none";

    function setBars(pct1, txt1, pct2, txt2) {
      if (pct1 != null) importBar.firstChild.style.width = `${Math.max(0, Math.min(100, Math.round(pct1)))}%`;
      if (txt1 != null) importText.textContent = String(txt1 || "—");
      if (pct2 != null) buildBar.firstChild.style.width = `${Math.max(0, Math.min(100, Math.round(pct2)))}%`;
      if (txt2 != null) buildText.textContent = String(txt2 || "—");
    }

    async function runImport() {
      const pdfs = selectedFiles.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) return;
      importing = true;
      importCanceled = false;
      setBars(0, `导入中… 0/${fmtCount(pdfs.length)}`, null, null);

      for (let i = 0; i < pdfs.length; i++) {
        if (importCanceled) break;
        const f = pdfs[i];
        const rel = String(f.webkitRelativePath || f.name || "");
        setBars(((i + 1) / pdfs.length) * 100, `导入中… ${fmtCount(i + 1)}/${fmtCount(pdfs.length)} · ${rel}`, null, null);

        const fd = new FormData();
        fd.append("library", state.library);
        fd.append("overwrite", "0");
        fd.append("file", f, rel || f.name || `file_${i + 1}.pdf`);
        await apiFormPost("/api/library/upload_pdf", fd);
      }

      importing = false;
      if (importCanceled) toast("已取消导入（部分文件可能已导入）。", "bad", 4500);
      else toast("导入完成。");
      await syncImportedCount().catch(() => {});
    }

    async function runBuildLibrary() {
      const r = await apiPost("/api/library/build", { library: state.library, folder: "" });
      currentTaskId = (r && r.task_id) || "";
      if (!currentTaskId) throw new Error("missing task_id");

      const update = (t) => {
        const done = Number(t.done || 0);
        const total = Number(t.total || 0);
        const pct = total > 0 ? Math.round((done / total) * 100) : 0;
        const st = humanTaskStatus(t.status);
        const stage = prettyStage(t.stage);
        const detail = String(t.detail || "").trim();
        setBars(null, null, pct, `${st} · ${stage}${total ? ` · ${done}/${total}` : ""}${detail ? ` · ${detail}` : ""}`);
      };

      // First pull immediately to show something.
      await pollTaskOnce(update);

      stopPolling();
      pollTimer = window.setInterval(async () => {
        try {
          const t = await pollTaskOnce(update);
          if (!t) return;
          if (t.status !== "running") {
            stopPolling();
          }
        } catch (e) {
          stopPolling();
          toast(String(e.message || e), "bad", 6500);
        }
      }, 800);

      // Wait for completion.
      while (true) {
        const t = await pollTaskOnce(update);
        if (!t) break;
        if (t.status !== "running") {
          stopPolling();
          if (t.status === "done") return t;
          if (t.status === "canceled") throw new Error("已取消。");
          throw new Error(String(t.error || "准备失败。").slice(0, 500));
        }
        await new Promise((res) => window.setTimeout(res, 800));
      }
      return null;
    }

    async function runBuildCite() {
      const r = await apiPost("/api/cite/build", { library: state.library, folder: "", max_pages: null });
      currentTaskId = (r && r.task_id) || "";
      if (!currentTaskId) throw new Error("missing task_id");

      const update = (t) => {
        const done = Number(t.done || 0);
        const total = Number(t.total || 0);
        const pct = total > 0 ? Math.round((done / total) * 100) : 0;
        const st = humanTaskStatus(t.status);
        const stage = prettyStage(t.stage);
        const detail = String(t.detail || "").trim();
        setBars(null, null, pct, `${st} · ${stage}${total ? ` · ${done}/${total}` : ""}${detail ? ` · ${detail}` : ""}`);
      };

      await pollTaskOnce(update);

      stopPolling();
      pollTimer = window.setInterval(async () => {
        try {
          const t = await pollTaskOnce(update);
          if (!t) return;
          if (t.status !== "running") stopPolling();
        } catch (e) {
          stopPolling();
          toast(String(e.message || e), "bad", 6500);
        }
      }, 800);

      while (true) {
        const t = await pollTaskOnce(update);
        if (!t) break;
        if (t.status !== "running") {
          stopPolling();
          if (t.status === "done") return t;
          if (t.status === "canceled") throw new Error("已取消。");
          throw new Error(String(t.error || "构建引用证据失败。").slice(0, 500));
        }
        await new Promise((res) => window.setTimeout(res, 800));
      }
      return null;
    }

    function resumeAfterReady() {
      const cur = route();
      const target = resume && resume.route ? resume.route : cur;
      if (resume && resume.autoKey) localStorage.setItem(String(resume.autoKey), String(resume.autoValue || "1"));
      if (resume && resume.route && resume.route !== cur) setRoute(resume.route);

      // Avoid wiping user's in-progress draft on scan/polish/cite/norms pages when the wizard
      // was opened manually (no resume). Library page is safe to re-render to refresh cards.
      const safeToRerender = !!resume || target === "library";
      if (safeToRerender) window.setTimeout(() => render().catch(() => {}), 80);
    }

    cancelBtn.onclick = async () => {
      if (importing) {
        importCanceled = true;
        toast("已请求取消导入（会在当前文件完成后停止）。", "bad", 4500);
        return;
      }
      if (!currentTaskId) return;
      try {
        await apiPost(`/api/tasks/${encodeURIComponent(currentTaskId)}/cancel`, {});
        toast("已请求取消。", "bad", 4500);
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      }
    };

    startBtn.onclick = async () => {
      if (!state.library) return toast(isImportOnly ? "请先选择/创建库。" : "请先选择/创建范文库。", "bad", 4500);

      startBtn.disabled = true;
      pickFilesBtn.disabled = true;
      libSel.disabled = true;
      libNewBtn.disabled = true;
      libCreateBtn.disabled = true;
      cancelBtn.style.display = "";
      cancelBtn.disabled = false;

      try {
        setBars(0, "—", 0, "—");
        await refreshLibraryStatus().catch(() => {});
        const st0 = state.libraryStatus || {};
        const ragOk0 = !!st0.rag_index;
        const citeOk0 = !!st0.cite_index;

        const imported = await syncImportedCount().catch(() => ({ pdf_count: 0 }));
        const importedN = imported && imported.pdf_count != null ? Number(imported.pdf_count) : 0;

        const hasSelection = selectedFiles.some((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
        if (isImportOnly) {
          if (!hasSelection) {
            toast("请先选择要导入的 PDF。", "bad", 4500);
            return;
          }
          await runImport();
          try {
            await refreshLibraries();
          } catch {}
          toast("参考文献原文 PDF 导入完成。");
          closeModal();
          resumeAfterReady();
          return;
        } else {
          if (!hasSelection && (!Number.isFinite(importedN) || importedN <= 0) && !ragOk0) {
            toast("请先选择要导入的 PDF（或拖拽 PDF 到这里）。", "bad", 4500);
            return;
          }
        }

        let didImport = false;
        if (hasSelection) {
          await runImport();
          didImport = true;
        } else {
      if (ragOk0) setBars(100, `跳过导入：已存在范文片段（${fmtCount(importedN)} 篇范文）`, null, null);
          else setBars(100, `跳过导入：已检测到本地已导入 ${fmtCount(importedN)} 个 PDF`, null, null);
        }

        if (!ragOk0 || didImport) {
      setBars(null, null, 0, "正在生成范文片段…（首次可能较慢）");
          await runBuildLibrary();
        } else {
      setBars(null, null, 100, "已就绪：范文片段已存在");
        }

        await refreshLibraryStatus().catch(() => {});
        const st1 = state.libraryStatus || {};
    if (!st1.rag_index) throw new Error("范文片段未就绪（请重试）。");

        if (includeCite.checked) {
          if (!citeOk0) {
            setBars(null, null, 0, "正在准备引用证据…（可选项）");
            await runBuildCite();
            await refreshLibraryStatus().catch(() => {});
          } else {
            setBars(null, null, 100, "已就绪：引用证据已存在");
          }
        }

        toast("范文库准备完成，可以开始对齐写作了。");
        closeModal();
        resumeAfterReady();
      } catch (e) {
        const msg = String(e.message || e);
        toast(msg || "准备失败。", "bad", 6500);
        setBars(null, null, 0, msg || "准备失败。");
      } finally {
        startBtn.disabled = false;
        pickFilesBtn.disabled = false;
        libSel.disabled = !!lockLibrary;
        libNewBtn.disabled = false;
        libCreateBtn.disabled = false;
        cancelBtn.style.display = "none";
        stopPolling();
        currentTaskId = "";
        importing = false;
        importCanceled = false;
      }
    };

    const needLabel =
      need === "import"
          ? "提示：这里只负责把 PDF 离线导入到本地库（不生成范文片段）。导入后可去“写作规范 → 引用核查”。"
        : need === "cite"
          ? "要用“引用写法”需要先准备引用证据（可选项会更慢）。"
          : "准备完成后，“找差距/对齐润色”会自动出现范文片段（证据）。";

    function syncReadyHint() {
      const st = state.libraryStatus || {};
      const ragOk = !!st.rag_index;
      if (!state.library) {
        readyHint.textContent = isImportOnly ? "请先选择/创建库。" : "请先选择/创建范文库。";
        goBtn.style.display = "none";
        startBtn.textContent = isImportOnly ? "开始导入" : "一键准备（导入 + 生成证据）";
        return;
      }
      if (isImportOnly) {
        const lib = libraryByName(state.library);
        const n = lib && lib.pdf_import_count != null ? Number(lib.pdf_import_count) : 0;
        if (Number.isFinite(n) && n > 0) {
          readyHint.textContent = `✅ 已导入 ${fmtCount(n)} 个 PDF。可继续导入更多参考文献原文 PDF（可选）。`;
          goBtn.style.display = "";
          goBtn.textContent = "去引用核查";
          startBtn.textContent = "导入更多（可选）";
        } else {
        readyHint.textContent = "导入你的参考文献原文 PDF 到本地库（离线）。导入后可在“写作规范”里开始引用核查。";
          goBtn.style.display = "none";
          startBtn.textContent = "开始导入";
        }
        return;
      }
      if (ragOk) {
        readyHint.textContent = "✅ 已检测到范文片段：你可以直接开始写作（找差距/对齐润色）。如要新增范文，再选择文件并点击“更新证据”。";
        goBtn.style.display = "";
        startBtn.textContent = "更新证据（可选）";
      } else {
        readyHint.textContent = "第一次需要：导入同领域 PDF → 生成范文片段。完成后写作过程会显示“参考哪段范文/哪里不像/怎么改更像”。";
        goBtn.style.display = "none";
        startBtn.textContent = "一键准备（导入 + 生成证据）";
      }
    }

    goBtn.onclick = () => {
      closeModal();
      resumeAfterReady();
    };

    const body = el(
      "div",
      { class: "grid", style: "gap:14px" },
      el(
        "div",
        { class: "muted" },
        isImportOnly
          ? "把你论文里引用到的“原文论文 PDF”导入到本地库。不会生成范文片段，只用于引用核查（白箱可追溯）。"
          : "你只需要把同领域的范文 PDF 选进来。软件会在本地生成“可引用的范文片段（证据）”，之后写作过程就能白箱对照。"
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, isImportOnly ? "1) 选择参考文献库" : "1) 选择范文库"),
        el("div", { class: "row" }, libSel, libNewBtn, libName, libCreateBtn),
        libHint
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, isImportOnly ? "2) 导入参考文献原文 PDF" : "2) 导入范文 PDF（可选：新增范文时）"),
        dropZone,
        el("div", { class: "row" }, pickFilesBtn, selectedInfo),
        el(
          "div",
          { class: "muted" },
          isImportOnly
            ? "建议：尽量覆盖你论文中引用到的参考文献原文 PDF（越全，“未找到原文 PDF” 越少）。"
            : "建议：50–100 篇 PDF，尽量同领域/同期刊/同风格。越“同风格”，对齐越像。"
          )
        ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, isImportOnly ? "3) 开始导入（离线）" : "3) 一键准备（离线）"),
        readyHint,
        el(
          "div",
          { class: "row" },
          goBtn,
          startBtn,
          isImportOnly ? null : el("label", { class: "row", style: "gap:8px" }, includeCite, el("span", { class: "muted" }, "同时准备引用证据（可选）")),
          cancelBtn
        ),
        el("div", { class: "muted" }, needLabel),
        importedInfo,
        el("div", { class: "label", style: "margin-top:10px" }, "导入进度"),
        importBar,
        importText,
        isImportOnly ? null : el("div", { class: "label", style: "margin-top:10px" }, "准备进度"),
        isImportOnly ? null : buildBar,
        isImportOnly ? null : buildText
      ),
      pdfInput,
    );

    syncLibSelOptions();
    // If no library selected but we have exactly one existing library, auto-select it.
    if (!state.library) {
      const names = libraryNames();
      if (names.length === 1) {
        state.library = names[0];
        setSavedLibraryForRoute(route(), state.library);
        updateGlobalLibraryUI();
        libSel.value = state.library;
      } else if (names.length > 1) {
        libSel.value = names[0];
      }
    }

    if (lockLibrary && presetLibrary) {
      libSel.disabled = true;
      libNewBtn.style.display = "none";
      libName.style.display = "none";
      libCreateBtn.style.display = "none";
      libHint.textContent = isImportOnly
        ? "你正在导入参考文献原文 PDF：导入完成后即可在写作规范里引用核查（白箱可追溯）。"
        : "你正在准备当前范文专题库：导入范文 PDF → 生成范文片段 → 在写作里随时引用（白箱可追溯）。";
    }

    syncImportedCount().catch(() => {});
    syncReadyHint();
    refreshLibraryStatus()
      .catch(() => {})
      .finally(() => syncReadyHint());

    openModal(title, body, {
      onClose: () => {
        stopPolling();
        try {
          pdfInput.remove();
        } catch {}
      },
    });
  }

  function humanTaskStatus(status) {
    const s = String(status || "").trim().toLowerCase();
    if (s === "running") return "进行中";
    if (s === "done") return "完成";
    if (s === "failed") return "失败";
    if (s === "canceled") return "已取消";
    return String(status || "—");
  }

  function humanTaskStage(stage) {
    const s = String(stage || "").trim().toLowerCase();
    const map = {
      starting: "准备中",
      pdf_extract: "抽取 PDF 文本",
      semantic_embed: "提取写作特征",
      syntax: "句法统计（更准的句式分析）",
      rag_extract: "切分范文片段",
      rag_embed: "构建范文对照证据",
      rag_done: "范文对照证据完成",
      materials_scan: "扫描范文（素材库）",
      materials_doc: "结构化范文（素材库）",
      materials_done: "素材库完成",
      cite_extract: "抽取引用信息",
      cite_embed: "构建引用证据",
      cite_index: "整理引用证据",
      cite_done: "引用证据完成",
      audit_extract: "读取论文 PDF",
      audit_split: "切分句子",
      audit_align: "检索范文证据（对齐度）",
      audit_style: "检查句式/模板/冗余",
      llm_sentence: "LLM 逐句对齐诊断",
      llm_outline: "LLM 章节结构诊断",
      llm_cite: "LLM 引用写法诊断",
      citecheck_embed: "构建核查向量",
      citecheck_papers_index: "索引参考文献原文 PDF",
      citecheck_para: "抽取原文证据段落",
      citecheck_checking: "核查引用",
      citecheck_llm: "大模型判定",
      citecheck_missing_scan: "扫描缺失原文",
      citecheck_missing_oa: "联网查 DOI / OA",
      citecheck_missing_download: "下载 OA PDF",
    };
    return map[s] || String(stage || "—");
  }

  function formatIndexChips(status) {
    const box = $("#indexChips");
    clear(box);
    if (!status || !state.library) return;

    const ragOk = !!status.rag_index;
    box.appendChild(
      el(
        "button",
        {
          class: "chip " + (ragOk ? "ok" : "bad"),
          type: "button",
          title: ragOk ? "范文片段已就绪" : "点击一键准备范文库（生成范文片段）",
          onclick: () => openPrepWizard({ need: "rag" }),
        },
        ragOk ? "✅ 范文片段就绪" : "⚠️ 准备范文库"
      )
    );

    const citeOk = !!status.cite_index;
    box.appendChild(
      el(
        "button",
        {
          class: "chip " + (citeOk ? "ok" : "warn"),
          type: "button",
          title: citeOk ? "引用写法已准备好" : "点击一键准备引用写法（可选）",
          onclick: () => openPrepWizard({ need: "cite" }),
        },
        citeOk ? "✅ 引用写法就绪" : "＋ 引用写法（可选）"
      )
    );
  }

  async function refreshLibraryStatus() {
    if (!state.library) {
      state.libraryStatus = null;
      formatIndexChips(null);
      return;
    }
    const st = await apiGet(`/api/library/status?library=${encodeURIComponent(state.library)}`);
    state.libraryStatus = st || null;
    formatIndexChips(st);
    return st;
  }

  function renderHeader(title, subtitle) {
    $("#routeTitle").textContent = title;
    $("#routeSubtitle").textContent = subtitle || "";
  }

  function openModal(title, bodyNode) {
    const opts = arguments.length >= 3 ? arguments[2] : null;
    state.modalOnClose = opts && typeof opts.onClose === "function" ? opts.onClose : null;
    const modalClass = opts && typeof opts.modalClass === "string" ? opts.modalClass : "";

    // Reset modal class between openings (prevents "sticky" layout issues).
    try {
      const modal = document.querySelector("#modalBackdrop .modal");
      if (modal) {
        if (state.modalClass) modal.classList.remove(...String(state.modalClass).split(/\s+/).filter(Boolean));
        if (modalClass) modal.classList.add(...String(modalClass).split(/\s+/).filter(Boolean));
      }
    } catch {}
    state.modalClass = modalClass;
    $("#modalTitle").textContent = title;
    const body = $("#modalBody");
    clear(body);
    body.appendChild(bodyNode);
    $("#modalBackdrop").classList.remove("hidden");
  }

  function closeModal() {
    const onClose = state.modalOnClose;
    state.modalOnClose = null;
    try {
      const modal = document.querySelector("#modalBackdrop .modal");
      if (modal && state.modalClass) modal.classList.remove(...String(state.modalClass).split(/\s+/).filter(Boolean));
    } catch {}
    state.modalClass = "";
    $("#modalBackdrop").classList.add("hidden");
    $("#modalTitle").textContent = "—";
    clear($("#modalBody"));
    try {
      if (typeof onClose === "function") onClose();
    } catch {}
  }

  const LLM_TEST_CACHE_KEY = "aiw.llmTestCache";
  const LLM_TEST_MAX_AGE_MS = 6 * 60 * 60 * 1000; // 6 hours

  function llmConfigured(api) {
    const st = api && typeof api === "object" ? api : {};
    const hasKey = !!st.api_key_present;
    const hasUrl = !!String(st.base_url || "").trim();
    const hasModel = !!String(st.model || "").trim();
    return !!(hasKey && hasUrl && hasModel);
  }

  function readLlmTestCache() {
    try {
      const raw = localStorage.getItem(LLM_TEST_CACHE_KEY) || "";
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (!obj || typeof obj !== "object") return null;
      const base_url = String(obj.base_url || "").trim();
      const model = String(obj.model || "").trim();
      const ok = !!obj.ok;
      const http = Number(obj.http || 0) || 0;
      const error = String(obj.error || "").trim();
      const at = Number(obj.at || 0) || 0;
      if (!base_url || !model) return null;
      return { base_url, model, ok, http, error, at };
    } catch {
      return null;
    }
  }

  function writeLlmTestCache(data) {
    try {
      if (!data || typeof data !== "object") return;
      const base_url = String(data.base_url || "").trim();
      const model = String(data.model || "").trim();
      if (!base_url || !model) return;
      const payload = {
        ok: !!data.ok,
        http: Number(data.http || 0) || 0,
        base_url,
        model,
        error: String(data.error || "").trim(),
        at: Number.isFinite(Number(data.at)) && Number(data.at) > 0 ? Number(data.at) : Date.now(),
      };
      localStorage.setItem(LLM_TEST_CACHE_KEY, JSON.stringify(payload));
    } catch {}
  }

  function getLlmTestForApi(api) {
    if (!llmConfigured(api)) return null;
    const t = readLlmTestCache();
    if (!t) return null;
    const base_url = String((api && api.base_url) || "").trim();
    const model = String((api && api.model) || "").trim();
    if (!base_url || !model) return null;
    if (t.base_url !== base_url) return null;
    if (t.model !== model) return null;
    try {
      const age = Date.now() - Number(t.at || 0);
      if (!Number.isFinite(age) || age < 0) return t;
      if (age > LLM_TEST_MAX_AGE_MS) return null;
    } catch {}
    return t;
  }

  function llmUsable(api) {
    const t = getLlmTestForApi(api);
    return !!(t && t.ok);
  }

  async function refreshLLMStatus() {
    let api = null;
    try {
      api = await apiGet("/api/llm/status");
    } catch {
      try {
        api = await apiGet("/api/llm/api/status");
      } catch {
        api = null;
      }
    }
    state.llmApi = api;
    state.llm = null;
    state.llmTest = getLlmTestForApi(api);

    const badge = $("#llmBadge");
    if (!badge) return;

    let text = "润色：";
    if (!api) text += "检查中…";
    else if (!llmConfigured(api)) text += "未配置";
    else if (state.llmTest && state.llmTest.ok) text += "可用";
    else if (state.llmTest && !state.llmTest.ok) text += "上次失败";
    else text += "待测试";

    badge.textContent = text;
    badge.classList.remove("ok", "warn", "bad");
    if (!api) badge.classList.add("warn");
    else if (!llmConfigured(api)) badge.classList.add("warn");
    else if (state.llmTest && state.llmTest.ok) badge.classList.add("ok");
    else if (state.llmTest && !state.llmTest.ok) badge.classList.add("warn");
    else badge.classList.add("warn");
  }

  async function copyText(s) {
    try {
      await navigator.clipboard.writeText(String(s || ""));
      toast("已复制到剪贴板。");
    } catch {
      toast("复制失败：浏览器不允许（可手动复制）。", "bad");
    }
  }

  function downloadText(filename, text, mime) {
    const name = String(filename || "").trim() || "download.txt";
    const type = String(mime || "").trim() || "text/plain;charset=utf-8";
    try {
      const blob = new Blob([String(text || "")], { type });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast("下载失败：" + String(e.message || e), "bad", 6500);
    }
  }

  function normPdfPage(page) {
    const p = Number(page || 0);
    if (!Number.isFinite(p) || p <= 0) return 1;
    return Math.max(1, Math.round(p));
  }

  function buildLibraryPdfUrl(library, pdfRel, page) {
    const lib = encodeURIComponent(String(library || "").trim());
    const pdf = encodeURIComponent(String(pdfRel || "").trim());
    const p = normPdfPage(page);
    return `/api/library/pdf?library=${lib}&pdf=${pdf}#page=${p}`;
  }

  function buildLibraryPdfPngUrl(library, pdfRel, page) {
    const lib = encodeURIComponent(String(library || "").trim());
    const pdf = encodeURIComponent(String(pdfRel || "").trim());
    const p = normPdfPage(page);
    return `/api/library/pdf_page.png?library=${lib}&pdf=${pdf}&page=${p}`;
  }

  function buildDraftPdfUrl(draftId, page) {
    const did = encodeURIComponent(String(draftId || "").trim());
    const p = normPdfPage(page);
    return `/api/norms/citecheck/draft_pdf?draft_id=${did}#page=${p}`;
  }

  function buildDraftPdfPngUrl(draftId, page) {
    const did = encodeURIComponent(String(draftId || "").trim());
    const p = normPdfPage(page);
    return `/api/norms/citecheck/draft_page.png?draft_id=${did}&page=${p}`;
  }

  function buildAuditPaperPdfUrl(paperId, page) {
    const pid = encodeURIComponent(String(paperId || "").trim());
    const p = normPdfPage(page);
    return `/api/audit/paper_pdf?paper_id=${pid}#page=${p}`;
  }

  function buildAuditPaperPngUrl(paperId, page) {
    const pid = encodeURIComponent(String(paperId || "").trim());
    const p = normPdfPage(page);
    return `/api/audit/paper_page.png?paper_id=${pid}&page=${p}`;
  }

  function openPdfPreview(title, pdfUrl, imgUrl, opts = {}) {
    const u = String(pdfUrl || "").trim();
    if (!u) return toast("PDF 预览失败：缺少 URL。", "bad");

    const openTab = el(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: () => {
          try {
            window.open(u, "_blank");
          } catch {}
        },
      },
      "新标签打开"
    );
    const openExternal =
      opts && typeof opts.onExternalOpen === "function"
        ? el(
            "button",
            {
              class: "btn",
              type: "button",
              onclick: () => {
                try {
                  opts.onExternalOpen();
                } catch {}
              },
            },
            "外部打开"
          )
        : null;

    const imgSrc = String(imgUrl || "").trim();
    const img = el("img", { class: "pdf-img", src: imgSrc || u, alt: String(title || "PDF 预览"), loading: "lazy" });
    img.onerror = () => {
      try {
        img.replaceWith(el("div", { class: "muted" }, "预览加载失败：请点“新标签打开”或“外部打开”。"));
      } catch {}
    };
    const body = el(
      "div",
      { class: "grid", style: "gap:12px" },
      el("div", { class: "row", style: "justify-content:flex-end" }, openTab, openExternal),
      img,
      el("div", { class: "muted" }, "提示：这里是页面截图（更稳定）。需要全文可点“新标签打开”或“外部打开”。")
    );
    openModal(String(title || "PDF 预览"), body, { modalClass: "pdf" });
  }

  function openLibraryPdfPreview(library, pdfRel, page) {
    const lib = String(library || "").trim();
    const pdf = String(pdfRel || "").trim();
    if (!lib || !pdf) return toast("缺少 PDF 信息。", "bad");
    const p = normPdfPage(page);
    const pdfUrl = buildLibraryPdfUrl(lib, pdf, p);
    const imgUrl = buildLibraryPdfPngUrl(lib, pdf, p);
    openPdfPreview(`${pdf} · p${p}`, pdfUrl, imgUrl, {
      onExternalOpen: async () => {
        try {
          await apiPost("/api/library/open_pdf", { library: lib, pdf });
        } catch (e) {
          toast(String(e.message || e), "bad");
        }
      },
    });
  }

  function openDraftPdfPreview(draftId, filename, page) {
    const did = String(draftId || "").trim();
    if (!did) return toast("缺少论文 PDF。", "bad");
    const p = normPdfPage(page);
    const name = String(filename || "").trim() || "论文.pdf";
    const pdfUrl = buildDraftPdfUrl(did, p);
    const imgUrl = buildDraftPdfPngUrl(did, p);
    openPdfPreview(`${name} · p${p}`, pdfUrl, imgUrl, {
      onExternalOpen: async () => {
        try {
          await apiPost("/api/norms/citecheck/open_draft", { draft_id: did });
        } catch (e) {
          toast(String(e.message || e), "bad");
        }
      },
    });
  }

  function openAuditPaperPreview(paperId, filename, page) {
    const pid = String(paperId || "").trim();
    if (!pid) return toast("缺少论文 PDF。", "bad");
    const p = normPdfPage(page);
    const name = String(filename || "").trim() || "论文.pdf";
    const pdfUrl = buildAuditPaperPdfUrl(pid, p);
    const imgUrl = buildAuditPaperPngUrl(pid, p);
    openPdfPreview(`${name} · p${p}`, pdfUrl, imgUrl, {
      onExternalOpen: async () => {
        try {
          await apiPost("/api/audit/open_paper", { paper_id: pid });
        } catch (e) {
          toast(String(e.message || e), "bad");
        }
      },
    });
  }

  function extractScaffolds(text) {
    const s = String(text || "");
    const out = [];
    const re = /scaffold\s*:\s*["“]([^"”]{1,120})["”]/gi;
    let m = null;
    while ((m = re.exec(s))) {
      const p = String(m[1] || "").trim();
      if (p && !out.includes(p)) out.push(p);
      if (out.length >= 3) break;
    }
    return out;
  }

  function stripScaffoldPrefix(text) {
    const s = String(text || "").trim();
    const re = /^\s*scaffold\s*:\s*["“][^"”]{1,120}["”]\s*/i;
    return s.replace(re, "").trim();
  }

  function highlightNeedle(text, needle) {
    const s = String(text || "");
    const n = String(needle || "").trim();
    if (!s || !n) return document.createTextNode(s);
    let i = s.indexOf(n);
    if (i < 0) return document.createTextNode(s);
    const frag = document.createDocumentFragment();
    let pos = 0;
    while (i >= 0) {
      frag.appendChild(document.createTextNode(s.slice(pos, i)));
      frag.appendChild(el("mark", { class: "mark" }, n));
      pos = i + n.length;
      i = s.indexOf(n, pos);
    }
    frag.appendChild(document.createTextNode(s.slice(pos)));
    return frag;
  }

  function highlightNeedles(text, needles) {
    const s = String(text || "");
    const arr = Array.isArray(needles) ? needles.map((x) => String(x || "").trim()).filter(Boolean) : [];
    if (!s || !arr.length) return document.createTextNode(s);
    const uniq = [];
    for (const x of arr) if (x && !uniq.includes(x)) uniq.push(x);
    if (!uniq.length) return document.createTextNode(s);

    const frag = document.createDocumentFragment();
    let pos = 0;
    while (pos < s.length) {
      let bestIdx = -1;
      let bestNeedle = "";
      for (const n of uniq) {
        const i = s.indexOf(n, pos);
        if (i < 0) continue;
        if (bestIdx < 0 || i < bestIdx || (i === bestIdx && n.length > bestNeedle.length)) {
          bestIdx = i;
          bestNeedle = n;
        }
      }
      if (bestIdx < 0) break;
      if (bestIdx > pos) frag.appendChild(document.createTextNode(s.slice(pos, bestIdx)));
      frag.appendChild(el("mark", { class: "mark" }, bestNeedle));
      pos = bestIdx + bestNeedle.length;
    }
    if (pos < s.length) frag.appendChild(document.createTextNode(s.slice(pos)));
    return frag;
  }

  function isCJKText(s) {
    return /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s || ""));
  }

  function normalizeEvidenceText(s) {
    return String(s || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function splitSentencesLoose(text) {
    const s = normalizeEvidenceText(text);
    if (!s) return [];
    // Split by common sentence enders without lookbehind (better browser compatibility).
    const out = [];
    let buf = "";
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      buf += ch;
      if ("。！？.!?".includes(ch)) {
        const next = s[i + 1] || "";
        if (next && /\s/.test(next)) {
          const t = buf.trim();
          if (t) out.push(t);
          buf = "";
          while (i + 1 < s.length && /\s/.test(s[i + 1])) i++;
          if (out.length >= 6) break;
        }
      }
    }
    const tail = buf.trim();
    if (tail && out.length < 6) out.push(tail);
    return out.length ? out : [s];
  }

  function suggestScaffoldsFromEvidence(text, max = 3) {
    const s = normalizeEvidenceText(text);
    if (!s) return [];
    const cjk = isCJKText(s);
    const sents = splitSentencesLoose(s);
    const out = [];

    function pushCandidate(x) {
      const p = String(x || "").trim();
      if (!p) return;
      if (p.length < (cjk ? 6 : 10)) return;
      if (p.length > (cjk ? 24 : 60)) return;
      if (/\d/.test(p)) return; // avoid author-year / numbers
      if (out.includes(p)) return;
      out.push(p);
    }

    for (const sent of sents) {
      if (out.length >= max) break;
      const t = String(sent || "").trim();
      if (!t) continue;

      if (cjk) {
        const m = t.match(/^.{6,24}/);
        if (m) pushCandidate(m[0].replace(/[，,;；:：。！？.!?]+$/g, ""));
        continue;
      }

      const words = t.split(/\s+/).filter(Boolean);
      for (const k of [6, 8, 10]) {
        if (out.length >= max) break;
        if (words.length < 4) continue;
        const pick = words.slice(0, Math.min(k, words.length)).join(" ");
        pushCandidate(pick.replace(/[，,;；:：。！？.!?]+$/g, ""));
      }
    }

    if (!out.length) {
      if (cjk) pushCandidate(s.slice(0, 16));
      else pushCandidate(s.split(/\s+/).slice(0, 8).join(" "));
    }

    return out.slice(0, max);
  }

  function tokenizeForDiff(text) {
    const s = String(text || "");
    if (!s) return [];
    try {
      if (typeof Intl !== "undefined" && typeof Intl.Segmenter === "function") {
        const seg = new Intl.Segmenter(undefined, { granularity: "word" });
        const parts = [];
        for (const it of seg.segment(s)) parts.push(it.segment);
        if (parts.join("") === s) return parts;
      }
    } catch {}

    // Fallback: keep spaces, group ascii words, keep CJK chars.
    const out = [];
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (/\s/.test(ch)) {
        let j = i + 1;
        while (j < s.length && /\s/.test(s[j])) j++;
        out.push(s.slice(i, j));
        i = j;
        continue;
      }
      if (/[A-Za-z0-9_]/.test(ch)) {
        let j = i + 1;
        while (j < s.length && /[A-Za-z0-9_]/.test(s[j])) j++;
        out.push(s.slice(i, j));
        i = j;
        continue;
      }
      out.push(ch);
      i++;
    }
    return out;
  }

  function diffTokens(aTokens, bTokens) {
    const a = Array.isArray(aTokens) ? aTokens : [];
    const b = Array.isArray(bTokens) ? bTokens : [];
    const n = a.length;
    const m = b.length;
    if (!n && !m) return [];
    // Guard: avoid heavy DP for very long inputs.
    if (n * m > 650000) return null;

    const cols = m + 1;
    const dp = new Uint16Array((n + 1) * (m + 1));
    for (let i = n - 1; i >= 0; i--) {
      const row = i * cols;
      const rowDown = (i + 1) * cols;
      for (let j = m - 1; j >= 0; j--) {
        const idx = row + j;
        if (a[i] === b[j]) {
          dp[idx] = dp[rowDown + j + 1] + 1;
        } else {
          const down = dp[rowDown + j];
          const right = dp[row + j + 1];
          dp[idx] = down >= right ? down : right;
        }
      }
    }

    const ops = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) {
        ops.push({ t: "eq", v: a[i] });
        i++;
        j++;
        continue;
      }
      const down = dp[(i + 1) * cols + j];
      const right = dp[i * cols + (j + 1)];
      if (down >= right) {
        ops.push({ t: "del", v: a[i] });
        i++;
      } else {
        ops.push({ t: "ins", v: b[j] });
        j++;
      }
    }
    while (i < n) {
      ops.push({ t: "del", v: a[i++] });
    }
    while (j < m) {
      ops.push({ t: "ins", v: b[j++] });
    }

    // Merge adjacent ops for rendering.
    const merged = [];
    for (const op of ops) {
      const last = merged.length ? merged[merged.length - 1] : null;
      if (last && last.t === op.t) last.v += op.v;
      else merged.push({ t: op.t, v: op.v });
    }
    return merged;
  }

  function renderDiffView(originalText, revisedText) {
    const a = String(originalText || "");
    const b = String(revisedText || "");
    if (!a && !b) return el("div", { class: "muted" }, "空文本。");

    const ops = diffTokens(tokenizeForDiff(a), tokenizeForDiff(b));
    if (ops == null) return el("div", { class: "muted" }, "文本较长：已隐藏差异高亮（仍可直接复制/替换）。");

    const left = document.createDocumentFragment();
    const right = document.createDocumentFragment();
    for (const op of ops) {
      if (!op || !op.t) continue;
      if (op.t === "eq") {
        left.appendChild(document.createTextNode(op.v));
        right.appendChild(document.createTextNode(op.v));
      } else if (op.t === "del") {
        left.appendChild(el("del", { class: "del" }, op.v));
      } else if (op.t === "ins") {
        right.appendChild(el("ins", { class: "ins" }, op.v));
      }
    }

    return el(
      "div",
      { class: "diff-grid" },
      el("div", { class: "diff-panel" }, el("div", { class: "muted" }, "原文"), el("div", { class: "diff-text" }, left)),
      el("div", { class: "diff-panel" }, el("div", { class: "muted" }, "润色"), el("div", { class: "diff-text" }, right))
    );
  }

  function exemplarList(exemplars, opts = {}) {
    const { library, onInsertScaffold } = opts;
    const list = el("div", { class: "list" });
    if (!exemplars || !exemplars.length) {
      list.appendChild(el("div", { class: "muted" }, "没有检索到范文片段。"));
      return list;
    }
    for (const ex of exemplars) {
      const scaffolds = suggestScaffoldsFromEvidence(ex.text || "", 3);
      const head = el(
        "div",
        { class: "item-header" },
        el("div", null, el("span", { class: "badge mono" }, (ex.id ? `${ex.id} ` : "") + `${Math.round((ex.score || 0) * 100)}%`)),
        el(
          "div",
          { class: "row" },
          ex.pdf ? el("span", { class: "muted mono" }, `${ex.pdf}#p${ex.page || 0}`) : el("span", { class: "muted" }, "—"),
          ex.pdf
            ? el(
                "button",
                {
                  class: "btn btn-small",
                  type: "button",
                  onclick: () => openLibraryPdfPreview(library, ex.pdf, ex.page || 1),
                },
                "预览"
              )
            : null
        )
      );
      const scaffoldRow = scaffolds.length
        ? el(
            "div",
            { class: "row", style: "gap:8px; align-items:flex-start; margin-top:10px" },
            el("span", { class: "muted" }, "可复用句式"),
            ...scaffolds.map((p) =>
              el(
                "button",
                {
                  class: "chip scaffold",
                  type: "button",
                  title: onInsertScaffold ? "点击插入到输入框（并复制）" : "点击复制",
                  onclick: async () => {
                    try {
                      if (typeof onInsertScaffold === "function") onInsertScaffold(p);
                    } catch {}
                    await copyText(p);
                  },
                },
                p
              )
            )
          )
        : null;

      list.appendChild(el("div", { class: "item" }, head, scaffoldRow, el("div", { class: "quote" }, ex.text || "")));
    }
    return list;
  }

  const HOME_SAMPLE_TEXT =
    "This paper studies how risk premia vary with market conditions. We document strong cross-sectional dispersion and show that a parsimonious factor model explains most of the variation.";

  function pageHome() {
    renderHeader("开始", "把句式写得更像范文：哪里不像 → 参考哪段范文 → 怎么改更像（可追溯、有背书）。");

    const root = el("div", { class: "home" });
    const inner = el("div", { class: "home-inner" });

    const hero = el(
      "div",
      { class: "home-hero" },
      el("div", { class: "home-title" }, "TopHumanWriting"),
      el(
        "div",
        { class: "home-sub" },
        "像顶级论文/研报那样写：用同领域“范文库（范文 PDF）”做背书，每条建议都附证据（PDF + 页码 + 原文片段）。"
      ),
      el("div", { class: "home-kicker" }, "不需要懂模型：先离线“找差距”，需要生成润色时再配置 API（默认温度 0，尽量不发散）。")
    );

    function apiConfigured() {
      return llmConfigured(state.llmApi);
    }

    function apiUsable() {
      return llmUsable(state.llmApi);
    }

    function apiTestStatus() {
      return getLlmTestForApi(state.llmApi);
    }

    function ragReady() {
      return !!(state.library && state.libraryStatus && state.libraryStatus.rag_index);
    }

    function getLibByName(name) {
      const n = String(name || "").trim();
      if (!n) return null;
      const libs = Array.isArray(state.libraries) ? state.libraries : [];
      for (const it of libs) {
        const nm = String((it && it.name) || "").trim();
        if (nm === n) return it;
      }
      return null;
    }

    function isSampleLib(libOrName) {
      const name = typeof libOrName === "string" ? libOrName : libOrName && typeof libOrName === "object" ? libOrName.name : "";
      const n = String(name || "").trim().toLowerCase();
      if (!n) return false;
      if (n.includes("demo") || n.includes("smoke") || n.includes("sample") || n.includes("test") || n.includes("ux")) return true;
      if (!libOrName || typeof libOrName !== "object") return false;
      const src = String(libOrName.rag_pdf_root || libOrName.pdf_import_root || "");
      return src.toLowerCase().includes("sample_pdfs");
    }

    const modeKey = "aiw.homeMode";
    const savedMode = localStorage.getItem(modeKey) || "polish";
    const hasExplicitMode = !!localStorage.getItem(modeKey);
    let mode = savedMode === "scan" || savedMode === "polish" || savedMode === "cite" ? savedMode : "polish";
    if (!hasExplicitMode) {
      // First-run default: if LLM isn't verified yet, start from the offline "find gaps" tool.
      if (!apiUsable()) mode = "scan";
    }

    const text = el("textarea", { class: "textarea home-textarea", placeholder: "粘贴你要改的句子/段落（中英混合可）…" });
    const homeDraftKey = "aiw.homeDraft";
    text.value = localStorage.getItem(homeDraftKey) || "";
    text.addEventListener("input", () => localStorage.setItem(homeDraftKey, text.value || ""));

    const sampleBtn = el("button", { class: "chip", type: "button" }, "填入示例");
    const clearBtn = el("button", { class: "chip", type: "button" }, "清空");
    const helpBtn = el("button", { class: "chip", type: "button" }, "新手教程");
    const auditBtn = el("button", { class: "chip", type: "button" }, "全稿体检（PDF）");
    const prepBtn = el("button", { class: "chip", type: "button" }, "准备/更新范文库");
    const manageBtn = el("button", { class: "chip", type: "button" }, "管理范文库");

    sampleBtn.onclick = () => {
      text.value = HOME_SAMPLE_TEXT;
      localStorage.setItem(homeDraftKey, text.value);
      try {
        text.focus();
      } catch {}
      toast("已填入示例文本。");
    };
    clearBtn.onclick = () => {
      text.value = "";
      localStorage.setItem(homeDraftKey, "");
      toast("已清空。");
    };

    helpBtn.onclick = () => setRoute("help");
    auditBtn.onclick = () => setRoute("audit");
    prepBtn.onclick = () => openPrepWizard({ need: "rag" });
    manageBtn.onclick = () => setRoute("library");

    const modeBtns = {};

    const runBtn = el("button", { class: "btn btn-primary home-primary", type: "button" }, "开始对齐润色");
    const runHint = el("div", { class: "home-hint" }, "—");
    const calloutBox = el("div");

    const badgesRow = el("div", { class: "home-badges" });
    function syncBadges() {
      clear(badgesRow);
      const ragOk = ragReady();
      const apiCfg = apiConfigured();
      const apiOk = apiUsable();
      const apiT = apiTestStatus();
      const citeOk = !!(state.libraryStatus && state.libraryStatus.cite_index);
      const cur = state.library ? getLibByName(state.library) : null;
      const sample = cur ? isSampleLib(cur) : false;

      if (!state.library) badgesRow.appendChild(el("span", { class: "badge bad" }, "范文专题库：未选择"));
      else badgesRow.appendChild(el("span", { class: "badge " + (ragOk ? "good" : "bad") }, ragOk ? "范文专题库：已准备" : "范文专题库：未准备"));
      if (sample) badgesRow.appendChild(el("span", { class: "badge warn" }, "示例库"));
      if (mode === "polish") {
        const cls = !apiCfg ? "badge warn" : apiOk ? "badge good" : apiT && !apiT.ok ? "badge bad" : "badge warn";
        const txt = !apiCfg ? "润色：未配置" : apiOk ? "润色：可用" : apiT && !apiT.ok ? "润色：不可用" : "润色：待测试";
        badgesRow.appendChild(el("span", { class: cls }, txt));
      } else if (mode === "cite") {
        badgesRow.appendChild(el("span", { class: "badge " + (citeOk ? "good" : "warn") }, citeOk ? "引用：已就绪" : "引用：可选"));
      } else {
        badgesRow.appendChild(el("span", { class: "badge" }, "找差距：离线"));
      }
    }

    function updateRunCopy() {
      const m = String(mode || "polish");
      if (m === "scan") {
        runBtn.textContent = ragReady() ? "开始找差距（离线）" : "先准备范文库";
        text.placeholder = "粘贴你的正文（可很长；会自动拆句）…";
        runHint.textContent = ragReady()
          ? "只做对照：不生成内容。会标出“最不像范文”的句子，并给出范文证据。"
          : "先准备一次范文库（导入同领域 PDF），之后才能逐句对照并查看范文证据。";
        syncBadges();
        syncCallout();
        return;
      }
      if (m === "cite") {
        runBtn.textContent = state.libraryStatus && state.libraryStatus.cite_index ? "检索引用写法" : "先准备引用证据（可选）";
        text.placeholder = "输入一个引用问题/关键词（例如：consistent with / 据…）…";
        runHint.textContent = state.libraryStatus && state.libraryStatus.cite_index
          ? "可选功能：从范文中检索常见引用表达（含证据与参考文献线索）。"
          : "引用写法是可选功能：需要先准备范文库，然后（可选）一键抽取引用证据。";
        syncBadges();
        syncCallout();
        return;
      }
      if (!ragReady()) runBtn.textContent = "先准备范文库";
      else if (!apiConfigured()) runBtn.textContent = "先配置润色设置";
      else runBtn.textContent = apiUsable() ? "开始对齐润色" : "先测试润色连接";
      text.placeholder = "粘贴你要改的句子/段落（中英混合可）…";
      runHint.textContent = apiUsable()
        ? "会输出：哪里不像 + 句式模板 + 轻改/中改，并附本次用到的范文证据（PDF+页码）。"
        : apiConfigured()
          ? "建议先到“润色设置”点一次“测试连接”。不想生成也没关系：可用“找差距/查看范文”做离线白箱对照。"
          : "不想生成也没关系：可用“找差距/查看范文”做离线白箱对照。";
      syncBadges();
      syncCallout();
    }

    function syncCallout() {
      clear(calloutBox);
      const m = String(mode || "polish");
      const hasLib = !!state.library;
      const ragOk = ragReady();
      const apiCfg = apiConfigured();
      const apiOk = apiUsable();
      const apiT = apiTestStatus();
      const citeOk = !!(state.libraryStatus && state.libraryStatus.cite_index);
      const cur = state.library ? getLibByName(state.library) : null;
      const sample = cur ? isSampleLib(cur) : false;

      let title = "";
      let desc = "";
      let primary = null;
      let secondary = null;

      if (!hasLib) {
        title = "先选择一个范文库";
        desc = "范文库 = 同领域顶级 PDF。先准备一次，后面每条建议都会带“证据”（PDF+页码+原文片段）。";
        primary = el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "创建并准备");
        secondary = el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页");
      } else if (!ragOk) {
        title = "范文库还没准备好";
        desc = "第一次需要导入同领域 PDF，并在本地生成“范文片段（证据）”。完成后才会出现“参考哪段范文/哪里不像/怎么改更像”。";
        primary = el(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            onclick: () => openPrepWizard({ need: "rag", library: state.library, lockLibrary: true }),
          },
          "一键准备"
        );
        secondary = sample
          ? el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "换成自己的范文库")
          : el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页");
      } else if (m === "polish" && !apiOk) {
        if (!apiCfg) {
          title = "还不能生成润色（需要配置大模型）";
          desc = "润色默认温度固定 0，尽量不发散；并会把范文片段（证据）一起引用出来，方便你“有法可依”。";
          primary = el("button", { class: "btn btn-primary", type: "button", onclick: () => setRoute("llm") }, "去润色设置");
          secondary = el("button", { class: "btn", type: "button", onclick: () => setMode("scan") }, "先用找差距（离线）");
        } else if (apiT && !apiT.ok) {
          title = "润色接口不可用（上次测试失败）";
          desc = "建议先到“润色设置”点击“测试连接”，或更换可用的 base_url / model。";
          primary = el("button", { class: "btn btn-primary", type: "button", onclick: () => setRoute("llm") }, "去润色设置");
          secondary = el("button", { class: "btn", type: "button", onclick: () => setMode("scan") }, "先用找差距（离线）");
        } else {
          title = "先测试一次润色接口（避免生成时失败）";
          desc = "你的配置已读取到，但还没做过连接测试。测试通过后再生成，会更顺滑。";
          primary = el("button", { class: "btn btn-primary", type: "button", onclick: () => setRoute("llm") }, "去测试连接");
          secondary = el("button", { class: "btn", type: "button", onclick: () => setMode("scan") }, "先用找差距（离线）");
        }
      } else if (m === "cite" && !citeOk) {
        title = "引用写法是可选项（需要先准备引用证据）";
        desc = "如果你想模仿顶级论文的引用表达，可以先抽取引用证据；不需要的话可先用找差距/对齐润色。";
        primary = el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "cite" }) }, "准备引用证据");
        secondary = el("button", { class: "btn", type: "button", onclick: () => setMode("scan") }, "先用找差距");
      }

      if (!title) return;
      calloutBox.appendChild(
        el(
          "div",
          { class: "callout" },
          el("div", null, el("div", { class: "callout-title" }, title), el("div", { class: "callout-desc" }, desc)),
          el("div", { class: "callout-actions" }, secondary, primary)
        )
      );
    }

    function setMode(next) {
      const m = String(next || "").trim().toLowerCase();
      if (m !== "scan" && m !== "polish" && m !== "cite") return;
      mode = m;
      localStorage.setItem(modeKey, mode);
      for (const [id, b] of Object.entries(modeBtns)) {
        if (!b) continue;
        b.classList.toggle("active", id === mode);
      }
      updateRunCopy();
    }

    function runTool(nextMode) {
      setMode(nextMode);
      // Guide users to setup steps before requiring input text.
      if (mode === "scan") {
        if (!state.library || !(state.libraryStatus && state.libraryStatus.rag_index)) {
          openPrepWizard({ need: "rag", resume: { route: "scan", autoKey: "aiw.scanAutoRun", autoValue: "1" } });
          toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
          return;
        }
      } else if (mode === "polish") {
        if (!apiConfigured()) {
          toast("对齐润色需要大模型：请先在“润色设置”里配置接口。", "bad", 6500);
          setRoute("llm");
          return;
        }
        if (!apiUsable()) {
          toast("润色接口尚未通过测试（或上次失败）：仍可尝试生成（已加重试），失败再去“润色设置”测试。", "bad", 6500);
        }
        if (!state.library || !(state.libraryStatus && state.libraryStatus.rag_index)) {
          openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
          toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
          return;
        }
      } else if (mode === "cite") {
        if (!state.library || !(state.libraryStatus && state.libraryStatus.cite_index)) {
          openPrepWizard({ need: "cite", resume: { route: "cite", autoKey: "aiw.citeAutoRun", autoValue: "1" } });
          toast("先准备范文库（可选：同时准备引用写法）。", "bad", 4500);
          return;
        }
      }

      const raw = (text.value || "").trim();
      if (!raw) return toast("请先粘贴文本。", "bad");

      if (mode === "scan") {
        state.scanDraft = raw;
        localStorage.setItem("aiw.scanDraft", state.scanDraft);
        localStorage.setItem("aiw.scanAutoRun", "1");
        return setRoute("scan");
      }
      if (mode === "polish") {
        state.polishDraft = raw;
        localStorage.setItem("aiw.polishDraft", state.polishDraft);
        localStorage.setItem("aiw.polishAutoRun", "generate");
        return setRoute("polish");
      }
      if (mode === "cite") {
        localStorage.setItem("aiw.citeQueryDraft", raw);
        localStorage.setItem("aiw.citeAutoRun", "1");
        return setRoute("cite");
      }
    }

    text.addEventListener("keydown", (e) => {
      if (!e) return;
      // Ctrl+Enter / Cmd+Enter: run last selected tool.
      const isMac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || "");
      const hot = isMac ? e.metaKey && e.key === "Enter" : e.ctrlKey && e.key === "Enter";
      if (hot) {
        e.preventDefault();
        runTool(mode);
      }
    });

    updateRunCopy();

    const homeLibSel = el("select", { class: "select" });
    function syncHomeLibSel() {
      clear(homeLibSel);
      homeLibSel.appendChild(el("option", { value: "" }, "— 选择范文专题库 —"));
      const mineGroup = el("optgroup", { label: "我的范文库" });
      const sampleGroup = el("optgroup", { label: "示例" });
      for (const it of librariesForRoute("home", state.libraries || [])) {
        const name = String((it && it.name) || "").trim();
        if (!name) continue;
        const opt = el("option", { value: name }, name);
        if (isSampleLib(it)) sampleGroup.appendChild(opt);
        else mineGroup.appendChild(opt);
      }
      if (mineGroup.children.length) homeLibSel.appendChild(mineGroup);
      if (sampleGroup.children.length) homeLibSel.appendChild(sampleGroup);
      homeLibSel.value = state.library || "";
    }
    syncHomeLibSel();
    homeLibSel.addEventListener("change", () => {
      state.library = homeLibSel.value || "";
      setSavedLibraryForRoute(route(), state.library);
      updateGlobalLibraryUI();
      render().catch(() => {});
    });

    function modePill(id, icon, label) {
      const b = el("button", { class: "pill mode" + (mode === id ? " active" : ""), type: "button" }, `${icon} ${label}`);
      b.onclick = () => setMode(id);
      modeBtns[id] = b;
      return b;
    }

    const modeRow = el(
      "div",
      { class: "home-modebar" },
      modePill("polish", "✨", "对齐润色"),
      modePill("scan", "🧭", "找差距"),
      modePill("cite", "🔖", "引用写法")
    );

    runBtn.onclick = () => runTool(mode);

    const topRow = el(
      "div",
      { class: "home-toprow" },
      el(
        "div",
        { class: "home-libpicker" },
        el("span", { class: "muted" }, "范文专题库"),
        homeLibSel
      ),
      el("div", { class: "chips" }, prepBtn, manageBtn)
    );

    const modeWrap = el("div", { class: "home-modewrap" }, modeRow);
    const statusRow = el("div", { class: "home-statusrow" }, badgesRow, runHint);
    const tipLine = el(
      "div",
      { class: "muted" },
      "提示：没准备范文库也没关系，会自动弹出“准备向导”。快捷键：Ctrl+Enter 运行当前工具。"
    );

    const actionsRow = el(
      "div",
      { class: "home-actionsbar" },
      el("div", { class: "chips" }, helpBtn, auditBtn, sampleBtn, clearBtn),
      el("div", { class: "home-cta" }, runBtn)
    );

    const stepsRow = el(
      "div",
      { class: "home-steps" },
      el("div", { class: "home-step" }, el("div", { class: "home-step-k" }, "1"), el("div", null, "选范文库（范文 PDF）")),
      el("div", { class: "home-step" }, el("div", { class: "home-step-k" }, "2"), el("div", null, "粘贴你的文本")),
      el("div", { class: "home-step" }, el("div", { class: "home-step-k" }, "3"), el("div", null, "找差距 / 对齐润色"))
    );

    const inputCard = el(
      "div",
      { class: "card home-panel" },
      stepsRow,
      topRow,
      calloutBox,
      modeWrap,
      el("div", { class: "home-inputwrap" }, text),
      actionsRow,
      statusRow,
      tipLine
    );

    const libsSection = (() => {
      const libsAll = librariesForRoute("home", state.libraries || []);
      const mine = libsAll.filter((x) => x && x.name && !isSampleLib(x));
      const samples = libsAll.filter((x) => x && x.name && isSampleLib(x));
      mine.sort((a, b) => String((a && a.name) || "").localeCompare(String((b && b.name) || ""), "zh-Hans-CN"));
      samples.sort((a, b) => String((a && a.name) || "").localeCompare(String((b && b.name) || ""), "zh-Hans-CN"));

      function pick(name) {
        const n = String(name || "").trim();
        if (!n) return;
        state.library = n;
        setSavedLibraryForRoute(route(), state.library);
        updateGlobalLibraryUI();
        render().catch(() => {});
      }

      function libSubtitle(lib) {
        const ragOk = !!(lib && lib.rag_index);
        const pdfCount = ragOk ? Number(lib.rag_pdf_count || 0) : Number(lib.pdf_import_count || 0);
        const chunks = Number(lib.rag_node_count || 0);
        if (ragOk) return `范文 ${Math.max(0, Math.round(pdfCount))} 篇 · 范文片段 ${Math.max(0, Math.round(chunks))} 段`;
        return pdfCount > 0 ? `已导入 ${Math.max(0, Math.round(pdfCount))} 篇 PDF · 还未生成范文片段` : "还没导入 PDF";
      }

      function libBadges(lib) {
        const ragOk = !!(lib && lib.rag_index);
        const citeOk = !!(lib && lib.cite_index);
        const row = el("div", { class: "topic-badges" });
        if (isSampleLib(lib)) row.appendChild(el("span", { class: "badge" }, "示例"));
        row.appendChild(el("span", { class: "badge " + (ragOk ? "good" : "bad") }, ragOk ? "可写作" : "需准备"));
        row.appendChild(el("span", { class: "badge " + (citeOk ? "good" : "warn") }, citeOk ? "引用就绪" : "引用可选"));
        if (String((lib && lib.name) || "") === String(state.library || "")) row.appendChild(el("span", { class: "badge good" }, "正在使用"));
        return row;
      }

      function topicTile(lib) {
        const name = String((lib && lib.name) || "").trim();
        if (!name) return null;
        const ragOk = !!(lib && lib.rag_index);
        const active = name === state.library;

        const useBtn = el(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            disabled: active,
            onclick: (e) => {
              try {
                e.stopPropagation();
              } catch {}
              pick(name);
              toast(`已切换范文专题库：${name}`);
            },
          },
          active ? "正在使用" : "使用"
        );
        const prepBtn = ragOk
          ? el(
              "button",
              {
                class: "btn",
                type: "button",
                onclick: (e) => {
                  try {
                    e.stopPropagation();
                  } catch {}
                  setRoute("polish");
                },
              },
              "去对齐润色"
            )
          : el(
              "button",
              {
                class: "btn",
                type: "button",
                onclick: (e) => {
                  try {
                    e.stopPropagation();
                  } catch {}
                  openPrepWizard({ need: "rag", library: name, lockLibrary: true });
                },
              },
              "一键准备"
            );

        return el(
          "div",
          {
            class: "card topic-card" + (active ? " active" : ""),
            role: "button",
            tabindex: "0",
            onclick: () => pick(name),
          },
          el(
            "div",
            { class: "topic-head" },
            el("div", { class: "topic-icon", "aria-hidden": "true" }, "📚"),
            el("div", { class: "topic-meta" }, el("div", { class: "topic-name" }, name), el("div", { class: "topic-sub" }, libSubtitle(lib)))
          ),
          libBadges(lib),
          el("div", { class: "topic-actions" }, useBtn, prepBtn)
        );
      }

      const titleRow = el(
        "div",
        { class: "row", style: "justify-content:space-between; align-items:flex-end" },
        el(
          "div",
          null,
          el("div", { class: "label" }, "范文专题库（你的范文库）"),
          el("div", { class: "muted" }, "每个专题库 = 一套同领域顶级 PDF。准备一次，写作全程白箱对齐。")
        ),
        el(
          "div",
          { class: "row", style: "gap:10px" },
          el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "创建范文库"),
          el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "管理")
        )
      );

      const grid = el("div", { class: "topic-grid" });
      const shownMine = mine.slice(0, 4);
      const shownSamples = samples.slice(0, 1);
      for (const lib of [...shownMine, ...shownSamples]) {
        const t = topicTile(lib);
        if (t) grid.appendChild(t);
      }
      grid.appendChild(
        el(
          "div",
          { class: "card topic-card topic-create" },
          el(
            "div",
            { class: "topic-head" },
            el("div", { class: "topic-icon big", "aria-hidden": "true" }, "＋"),
            el("div", { class: "topic-meta" }, el("div", { class: "topic-name" }, "新建范文库"), el("div", { class: "topic-sub" }, "同领域 PDF 越“像”，对齐建议越“像”。"))
          ),
          el("div", { class: "topic-actions" }, el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "开始创建"))
        )
      );

      const totalShown = shownMine.length + shownSamples.length;
      if ((mine.length + samples.length) > totalShown) {
        grid.appendChild(
          el(
            "div",
            { class: "card topic-card topic-create" },
            el(
              "div",
              { class: "topic-head" },
              el("div", { class: "topic-icon big", "aria-hidden": "true" }, "…"),
              el("div", { class: "topic-meta" }, el("div", { class: "topic-name" }, "查看更多"), el("div", { class: "topic-sub" }, "去范文库页管理全部范文库"))
            ),
            el("div", { class: "topic-actions" }, el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页"))
          )
        );
      }

      if (!grid.children.length) {
        return el(
          "div",
          { class: "card" },
          titleRow,
          el("div", { class: "muted" }, "你还没有范文专题库：建议先创建一个，把同领域 PDF 导入进来。")
        );
      }

      const footer = el(
        "div",
        { class: "home-libmeta" },
        state.library
          ? `当前选择：${state.library}（越同领域，越像；不想生成也可用“找差距”离线对白箱证据）`
          : "未选择范文专题库：建议先选一个范文专题库再开始。"
      );

      return el("div", { class: "grid", style: "gap:12px" }, titleRow, grid, footer);
    })();

    inner.appendChild(hero);
    inner.appendChild(inputCard);
    inner.appendChild(libsSection);
    root.appendChild(inner);

    return root;
  }

  function pageLibrary() {
    renderHeader("范文专题库", "管理你的范文库：每个专题库 = 同领域顶级文档集合。准备一次，写作全程白箱对齐。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    function fmtCount(n) {
      const x = Number(n || 0);
      if (!Number.isFinite(x)) return "0";
      return String(Math.max(0, Math.round(x)));
    }

    async function useLibrary(name, nextRoute = "") {
      const libName = String(name || "").trim();
      if (!libName) return;
      state.library = libName;
      setSavedLibraryForRoute(route(), state.library);
      updateGlobalLibraryUI();
      await refreshLibraryStatus().catch(() => {});
      toast(`已切换范文专题库：${libName}`);
      if (nextRoute) setRoute(nextRoute);
    }

    function openCreateLibraryModal() {
      const nameInput = el("input", { class: "input", placeholder: "例如：finance_2026（建议用领域/年份命名）" });
      const createBtn = el(
        "button",
        {
          class: "btn btn-primary",
          type: "button",
          onclick: async () => {
            const name = String(nameInput.value || "").trim();
            if (!name) return toast("请输入范文库名字。", "bad", 4500);
            createBtn.disabled = true;
            createBtn.textContent = "创建中…";
            try {
              const r = await apiPost("/api/libraries", { name, kind: "exemplar" });
              let safe = name;
              try {
                const p = String((r && r.path) || "");
                const base = p.split(/[\\/]/).pop() || "";
                if (base.toLowerCase().endsWith(".json")) safe = base.slice(0, -5) || safe;
              } catch {}
              await useLibrary(safe);
              await refreshLibraries().catch(() => {});
              closeModal();
              openPrepWizard({ need: "rag", library: safe, lockLibrary: true });
            } catch (e) {
              toast(String(e.message || e), "bad", 6500);
            } finally {
              createBtn.disabled = false;
              createBtn.textContent = "创建并开始准备";
            }
          },
        },
        "创建并开始准备"
      );

      const body = el(
        "div",
        { class: "grid", style: "gap:14px" },
        el("div", { class: "muted" }, "范文库=你的同领域顶级文档集合。把同领域 PDF 放进来，之后写作就能逐句对照、可追溯。"),
        el("div", { class: "row" }, nameInput),
        el("div", { class: "row" }, createBtn, el("button", { class: "btn", type: "button", onclick: closeModal }, "取消")),
        el("div", { class: "muted" }, "提示：第一次准备会占用 CPU（8GB 笔记本也可用，耐心等几分钟）。")
      );
      openModal("新建范文库", body);
      try {
        nameInput.focus();
      } catch {}
    }

    function isSample(lib) {
      const name = String((lib && lib.name) || "").toLowerCase();
      if (!name) return false;
      if (name.includes("demo") || name.includes("smoke")) return true;
      const src = String((lib && lib.rag_pdf_root) || "");
      return src.toLowerCase().includes("sample_pdfs");
    }

    function librarySubtitle(lib) {
      const ragOk = !!(lib && lib.rag_index);
      const citeOk = !!(lib && lib.cite_index);
      const pdfCount = ragOk ? Number(lib.rag_pdf_count || 0) : Number(lib.pdf_import_count || 0);
      const bits = [`范文 ${fmtCount(pdfCount)} 篇`];
      if (ragOk) {
        const chunks = Number((lib && lib.rag_node_count) || 0);
        if (Number.isFinite(chunks) && chunks > 0) bits.push(`范文片段 ${fmtCount(chunks)} 段`);
      }
      bits.push(ragOk ? "已准备" : "未准备");
      bits.push(citeOk ? "含引用写法" : "引用可选");
      return bits.join(" · ");
    }

    function librarySourceLine(lib) {
      const ragOk = !!(lib && lib.rag_index);
      const src = ragOk ? String(lib.rag_pdf_root || "") : String(lib.pdf_import_root || "");
      if (!src) return "来源：—";
      return `来源：${src}`;
    }

    function topicCard(lib) {
      const name = String((lib && lib.name) || "").trim();
      if (!name) return null;
      const ragOk = !!lib.rag_index;
      const citeOk = !!lib.cite_index;
      const active = name === state.library;

      const badgeRow = el("div", { class: "topic-badges" });
      if (isSample(lib)) badgeRow.appendChild(el("span", { class: "badge" }, "示例"));
      badgeRow.appendChild(el("span", { class: "badge " + (ragOk ? "good" : "bad") }, ragOk ? "已准备" : "未准备"));
      badgeRow.appendChild(el("span", { class: "badge " + (citeOk ? "good" : "warn") }, citeOk ? "引用就绪" : "引用可选"));
      if (active) badgeRow.appendChild(el("span", { class: "badge good" }, "正在使用"));

      const primaryBtn = el(
        "button",
        {
          class: "btn btn-primary",
          type: "button",
          onclick: async () => {
            await useLibrary(name, "home");
          },
        },
          active ? "继续写作" : "使用此范文库"
      );

      const prepBtn = el(
        "button",
        {
          class: "btn",
          type: "button",
          onclick: async () => {
            await useLibrary(name);
            openPrepWizard({ need: "rag", library: name, lockLibrary: true });
          },
        },
        ragOk ? "更新证据" : "一键准备"
      );

      const moreBtn = el(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          onclick: () => {
            const src = ragOk ? String(lib.rag_pdf_root || "") : String(lib.pdf_import_root || "");
            const builtAt = String((lib && lib.rag_built_at_iso) || "").trim();
            const pdfCount = ragOk ? Number(lib.rag_pdf_count || 0) : Number(lib.pdf_import_count || 0);

            const citeAction = el(
              "button",
              {
                class: "btn btn-primary",
                type: "button",
                onclick: async () => {
                  closeModal();
                  await useLibrary(name);
                  if (citeOk) return setRoute("cite");
                  openPrepWizard({ need: "cite", library: name, lockLibrary: true });
                },
              },
              citeOk ? "打开引用写法" : "准备引用写法（可选）"
            );

            const copyAction = el(
              "button",
              {
                class: "btn",
                type: "button",
                disabled: !src,
                onclick: () => {
                  if (!src) return toast("暂无来源路径。", "bad", 3500);
                  copyText(src);
                  toast("已复制来源路径。");
                },
              },
              "复制来源路径"
            );

            const body = el(
              "div",
              { class: "grid", style: "gap:14px" },
              el("div", { class: "muted" }, "这里是范文库的“更多操作”。不会改变你的正文，只影响范文片段的准备与引用写法。"),
              el(
                "div",
                { class: "card" },
                el("div", { class: "label" }, "状态"),
                el("div", null, librarySubtitle(lib)),
                builtAt ? el("div", { class: "muted" }, `构建时间：${builtAt}`) : null,
                el("div", { class: "muted" }, `PDF 数量：${fmtCount(pdfCount)} 篇`)
              ),
              el("div", { class: "card" }, el("div", { class: "label" }, "来源"), el("div", { class: "quote" }, src || "—")),
              el("div", { class: "row" }, citeAction, copyAction, el("button", { class: "btn", type: "button", onclick: closeModal }, "关闭"))
            );
            openModal(`${name} · 更多`, body);
          },
        },
        "更多"
      );

      return el(
        "div",
        { class: "card topic-card" + (active ? " active" : "") },
        el(
          "div",
          { class: "topic-head" },
          el("div", { class: "topic-icon", "aria-hidden": "true" }, "📚"),
          el("div", { class: "topic-meta" }, el("div", { class: "topic-name" }, name), el("div", { class: "topic-sub" }, librarySubtitle(lib)))
        ),
        badgeRow,
        el("div", { class: "topic-actions" }, primaryBtn, prepBtn, moreBtn)
      );
    }

    const search = el("input", { class: "input", placeholder: "搜索范文库…", style: "flex:1; min-width:240px" });
    search.value = localStorage.getItem("aiw.libraryQuery") || "";

    const showSamplesKey = "aiw.libraryShowSamples";
    const showSamples = el("input", { type: "checkbox" });
    showSamples.checked = localStorage.getItem(showSamplesKey) === "1";
    showSamples.onchange = () => {
      localStorage.setItem(showSamplesKey, showSamples.checked ? "1" : "0");
      renderGrid();
    };
    const showSamplesWrap = el("label", { class: "row", style: "gap:8px" }, showSamples, el("span", { class: "muted" }, "显示示例/测试库"));

    const createBtn = el("button", { class: "btn btn-primary", type: "button", onclick: () => openCreateLibraryModal() }, "新建范文库");
    const prepBtn = el("button", { class: "btn", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "导入/更新 PDF…");

    const details = el(
      "details",
      { class: "details" },
      el("summary", { class: "label" }, "这是什么？（点开查看）"),
      el("div", { class: "muted" }, "范文库=你的同领域顶级文档集合：放入 PDF，准备一次，后续写作就能逐句对照（白箱可追溯）。"),
        el(
          "ol",
          null,
          el("li", null, "找差距：不生成内容，只做对照，定位哪里不像范文。"),
          el("li", null, "对齐润色：引用范文证据，给出“哪里不像 + 怎么改更像 + 两种改法”。"),
          el("li", null, "引用写法：检索范文里常见的引用句式与参考文献（可选）。")
        )
      );

    root.appendChild(el("div", { class: "card" }, el("div", { class: "row" }, search, showSamplesWrap, createBtn, prepBtn), details));

    const grid = el("div", { class: "topic-grid" });

    function renderGrid() {
      clear(grid);

      const libs = librariesForRoute("library", state.libraries || [])
        .slice()
        .filter((x) => showSamples.checked || !isSample(x));
      libs.sort((a, b) => {
        const aOk = a && a.rag_index ? 1 : 0;
        const bOk = b && b.rag_index ? 1 : 0;
        if (aOk !== bOk) return bOk - aOk;
        return String((a && a.name) || "").localeCompare(String((b && b.name) || ""), "zh-Hans-CN");
      });

      const q = String(search.value || "").trim().toLowerCase();
      const shown = q ? libs.filter((x) => String((x && x.name) || "").toLowerCase().includes(q)) : libs;

      for (const lib of shown) {
        const c = topicCard(lib);
        if (c) grid.appendChild(c);
      }

      if (!shown.length) {
        const hasAny = librariesForRoute("library", state.libraries || []).length > 0;
        grid.appendChild(
          el(
            "div",
            { class: "card" },
            el("div", { class: "label" }, hasAny && !showSamples.checked ? "没有匹配的范文库（示例库已隐藏）" : "没有匹配的范文库"),
            el(
              "div",
              { class: "muted" },
              hasAny && !showSamples.checked ? "你可以新建一个范文库，或勾选“显示示例/测试库”。" : "你可以新建一个范文库，或清空搜索关键字。"
            ),
            el(
              "div",
              { class: "row" },
              el("button", { class: "btn btn-primary", type: "button", onclick: openCreateLibraryModal }, "新建范文库"),
              hasAny && !showSamples.checked
                ? el(
                    "button",
                    {
                      class: "btn",
                      type: "button",
                      onclick: () => {
                        showSamples.checked = true;
                        localStorage.setItem(showSamplesKey, "1");
                        renderGrid();
                      },
                    },
                    "显示示例/测试库"
                  )
                : null
            )
          )
        );
      }
    }

    search.addEventListener("input", () => {
      localStorage.setItem("aiw.libraryQuery", search.value || "");
      renderGrid();
    });

    renderGrid();
    root.appendChild(grid);
    return root;
  }

  function pageScan() {
    renderHeader("找差距", "先找出“最不像范文”的句子，并给出对应的范文证据（可继续一键对齐润色）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const text = el("textarea", { class: "textarea", placeholder: "粘贴你的正文（中英混合可）…" });
    text.value = state.scanDraft || "";
    text.addEventListener("input", () => {
      state.scanDraft = text.value || "";
      localStorage.setItem("aiw.scanDraft", state.scanDraft);
    });

    const topk = el("input", { class: "input", value: "6", style: "width:110px", inputmode: "numeric", title: "每句检索多少条范文片段作为对照（越大越慢）" });
    const maxItems = el("input", { class: "input", value: "220", style: "width:130px", inputmode: "numeric", title: "最多扫描多少句（越大越慢）" });

    const runBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) {
            openPrepWizard({ need: "rag", resume: { route: "scan", autoKey: "aiw.scanAutoRun", autoValue: "1" } });
            return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
          }
          if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
            openPrepWizard({ need: "rag", resume: { route: "scan", autoKey: "aiw.scanAutoRun", autoValue: "1" } });
            return toast("范文库还没准备好：先完成一次“导入 + 准备”。", "bad", 4500);
          }
          const raw = (text.value || "").trim();
          if (!raw) return toast("请先粘贴文本。", "bad");
          runBtn.disabled = true;
          runBtn.textContent = "扫描中…";
          try {
            const r = await apiPost("/api/align/scan", {
              library: state.library,
              text: raw,
              top_k: Number(topk.value || 6),
              max_items: Number(maxItems.value || 220),
            }, { timeout_ms: 240000 });
            state.lastScan = r;
            toast("扫描完成。");
            renderScanResults();
          } catch (e) {
            const msg = String(e.message || e);
            if (!maybeOpenIndexModalForError(msg)) toast(msg, "bad", 6500);
          } finally {
            runBtn.disabled = false;
            runBtn.textContent = "开始找差距";
          }
        },
      },
      "开始找差距"
    );

    const resultsBox = el("div", { class: "card" });
    function renderEmptyResultsHint() {
      clear(resultsBox);
      if (!state.library) {
        resultsBox.appendChild(el("div", { class: "label" }, "还不能开始：请先准备范文库"));
        resultsBox.appendChild(el("div", { class: "muted" }, "准备一次后，就能对白箱对照：哪里不像范文、参考哪段范文、怎么改更像。"));
        resultsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              {
                class: "btn btn-primary",
                type: "button",
                onclick: () => openPrepWizard({ need: "rag", resume: { route: "scan" } }),
              },
              "一键准备范文库"
            ),
            el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
          )
        );
        return;
      }
      const ragOk = !!(state.libraryStatus && state.libraryStatus.rag_index);
      if (!ragOk) {
        resultsBox.appendChild(el("div", { class: "label" }, "还不能扫描：范文库未准备好"));
        resultsBox.appendChild(el("div", { class: "muted" }, "第一次需要导入同领域 PDF，并在本地生成“范文片段（证据）”。完成后才能对白箱对照。"));
        resultsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              {
                class: "btn btn-primary",
                type: "button",
                onclick: () => openPrepWizard({ need: "rag", resume: { route: "scan" } }),
              },
              "一键准备范文库"
            ),
            el(
              "button",
              {
                class: "btn",
                type: "button",
                onclick: () => setRoute("library"),
              },
              "去范文库页"
            )
          )
        );
        return;
      }
      resultsBox.appendChild(el("div", { class: "muted" }, "结果将在这里显示（按对齐度从低到高排序）。"));
    }
    renderEmptyResultsHint();

    function renderScanResults() {
      clear(resultsBox);
      const items = (state.lastScan && state.lastScan.items) || [];
      if (!items.length) {
        resultsBox.appendChild(el("div", { class: "muted" }, "没有可扫描的句子（太短会被跳过）。"));
        return;
      }

      resultsBox.appendChild(el("div", { class: "label" }, `找到 ${items.length} 条句子（按对齐度从低到高排序）`));
      resultsBox.appendChild(
        el("div", { class: "muted" }, "对齐度参考：<40% 建议优先改 · 40–70% 一般 · >70% 较像范文（仅供参考）。")
      );

      const list = el("div", { class: "list" });
      for (const it of items) {
        const pct = Number(it.pct || 0);
        const badgeCls = pct >= 80 ? "badge good" : pct >= 60 ? "badge" : "badge bad";
        const sent = String(it.text || "");
        const exs = Array.isArray(it.exemplars) ? it.exemplars : [];
        const firstEx = exs.length ? exs[0] : null;
        const scaffolds = firstEx && firstEx.text ? suggestScaffoldsFromEvidence(firstEx.text, 3) : [];

        const head = el(
          "div",
          { class: "item-header" },
          el("div", null, el("span", { class: badgeCls }, `${pct}%`), " ", el("span", null, sent.slice(0, 220) + (sent.length > 220 ? "…" : ""))),
          el(
            "div",
            { class: "actions-col" },
            el(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: () => openModal("范文对照（证据）", exemplarList(exs || [], { library: state.library })),
              },
              "查看范文"
            ),
            el(
              "button",
              {
                class: "btn btn-small btn-primary",
                type: "button",
                onclick: () => {
                  state.polishDraft = String(it.text || "");
                  localStorage.setItem("aiw.polishDraft", state.polishDraft);
                  setRoute("polish");
                },
              },
              "对齐润色这个句子"
            )
          )
        );

        const scaffoldRow = scaffolds.length
          ? el(
              "div",
              { class: "row", style: "gap:8px; align-items:flex-start; margin-top:10px" },
              el("span", { class: "muted" }, "推荐句式"),
              ...scaffolds.map((p) =>
                el(
                  "button",
                  {
                    class: "chip scaffold",
                    type: "button",
                    title: "点击复制（把这个句式放进你的句子开头/过渡处）",
                    onclick: () => copyText(p),
                  },
                  p
                )
              )
            )
          : el("div", { class: "muted", style: "margin-top:10px" }, "提示：点“查看范文”，从范文片段里复制可复用的句式模板。");

        list.appendChild(el("div", { class: "item" }, head, scaffoldRow));
      }
      resultsBox.appendChild(list);
    }

    const inputCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "输入文本"),
      text,
      el("div", { class: "row", style: "justify-content:flex-start" }, runBtn, el("span", { class: "muted" }, "只做对照：不生成内容。")),
      el(
        "details",
        { class: "details" },
        el("summary", { class: "label" }, "高级设置"),
        el(
          "div",
          { class: "row", style: "margin-top:10px" },
          el("span", { class: "label" }, "每句证据数"),
          topk,
          el("span", { class: "label" }, "最多扫描句子"),
          maxItems,
          el("span", { class: "muted" }, "提示：数字越大越慢。")
        )
      )
    );
    root.appendChild(
      el(
        "div",
        { class: "grid two", style: "gap:18px; align-items:start" },
        inputCard,
        resultsBox
      )
    );

    const autoRun = localStorage.getItem("aiw.scanAutoRun") === "1";
    if (autoRun) {
      localStorage.removeItem("aiw.scanAutoRun");
      window.setTimeout(() => {
        try {
          runBtn.click();
        } catch {}
      }, 80);
    }
    const auto = localStorage.getItem("aiw.scanAutoRun") || "";
    if (auto) {
      localStorage.removeItem("aiw.scanAutoRun");
      window.setTimeout(() => {
        try {
          runBtn.click();
        } catch {}
      }, 120);
    }
    return root;
  }

  function pagePolish() {
    renderHeader("对齐润色", "白箱：参考哪段范文 → 哪里不像 → 怎么改更像（生成润色需要大模型）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const selected = el("textarea", { class: "textarea", placeholder: "选中要对齐润色的句子/段落…" });
    selected.value = state.polishDraft || "";
    selected.addEventListener("input", () => {
      state.polishDraft = selected.value || "";
      localStorage.setItem("aiw.polishDraft", state.polishDraft);
      try {
        syncGenerateUi();
      } catch {}
    });

    function apiConfiguredNow() {
      return llmConfigured(state.llmApi);
    }

    function apiUsableNow() {
      return llmUsable(state.llmApi);
    }

    function selectedTextOk() {
      const txt = String((selected.value || "").trim());
      return txt.length >= 8;
    }

    function insertAtCursor(textarea, text) {
      const ta = textarea;
      const ins = String(text || "");
      if (!ta || !ins) return;
      let start = 0;
      let end = 0;
      try {
        start = Number(ta.selectionStart || 0);
        end = Number(ta.selectionEnd || 0);
      } catch {
        start = end = ta.value.length;
      }
      const before = ta.value.slice(0, start);
      const after = ta.value.slice(end);
      const isCjk = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/.test(ins);
      const needSpaceLeft = !isCjk && before && !/[\s\n]$/.test(before);
      const needSpaceRight = !isCjk && after && !/^[\s\n]/.test(after);
      const mid = (needSpaceLeft ? " " : "") + ins + (needSpaceRight ? " " : "");
      ta.value = before + mid + after;
      const pos = (before + (needSpaceLeft ? " " : "") + ins).length;
      try {
        ta.selectionStart = ta.selectionEnd = pos;
        ta.focus();
      } catch {}
      try {
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      } catch {}
    }

    const topk = el("input", { class: "input", value: "8", style: "width:110px", inputmode: "numeric", title: "检索多少条范文片段作为证据（越大越慢）" });
    const storedMaxTok = Number(localStorage.getItem("aiw.polishMaxTokens") || "");
    const maxTokDefault = Number.isFinite(storedMaxTok) && storedMaxTok > 0 ? storedMaxTok : 4096;
    const maxTok = el("input", { class: "input", value: String(Math.round(maxTokDefault)), style: "width:120px", inputmode: "numeric", title: "输出长度上限（越大越慢）" });
    maxTok.addEventListener("change", () => {
      const v = Number((maxTok.value || "").trim() || 0);
      if (Number.isFinite(v) && v > 0) localStorage.setItem("aiw.polishMaxTokens", String(Math.round(v)));
    });

    let advOpen = localStorage.getItem("aiw.polishAdv") === "1";
    const advRow = el(
      "div",
      { class: "row", style: `display:${advOpen ? "flex" : "none"}` },
      el("span", { class: "label" }, "证据条数"),
      topk,
      el("span", { class: "label" }, "输出长度"),
      maxTok,
      el("span", { class: "muted" }, "温度固定 0（尽量不发散）。建议先在“润色设置”测试连接。")
    );

    const exemplarsBox = el("div", { class: "card" });
    const outBox = el("div", { class: "card" });
    const selHint = el("div", { class: "muted" }, "—");

    function renderExemplars(exs, title = "范文对照（将作为证据引用）") {
      clear(exemplarsBox);
      exemplarsBox.appendChild(el("div", { class: "label" }, title));
      exemplarsBox.appendChild(exemplarList(exs || [], { library: state.library, onInsertScaffold: (p) => insertAtCursor(selected, p) }));
    }

      function renderExemplarsEmpty() {
        clear(exemplarsBox);
        if (!state.library) {
        exemplarsBox.appendChild(el("div", { class: "label" }, "还不能开始：请先准备范文库"));
        exemplarsBox.appendChild(el("div", { class: "muted" }, "准备一次后，对齐润色会显示：参考哪段范文、哪里不像、怎么改更像。"));
        exemplarsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              {
                class: "btn btn-primary",
                type: "button",
                onclick: () => openPrepWizard({ need: "rag", resume: { route: "polish" } }),
              },
              "一键准备范文库"
            ),
            el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
          )
        );
        return;
      }
      const ragOk = !!(state.libraryStatus && state.libraryStatus.rag_index);
      if (!ragOk) {
        exemplarsBox.appendChild(el("div", { class: "label" }, "还不能润色：范文库未准备好"));
        exemplarsBox.appendChild(el("div", { class: "muted" }, "第一次需要导入同领域 PDF，并在本地生成“范文片段（证据）”。完成后才能白箱对照。"));
        exemplarsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag", resume: { route: "polish" } }) },
              "一键准备范文库"
            ),
            el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
          )
        );
        return;
      }
      exemplarsBox.appendChild(el("div", { class: "muted" }, "你可以直接点“一键对齐润色”（自动带证据）；也可以先点“只看范文证据”。"));
    }

    function renderOutEmpty() {
      clear(outBox);
      outBox.appendChild(el("div", { class: "label" }, "输出会显示什么？"));
      outBox.appendChild(el("div", { class: "muted" }, "运行后你会看到：参考范文片段（证据） → 哪里不像 → 句式模板 → 两版润色（轻改/中改）。"));
      outBox.appendChild(el("div", { class: "muted" }, "不想生成也可以先点“只看范文证据”，先确认本次参考了哪些范文片段。"));
    }

    let genUiTimer = null;
    function stopGenUiTimer() {
      if (genUiTimer) window.clearInterval(genUiTimer);
      genUiTimer = null;
    }

    function renderOutGenerating(onCancel) {
      stopGenUiTimer();
      clear(outBox);
      const title = "对齐润色中…（正在请求模型）";

      const stage = el("div", { class: "muted" }, "阶段：准备中…");
      const timeEl = el("div", { class: "muted mono" }, "耗时：0s");
      const bar = el("div", { class: "progress" }, el("div"));

      const stages = [
        "检索范文片段（证据，可追溯）",
        "生成诊断（哪里不像 + 句式模板）",
        "生成润色（轻改/中改）",
        "白箱校验（不增事实/不增数字/引用可追溯）",
      ];
      const ul = el("ul", { style: "margin:10px 0 0 18px" }, ...stages.map((t) => el("li", null, t)));

      let pct = 6;
      const start = Date.now();
      const inner = bar.firstChild;
      inner.style.width = `${pct}%`;

      const cancelBtn =
        typeof onCancel === "function"
          ? el(
              "button",
              {
                class: "btn btn-danger btn-small",
                type: "button",
                onclick: async () => {
                  cancelBtn.disabled = true;
                  cancelBtn.textContent = "取消中…";
                  try {
                    await onCancel();
                  } catch {}
                },
              },
              "取消生成"
            )
          : null;

      outBox.appendChild(el("div", { class: "label" }, title));
      outBox.appendChild(el("div", { class: "muted" }, "请稍等：会输出“诊断 + 轻改/中改”，并附范文证据。"));
      outBox.appendChild(ul);
      outBox.appendChild(el("div", { class: "row", style: "justify-content:space-between; margin-top:10px" }, stage, timeEl));
      outBox.appendChild(bar);
      outBox.appendChild(
        el(
          "div",
          { class: "row", style: "margin-top:10px" },
          cancelBtn,
          el("div", { class: "muted" }, "提示：若失败，多数是输出长度太小导致 JSON 截断；建议输出长度 ≥ 4096（Gemini 建议 ≥ 8192）。")
        )
      );

      genUiTimer = window.setInterval(() => {
        const elapsed = Date.now() - start;
        timeEl.textContent = `耗时：${Math.floor(elapsed / 1000)}s`;

        // Pseudo progress: keep moving but never "complete" before the response returns.
        pct = Math.min(92, pct + 0.35);
        inner.style.width = `${Math.floor(pct)}%`;

        // Stage hints by time (heuristic).
        let idx = 0;
        if (elapsed > 1200) idx = 1;
        if (elapsed > 5200) idx = 2;
        if (elapsed > 14000) idx = 3;
        stage.textContent = `阶段：${stages[Math.min(idx, stages.length - 1)]}`;
      }, 240);
    }

    function renderOutError(msg) {
      stopGenUiTimer();
      clear(outBox);
      outBox.appendChild(el("div", { class: "label" }, "生成失败"));
      outBox.appendChild(el("div", { class: "quote" }, msg || "未知错误。"));
      outBox.appendChild(
        el(
          "div",
          { class: "row" },
          el(
            "button",
            {
              class: "btn btn-primary",
              type: "button",
              onclick: () => {
                advOpen = true;
                localStorage.setItem("aiw.polishAdv", "1");
                advRow.style.display = "flex";
                toast("已展开高级设置（可调输出长度）。");
              },
            },
            "打开高级设置"
          ),
          el("button", { class: "btn", type: "button", onclick: () => setRoute("llm") }, "去润色设置")
        )
      );
    }

    renderExemplarsEmpty();
    renderOutEmpty();

      async function fetchExemplars() {
        if (!state.library) {
          openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "exemplars" } });
          return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
        }
        if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
          openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "exemplars" } });
          return toast("范文库还没准备好：先完成一次“导入 + 准备”。", "bad", 4500);
        }
      const txt = (selected.value || "").trim();
      if (txt.length < 8) return toast("选中文本太短。", "bad");
      try {
        const r = await apiPost("/api/align/polish", {
          library: state.library,
          selected_text: txt,
          top_k: Number(topk.value || 8),
          generate: false,
        });
        renderExemplars(r.exemplars || []);
        toast("已获取范文证据。");
        return r;
      } catch (e) {
        const msg = String(e.message || e);
        if (!maybeOpenIndexModalForError(msg)) toast(msg, "bad", 6500);
        throw e;
      }
    }

    function renderPolishResult(r) {
      stopGenUiTimer();
      clear(outBox);
      const result = r && r.result;
      if (!result) {
        outBox.appendChild(el("div", { class: "muted" }, "未生成结果。"));
        return;
      }

      const baseText = String((r && r.selected_text) || "").trim();

      const diag = result.diagnosis || [];
      const vars = result.variants || [];

      function renderLibraryEvidence(c, needles) {
        const cid = String((c && c.id) || "").trim();
        const pdf = String((c && c.pdf) || "").trim();
        const page = Number((c && c.page) || 0) || 0;
        const quote = String((c && c.quote) || "");
        const meta = `${cid || "C?"} · ${pdf || "—"}#p${page || 0}`;
        const actions = el(
          "div",
          { class: "row", style: "gap:8px; justify-content:flex-end" },
          pdf
            ? el(
                "button",
                { class: "btn btn-small", type: "button", onclick: () => openLibraryPdfPreview(state.library, pdf, page || 1) },
                "预览"
              )
            : null,
          quote ? el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(quote) }, "复制") : null
        );
        return el(
          "div",
          { class: "quote" },
          el("div", { class: "row", style: "justify-content:space-between; gap:10px; align-items:center" }, el("span", { class: "muted mono" }, meta), actions),
          el("div", null, needles && needles.length ? highlightNeedles(quote, needles) : quote || "—")
        );
      }

      outBox.appendChild(
        el("div", { class: "label" }, `输出语言：${result.language || "mixed"} · 诊断 ${diag.length} 条 · 润色 ${vars.length} 条`)
      );

      const llmInfo = (r && r.llm) || null;
      if (llmInfo && llmInfo.provider === "api") {
        outBox.appendChild(el("div", { class: "muted" }, `模型：${llmInfo.model || "—"} · ${llmInfo.base_url || "—"}`));
      }

      // White-box alignment score before/after (retrieval-only, no LLM).
      const al = (r && r.alignment) || null;
      if (al && al.selected) {
        const basePct = Number((al.selected && al.selected.pct) || 0) || 0;
        const rows = [];
        rows.push({ name: "原文", pack: al.selected });
        const vs = Array.isArray(al.variants) ? al.variants : [];
        for (const v of vs) {
          const lvl = String(v.level || "").toLowerCase();
          const name = lvl === "light" ? "轻改" : lvl === "medium" ? "中改" : lvl || "润色";
          rows.push({ name, pack: v });
        }

        const wrap = el("div", { class: "list" });
        for (const it of rows) {
          const pack = it.pack || {};
          const pct = Number(pack.pct || 0);
          const badgeCls = pct >= 80 ? "badge good" : pct >= 60 ? "badge" : "badge bad";
          const delta = Math.round(pct - basePct);
          const deltaNode =
            it.name === "原文"
              ? el("span", { class: "badge" }, "基准")
              : delta > 0
                ? el("span", { class: "badge good" }, `↑ +${delta}%`)
                : delta < 0
                  ? el("span", { class: "badge bad" }, `↓ ${delta}%`)
                  : el("span", { class: "badge" }, "—");
          const best = pack.best || {};
          const bestText = best && best.pdf ? `${best.pdf}#p${best.page || 0}` : "—";
          const exs = Array.isArray(pack.exemplars) ? pack.exemplars : [];
          wrap.appendChild(
            el(
              "div",
              { class: "item" },
              el(
                "div",
                { class: "item-header" },
                el(
                  "div",
                  null,
                  el("span", { class: badgeCls }, `${Math.round(pct)}%`),
                  " ",
                  deltaNode,
                  " ",
                  el("span", null, `${it.name} 对齐度`)
                ),
                el(
                  "div",
                  { class: "row" },
                  el("span", { class: "muted mono" }, bestText),
                  exs.length
                    ? el(
                        "button",
                        {
                          class: "btn btn-small",
                          type: "button",
                          onclick: () => openModal(`${it.name} · 对齐范文（证据）`, exemplarList(exs, { library: state.library })),
                        },
                        "查看范文"
                      )
                    : null
                )
              )
            )
          );
        }

        outBox.appendChild(el("div", { class: "hr" }));
        outBox.appendChild(el("div", { class: "label" }, "对齐度（离线检索得分；越高越像本范文库写法）"));
        outBox.appendChild(el("div", { class: "muted" }, "说明：该分数来自离线检索（不生成内容），用于量化“润色后是否更像范文”。"));
        outBox.appendChild(wrap);
      }

      if (diag.length) {
        outBox.appendChild(el("div", { class: "hr" }));
        outBox.appendChild(el("div", { class: "label" }, "白箱诊断（每条都有范文证据）"));
        const list = el("div", { class: "list" });
        for (const d of diag) {
          const ev = d.evidence || [];
          const scaffolds = extractScaffolds(d.suggestion || "");
          const rest = stripScaffoldPrefix(d.suggestion || "");
          const evNodes = ev.map((c) => renderLibraryEvidence(c, scaffolds));
          const scaffoldRow =
            scaffolds && scaffolds.length
              ? el(
                  "div",
                  { class: "row", style: "gap:8px; margin-top:8px" },
                  el("span", { class: "muted" }, "句式模板"),
                  ...scaffolds.map((p) =>
                    el(
                      "button",
                      {
                        class: "chip scaffold",
                        type: "button",
                        title: "点击复制；Shift+点击插入到输入框",
                        onclick: (e) => {
                          const ev2 = e || window.event;
                          if (ev2 && ev2.shiftKey) {
                            insertAtCursor(selected, p);
                            toast("已插入句式模板。");
                            return;
                          }
                          copyText(p);
                        },
                      },
                      p
                    )
                  )
                )
              : null;
          list.appendChild(
            el(
              "div",
              { class: "item" },
              el("div", { class: "item-header" }, el("div", null, el("span", { class: "badge mono" }, d.title || "Diagnosis"))),
              el(
                "div",
                null,
                el("div", { class: "muted" }, d.problem || ""),
                scaffoldRow,
                rest ? el("div", null, rest) : d.suggestion ? el("div", null, d.suggestion || "") : null
              ),
              ...evNodes
            )
          );
        }
        outBox.appendChild(list);
      }

      const byLevel = {};
      for (const v of vars) byLevel[v.level] = v;

      function variantCard(v, title) {
        if (!v) return null;
        const rewrite = String(v.rewrite || "");
        const changes = v.changes || [];
        const cits = v.citations || [];
        const allScaffolds = [];
        for (const d of diag || []) {
          for (const p of extractScaffolds(d && d.suggestion ? d.suggestion : "")) allScaffolds.push(p);
        }
        const usedScaffolds = [];
        for (const p of allScaffolds) {
          if (!p) continue;
          if (rewrite.includes(p) && !usedScaffolds.includes(p)) usedScaffolds.push(p);
          if (usedScaffolds.length >= 4) break;
        }

        let diffBuilt = false;
        const diffWrap = el("div", { class: "diff-wrap hidden" });
        const diffBtn = el(
          "button",
          {
            class: "btn btn-small",
            type: "button",
            onclick: () => {
              const hidden = diffWrap.classList.contains("hidden");
              if (hidden && !diffBuilt) {
                clear(diffWrap);
                diffWrap.appendChild(renderDiffView(baseText || String(selected.value || ""), rewrite));
                diffBuilt = true;
              }
              diffWrap.classList.toggle("hidden");
              diffBtn.textContent = diffWrap.classList.contains("hidden") ? "显示差异" : "隐藏差异";
            },
          },
          "显示差异"
        );

        return el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "item-header" },
            el("div", { class: "label" }, title),
            el(
              "div",
              { class: "row", style: "justify-content:flex-end" },
              diffBtn,
              el(
                "button",
                {
                  class: "btn btn-small",
                  type: "button",
                  onclick: () => copyText(rewrite),
                },
                "复制"
              )
            )
          ),
          el("div", { class: "quote" }, usedScaffolds.length ? highlightNeedles(rewrite, usedScaffolds) : rewrite),
              usedScaffolds.length
              ? el(
                  "div",
                  { class: "row", style: "gap:8px; margin-top:10px" },
                  el("span", { class: "muted" }, "本次润色用到"),
                  ...usedScaffolds.map((p) =>
                    el(
                      "button",
                      {
                      class: "chip scaffold",
                      type: "button",
                      title: "点击复制；Shift+点击插入到输入框",
                      onclick: (e) => {
                        const ev2 = e || window.event;
                        if (ev2 && ev2.shiftKey) {
                          insertAtCursor(selected, p);
                          toast("已插入句式模板。");
                          return;
                        }
                        copyText(p);
                      },
                    },
                    p
                  )
                )
              )
            : null,
          diffWrap,
           changes && changes.length ? el("div", null, el("div", { class: "label" }, "变更点"), el("ul", null, ...changes.map((x) => el("li", null, x)))) : null,
           cits && cits.length
             ? el(
                 "div",
                 null,
                 el("div", { class: "label" }, "引用（白箱）"),
                 ...cits.map((c) => renderLibraryEvidence(c, usedScaffolds))
               )
             : null,
           el(
             "div",
             { class: "row" },
            el(
              "button",
              {
                class: "btn btn-small btn-primary",
                type: "button",
                onclick: () => {
                  selected.value = rewrite;
                  state.polishDraft = rewrite;
                  localStorage.setItem("aiw.polishDraft", state.polishDraft);
                  toast("已替换到输入框（可继续对齐润色）。");
                },
              },
              "替换到输入框"
            )
          )
        );
      }

      outBox.appendChild(el("div", { class: "hr" }));
      const light = variantCard(byLevel.light, "轻改（更保守）");
      const medium = variantCard(byLevel.medium, "中改（更像范文）");
      if (light) outBox.appendChild(light);
      if (medium) outBox.appendChild(medium);
    }

    const exBtn = el(
      "button",
      { class: "btn btn-ghost", type: "button", title: "只检索范文证据，不生成润色", onclick: fetchExemplars },
      "只看范文证据"
    );
    const genBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!apiConfiguredNow()) {
            toast("要生成润色，请先到“润色设置”配置大模型接口。", "bad", 6500);
            return setRoute("llm");
          }
          if (!apiUsableNow()) {
            const t = getLlmTestForApi(state.llmApi);
            if (t && !t.ok) toast("润色接口上次测试失败：仍会尝试生成（已加重试）。如失败请到“润色设置”重新测试。", "bad", 6500);
            else toast("润色接口尚未测试：仍会尝试生成（已加重试）。建议有空到“润色设置”点一次“测试连接”。", "bad", 6500);
          }
          if (!state.library) {
            openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
            return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
          }
          if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
            openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
            return toast("范文库还没准备好：先完成一次“导入 + 准备”。", "bad", 4500);
          }
          const txt = (selected.value || "").trim();
          if (!txt) return toast("请先粘贴要润色的句子/段落。", "bad");
          if (txt.length < 8) return toast("文本太短：请至少提供 8 个字符。", "bad");

          let maxTokens = Number(maxTok.value || 4096);
          if (!Number.isFinite(maxTokens) || maxTokens <= 0) maxTokens = 4096;
          const cap = 8192;
          maxTokens = Math.max(64, Math.min(cap, Math.round(maxTokens)));
          if (maxTokens < 256) {
            maxTokens = 256;
            maxTok.value = String(maxTokens);
            localStorage.setItem("aiw.polishMaxTokens", String(maxTokens));
          }
          if (maxTokens < 2048) {
            maxTokens = Math.min(cap, 4096);
            maxTok.value = String(maxTokens);
            localStorage.setItem("aiw.polishMaxTokens", String(maxTokens));
            toast("输出长度已自动调大（4096）以避免 JSON 截断。");
          }

          genBtn.disabled = true;
            genBtn.textContent = "对齐润色中…（请求大模型）";

          // Allow cancel: abort the HTTP request.
          const abort = new AbortController();
          let canceled = false;
          const onCancel = async () => {
            if (canceled) return;
            canceled = true;
            try {
              abort.abort();
            } catch {}
            toast("已取消生成。", "bad", 4500);
          };

          renderOutGenerating(onCancel);
          try {
            outBox.scrollIntoView({ behavior: "smooth", block: "start" });
          } catch {}
          try {
            await refreshLLMStatus();
            const r = await apiPost(
              "/api/align/polish",
              {
                library: state.library,
                selected_text: txt,
                top_k: Number(topk.value || 8),
                generate: true,
                temperature: 0.0,
                max_tokens: maxTokens,
                retries: 2,
              },
              { signal: abort.signal, timeout_ms: 240000 }
            );
            await refreshLLMStatus();
            if (r && r.exemplars) renderExemplars(r.exemplars || [], "本次生成使用的范文对照（证据）");
            renderPolishResult(r);
            toast("生成完成。");
          } catch (e) {
            const isAbort =
              (e && typeof e === "object" && (e.name === "AbortError" || e.code === "ABORT_ERR")) ||
              String(e && (e.message || e) ? e.message || e : e)
                .toLowerCase()
                .includes("abort");
            if (isAbort || canceled) {
              renderOutError("已取消生成（没有产生输出）。你可以修改文本后重新生成。");
              return;
            }
            await refreshLLMStatus().catch(() => {});
            let msg = String(e && (e.message || e) ? e.message || e : e);
            const lower = msg.toLowerCase();
            if (lower.includes("abort") || lower.includes("timeout") || lower.includes("timed out")) {
              msg = "生成超时：模型响应较慢或网络不稳定。可稍后重试，或在“高级设置”把输出长度调小一些。";
            }
            if (msg.includes("LLM output invalid") && msg.includes("bad json")) {
              msg =
                "生成结果格式不完整（常见原因：输出长度太小或推理占用大量 tokens）。请打开“高级设置”，把输出长度调大（建议 ≥ 4096；Gemini 建议 ≥ 8192）后重试。";
            } else if (maybeOpenIndexModalForError(msg)) {
              msg = "";
            } else if (msg.includes("missing api key")) {
              msg = "未配置大模型：请到“润色设置”页填写/测试，或设置环境变量 SKILL_LLM_API_KEY / OPENAI_API_KEY。";
            } else if (msg.includes("missing base_url")) {
              msg = "未配置接口 URL：请到“润色设置”页填写 base_url（通常以 /v1 结尾），或设置 SKILL_LLM_BASE_URL / OPENAI_BASE_URL。";
            } else if (msg.includes("missing model")) {
              msg = "未配置模型名：请到“润色设置”页填写 model，或设置 SKILL_LLM_MODEL / OPENAI_MODEL。";
            } else if (msg.includes("verify your account") || msg.includes("validation_required")) {
              msg = "拒绝访问（403）：当前模型/网关要求先完成账号验证（VALIDATION_REQUIRED）。请到“润色设置”点“测试连接”查看原因，并按提示完成验证或更换模型。";
            } else if (msg.includes("api request failed") && msg.includes("http 401")) {
              msg = "鉴权失败（401）：请检查密钥是否正确，或到“润色设置”页先点“测试连接”。";
            } else if (msg.includes("api request failed") && msg.includes("http 403")) {
              msg = "拒绝访问（403）：可能是权限不足、白名单限制或网关不支持。请到“润色设置”页先点“测试连接”。";
            } else if (msg.includes("api request failed") && msg.includes("http 429")) {
              msg = "触发限流（429）：请稍后重试，或降低频率/更换模型。";
            }
            if (msg) {
              toast(msg, "bad", 6500);
              renderOutError(msg);
            }
          } finally {
            genBtn.disabled = false;
            syncGenerateUi();
          }
        },
      },
      "一键对齐润色"
    );

    function syncGenerateUi() {
      const cfg = apiConfiguredNow();
      const okText = selectedTextOk();
      const okApi = apiUsableNow();
      const tested = !!getLlmTestForApi(state.llmApi);

      genBtn.disabled = cfg ? !okText : false;
      genBtn.textContent = cfg ? "一键对齐润色" : "先配置润色设置";

      if (!cfg) {
        selHint.textContent = "生成润色需要大模型：先去“润色设置”配置接口（也可用环境变量）。";
        return;
      }
      if (!okText) {
        selHint.textContent = "先粘贴一句/一段要润色的文本（建议 ≥ 8 个字符）。";
        return;
      }
      if (okApi) {
        selHint.textContent = "提示：生成会尽量“模仿范文句式”，并给出可追溯的范文证据。";
        return;
      }
      selHint.textContent = tested
        ? "提示：大模型上次测试失败也可以直接尝试生成（已加重试）。如失败请到“润色设置”重新测试。"
        : "提示：未测试连接也可以直接尝试生成（已加重试）。建议有空到“润色设置”点一次“测试连接”。";
    }

    const advBtn = el(
      "button",
      {
        class: "btn btn-small",
        type: "button",
        onclick: () => {
          advOpen = !advOpen;
          localStorage.setItem("aiw.polishAdv", advOpen ? "1" : "0");
          advRow.style.display = advOpen ? "flex" : "none";
          advBtn.textContent = advOpen ? "收起高级" : "高级设置";
        },
      },
      advOpen ? "收起高级" : "高级设置"
    );

    const inputCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "选中要对齐润色的文本"),
      selected,
      el(
        "div",
        { class: "row" },
        genBtn,
        exBtn,
        advBtn
      ),
      advRow,
      selHint
    );

    const leftCol = el("div", { class: "grid", style: "gap:18px" }, inputCard, outBox);

    const topGrid = el(
      "div",
      { class: "grid two", style: "gap:18px; align-items:start" },
      leftCol,
      exemplarsBox
    );
    root.appendChild(topGrid);

    const auto = localStorage.getItem("aiw.polishAutoRun") || "";
    if (auto) {
      localStorage.removeItem("aiw.polishAutoRun");
      window.setTimeout(() => {
        try {
          if (auto === "exemplars") exBtn.click();
          else if (auto === "generate") genBtn.click();
        } catch {}
      }, 120);
    }

    // Ensure we reflect API readiness (env vars / saved settings) on first render.
    refreshLLMStatus()
      .catch(() => {})
      .finally(() => syncGenerateUi());

    return root;
  }

  function pageAudit() {
    renderHeader("全稿体检", "输入：你的论文 PDF + 同领域范文库。输出：哪里不像范文 → 参考哪段范文 → 怎么改更像（白箱证据）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const uploadInput = el("input", { type: "file", accept: ".pdf,application/pdf", style: "display:none" });
    const uploadBtn = el("button", { class: "btn btn-primary", type: "button" }, "上传论文 PDF");
    const refreshBtn = el("button", { class: "btn", type: "button" }, "刷新列表");
    const paperSel = el("select", { class: "select", style: "flex:1; min-width:260px" });
    const openBtn = el("button", { class: "btn", type: "button" }, "外部打开");
    const previewBtn = el("button", { class: "btn", type: "button" }, "预览第 1 页");
    const paperHint = el("div", { class: "muted" }, "—");

    let paperMetaById = {};

    async function syncPapers() {
      const r = await apiGet("/api/audit/papers?limit=30");
      const arr = (r && r.papers) || [];
      paperMetaById = {};
      clear(paperSel);
      paperSel.appendChild(el("option", { value: "" }, "— 选择已上传论文 PDF —"));
      for (const it of arr) {
        const id = String((it && it.id) || "").trim();
        const name = String((it && it.filename) || "").trim() || id;
        if (!id) continue;
        paperMetaById[id] = it;
        paperSel.appendChild(el("option", { value: id }, name));
      }
      if (state.auditPaperId && paperMetaById[state.auditPaperId]) paperSel.value = state.auditPaperId;
      else if (arr && arr.length && arr[0] && arr[0].id) {
        // Auto-select newest if nothing picked yet.
        if (!state.auditPaperId) state.auditPaperId = String(arr[0].id);
        paperSel.value = state.auditPaperId;
      }
      localStorage.setItem("aiw.auditPaperId", state.auditPaperId || "");
      const m = state.auditPaperId ? paperMetaById[state.auditPaperId] : null;
      paperHint.textContent = m ? `已选论文：${m.filename || m.id}` : "请先上传论文 PDF。";
    }

    uploadBtn.onclick = () => {
      try {
        uploadInput.value = "";
      } catch {}
      uploadInput.click();
    };

    uploadInput.onchange = async () => {
      const f = uploadInput.files && uploadInput.files[0] ? uploadInput.files[0] : null;
      if (!f) return;
      uploadBtn.disabled = true;
      uploadBtn.textContent = "上传中…";
      try {
        const fd = new FormData();
        fd.append("file", f, f.name || "paper.pdf");
        const r = await apiFormPost("/api/audit/upload_paper_pdf", fd);
        const p = r && r.paper ? r.paper : null;
        const pid = p && p.id ? String(p.id) : "";
        if (pid) {
          state.auditPaperId = pid;
          localStorage.setItem("aiw.auditPaperId", state.auditPaperId);
        }
        toast("已上传论文 PDF。");
        await syncPapers();
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = "上传论文 PDF";
      }
    };

    refreshBtn.onclick = () => syncPapers().catch((e) => toast(String(e.message || e), "bad", 6500));

    paperSel.onchange = () => {
      state.auditPaperId = paperSel.value || "";
      localStorage.setItem("aiw.auditPaperId", state.auditPaperId || "");
      const m = state.auditPaperId ? paperMetaById[state.auditPaperId] : null;
      paperHint.textContent = m ? `已选论文：${m.filename || m.id}` : "请先上传论文 PDF。";
    };

    openBtn.onclick = async () => {
      if (!state.auditPaperId) return toast("请先上传/选择论文 PDF。", "bad", 4500);
      try {
        await apiPost("/api/audit/open_paper", { paper_id: state.auditPaperId });
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      }
    };

    previewBtn.onclick = () => {
      if (!state.auditPaperId) return toast("请先上传/选择论文 PDF。", "bad", 4500);
      const m = paperMetaById[state.auditPaperId] || null;
      openAuditPaperPreview(state.auditPaperId, (m && m.filename) || "", 1);
    };

    const paperCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "1) 论文 PDF"),
      el("div", { class: "row" }, uploadBtn, refreshBtn),
      el("div", { class: "row" }, paperSel, openBtn, previewBtn),
      paperHint,
      uploadInput
    );

    // Options
    const topk = el("input", { class: "input", style: "width:110px", value: localStorage.getItem("aiw.auditTopK") || "20", inputmode: "numeric" });
    const maxSents = el("input", { class: "input", style: "width:140px", value: localStorage.getItem("aiw.auditMaxSentences") || "3600", inputmode: "numeric" });
    const minLen = el("input", { class: "input", style: "width:140px", value: localStorage.getItem("aiw.auditMinLen") || "20", inputmode: "numeric" });
    const lowThr = el("input", { class: "input", style: "width:140px", value: localStorage.getItem("aiw.auditLowThr") || "0.35", inputmode: "decimal" });
    const maxPages = el("input", { class: "input", style: "width:140px", value: localStorage.getItem("aiw.auditMaxPages") || "", inputmode: "numeric", placeholder: "不限制" });

    const includeCitecheck = el("input", { type: "checkbox" });
    includeCitecheck.checked = localStorage.getItem("aiw.auditIncludeCitecheck") !== "0";
    const useLLM = el("input", { type: "checkbox" });
    useLLM.checked = localStorage.getItem("aiw.auditUseLLM") !== "0";
    const useLLMReview = el("input", { type: "checkbox" });
    useLLMReview.checked = localStorage.getItem("aiw.auditUseLLMReview") !== "0";
    const maxTokensKey = "aiw.auditMaxLLMTokens";
    let maxTokensInit = String(localStorage.getItem(maxTokensKey) || "").trim();
    if (!maxTokensInit) {
      // Legacy migration: if user previously set cost budget, convert to tokens.
      const oldMaxCost = Number(String(localStorage.getItem("aiw.auditMaxCost") || "").trim());
      const oldCpm = Number(String(localStorage.getItem("aiw.auditCostPer1M") || "").trim());
      if (Number.isFinite(oldMaxCost) && Number.isFinite(oldCpm) && oldMaxCost > 0 && oldCpm > 0) {
        maxTokensInit = String(Math.max(0, Math.min(10000000, Math.round((oldMaxCost / oldCpm) * 1000000))));
      } else {
        maxTokensInit = "200000";
      }
      localStorage.setItem(maxTokensKey, maxTokensInit);
    }
    const maxTokens = el("input", { class: "input", style: "width:160px", value: maxTokensInit, inputmode: "numeric" });

    // Optional: cost estimate (unitless). Used only for display.
    const maxCost = el("input", { class: "input", style: "width:120px", value: localStorage.getItem("aiw.auditMaxCost") || "0", inputmode: "decimal" });
    const costPer1M = el("input", { class: "input", style: "width:160px", value: localStorage.getItem("aiw.auditCostPer1M") || "0", inputmode: "decimal" });
    const refsSel = el("select", { class: "select", style: "min-width:260px" });
    const refsKey = "aiw.auditRefsLibrary";
    const savedRefs = String(localStorage.getItem(refsKey) || "").trim();

    function syncRefsSel() {
      clear(refsSel);
      refsSel.appendChild(el("option", { value: "" }, "— 选择参考文献库（可选）—"));
      for (const lib of librariesForRoute("norms", state.libraries || [])) {
        const name = String((lib && lib.name) || "").trim();
        if (!name) continue;
        refsSel.appendChild(el("option", { value: name }, name));
      }
      if (savedRefs) refsSel.value = savedRefs;
    }
    syncRefsSel();
    refsSel.onchange = () => localStorage.setItem(refsKey, refsSel.value || "");

    includeCitecheck.onchange = () => localStorage.setItem("aiw.auditIncludeCitecheck", includeCitecheck.checked ? "1" : "0");
    useLLM.onchange = () => localStorage.setItem("aiw.auditUseLLM", useLLM.checked ? "1" : "0");
    useLLMReview.onchange = () => localStorage.setItem("aiw.auditUseLLMReview", useLLMReview.checked ? "1" : "0");
    maxTokens.onchange = () => localStorage.setItem(maxTokensKey, String(maxTokens.value || "").trim());
    maxCost.onchange = () => localStorage.setItem("aiw.auditMaxCost", String(maxCost.value || "").trim());
    costPer1M.onchange = () => localStorage.setItem("aiw.auditCostPer1M", String(costPer1M.value || "").trim());

    const optionsCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "2) 体检设置"),
      el("div", { class: "muted" }, "范文库从右上角选择（同领域越强，建议越像）。"),
      el(
        "details",
        null,
        el("summary", null, "高级设置（可选）"),
        el(
          "div",
          { class: "grid", style: "gap:12px; margin-top:10px" },
          el("div", { class: "row" }, el("span", { class: "muted" }, "范文证据条数"), topk, el("span", { class: "muted" }, "最多扫描句子"), maxSents),
          el("div", { class: "row" }, el("span", { class: "muted" }, "最短句长"), minLen, el("span", { class: "muted" }, "低对齐阈值"), lowThr),
          el("div", { class: "row" }, el("span", { class: "muted" }, "最多页数"), maxPages, el("span", { class: "muted" }, "（空=不限制）")),
          el(
            "div",
            { class: "row" },
            useLLMReview,
            el("span", null, "LLM 分治体检（更像人工审稿）"),
            el("span", { class: "muted" }, "（默认只给诊断+模板，不直接改写）")
          ),
          el(
            "div",
            { class: "row" },
            el("span", { class: "muted" }, "LLM 预算（tokens）"),
            maxTokens,
            el("span", { class: "muted" }, "（0=不限制；建议 200000 起）")
          ),
          el(
            "div",
            { class: "row" },
            el("span", { class: "muted" }, "成本估算（可选）"),
            maxCost,
            el("span", { class: "muted" }, "单价（/100万 tokens）"),
            costPer1M,
            el("span", { class: "muted" }, "（单位自定，仅用于展示）")
          ),
          el("div", { class: "row" }, includeCitecheck, el("span", null, "同时做引用核查（可选）"), refsSel),
          el("div", { class: "row" }, useLLM, el("span", null, "引用核查使用大模型判定（更准）"), el("span", { class: "muted" }, "（需要在“润色设置”配置）"))
        )
      )
    );

    const runBtn = el("button", { class: "btn btn-primary", type: "button" }, "开始体检");
    const cancelBtn = el("button", { class: "btn btn-danger", type: "button" }, "取消");
    cancelBtn.disabled = !state.auditTaskId;

    const progressBar = el("div", { class: "progress" }, el("div"));
    const progressText = el("div", { class: "muted mono" }, "—");

    function setProgress(pct, text) {
      if (pct != null) progressBar.firstChild.style.width = `${Math.max(0, Math.min(100, Math.round(pct)))}%`;
      if (text != null) progressText.textContent = String(text || "—");
    }

    function updateProgressUI(t) {
      if (!t) return setProgress(null, "—");
      const done = Number(t.done || 0);
      const total = Number(t.total || 0);
      const pct = total > 0 ? (done / total) * 100 : null;
      const st = humanTaskStage(t.stage || "");
      const detail = String(t.detail || "").trim();
      setProgress(pct, `${st} · ${done}/${total}${detail ? ` · ${detail}` : ""}`);
    }

    const resultsBox = el("div", { class: "card" });
    const lastResultKey = "aiw.auditLastResultTaskId";

    function issueBadge(it) {
      const t = String((it && it.issue_type) || "").trim();
      const sev = String((it && it.severity) || "").trim().toLowerCase();
      const cls = sev === "warning" ? "badge bad" : "badge warn";
      const label =
        t === "low_alignment"
          ? "对齐度低"
          : t === "ai_transition"
            ? "AI 过渡词"
            : t === "ai_word"
              ? "AI 高频词"
              : t === "long_sentence"
                ? "句子偏长"
                : t === "passive"
                  ? "被动句"
                  : t === "template"
                    ? "模板句"
                    : t === "repetition"
                      ? "开头重复"
                      : t === "syntax_outlier"
                        ? "句法异常"
                        : t || "问题";
      return el("span", { class: cls }, label);
    }

    function renderAuditResult(r) {
      clear(resultsBox);
      if (!r || typeof r !== "object") {
        resultsBox.appendChild(el("div", { class: "muted" }, "还没有结果。"));
        return;
      }
      state.lastAudit = r;

      const meta = (r && r.meta) || {};
      const sum = (r && r.summary) || {};
      const items = Array.isArray(r.items) ? r.items : [];
      const llmUsage = r && typeof r.llm_usage === "object" ? r.llm_usage : null;
      const llmReviews = r && typeof r.llm_reviews === "object" ? r.llm_reviews : {};

      // Map sentence-id -> LLM review (if present)
      const sentReviewById = (() => {
        const out = {};
        try {
          const pack = llmReviews && llmReviews.sentence_alignment && typeof llmReviews.sentence_alignment === "object" ? llmReviews.sentence_alignment : null;
          const arr = pack && Array.isArray(pack.items) ? pack.items : [];
          for (const it of arr) {
            const id = Number((it && it.id) || 0);
            if (!Number.isFinite(id) || id < 0) continue;
            out[Math.round(id)] = it;
          }
        } catch {}
        return out;
      })();

      const header = el(
        "div",
        { class: "grid", style: "gap:10px" },
        el("div", { class: "label" }, "体检结果"),
        el(
          "div",
          { class: "muted" },
          `语言：${meta.language || "—"} · 句子总数：${sum.sentence_total || 0} · 已扫描：${sum.sentence_scored || 0} · 对齐度低：${sum.low_alignment_sentences || 0}` +
            (meta.truncated ? " ·（已抽样，建议调大“最多扫描句子”）" : "")
        ),
        el(
          "div",
          { class: "row" },
          el(
            "button",
            {
              class: "btn btn-small",
              type: "button",
              onclick: () => downloadText(`audit_${Date.now()}.json`, JSON.stringify(r, null, 2), "application/json;charset=utf-8"),
            },
            "导出 JSON"
          )
        )
      );
      resultsBox.appendChild(header);

      if (llmUsage && Number(llmUsage.calls || 0) > 0) {
        const calls = Number(llmUsage.calls || 0) || 0;
        const tokens = (Number(llmUsage.total_tokens || 0) || 0) + (Number(llmUsage.approx_total_tokens || 0) || 0);
        const maxTokens = Number(llmUsage.max_total_tokens || 0) || 0;
        const remainingTokens = Number(llmUsage.remaining_tokens || 0) || 0;
        const tokenCls = maxTokens > 0 && tokens > maxTokens ? "bad" : "good";

        // Optional legacy cost estimate (unitless; depends on user-provided rate).
        const cost = Number(llmUsage.estimated_cost || llmUsage.estimated_cost_rmb || 0) || 0;
        const budget = Number(llmUsage.max_cost || llmUsage.max_cost_rmb || 0) || 0;
        const costPer = Number(llmUsage.cost_per_1m_tokens || llmUsage.cost_per_1m_tokens_rmb || 0) || 0;
        resultsBox.appendChild(
          el(
            "div",
            { class: "row", style: "gap:10px; flex-wrap:wrap; margin-top:10px" },
            el("span", { class: "badge good" }, `LLM 调用 ${calls} 次`),
            el(
              "span",
              { class: "badge " + tokenCls },
              maxTokens > 0 ? `tokens≈${tokens} / 预算 ${maxTokens}（剩余≈${remainingTokens}）` : `tokens≈${tokens}`
            ),
            costPer > 0 && budget > 0 ? el("span", { class: "badge " + (cost <= budget ? "good" : "bad") }, `成本≈${cost.toFixed(3)} / 预算 ${budget}`) : null
          )
        );
      }

      // Outline review (structure-level)
      try {
        const outline = llmReviews && llmReviews.outline && typeof llmReviews.outline === "object" ? llmReviews.outline : null;
        if (outline && !outline.skipped) {
          const issues = Array.isArray(outline.issues) ? outline.issues : [];
          if (issues.length) {
            const box = el("div", { class: "card", style: "margin-top:12px" });
            box.appendChild(el("div", { class: "label" }, "章节结构审稿（LLM 分治）"));
            if (outline.summary) box.appendChild(el("div", { class: "muted" }, String(outline.summary || "")));
            const ul = el("div", { class: "grid", style: "gap:10px; margin-top:10px" });
            for (const it of issues.slice(0, 12)) {
              if (!it || typeof it !== "object") continue;
              ul.appendChild(
                el(
                  "div",
                  { class: "item" },
                  el("div", { class: "quote" }, String(it.problem || "结构问题")),
                  it.detail ? el("div", { class: "muted" }, String(it.detail || "")) : null,
                  it.suggestion ? el("div", null, String(it.suggestion || "")) : null
                )
              );
            }
            box.appendChild(ul);
            resultsBox.appendChild(box);
          }
        }
      } catch {}

      const filterKey = "aiw.auditFilter";
      let filter = String(localStorage.getItem(filterKey) || "low");
      const filterAll = el("button", { class: "chip", type: "button" }, "全部问题");
      const filterLow = el("button", { class: "chip", type: "button" }, "只看对齐度低");
      const filterWarn = el("button", { class: "chip", type: "button" }, "只看警告");

      const filtersRow = el("div", { class: "row", style: "gap:10px; flex-wrap:wrap; margin-top:10px" }, filterAll, filterLow, filterWarn);
      resultsBox.appendChild(filtersRow);

      const list = el("div", { class: "list" });
      resultsBox.appendChild(list);

      function renderList() {
        clear(list);
        const show = [];
        for (const it of items) {
          const issues = Array.isArray(it.issues) ? it.issues : [];
          const hasLow = issues.some((x) => x && x.issue_type === "low_alignment");
          const hasWarn = issues.some((x) => x && String(x.severity || "").toLowerCase() === "warning");
          if (filter === "low" && !hasLow) continue;
          if (filter === "warn" && !hasWarn) continue;
          show.push(it);
        }
        if (!show.length) {
          list.appendChild(el("div", { class: "muted" }, "没有匹配的条目。"));
          return;
        }

        for (const it of show.slice(0, 280)) {
          const page = Number(it.page || 0) || 0;
          const text = String(it.text || "").trim();
          const issues = Array.isArray(it.issues) ? it.issues : [];
          const align = it.alignment && typeof it.alignment === "object" ? it.alignment : {};
          const exs = Array.isArray(align.exemplars) ? align.exemplars : [];
          const topEx = exs && exs.length ? exs[0] : null;
          const pct = Number(align.pct || 0) || 0;
          const paperName = (meta && meta.paper_filename) || "论文.pdf";

          const badges = el("div", { class: "row", style: "gap:8px; flex-wrap:wrap" }, ...issues.slice(0, 6).map((x) => issueBadge(x)));
          const actions = el(
            "div",
            { class: "row", style: "gap:8px; flex-wrap:wrap" },
            el("span", { class: "badge " + (pct >= 55 ? "good" : pct >= 40 ? "warn" : "bad") }, `对齐度 ${pct}%`),
            page ? el("span", { class: "muted" }, `· 论文 p${page}`) : null,
            el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(text) }, "复制句子"),
            page ? el("button", { class: "btn btn-small", type: "button", onclick: () => openAuditPaperPreview(meta.paper_id || state.auditPaperId, paperName, page) }, "预览论文页") : null,
            state.library && topEx && topEx.pdf
              ? el(
                  "button",
                  { class: "btn btn-small", type: "button", onclick: () => openLibraryPdfPreview(state.library, topEx.pdf, topEx.page || 1) },
                  "预览范文"
                )
              : null,
            el(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: () => {
                  state.polishDraft = text;
                  localStorage.setItem("aiw.polishDraft", state.polishDraft);
                  localStorage.setItem("aiw.polishAutoRun", "exemplars");
                  setRoute("polish");
                },
              },
              "去对齐润色"
            )
          );

          const scaffold = topEx && topEx.scaffold ? String(topEx.scaffold || "").trim() : "";
          const scaffoldRow = scaffold
            ? el(
                "div",
                { class: "row", style: "gap:8px; align-items:flex-start; margin-top:10px" },
                el("span", { class: "muted" }, "可复用句式"),
                el(
                  "button",
                  {
                    class: "chip scaffold",
                    type: "button",
                    title: "点击复制",
                    onclick: async () => {
                      await copyText(scaffold);
                    },
                  },
                  scaffold
                )
              )
            : null;

          const review = sentReviewById[Number(it.id || 0) || 0] || null;
          const reviewBox = (() => {
            if (!review) return null;
            const diags = Array.isArray(review.diagnosis) ? review.diagnosis : [];
            const temps = Array.isArray(review.templates) ? review.templates : [];
            const diagList = el(
              "div",
              { class: "grid", style: "gap:10px; margin-top:10px" },
              ...diags.slice(0, 4).map((d) => {
                if (!d || typeof d !== "object") return null;
                const ev = Array.isArray(d.evidence) ? d.evidence : [];
                const quotes = ev.map((x) => (x && x.quote ? String(x.quote) : "")).filter(Boolean);
                return el(
                  "div",
                  { class: "item" },
                  el("div", { class: "quote" }, String(d.problem || "问题")),
                  d.suggestion ? el("div", null, String(d.suggestion || "")) : null,
                  quotes.length ? el("div", { class: "muted" }, "范文证据（摘录）： " + quotes.slice(0, 2).join(" · ")) : null
                );
              })
            );
            const tempRow =
              temps.length > 0
                ? el(
                    "div",
                    { class: "row", style: "gap:8px; flex-wrap:wrap; margin-top:10px" },
                    el("span", { class: "muted" }, "可复用模板"),
                    ...temps.slice(0, 6).map((t) => {
                      const tx = String((t && t.text) || "").trim();
                      if (!tx) return null;
                      return el(
                        "button",
                        { class: "chip scaffold", type: "button", title: "点击复制", onclick: async () => copyText(tx) },
                        tx
                      );
                    })
                  )
                : null;
            return el(
              "details",
              { style: "margin-top:10px" },
              el("summary", null, "LLM 对齐诊断（白箱：问题+模板+范文证据）"),
              tempRow,
              diagList
            );
          })();

          list.appendChild(
            el(
              "div",
              { class: "item" },
              el("div", { class: "row", style: "justify-content:space-between; align-items:flex-start; gap:10px" }, badges, actions),
              el("div", { class: "quote" }, text),
              scaffoldRow,
              reviewBox,
              topEx && topEx.text ? el("div", { class: "muted" }, `范文证据预览：${String(topEx.text || "").slice(0, 240)}${String(topEx.text || "").length > 240 ? "…" : ""}`) : null
            )
          );
        }
        if (show.length > 280) list.appendChild(el("div", { class: "muted" }, `仅展示前 280 条（共 ${show.length} 条）。可导出 JSON 查看全部。`));
      }

      function applyFilter(id) {
        filter = id;
        localStorage.setItem(filterKey, filter);
        filterAll.classList.toggle("active", filter === "all");
        filterLow.classList.toggle("active", filter === "low");
        filterWarn.classList.toggle("active", filter === "warn");
        renderList();
      }
      filterAll.onclick = () => applyFilter("all");
      filterLow.onclick = () => applyFilter("low");
      filterWarn.onclick = () => applyFilter("warn");
      applyFilter(filter === "warn" || filter === "all" ? filter : "low");

      renderList();
    }

    function renderEmptyHint() {
      clear(resultsBox);
      resultsBox.appendChild(el("div", { class: "label" }, "还没有体检结果"));
      resultsBox.appendChild(el("div", { class: "muted" }, "流程：上传论文 PDF → 选择范文库（右上角）→ 开始体检。"));
      resultsBox.appendChild(el("div", { class: "muted" }, "提示：体检结束后，点击“去对齐润色”可以对白箱证据逐句改写。"));
    }
    renderEmptyHint();

    function stopAuditPolling() {
      if (state.auditPollTimer) window.clearInterval(state.auditPollTimer);
      state.auditPollTimer = null;
    }

    async function fetchAndRenderResult(taskId) {
      const r = await apiGet(`/api/audit/result?task_id=${encodeURIComponent(taskId)}`);
      renderAuditResult(r);
      try {
        localStorage.setItem(lastResultKey, String(taskId || ""));
      } catch {}
      return r;
    }

    async function pollOnce() {
      if (!state.auditTaskId) return;
      // If user navigated away, stop polling.
      if (!document.body.contains(progressText)) return stopAuditPolling();

      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(state.auditTaskId)}`);
        updateProgressUI(t);
        if (t.status !== "running") {
          stopAuditPolling();
          const finishedId = state.auditTaskId;
          state.auditTaskId = "";
          localStorage.setItem("aiw.auditTaskId", "");
          cancelBtn.disabled = true;
          runBtn.disabled = false;
          runBtn.textContent = "开始体检";

          if (t.status === "done") {
            toast("体检完成。");
            await fetchAndRenderResult(finishedId);
          } else if (t.status === "canceled") {
            toast("已取消。", "bad");
          } else {
            toast("失败：" + (t.error || ""), "bad", 6500);
          }
        }
      } catch (e) {
        stopAuditPolling();
        const msg = String(e && e.message ? e.message : e);
        const lost = msg.toLowerCase().includes("task not found");
        const prev = state.auditTaskId;
        state.auditTaskId = "";
        localStorage.setItem("aiw.auditTaskId", "");
        cancelBtn.disabled = true;
        runBtn.disabled = false;
        runBtn.textContent = "开始体检";
        toast(lost ? "任务已丢失（可能因为服务重启/关闭）。请重新开始体检。" : msg, "bad", 6500);
        if (prev && lost) fetchAndRenderResult(prev).catch(() => {});
      }
    }

    function startAuditPolling() {
      stopAuditPolling();
      runBtn.disabled = true;
      runBtn.textContent = "体检中…";
      cancelBtn.disabled = false;
      pollOnce();
      state.auditPollTimer = window.setInterval(pollOnce, 1000);
    }

    runBtn.onclick = async () => {
      if (!state.library) {
        openPrepWizard({ need: "rag", resume: { route: "audit" } });
        return toast("请先选择并准备范文库（右上角）。", "bad", 4500);
      }
      if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
        openPrepWizard({ need: "rag", resume: { route: "audit" } });
        return toast("范文库还没准备好：先完成一次“导入 + 一键准备”。", "bad", 4500);
      }
      if (!state.auditPaperId) return toast("请先上传/选择论文 PDF。", "bad", 4500);

      if (includeCitecheck.checked) {
        if (!refsSel.value) return toast("已勾选引用核查：请先选择参考文献库。", "bad", 4500);
        if (useLLM.checked && !llmConfigured(state.llmApi)) {
          toast("引用核查使用大模型判定：请先到“润色设置”配置接口。", "bad", 6500);
          setRoute("llm");
          return;
        }
        // Do not hard-block on llmUsable() here; allow retry on flaky networks.
        if (useLLM.checked && !llmUsable(state.llmApi)) {
          toast("大模型上次测试失败：仍会尝试运行（已加重试），如失败请到“润色设置”重新测试。", "bad", 6500);
        }
      }

      const tk = Math.max(1, Math.min(50, Number((topk.value || "").trim() || 20) || 20));
      topk.value = String(tk);
      localStorage.setItem("aiw.auditTopK", String(tk));

      const ms = Math.max(50, Math.min(20000, Number((maxSents.value || "").trim() || 3600) || 3600));
      maxSents.value = String(ms);
      localStorage.setItem("aiw.auditMaxSentences", String(ms));

      const ml = Math.max(8, Math.min(400, Number((minLen.value || "").trim() || 20) || 20));
      minLen.value = String(ml);
      localStorage.setItem("aiw.auditMinLen", String(ml));

      const thr = Math.max(0.05, Math.min(0.95, Number((lowThr.value || "").trim() || 0.35) || 0.35));
      lowThr.value = String(thr);
      localStorage.setItem("aiw.auditLowThr", String(thr));

      let mp = null;
      const mpRaw = String(maxPages.value || "").trim();
      if (mpRaw) {
        const n = Number(mpRaw);
        if (Number.isFinite(n) && n > 0) mp = Math.max(1, Math.min(300, Math.round(n)));
      }
      localStorage.setItem("aiw.auditMaxPages", mpRaw);

      // Token budget (recommended). 0 => unlimited.
      let mtRaw = String(maxTokens.value || "").trim();
      let mt = 200000;
      if (mtRaw) {
        const n = Number(mtRaw);
        if (Number.isFinite(n)) mt = Math.max(0, Math.min(10000000, Math.round(n)));
      }
      maxTokens.value = String(mt);
      localStorage.setItem(maxTokensKey, String(mt));

      // Optional cost estimate (unitless; only for display in report).
      let mcRaw = String(maxCost.value || "").trim();
      let cpmRaw = String(costPer1M.value || "").trim();
      let mc = 0;
      let cpm = 0;
      if (mcRaw && cpmRaw) {
        const mcN = Number(mcRaw);
        const cpmN = Number(cpmRaw);
        if (Number.isFinite(mcN) && Number.isFinite(cpmN) && mcN > 0 && cpmN > 0) {
          mc = Math.max(0, Math.min(1000000, mcN));
          cpm = Math.max(0, Math.min(1000000, cpmN));
        }
      }
      maxCost.value = mc ? String(mc) : "0";
      costPer1M.value = cpm ? String(cpm) : "0";
      localStorage.setItem("aiw.auditMaxCost", String(maxCost.value || "").trim());
      localStorage.setItem("aiw.auditCostPer1M", String(costPer1M.value || "").trim());

      runBtn.disabled = true;
      runBtn.textContent = "启动中…";
      try {
        const body = {
          exemplar_library: state.library,
          paper_id: state.auditPaperId,
          top_k: tk,
          max_sentences: ms,
          min_sentence_len: ml,
          low_alignment_threshold: thr,
          max_pages: mp,
          use_llm_review: !!useLLMReview.checked,
          max_llm_tokens: mt,
          max_cost: mc,
          cost_per_1m_tokens: cpm,
          include_citecheck: !!includeCitecheck.checked,
          references_library: refsSel.value || "",
          use_llm: !!useLLM.checked,
        };
        const r = await apiPost("/api/audit/run", body, { timeout_ms: 180000 });
        state.auditTaskId = (r && r.task_id) || "";
        localStorage.setItem("aiw.auditTaskId", state.auditTaskId || "");
        toast("已开始体检（后台进行）。");
        startAuditPolling();
      } catch (e) {
        const msg = String(e.message || e);
        if (msg.toLowerCase().includes("missing api key") || msg.toLowerCase().includes("missing base_url") || msg.toLowerCase().includes("missing model")) {
          toast(msg, "bad", 6500);
          setRoute("llm");
        } else if (maybeOpenIndexModalForError(msg)) {
          // already handled
        } else {
          toast(msg, "bad", 6500);
        }
      } finally {
        if (state.auditTaskId) {
          runBtn.disabled = true;
          runBtn.textContent = "体检中…";
          cancelBtn.disabled = false;
        } else {
          runBtn.disabled = false;
          runBtn.textContent = "开始体检";
          cancelBtn.disabled = true;
        }
      }
    };

    cancelBtn.onclick = async () => {
      if (!state.auditTaskId) return;
      try {
        await apiPost(`/api/tasks/${encodeURIComponent(state.auditTaskId)}/cancel`, {});
        toast("已请求取消。");
      } catch (e) {
        toast(String(e.message || e), "bad");
      }
    };

    const actionsCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "3) 开始体检"),
      el("div", { class: "row" }, runBtn, cancelBtn),
      progressBar,
      progressText
    );

    root.appendChild(paperCard);
    root.appendChild(optionsCard);
    root.appendChild(actionsCard);
    root.appendChild(resultsBox);

    syncPapers().catch(() => {});
    if (state.auditTaskId) startAuditPolling();
    else {
      const lastId = String(localStorage.getItem(lastResultKey) || "").trim();
      if (lastId) fetchAndRenderResult(lastId).catch(() => renderEmptyHint());
    }

    return root;
  }

  function pageCite() {
    renderHeader("引用写法", "从范文中抽取“引用句子 + 参考文献”，用于白箱检索可借鉴的引用表达。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const statusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取引用库状态…"));

    async function syncStatus() {
      if (!state.library) {
        clear(statusBox);
        statusBox.appendChild(el("div", { class: "label" }, "还不能开始：请先准备范文库"));
        statusBox.appendChild(el("div", { class: "muted" }, "引用写法需要先有范文库（PDF）作为证据来源。"));
        statusBox.appendChild(
          el(
            "div",
            { class: "row" },
            el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "cite", resume: { route: "cite" } }) }, "一键准备"),
            el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
          )
        );
        return;
      }
      try {
        const st = await apiGet(`/api/cite/status?library=${encodeURIComponent(state.library)}`);
        const ok = !!st.cite_index;
        const m = st.manifest || {};
        clear(statusBox);
        statusBox.appendChild(
          el(
            "div",
            { class: "row" },
            el("span", { class: "badge " + (ok ? "good" : "bad") }, ok ? "已构建" : "未构建"),
            el("span", { class: "muted mono" }, m.pdf_root || "—"),
            ok && m.citation_sentence_count != null ? el("span", { class: "muted" }, `句子：${m.citation_sentence_count}`) : null,
            ok && m.reference_count != null ? el("span", { class: "muted" }, `参考文献：${m.reference_count}`) : null
          )
        );
        if (!ok) {
          statusBox.appendChild(el("div", { class: "muted" }, "提示：先准备好范文库，再抽取引用写法会更顺。"));
        }
      } catch (e) {
        clear(statusBox);
        statusBox.appendChild(el("div", { class: "muted" }, "无法读取引用库状态（可先准备范文库）。"));
      }
    }

    const maxPages = el("input", {
      class: "input",
      placeholder: "最多处理页数（可选）",
      style: "width:150px",
      inputmode: "numeric",
      title: "可选：只处理前 N 页（用于加速；留空代表不限制）",
    });

    const buildBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
          onclick: async () => {
            if (!state.library) {
              openPrepWizard({ need: "cite", resume: { route: "cite" } });
              return toast("先准备范文库（可选：同时准备引用写法）。", "bad", 4500);
            }
          buildBtn.disabled = true;
          buildBtn.textContent = "启动中…";
          try {
            const body = { library: state.library };
            const mp = Number((maxPages.value || "").trim() || 0);
            if (mp > 0) body.max_pages = mp;
            const r = await apiPost("/api/cite/build", body);
            state.citeTaskId = r.task_id;
            localStorage.setItem("aiw.citeTaskId", state.citeTaskId || "");
            startCitePolling();
            toast("已开始抽取引用句子（后台进行）。");
          } catch (e) {
            toast(String(e.message || e), "bad", 6500);
          } finally {
            buildBtn.disabled = false;
            buildBtn.textContent = "准备引用写法（抽取引用证据）";
          }
        },
      },
      "准备引用写法（抽取引用证据）"
    );

    const citeProgress = el("div", { class: "progress" }, el("div"));
    const citeProgressText = el("div", { class: "muted mono" }, "—");
    const citeCancelBtn = el(
      "button",
      {
        class: "btn btn-danger btn-small",
        type: "button",
        onclick: async () => {
          if (!state.citeTaskId) return;
          try {
            await apiPost(`/api/tasks/${encodeURIComponent(state.citeTaskId)}/cancel`, {});
            toast("已请求取消。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "取消"
    );

    function updateCiteProgressUI(t) {
      if (!t) return;
      const done = Number(t.done || 0);
      const total = Number(t.total || 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
      citeProgress.firstChild.style.width = `${pct}%`;
      const stage = humanTaskStage(t.stage);
      const status = humanTaskStatus(t.status);
      const detail = String(t.detail || "");
      citeProgressText.textContent = `${status} · ${stage} · ${done}/${total} ${detail ? "· " + detail : ""}`;
    }

    async function pollCiteOnce() {
      if (!state.citeTaskId) return;
      if (!document.body.contains(citeProgressText)) return stopCitePolling();
      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(state.citeTaskId)}`);
        updateCiteProgressUI(t);
        if (t.status !== "running") {
          stopCitePolling();
          state.citeTaskId = "";
          localStorage.setItem("aiw.citeTaskId", "");
          await refreshLibraryStatus();
          await syncStatus();
          if (t.status === "done") toast("引用库构建完成。");
          else if (t.status === "canceled") toast("引用库构建已取消。", "bad");
          else toast("引用库构建失败：" + (t.error || ""), "bad", 6500);
        }
      } catch (e) {
        stopCitePolling();
        toast(String(e.message || e), "bad");
      }
    }

    function startCitePolling() {
      stopCitePolling();
      pollCiteOnce();
      state.citePollTimer = window.setInterval(pollCiteOnce, 1000);
    }

    function stopCitePolling() {
      if (state.citePollTimer) window.clearInterval(state.citePollTimer);
      state.citePollTimer = null;
    }

    const query = el("input", { class: "input", placeholder: "搜索：例如 “Following”, “we contribute”, “et al.”", style: "flex:1; min-width:360px" });
    query.value = localStorage.getItem("aiw.citeQueryDraft") || "";
    const topk = el("input", { class: "input", value: "10", style: "width:100px", inputmode: "numeric" });
    const searchBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
          onclick: async () => {
            if (!state.library) {
              openPrepWizard({ need: "cite", resume: { route: "cite", autoKey: "aiw.citeAutoRun", autoValue: "1" } });
              return toast("先准备范文库（可选：同时准备引用写法）。", "bad", 4500);
            }
          const q = (query.value || "").trim();
          if (!q) return toast("请输入搜索关键词。", "bad");
          searchBtn.disabled = true;
          searchBtn.textContent = "检索中…";
          try {
            const r = await apiPost("/api/cite/search", { library: state.library, query: q, top_k: Number(topk.value || 10) });
            state.lastCite = r;
            toast("检索完成。");
            renderCiteResults();
          } catch (e) {
            const msg = String(e.message || e);
            if (!maybeOpenIndexModalForError(msg)) toast(msg, "bad", 6500);
          } finally {
            searchBtn.disabled = false;
            searchBtn.textContent = "检索引用写法";
          }
        },
      },
      "检索引用写法"
    );

    const resultsBox = el("div", { class: "card" }, el("div", { class: "muted" }, "检索结果将在这里显示。"));

    async function openRefs(pdfRel) {
      if (!pdfRel) return;
      try {
        const r = await apiGet(
          `/api/cite/references?library=${encodeURIComponent(state.library)}&pdf=${encodeURIComponent(pdfRel)}&limit=1200`
        );
        const refs = (r && r.references) || [];
        const body = el(
          "div",
          { class: "grid", style: "gap:10px" },
          el("div", { class: "muted" }, `共 ${refs.length} 条（自动抽取，可能有噪声）。`),
          el(
            "div",
            { class: "list" },
            ...refs.map((it) =>
              el(
                "div",
                { class: "item" },
                el(
                  "div",
                  { class: "item-header" },
                  el("span", { class: "badge mono" }, `#${it.index || "?"}`),
                  el("span", { class: "muted mono" }, `${it.year || ""} ${it.authors || ""}`),
                  el(
                    "button",
                    { class: "btn btn-small", type: "button", onclick: () => copyText(String(it.reference || "")) },
                    "复制"
                  )
                ),
                el("div", { class: "quote" }, it.reference || "")
              )
            )
          )
        );
        openModal(`参考文献 · ${pdfRel}`, body);
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      }
    }

    function renderCiteResults() {
      clear(resultsBox);
      const hits = (state.lastCite && state.lastCite.hits) || [];
      if (!hits.length) {
        resultsBox.appendChild(el("div", { class: "muted" }, "没有检索到结果。"));
        return;
      }
      resultsBox.appendChild(el("div", { class: "label" }, `Top ${hits.length}（按相似度排序）`));
      const list = el("div", { class: "list" });
      for (const h of hits) {
        const score = Number(h.score || 0);
        const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
        const badgeCls = pct >= 75 ? "badge good" : pct >= 55 ? "badge" : "badge bad";
        const pdfRel = String(h.pdf || "");
        const page = Number(h.page || 0);
        const sentence = String(h.sentence || "");
        const citations = (h.citations || []).map((c) => `${c.authors || ""} ${c.year || ""}`.trim()).filter(Boolean);

        list.appendChild(
          el(
            "div",
            { class: "item" },
            el(
              "div",
              { class: "item-header" },
              el("div", null, el("span", { class: badgeCls }, `${pct}%`), " ", el("span", null, sentence.slice(0, 220) + (sentence.length > 220 ? "…" : ""))),
              el(
                "div",
                { class: "row" },
                pdfRel ? el("span", { class: "muted mono" }, `${pdfRel}#p${page || 0}`) : el("span", { class: "muted" }, "—"),
                el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(sentence) }, "复制"),
                 pdfRel
                   ? el(
                       "button",
                       {
                         class: "btn btn-small",
                         type: "button",
                         onclick: () => openLibraryPdfPreview(state.library, pdfRel, page || 1),
                       },
                       "预览"
                     )
                   : null,
                pdfRel ? el("button", { class: "btn btn-small", type: "button", onclick: () => openRefs(pdfRel) }, "参考文献") : null
              )
            ),
            citations.length ? el("div", { class: "muted" }, "命中引用：", citations.slice(0, 6).join("; "), citations.length > 6 ? "…" : "") : null,
            el("div", { class: "quote" }, sentence)
          )
        );
      }
      resultsBox.appendChild(list);
    }

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "准备引用写法（一次即可，离线保存）"),
        el("div", { class: "row" }, maxPages, buildBtn, citeCancelBtn),
        citeProgress,
        citeProgressText,
        el("div", { class: "muted" }, "说明：仅抽取“作者-年份”引用句子（如 Smith (2020) / (Smith, 2020; …)）与参考文献。")
      )
    );

    root.appendChild(statusBox);

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "检索引用写法"),
        el("div", { class: "row" }, query, el("span", { class: "label" }, "返回条数"), topk, searchBtn),
        el("div", { class: "muted" }, "用途：找“顶级论文怎么写这句话/怎么引文”，并复制句式（白箱可追溯）。")
      )
    );

    root.appendChild(resultsBox);

    syncStatus().catch(() => {});
    if (state.citeTaskId) startCitePolling();

    const autoRun = localStorage.getItem("aiw.citeAutoRun") === "1";
    if (autoRun) {
      localStorage.removeItem("aiw.citeAutoRun");
      window.setTimeout(() => {
        try {
          searchBtn.click();
        } catch {}
      }, 120);
    }
    return root;
  }

  function pageNorms() {
    renderHeader("写作规范", "白箱核查：引用是否准确 / 是否张冠李戴（每条结论都给原文证据与页码）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const importPapersBtn = el("button", { class: "btn", type: "button" }, "导入参考文献原文 PDF…");
    importPapersBtn.onclick = () => {
      const preset = state.library ? { library: state.library, lockLibrary: true } : {};
      openPrepWizard({ need: "import", resume: { route: "norms" }, ...preset });
    };

    const intro = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "引用核查（白箱）"),
      el("div", null, "用途：检查你的论文里“作者-年份”引用是否准确，避免曲解/张冠李戴，并给出原文证据。"),
      el(
        "div",
        { class: "muted" },
        "提示：如果库里缺少对应的原文 PDF，会提示“未找到原文 PDF”。你可以先“导入参考文献原文 PDF”。"
      ),
      el("div", { class: "muted" }, "注意：右上角“准备范文库”仅用于找差距/对齐润色；引用核查不依赖范文证据索引，只要有参考文献原文 PDF 即可。"),
      el("div", { class: "row" }, importPapersBtn)
    );

    const draftSel = el("select", { class: "select", style: "flex:1; min-width:260px" });
    const draftHint = el("div", { class: "muted" }, "—");
    const uploadInput = el("input", {
      id: "aiwCitecheckMainPdf",
      type: "file",
      accept: ".pdf,application/pdf",
      "data-testid": "citecheck-main-pdf",
      style: "display:none",
    });

    const uploadBtn = el("button", { class: "btn btn-primary", type: "button" }, "上传论文 PDF");
    const openDraftBtn = el("button", { class: "btn btn-small", type: "button" }, "打开论文");
    openDraftBtn.disabled = !state.citecheckDraftId;

    const useLLM = el("input", { type: "checkbox" });
    const maxPairs = el("input", { class: "input", style: "width:120px", inputmode: "numeric" });
    maxPairs.value = String(Math.max(20, Math.min(200, Number(localStorage.getItem("aiw.citecheckMaxPairs") || "80") || 80)));

    const runBtn = el("button", { class: "btn btn-primary", type: "button" }, "开始引用核查");
    const cancelBtn = el("button", { class: "btn btn-danger btn-small", type: "button" }, "取消");
    cancelBtn.disabled = !state.citecheckTaskId;

    const progBar = el("div", { class: "progress" }, el("div"));
    const progText = el("div", { class: "muted mono" }, "—");

    const resultsBox = el("div", { class: "card" }, el("div", { class: "muted" }, "结果将在这里显示。"));

    // Missing-reference helper (optional online DOI/OA enrichment).
    const missingWithOA = el("input", { type: "checkbox" });
    const missingDownloadOA = el("input", { type: "checkbox" });
    const missingLimit = el("input", { class: "input", style: "width:120px", inputmode: "numeric" });
    const missingRunBtn = el("button", { class: "btn", type: "button" }, "生成缺失清单");
    const missingCancelBtn = el("button", { class: "btn btn-danger btn-small", type: "button" }, "取消");
    const missingProgBar = el("div", { class: "progress" }, el("div"));
    const missingProgText = el("div", { class: "muted mono" }, "—");
    const missingBox = el("div", { class: "card" }, el("div", { class: "muted" }, "缺失清单将在这里显示（可选）。"));

    function llmConfiguredNow() {
      return llmConfigured(state.llmApi);
    }

    function llmUsableNow() {
      return llmUsable(state.llmApi);
    }

    function verdictLabel(v) {
      const s = String(v || "").trim().toUpperCase();
      const map = {
        ACCURATE: "准确",
        INACCURATE: "疑似曲解",
        MISATTRIBUTED: "疑似张冠李戴",
        NOT_FOUND: "未找到依据",
        PDF_NOT_FOUND: "未找到原文 PDF",
        REF_NOT_FOUND: "参考文献缺失",
        EVIDENCE_ONLY: "仅检索证据",
        PARSE_ERROR: "解析失败",
        ERROR: "错误",
      };
      return map[s] || s || "—";
    }

    function verdictBadgeClass(v) {
      const s = String(v || "").trim().toUpperCase();
      if (s === "ACCURATE") return "badge good";
      if (s === "MISATTRIBUTED") return "badge warn";
      if (s === "INACCURATE") return "badge bad";
      if (s === "NOT_FOUND") return "badge warn";
      if (s === "EVIDENCE_ONLY") return "badge";
      return "badge bad";
    }

    async function syncDrafts() {
      clear(draftSel);
      draftSel.appendChild(el("option", { value: "" }, "— 选择已上传论文 —"));
      try {
        const r = await apiGet("/api/norms/citecheck/drafts?limit=25");
        const drafts = (r && r.drafts) || [];
        const seen = new Set();
        for (const d of drafts) {
          const id = String((d && d.id) || "").trim();
          const fn = String((d && d.filename) || "").trim() || id;
          if (!id) continue;
          const key = fn.toLowerCase();
          if (seen.has(key)) continue;
          seen.add(key);
          draftSel.appendChild(el("option", { value: id }, fn));
        }
      } catch {
        // ignore
      }
      const want = state.citecheckDraftId || "";
      draftSel.value = want;
      if (want && draftSel.value !== want) {
        state.citecheckDraftId = "";
        localStorage.setItem("aiw.citecheckDraftId", "");
      }
      draftHint.textContent = state.citecheckDraftId
        ? `已选论文：${draftSel.options[draftSel.selectedIndex] ? draftSel.options[draftSel.selectedIndex].textContent : state.citecheckDraftId}`
        : "还未选择论文 PDF。";
      openDraftBtn.disabled = !state.citecheckDraftId;
    }

    function stopCitecheckPolling() {
      if (state.citecheckPollTimer) window.clearInterval(state.citecheckPollTimer);
      state.citecheckPollTimer = null;
    }

    function updateProgressUI(t) {
      const done = Number(t.done || 0);
      const total = Number(t.total || 0);
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      progBar.firstChild.style.width = `${Math.max(0, Math.min(100, pct))}%`;
      const st = humanTaskStatus(t.status);
      const stage = humanTaskStage(t.stage);
      const detail = String(t.detail || "").trim();
      progText.textContent = `${st} · ${stage}${total ? ` · ${done}/${total}` : ""}${detail ? ` · ${detail}` : ""}`;
      cancelBtn.disabled = String(t.status || "").toLowerCase() !== "running";
    }

    async function fetchAndRenderResult(taskId) {
      if (!taskId) return;
      try {
        const r = await apiGet(`/api/norms/citecheck/result?task_id=${encodeURIComponent(taskId)}`);
        state.lastCitecheck = r;
        localStorage.setItem("aiw.citecheckLastResultId", String(taskId || ""));
        renderResult();
      } catch (e) {
        const msg = String((e && e.message) || e || "").trim();
        const m = msg.toLowerCase();
        if (m.includes("result not found") || m.includes("not found")) {
          // The last result might be deleted/expired; clear it silently to avoid a confusing red toast.
          try {
            localStorage.setItem("aiw.citecheckLastResultId", "");
          } catch {}
          state.lastCitecheck = null;
          renderResult();
          return;
        }
        toast(msg || "获取结果失败。", "bad", 6500);
      }
    }

    function stopCitecheckMissingPolling() {
      if (state.citecheckMissingPollTimer) window.clearInterval(state.citecheckMissingPollTimer);
      state.citecheckMissingPollTimer = null;
    }

    function updateMissingProgressUI(t) {
      const done = Number(t.done || 0);
      const total = Number(t.total || 0);
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      missingProgBar.firstChild.style.width = `${Math.max(0, Math.min(100, pct))}%`;
      const st = humanTaskStatus(t.status);
      const stage = humanTaskStage(t.stage);
      const detail = String(t.detail || "").trim();
      missingProgText.textContent = `${st} · ${stage}${total ? ` · ${done}/${total}` : ""}${detail ? ` · ${detail}` : ""}`;
      missingCancelBtn.disabled = String(t.status || "").toLowerCase() !== "running";
    }

    function renderMissingResult() {
      clear(missingBox);
      const r = state.lastCitecheckMissing;
      if (!r || typeof r !== "object") {
        missingBox.appendChild(el("div", { class: "label" }, "缺失参考文献原文（可选）"));
        missingBox.appendChild(el("div", { class: "muted" }, "如果核查结果提示“未找到原文 PDF”，可以先生成“缺失清单”，再补齐对应的原文 PDF。"));
        missingBox.appendChild(el("div", { class: "muted" }, "提示：联网模式只查 DOI / Open Access 线索，不提供 Sci-Hub 等来源。"));
        return;
      }

      const meta = (r && r.meta && typeof r.meta === "object" ? r.meta : {}) || {};
      const items = Array.isArray(r.missing) ? r.missing : [];
      const total = Number(r.missing_count || items.length || 0);
      const sec = Number(meta.seconds || 0);

      missingBox.appendChild(
        el(
          "div",
          { class: "row" },
          el("span", { class: "label" }, "缺失参考文献原文（清单）"),
          el("span", { class: "badge" }, `共 ${Math.max(0, Math.round(total))} 条`),
          sec ? el("span", { class: "muted" }, `耗时 ${sec.toFixed(1)}s`) : null,
          el(
            "button",
            {
              class: "btn btn-small",
              type: "button",
              onclick: () => {
                function esc(v) {
                  const s = String(v == null ? "" : v);
                  const t = s.replace(/\r?\n/g, " ").replace(/"/g, '""');
                  return `"${t}"`;
                }
                const header = [
                  "cited_author",
                  "cited_year",
                  "ref_title",
                  "doi",
                  "oa_pdf_url",
                  "landing_url",
                  "downloaded",
                  "download_rel",
                  "ref_missing",
                  "reference_entry",
                  "candidates",
                ];
                const lines = [header.map(esc).join(",")];
                for (const it of items) {
                  const candidates = Array.isArray(it && it.candidates)
                    ? it.candidates
                        .map((c) => String(((c && c.filename) || (c && c.rel) || "")).replace(/\\\\/g, "/"))
                        .filter(Boolean)
                        .slice(0, 4)
                        .join(";")
                    : "";
                  lines.push(
                    [
                      it.cited_author || "",
                      it.cited_year || "",
                      it.ref_title || it.oa_title || "",
                      it.doi || "",
                      it.oa_pdf_url || "",
                      it.landing_url || "",
                      it.downloaded ? "1" : "0",
                      it.download_rel || "",
                      it.ref_missing ? "1" : "0",
                      it.reference_entry || "",
                      candidates,
                    ]
                      .map(esc)
                      .join(",")
                  );
                }
                downloadText(`citecheck_missing_${Date.now()}.csv`, lines.join("\n"), "text/csv;charset=utf-8");
              },
            },
            "下载 CSV"
          ),
          el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(JSON.stringify(r, null, 2)) }, "复制 JSON"),
          el(
            "button",
            {
              class: "btn btn-small",
              type: "button",
              onclick: () => downloadText(`citecheck_missing_${Date.now()}.json`, JSON.stringify(r, null, 2), "application/json;charset=utf-8"),
            },
            "下载 JSON"
          )
        )
      );

      if (!items.length) {
        missingBox.appendChild(el("div", { class: "muted" }, "没有检测到缺失（或已全部匹配到本地原文 PDF）。"));
        return;
      }

      const list = el("div", { class: "list" });
      for (const it of items) {
        const author = String(it.cited_author || "");
        const year = String(it.cited_year || "");
        const title = String(it.ref_title || "") || String(it.oa_title || "");
        const reason = String(it.reason || "");
        const refMissing = !!it.ref_missing;
        const doi = String(it.doi || "");
        const oaUrl = String(it.oa_pdf_url || "");
        const landing = String(it.landing_url || "");
        const downloaded = !!it.downloaded;
        const downloadRel = String(it.download_rel || "");
        const candidates = Array.isArray(it.candidates) ? it.candidates : [];

        list.appendChild(
          el(
            "div",
            { class: "item" },
            el(
              "div",
              { class: "item-header" },
              el("span", { class: "badge bad" }, "缺失"),
              el("span", { class: "muted mono" }, `${author} (${year})`),
              refMissing ? el("span", { class: "badge warn" }, "References 缺失") : null,
              downloaded ? el("span", { class: "badge good" }, "已下载 OA") : null,
              el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(String(it.reference_entry || "")) }, "复制参考文献")
            ),
            title ? el("div", { class: "quote" }, title) : null,
            reason ? el("div", { class: "muted" }, reason) : null,
            doi || oaUrl || landing
              ? el(
                  "div",
                  { class: "row" },
                  doi ? el("span", { class: "muted mono" }, `DOI: ${doi}`) : null,
                  oaUrl
                    ? el(
                        "button",
                        {
                          class: "btn btn-small",
                          type: "button",
                          onclick: () => {
                            try {
                              window.open(oaUrl, "_blank");
                            } catch {}
                          },
                        },
                        "打开 OA PDF"
                      )
                    : null,
                  landing
                    ? el(
                        "button",
                        {
                          class: "btn btn-small",
                          type: "button",
                          onclick: () => {
                            try {
                              window.open(landing, "_blank");
                            } catch {}
                          },
                        },
                        "打开 DOI/网页"
                      )
                    : null,
                  downloadRel
                    ? el(
                        "button",
                        {
                          class: "btn btn-small",
                          type: "button",
                          onclick: () => openLibraryPdfPreview(state.library, downloadRel, 1),
                        },
                        "预览已下载"
                      )
                    : null
                )
              : null,
            candidates.length
              ? el(
                  "div",
                  { class: "grid", style: "gap:8px; margin-top:8px" },
                  el("div", { class: "muted" }, "可能的候选（请点开预览确认）："),
                  el(
                    "div",
                    { class: "list" },
                    ...candidates.slice(0, 3).map((c) => {
                      const rel = String((c && c.rel) || "").replace(/\\\\/g, "/");
                      const fn = String((c && c.filename) || rel || "");
                      const sc = Number((c && c.score) || 0);
                      const pct = Math.round(Math.max(-1, Math.min(1, sc)) * 100);
                      return el(
                        "div",
                        { class: "item" },
                        el(
                          "div",
                          { class: "item-header" },
                          el("span", { class: "badge mono" }, `${pct}%`),
                          el("span", { class: "muted mono" }, fn),
                          rel
                            ? el(
                                "button",
                                { class: "btn btn-small", type: "button", onclick: () => openLibraryPdfPreview(state.library, rel, 1) },
                                "预览"
                              )
                            : null
                        )
                      );
                    })
                  )
                )
              : null
          )
        );
      }

      missingBox.appendChild(list);
    }

    async function fetchAndRenderMissingResult(taskId) {
      if (!taskId) return;
      try {
        const r = await apiGet(`/api/norms/citecheck/missing/result?task_id=${encodeURIComponent(taskId)}`);
        state.lastCitecheckMissing = r;
        localStorage.setItem("aiw.citecheckMissingLastResultId", String(taskId || ""));
        renderMissingResult();
      } catch (e) {
        const msg = String((e && e.message) || e || "").trim();
        const m = msg.toLowerCase();
        if (m.includes("result not found") || m.includes("not found")) {
          try {
            localStorage.setItem("aiw.citecheckMissingLastResultId", "");
          } catch {}
          state.lastCitecheckMissing = null;
          renderMissingResult();
          return;
        }
        toast(msg || "获取缺失清单失败。", "bad", 6500);
      }
    }

    async function pollMissingOnce() {
      if (!state.citecheckMissingTaskId) return;
      const taskId = state.citecheckMissingTaskId;
      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(taskId)}`);
        updateMissingProgressUI(t);
        const status = String(t.status || "").toLowerCase();
        if (status !== "running") {
          stopCitecheckMissingPolling();
          const finishedId = state.citecheckMissingTaskId;
          state.citecheckMissingTaskId = "";
          localStorage.setItem("aiw.citecheckMissingTaskId", "");
          missingCancelBtn.disabled = true;
          missingRunBtn.disabled = false;
          missingRunBtn.textContent = "生成缺失清单";

          if (status === "done") {
            toast("缺失清单生成完成。");
            await fetchAndRenderMissingResult(finishedId);
          } else if (status === "canceled") {
            toast("已取消缺失清单生成。", "bad");
          } else {
            toast("失败：" + (t.error || ""), "bad", 6500);
          }
        }
      } catch (e) {
        stopCitecheckMissingPolling();
        const msg = String(e && e.message ? e.message : e);
        const lost = msg.toLowerCase().includes("task not found");
        const prev = state.citecheckMissingTaskId;
        state.citecheckMissingTaskId = "";
        localStorage.setItem("aiw.citecheckMissingTaskId", "");
        missingCancelBtn.disabled = true;
        missingRunBtn.disabled = false;
        missingRunBtn.textContent = "生成缺失清单";
        toast(lost ? "任务已丢失（可能因为服务重启/关闭）。请重新生成缺失清单。" : msg, "bad", 6500);
        if (prev && lost) {
          fetchAndRenderMissingResult(prev).catch(() => {});
        }
      }
    }

    function startCitecheckMissingPolling() {
      stopCitecheckMissingPolling();
      missingRunBtn.disabled = true;
      missingRunBtn.textContent = "生成中…";
      missingCancelBtn.disabled = false;
      pollMissingOnce();
      state.citecheckMissingPollTimer = window.setInterval(pollMissingOnce, 1000);
    }

    function openUncitedModal(list) {
      const items = (list || []).slice(0, 1200);
      const body = el(
        "div",
        { class: "grid", style: "gap:10px" },
        el("div", { class: "muted" }, `共 ${items.length} 条（从你的论文参考文献列表中抽取）。`),
        el(
          "div",
          { class: "list" },
          ...items.map((it) =>
            el(
              "div",
              { class: "item" },
              el(
                "div",
                { class: "item-header" },
                el("span", { class: "badge mono" }, `#${it.index || "?"}`),
                el("span", { class: "muted mono" }, `${it.year || ""} ${it.authors || ""}`),
                el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(String(it.reference || "")) }, "复制")
              ),
              el("div", { class: "quote" }, it.reference || "")
            )
          )
        )
      );
      openModal("未被正文引用的参考文献", body);
    }

    function openItemModal(it) {
      const pdfRel = String(it.matched_pdf_rel || "");
      const page = Number(it.page_in_main || 0);
      const sentence = String(it.original_sentence || "");
      const claim = String(it.claim || "");
      const reason = String(it.reason || "");
      const fix = String(it.suggested_fix || "");
      const refMissing = !!it.ref_missing;
      const refEntry = String(it.reference_entry || "");
      const evidence = (it.evidence || []).slice(0, 8);
      const evPage = evidence.length ? Number(evidence[0].page || 0) : 1;
      const did = String(state.citecheckDraftId || "").trim();
      let draftName = "";
      try {
        draftName = String(draftSel && draftSel.options && draftSel.selectedIndex >= 0 ? draftSel.options[draftSel.selectedIndex].textContent : "");
      } catch {}

      const body = el(
        "div",
        { class: "grid", style: "gap:12px" },
        el(
          "div",
          { class: "row" },
          el("span", { class: verdictBadgeClass(it.verdict) }, verdictLabel(it.verdict)),
          el("span", { class: "muted mono" }, `${it.cited_author || ""} (${it.cited_year || ""})`),
          refMissing ? el("span", { class: "badge warn" }, "References 缺失") : null,
          el("span", { class: "muted" }, page ? `你的论文页码：${page}` : "你的论文页码：—"),
          did && page
            ? el(
                "button",
                {
                  class: "btn btn-small",
                  type: "button",
                  onclick: () => openDraftPdfPreview(did, draftName, page),
                },
                "预览论文此页"
              )
            : null,
          pdfRel
            ? el(
                "button",
                {
                  class: "btn btn-small",
                  type: "button",
                  onclick: () => openLibraryPdfPreview(state.library, pdfRel, evPage || 1),
                },
                "预览参考文献原文"
              )
            : null
          ),
        el("div", { class: "label" }, "引用句"),
        el("div", { class: "quote" }, sentence),
        claim ? el("div", { class: "label" }, "被引用论点（LLM 提炼）") : null,
        claim ? el("div", { class: "quote" }, claim) : null,
        reason ? el("div", { class: "label" }, "判定说明") : null,
        reason ? el("div", { class: "quote" }, reason) : null,
        fix ? el("div", { class: "label" }, "建议改写（低风险）") : null,
        fix ? el("div", { class: "quote" }, fix) : null,
        refEntry ? el("div", { class: "label" }, "参考文献条目（来自你的论文）") : null,
        refEntry ? el("div", { class: "quote" }, refEntry) : null,
        el("div", { class: "label" }, "原文证据（页码 + 段落）"),
        evidence.length
          ? el(
              "div",
              { class: "list" },
              ...evidence.map((p) =>
                el(
                  "div",
                  { class: "item" },
                  el(
                    "div",
                    { class: "item-header" },
                    el(
                      "div",
                      { class: "row", style: "gap:10px; align-items:center" },
                      el("span", { class: "badge mono" }, `Page ${p.page || 0}`),
                      el("span", { class: "muted mono" }, `score ${Number(p.score || 0).toFixed(3)}`)
                    ),
                    pdfRel
                      ? el(
                          "button",
                          {
                            class: "btn btn-small",
                            type: "button",
                            onclick: () => openLibraryPdfPreview(state.library, pdfRel, Number(p.page || 1) || 1),
                          },
                          "预览"
                        )
                      : null
                  ),
                  el("div", { class: "quote" }, String(p.text || ""))
                )
              )
            )
          : el("div", { class: "muted" }, "未检索到相关段落（或参考文献原文 PDF 未导入）。")
      );

      openModal(`引用核查 · ${it.cited_author || ""} (${it.cited_year || ""})`, body);
    }

    function renderResult() {
      clear(resultsBox);
      const r = state.lastCitecheck;
      if (!r || typeof r !== "object") {
        resultsBox.appendChild(el("div", { class: "muted" }, "还没有结果。"));
        return;
      }
      const items0 = (r.items || []).filter(Boolean);
      const counts = (r.counts && typeof r.counts === "object" ? r.counts : {}) || {};
      const meta = (r.meta && typeof r.meta === "object" ? r.meta : {}) || {};
      const uncited = (r.uncited_references || []).filter(Boolean);

      const total = items0.length;
      const sec = Number(meta.seconds || 0);

      const filterBar = el("div", { class: "row" });
      const active = { v: localStorage.getItem("aiw.citecheckFilter") || "ALL" };

      const sev = {
        INACCURATE: 0,
        MISATTRIBUTED: 1,
        NOT_FOUND: 2,
        PDF_NOT_FOUND: 3,
        REF_NOT_FOUND: 4,
        PARSE_ERROR: 5,
        ERROR: 5,
        EVIDENCE_ONLY: 6,
        ACCURATE: 9,
      };

      const items = items0
        .slice()
        .sort((a, b) => {
          const va = String((a && a.verdict) || "").trim().toUpperCase();
          const vb = String((b && b.verdict) || "").trim().toUpperCase();
          const wa = sev[va] != null ? sev[va] : 50;
          const wb = sev[vb] != null ? sev[vb] : 50;
          if (wa !== wb) return wa - wb;
          const pa = Number((a && a.page_in_main) || 0) || 0;
          const pb = Number((b && b.page_in_main) || 0) || 0;
          if (pa !== pb) return pa - pb;
          const aa = String((a && a.cited_author) || "");
          const ab = String((b && b.cited_author) || "");
          return aa.localeCompare(ab);
        });

      function filterItems() {
        const key = String(active.v || "ALL").toUpperCase();
        if (key === "ALL") return items;
        return items.filter((x) => String(x.verdict || "").toUpperCase() === key);
      }

      function addFilterChip(key, label, n, cls) {
        const k = String(key || "ALL").toUpperCase();
        const chip = el(
          "button",
          {
            class: "chip " + (cls || ""),
            type: "button",
            onclick: () => {
              active.v = k;
              localStorage.setItem("aiw.citecheckFilter", active.v);
              renderResult();
            },
          },
          `${label} ${n != null ? `(${n})` : ""}`
        );
        if (active.v === k) chip.classList.add("active");
        filterBar.appendChild(chip);
      }

      addFilterChip("ALL", "全部", total, "");
      const order = [
        "INACCURATE",
        "MISATTRIBUTED",
        "NOT_FOUND",
        "PDF_NOT_FOUND",
        "REF_NOT_FOUND",
        "PARSE_ERROR",
        "ERROR",
        "EVIDENCE_ONLY",
        "ACCURATE",
      ];
      const keys = Object.keys(counts || {});
      keys.sort((a, b) => {
        const ia = order.indexOf(String(a || "").toUpperCase());
        const ib = order.indexOf(String(b || "").toUpperCase());
        const wa = ia >= 0 ? ia : 999;
        const wb = ib >= 0 ? ib : 999;
        if (wa !== wb) return wa - wb;
        return String(a || "").localeCompare(String(b || ""));
      });
      for (const k of keys) {
        const n = Number(counts[k] || 0);
        if (!n) continue;
        const cls = k === "ACCURATE" ? "ok" : k === "MISATTRIBUTED" ? "warn" : k === "INACCURATE" ? "bad" : "warn";
        addFilterChip(k, verdictLabel(k), n, cls);
      }

      resultsBox.appendChild(
        el(
          "div",
          { class: "grid", style: "gap:10px" },
          el(
            "div",
            { class: "row" },
            el("span", { class: "badge" }, `共 ${total} 条`),
            sec ? el("span", { class: "muted" }, `耗时 ${sec.toFixed(1)}s`) : null,
            meta.library ? el("span", { class: "muted mono" }, String(meta.library)) : null,
            uncited.length
              ? el("button", { class: "btn btn-small", type: "button", onclick: () => openUncitedModal(uncited) }, `未引用参考文献（${uncited.length}）`)
              : null,
            el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(JSON.stringify(r, null, 2)) }, "复制结果 JSON"),
            el(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: () => downloadText(`citecheck_${Date.now()}.json`, JSON.stringify(r, null, 2), "application/json;charset=utf-8"),
              },
              "下载 JSON"
            ),
            el(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: () => {
                  function esc(v) {
                    const s = String(v == null ? "" : v);
                    const t = s.replace(/\r?\n/g, " ").replace(/"/g, '""');
                    return `"${t}"`;
                  }
                  const header = [
                    "verdict",
                    "page_in_main",
                    "cited_author",
                    "cited_year",
                    "matched_pdf_rel",
                    "ref_missing",
                    "original_sentence",
                    "claim",
                    "reason",
                    "suggested_fix",
                    "evidence_pages",
                    "evidence_preview",
                  ];
                  const lines = [header.map(esc).join(",")];
                  for (const it of items0) {
                    const ev = (it && it.evidence) || [];
                    const pages = Array.isArray(ev) ? ev.map((p) => p && p.page).filter(Boolean).slice(0, 6).join(";") : "";
                    const preview = Array.isArray(ev) && ev[0] && ev[0].text ? String(ev[0].text).slice(0, 220) : "";
                    lines.push(
                      [
                        verdictLabel(it.verdict),
                        it.page_in_main || "",
                        it.cited_author || "",
                        it.cited_year || "",
                        it.matched_pdf_rel || "",
                        it.ref_missing ? "1" : "0",
                        it.original_sentence || "",
                        it.claim || "",
                        it.reason || "",
                        it.suggested_fix || "",
                        pages,
                        preview,
                      ]
                        .map(esc)
                        .join(",")
                    );
                  }
                  downloadText(`citecheck_${Date.now()}.csv`, lines.join("\n"), "text/csv;charset=utf-8");
                },
              },
              "下载 CSV"
            )
          ),
          filterBar
        )
      );

      const list = el("div", { class: "list" });
      for (const it of filterItems()) {
        const pdfRel = String(it.matched_pdf_rel || "");
        const pageInMain = Number(it.page_in_main || 0);
        const sent = String(it.original_sentence || "");
        const claim = String(it.claim || "");
        const reason = String(it.reason || "");
        const fix = String(it.suggested_fix || "");
        const refMissing = !!it.ref_missing;
        const ev0 = it.evidence && it.evidence.length ? String(it.evidence[0].text || "") : "";
        const evPage = it.evidence && it.evidence.length ? Number(it.evidence[0].page || 0) : 1;

        list.appendChild(
          el(
            "div",
            { class: "item" },
            el(
              "div",
              { class: "item-header" },
              el(
                "div",
                null,
                el("span", { class: verdictBadgeClass(it.verdict) }, verdictLabel(it.verdict)),
                " ",
                el("span", { class: "muted mono" }, `${it.cited_author || ""} (${it.cited_year || ""})`),
                refMissing ? el("span", { class: "badge warn" }, "References 缺失") : null,
                pageInMain ? el("span", { class: "muted" }, ` · 你的论文页码 ${pageInMain}`) : null
              ),
              el(
                "div",
                { class: "row" },
                pdfRel ? el("span", { class: "muted mono" }, `${pdfRel}`) : null,
                el("button", { class: "btn btn-small", type: "button", onclick: () => copyText(sent) }, "复制句子"),
                pdfRel
                  ? el(
                      "button",
                      {
                        class: "btn btn-small",
                        type: "button",
                        onclick: () => openLibraryPdfPreview(state.library, pdfRel, evPage || 1),
                      },
                      "预览原文"
                    )
                  : null,
                el("button", { class: "btn btn-small", type: "button", onclick: () => openItemModal(it) }, "查看证据")
              )
            ),
            el("div", { class: "quote" }, sent),
            claim ? el("div", { class: "muted" }, `论点：${claim.slice(0, 180)}${claim.length > 180 ? "…" : ""}`) : null,
            reason ? el("div", { class: "muted" }, reason.slice(0, 240) + (reason.length > 240 ? "…" : "")) : null,
            fix ? el("div", { class: "muted" }, `建议改写：${fix.slice(0, 220)}${fix.length > 220 ? "…" : ""}`) : null,
            ev0 ? el("div", { class: "muted" }, `证据预览：${ev0.slice(0, 220)}${ev0.length > 220 ? "…" : ""}`) : null
          )
        );
      }

      resultsBox.appendChild(list);
    }

    async function pollOnce() {
      if (!state.citecheckTaskId) return;
      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(state.citecheckTaskId)}`);
        updateProgressUI(t);
        if (t.status !== "running") {
          stopCitecheckPolling();
          const finishedId = state.citecheckTaskId;
          state.citecheckTaskId = "";
          localStorage.setItem("aiw.citecheckTaskId", "");
          cancelBtn.disabled = true;
          runBtn.disabled = false;
          runBtn.textContent = "开始引用核查";

          if (t.status === "done") {
            toast("引用核查完成。");
            await fetchAndRenderResult(finishedId);
          } else if (t.status === "canceled") {
            toast("已取消。", "bad");
          } else {
            toast("失败：" + (t.error || ""), "bad", 6500);
          }
        }
      } catch (e) {
        stopCitecheckPolling();
        const msg = String(e && e.message ? e.message : e);
        const lost = msg.toLowerCase().includes("task not found");
        const prev = state.citecheckTaskId;
        state.citecheckTaskId = "";
        localStorage.setItem("aiw.citecheckTaskId", "");
        cancelBtn.disabled = true;
        runBtn.disabled = false;
        runBtn.textContent = "开始引用核查";
        toast(lost ? "任务已丢失（可能因为服务重启/关闭）。请重新开始引用核查。" : msg, "bad", 6500);
        // If a result file already exists for the previous task id, try to load it.
        if (prev && lost) {
          fetchAndRenderResult(prev).catch(() => {});
        }
      }
    }

    function startCitecheckPolling() {
      stopCitecheckPolling();
      runBtn.disabled = true;
      runBtn.textContent = "核查中…";
      cancelBtn.disabled = false;
      pollOnce();
      state.citecheckPollTimer = window.setInterval(pollOnce, 1000);
    }

    uploadBtn.onclick = () => {
      try {
        uploadInput.value = "";
      } catch {}
      uploadInput.click();
    };

    uploadInput.onchange = async () => {
      const f = uploadInput.files && uploadInput.files[0] ? uploadInput.files[0] : null;
      if (!f) return;
      uploadBtn.disabled = true;
      uploadBtn.textContent = "上传中…";
      try {
        const fd = new FormData();
        fd.append("file", f, f.name || "main.pdf");
        const r = await apiFormPost("/api/norms/citecheck/upload_main_pdf", fd);
        const d = r && r.draft ? r.draft : null;
        const did = d && d.id ? String(d.id) : "";
        if (did) {
          state.citecheckDraftId = did;
          localStorage.setItem("aiw.citecheckDraftId", state.citecheckDraftId);
        }
        toast("已上传论文 PDF。");
        await syncDrafts();
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = "上传论文 PDF";
      }
    };

    draftSel.onchange = () => {
      state.citecheckDraftId = draftSel.value || "";
      localStorage.setItem("aiw.citecheckDraftId", state.citecheckDraftId);
      draftHint.textContent = state.citecheckDraftId
        ? `已选论文：${draftSel.options[draftSel.selectedIndex] ? draftSel.options[draftSel.selectedIndex].textContent : state.citecheckDraftId}`
        : "还未选择论文 PDF。";
      openDraftBtn.disabled = !state.citecheckDraftId;
    };

    openDraftBtn.onclick = async () => {
      if (!state.citecheckDraftId) return;
      const name = draftSel && draftSel.options && draftSel.selectedIndex >= 0 ? draftSel.options[draftSel.selectedIndex].textContent : "";
      openDraftPdfPreview(state.citecheckDraftId, name, 1);
    };

    const useLlmKey = "aiw.citecheckUseLLM";
    const savedUse = localStorage.getItem(useLlmKey);
    useLLM.checked = savedUse ? savedUse === "1" : llmUsableNow();
    useLLM.onchange = () => {
      localStorage.setItem(useLlmKey, useLLM.checked ? "1" : "0");
      if (useLLM.checked && !llmConfiguredNow()) {
        useLLM.checked = false;
        localStorage.setItem(useLlmKey, "0");
        toast("要启用大模型判定，请先到“润色设置”配置接口。", "bad", 6500);
        setRoute("llm");
        return;
      }
      if (useLLM.checked && !llmUsableNow()) {
        toast("大模型上次测试失败或尚未测试：仍可尝试运行（已加重试）。如失败请到“润色设置”测试连接。", "bad", 6500);
      }
    };

    runBtn.onclick = async () => {
      if (!state.library) return toast("请先选择参考文献库（右上角）。", "bad");
      if (!state.citecheckDraftId) return toast("请先上传/选择你的论文 PDF。", "bad", 4500);

      const mp = Math.max(10, Math.min(300, Number((maxPairs.value || "").trim() || 80) || 80));
      maxPairs.value = String(mp);
      localStorage.setItem("aiw.citecheckMaxPairs", String(mp));

      if (useLLM.checked && !llmConfiguredNow()) {
        toast("要启用大模型判定，请先到“润色设置”配置接口。", "bad", 6500);
        setRoute("llm");
        return;
      }
      if (useLLM.checked && !llmUsableNow()) {
        toast("大模型上次测试失败或尚未测试：仍会尝试运行（已加重试）。如失败请到“润色设置”测试连接。", "bad", 6500);
      }

      runBtn.disabled = true;
      runBtn.textContent = "启动中…";
      try {
        const body = { library: state.library, draft_id: state.citecheckDraftId, use_llm: !!useLLM.checked, max_pairs: mp };
        const r = await apiPost("/api/norms/citecheck/run", body);
        state.citecheckTaskId = (r && r.task_id) || "";
        localStorage.setItem("aiw.citecheckTaskId", state.citecheckTaskId || "");
        toast("已开始核查（后台进行）。");
        startCitecheckPolling();
      } catch (e) {
        const msg = String(e.message || e);
        if (msg.toLowerCase().includes("missing api key") || msg.toLowerCase().includes("missing base_url") || msg.toLowerCase().includes("missing model")) {
          toast(msg, "bad", 6500);
          setRoute("llm");
        } else {
          toast(msg, "bad", 6500);
        }
      } finally {
        if (state.citecheckTaskId) {
          runBtn.disabled = true;
          runBtn.textContent = "核查中…";
          cancelBtn.disabled = false;
        } else {
          runBtn.disabled = false;
          runBtn.textContent = "开始引用核查";
          cancelBtn.disabled = true;
        }
      }
    };

    cancelBtn.onclick = async () => {
      if (!state.citecheckTaskId) return;
      try {
        await apiPost(`/api/tasks/${encodeURIComponent(state.citecheckTaskId)}/cancel`, {});
        toast("已请求取消。");
      } catch (e) {
        toast(String(e.message || e), "bad");
      }
    };

    // Missing-reference helper controls
    missingLimit.value = String(Math.max(10, Math.min(500, Number(localStorage.getItem("aiw.citecheckMissingLimit") || "60") || 60)));
    missingWithOA.checked = localStorage.getItem("aiw.citecheckMissingWithOA") === "1";
    missingDownloadOA.checked = localStorage.getItem("aiw.citecheckMissingDownloadOA") === "1";
    if (!missingWithOA.checked) missingDownloadOA.checked = false;
    missingDownloadOA.disabled = !missingWithOA.checked;
    missingCancelBtn.disabled = !state.citecheckMissingTaskId;

    missingWithOA.onchange = () => {
      localStorage.setItem("aiw.citecheckMissingWithOA", missingWithOA.checked ? "1" : "0");
      if (!missingWithOA.checked) {
        missingDownloadOA.checked = false;
        localStorage.setItem("aiw.citecheckMissingDownloadOA", "0");
      }
      missingDownloadOA.disabled = !missingWithOA.checked;
    };

    missingDownloadOA.onchange = () => {
      if (missingDownloadOA.checked && !missingWithOA.checked) {
        missingDownloadOA.checked = false;
        return;
      }
      localStorage.setItem("aiw.citecheckMissingDownloadOA", missingDownloadOA.checked ? "1" : "0");
    };

    missingRunBtn.onclick = async () => {
      if (!state.library) return toast("请先选择参考文献库（右上角）。", "bad");
      if (!state.citecheckDraftId) return toast("请先上传/选择你的论文 PDF。", "bad", 4500);

      const lim = Math.max(5, Math.min(500, Number((missingLimit.value || "").trim() || 60) || 60));
      missingLimit.value = String(lim);
      localStorage.setItem("aiw.citecheckMissingLimit", String(lim));

      missingRunBtn.disabled = true;
      missingRunBtn.textContent = "启动中…";
      try {
        const body = {
          library: state.library,
          draft_id: state.citecheckDraftId,
          only_cited: true,
          limit: lim,
          with_oa: !!missingWithOA.checked,
          download_oa: !!missingDownloadOA.checked,
        };
        const r = await apiPost("/api/norms/citecheck/missing/run", body);
        state.citecheckMissingTaskId = (r && r.task_id) || "";
        localStorage.setItem("aiw.citecheckMissingTaskId", state.citecheckMissingTaskId || "");
        toast("已开始生成缺失清单（后台进行）。");
        startCitecheckMissingPolling();
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      } finally {
        if (!state.citecheckMissingTaskId) {
          missingRunBtn.disabled = false;
          missingRunBtn.textContent = "生成缺失清单";
          missingCancelBtn.disabled = true;
        }
      }
    };

    missingCancelBtn.onclick = async () => {
      if (!state.citecheckMissingTaskId) return;
      try {
        await apiPost(`/api/tasks/${encodeURIComponent(state.citecheckMissingTaskId)}/cancel`, {});
        toast("已请求取消。");
      } catch (e) {
        toast(String(e.message || e), "bad");
      }
    };

    function lastHasPdfNotFound() {
      const r = state.lastCitecheck;
      if (!r || typeof r !== "object") return false;
      const counts = r && r.counts && typeof r.counts === "object" ? r.counts : {};
      try {
        if (Number(counts.PDF_NOT_FOUND || 0) > 0) return true;
      } catch {}
      try {
        const items = Array.isArray(r.items) ? r.items : [];
        return items.some((it) => String((it && it.verdict) || "").toUpperCase() === "PDF_NOT_FOUND");
      } catch {
        return false;
      }
    }

    const openMissing = !!(lastHasPdfNotFound() || state.citecheckMissingTaskId || state.lastCitecheckMissing);
    const missingDetails = el(
      "details",
      { class: "details", open: openMissing },
      el("summary", null, "0) 补齐参考文献原文（可选）"),
      el(
        "div",
        { class: "grid", style: "gap:12px; margin-top:10px" },
        el("div", null, "如果核查结果提示“未找到原文 PDF”，先生成缺失清单，再补齐对应的原文 PDF。"),
        el(
          "div",
          { class: "row" },
          el("label", { class: "row", style: "gap:8px" }, missingWithOA, el("span", null, "联网查 DOI / Open Access（可选）")),
          el("label", { class: "row", style: "gap:8px" }, missingDownloadOA, el("span", null, "自动下载 OA PDF")),
          el("span", { class: "label" }, "最多查"),
          missingLimit,
          el("span", { class: "muted" }, "条")
        ),
        el("div", { class: "row" }, missingRunBtn, missingCancelBtn),
        missingProgBar,
        missingProgText,
        el("div", { class: "muted" }, "说明：只查合法来源（Semantic Scholar / Crossref），不提供 Sci-Hub 等来源。")
      )
    );

    const left = el(
      "div",
      { class: "grid", style: "gap:18px" },
      missingDetails,
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "1) 选择论文 PDF"),
        el("div", { class: "row" }, draftSel, uploadBtn, openDraftBtn),
        draftHint,
        el("div", { class: "muted" }, "支持：作者-年份引用（如 Smith (2020) / (Smith, 2020; …)）。数字型 [1] 暂不支持。")
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "2) 设置与启动"),
        el(
          "div",
          { class: "row" },
          el("label", { class: "row", style: "gap:8px" }, useLLM, el("span", null, "启用大模型判定（更精确；需先测试连接；温度固定 0）")),
          el("span", { class: "label" }, "最多核查"),
          maxPairs,
          el("span", { class: "muted" }, "条引用")
        ),
        el("div", { class: "row" }, runBtn, cancelBtn),
        progBar,
        progText
      )
    );

    const right = el("div", { class: "grid", style: "gap:18px" }, missingBox, resultsBox);

    root.appendChild(intro);
    root.appendChild(el("div", { class: "grid two", style: "gap:18px; align-items:start" }, left, right));
    root.appendChild(uploadInput);

    syncDrafts().catch(() => {});
    renderResult();
    renderMissingResult();
    const lastId = localStorage.getItem("aiw.citecheckLastResultId") || "";
    if (!state.lastCitecheck && lastId) fetchAndRenderResult(lastId).catch(() => {});
    const lastMissingId = localStorage.getItem("aiw.citecheckMissingLastResultId") || "";
    if (!state.lastCitecheckMissing && lastMissingId) fetchAndRenderMissingResult(lastMissingId).catch(() => {});
    if (state.citecheckTaskId) startCitecheckPolling();
    if (state.citecheckMissingTaskId) startCitecheckMissingPolling();

    return root;
  }

  function pageLLM() {
    renderHeader("润色设置", "配置大模型接口，用于白箱对齐润色。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    // OpenAI-compatible API (backend expects /v1/chat/completions)
    const apiBaseUrl = el("input", { class: "input", style: "flex:1", placeholder: "接口地址（通常以 /v1 结尾）" });
    const apiModel = el("input", { class: "input", style: "flex:1", placeholder: "模型名（例如 gemini-3-flash / deepseek-chat / gpt-4o-mini）" });
    const apiKey = el("input", { class: "input", style: "flex:1", type: "password", placeholder: "密钥（API Key，不显示；也可从环境变量读取）" });
    const saveKey = el("input", { type: "checkbox" });

    const apiStatusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取大模型状态…"));

    function renderApiStatus() {
      const st = state.llmApi || {};
      clear(apiStatusBox);
      const baseUrl = String(st.base_url || "").trim();
      const model = String(st.model || "").trim();
      const hasKey = !!st.api_key_present;
      const configured = !!(baseUrl && model && hasKey);
      const t = getLlmTestForApi(st);
      const tested = !!t;
      const usable = !!(t && t.ok);

      function fmtSource(v) {
        const s = String(v || "").trim();
        if (!s) return "";
        if (s === "settings") return "已保存到 settings.json";
        if (s === "default") return "默认值";
        if (s.startsWith("env:")) return "环境变量 " + s.slice(4);
        return s;
      }

      const src = (st && st.source && typeof st.source === "object" ? st.source : {}) || {};
      const srcBase = fmtSource(src.base_url);
      const srcModel = fmtSource(src.model);
      const srcKey = fmtSource(src.api_key);

      const configBadge = el("span", { class: "badge " + (configured ? "good" : "bad") }, configured ? "已配置" : "未配置");
      const testBadge = configured
        ? el("span", { class: "badge " + (usable ? "good" : tested ? "bad" : "warn") }, usable ? "测试通过" : tested ? "测试失败" : "待测试")
        : el("span", { class: "badge warn" }, "未测试");

      apiStatusBox.appendChild(
        el(
          "div",
          { class: "row" },
          configBadge,
          testBadge,
          el(
            "span",
            { class: "muted" },
            usable
              ? "可以回到“对齐润色”开始生成（温度默认 0，尽量不发散）。"
              : configured
                ? "建议先点一次“测试连接”（避免生成时失败）。"
                : "需要填写接口地址、模型名，并提供密钥。"
          ),
          usable
            ? el("button", { class: "btn btn-small", type: "button", onclick: () => setRoute("polish") }, "去对齐润色")
            : configured
              ? el("button", { class: "btn btn-small", type: "button", onclick: () => toast("请先点击下方的“测试连接”。", "bad", 4500) }, "先测试连接")
              : el("button", { class: "btn btn-small", type: "button", onclick: () => setRoute("scan") }, "先用找差距（离线）")
        )
      );

      apiStatusBox.appendChild(el("div", { class: "hr" }));
      apiStatusBox.appendChild(el("div", { class: "label" }, "当前配置"));
      apiStatusBox.appendChild(
        el(
          "div",
          { class: "list" },
          el(
            "div",
            { class: "item" },
            el(
              "div",
              { class: "item-header" },
              el("span", { class: "badge" }, "接口地址"),
              el("span", { class: "muted mono" }, baseUrl || "—")
            ),
            srcBase ? el("div", { class: "muted" }, "来源：" + srcBase) : null
          ),
          el(
            "div",
            { class: "item" },
            el("div", { class: "item-header" }, el("span", { class: "badge" }, "模型名"), el("span", { class: "muted mono" }, model || "—")),
            srcModel ? el("div", { class: "muted" }, "来源：" + srcModel) : null
          ),
          el(
            "div",
            { class: "item" },
            el(
              "div",
              { class: "item-header" },
              el("span", { class: "badge" }, "密钥"),
              el("span", { class: "muted mono" }, hasKey ? st.api_key_masked || "已检测到（隐藏）" : "未检测到")
            ),
            srcKey ? el("div", { class: "muted" }, "来源：" + srcKey) : null
          )
        )
      );

      if (configured) {
        apiStatusBox.appendChild(el("div", { class: "hr" }));
        apiStatusBox.appendChild(el("div", { class: "label" }, "连接测试"));

        function hintForTest(t0) {
          const http = Number((t0 && t0.http) || 0) || 0;
          const err = String((t0 && t0.error) || "").toLowerCase();
          if (http === 403 && (err.includes("validation_required") || err.includes("verify your account"))) {
            return "看起来是账号/权限未验证（VALIDATION_REQUIRED）。请按错误提示完成验证，或更换可用模型/网关。";
          }
          if (http === 401) return "密钥无效或已过期（401）。";
          if (http === 429) return "触发限流/额度不足（429）。";
          if (http === 0) return "网络错误/超时：检查网络、代理、或 base_url 是否可达。";
          return "";
        }

        if (!t) {
          apiStatusBox.appendChild(el("div", { class: "muted" }, "还没测试过。建议先点击下方“测试连接”，通过后再去生成润色。"));
        } else if (t.ok) {
          apiStatusBox.appendChild(el("div", { class: "muted" }, `上次测试：通过（HTTP ${t.http || 200}）。`));
        } else {
          const hint = hintForTest(t);
          const err = String(t.error || "").trim();
          apiStatusBox.appendChild(
            el(
              "div",
              { class: "grid", style: "gap:10px" },
              hint ? el("div", { class: "quote" }, hint) : null,
              err ? el("div", { class: "quote" }, err) : el("div", { class: "muted" }, `上次测试失败（HTTP ${t.http || 0}）。`),
              err
                ? el(
                    "div",
                    { class: "row" },
                    el(
                      "button",
                      {
                        class: "btn btn-small",
                        type: "button",
                        onclick: () => copyText(err),
                      },
                      "复制错误详情"
                    )
                  )
                : null
            )
          );
        }
      }

      apiStatusBox.appendChild(
        el(
          "details",
          { class: "details" },
          el("summary", { class: "label" }, "高级信息（可选）"),
          el(
            "div",
            { class: "muted" },
            "默认优先读取环境变量：SKILL_LLM_API_KEY / SKILL_LLM_BASE_URL / SKILL_LLM_MODEL（或 OPENAI_* / TOPHUMANWRITING_LLM_*）。"
          ),
          el(
            "div",
            { class: "muted", style: "margin-top:8px" },
            "提示：如果你刚设置了环境变量，需要重启 TopHumanWriting 才会生效。"
          )
        )
      );
    }

    async function syncApiFromStatus() {
      await refreshLLMStatus();
      const st = state.llmApi || {};
      apiBaseUrl.value = st.base_url || "";
      apiModel.value = st.model || "";
      renderApiStatus();
    }

    const apiSaveBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          apiSaveBtn.disabled = true;
          apiSaveBtn.textContent = "保存中…";
          try {
            await apiPost("/api/llm/api/save", {
              base_url: (apiBaseUrl.value || "").trim(),
              model: (apiModel.value || "").trim(),
              api_key: (apiKey.value || "").trim(),
              save_api_key: !!saveKey.checked,
            });
            apiKey.value = "";
            await syncApiFromStatus();
            toast("已保存润色设置。");
          } catch (e) {
            await syncApiFromStatus().catch(() => {});
            toast(String(e.message || e), "bad", 6500);
          } finally {
            apiSaveBtn.disabled = false;
            apiSaveBtn.textContent = "保存润色设置";
          }
        },
      },
      "保存润色设置"
    );

    const apiTestBtn = el(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: async () => {
          apiTestBtn.disabled = true;
          apiTestBtn.textContent = "测试中…";
          try {
            const r = await apiPost("/api/llm/api/test", {
              base_url: (apiBaseUrl.value || "").trim(),
              model: (apiModel.value || "").trim(),
              api_key: (apiKey.value || "").trim(),
            });
            writeLlmTestCache({
              ok: !!r.ok,
              http: Number(r.http || 0) || 0,
              base_url: String(r.base_url || (apiBaseUrl.value || "").trim()),
              model: String(r.model || (apiModel.value || "").trim()),
              error: String(r.error || ""),
              at: Date.now(),
            });
            await refreshLLMStatus();
            renderApiStatus();
            if (r.ok) {
              toast(`连接测试通过：${r.model}`, "good");
            } else {
              const http = Number(r.http || 0) || 0;
              const err = String(r.error || "").trim();
              const low = err.toLowerCase();
              let msg = `连接测试失败（HTTP ${http || 0}）`;
              if (http === 403 && (low.includes("validation_required") || low.includes("verify your account"))) {
                msg = "连接测试失败（403）：需要账号验证/开通权限（VALIDATION_REQUIRED）。";
              } else if (http === 401) {
                msg = "连接测试失败（401）：密钥无效或已过期。";
              } else if (http === 429) {
                msg = "连接测试失败（429）：触发限流/额度不足。";
              } else if (http === 0) {
                msg = "连接测试失败：网络错误/超时（检查网络、代理、或 base_url）。";
              }
              if (err && msg.length < 80) msg += ` ${err.slice(0, 120)}`;
              toast(msg, "bad", 6500);
            }
          } catch (e) {
            toast(String(e.message || e), "bad", 6500);
          } finally {
            apiTestBtn.disabled = false;
            apiTestBtn.textContent = "测试连接";
          }
        },
      },
      "测试连接"
    );

    const presetSel = el("select", { class: "select", style: "min-width:240px" });
    presetSel.appendChild(el("option", { value: "" }, "选择常用预设（可选）"));
    presetSel.appendChild(el("option", { value: "openai" }, "OpenAI（https://api.openai.com/v1）"));
    presetSel.appendChild(el("option", { value: "deepseek" }, "DeepSeek（https://api.deepseek.com/v1）"));
    presetSel.appendChild(el("option", { value: "custom" }, "自定义 / 网关（高级）"));

    function applyPreset(preset) {
      const p = String(preset || "").trim().toLowerCase();
      if (p === "openai") {
        apiBaseUrl.value = "https://api.openai.com/v1";
        if (!String(apiModel.value || "").trim() || String(apiModel.value || "").trim() === "deepseek-chat") apiModel.value = "gpt-4o-mini";
        toast("已填入 OpenAI 预设（可按需要修改）。");
        return;
      }
      if (p === "deepseek") {
        apiBaseUrl.value = "https://api.deepseek.com/v1";
        if (!String(apiModel.value || "").trim() || String(apiModel.value || "").trim() === "gpt-4o-mini") apiModel.value = "deepseek-chat";
        toast("已填入 DeepSeek 预设（可按需要修改）。");
        return;
      }
      if (p === "custom") {
        toast("自定义模式：请填写你的网关接口地址（需支持 /v1/chat/completions）。");
      }
    }
    presetSel.addEventListener("change", () => applyPreset(presetSel.value));

    function modelChip(label) {
      return el(
        "button",
        {
          class: "chip",
          type: "button",
          onclick: () => {
            apiModel.value = String(label || "").trim();
            toast("已填入模型名（可继续修改）。");
          },
        },
        label
      );
    }

    const modelChips = el(
      "div",
      { class: "row", style: "gap:8px; align-items:flex-start" },
      el("span", { class: "muted" }, "常用模型"),
      modelChip("gpt-4o-mini"),
      modelChip("deepseek-chat"),
      modelChip("gemini-3-flash")
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "大模型接口"),
        el("div", { class: "row" }, el("span", { class: "label" }, "常用预设"), presetSel),
        el("div", { class: "row" }, el("span", { class: "label" }, "接口地址"), apiBaseUrl),
        el("div", { class: "row" }, el("span", { class: "label" }, "模型名"), apiModel),
        modelChips,
        el("div", { class: "row" }, el("span", { class: "label" }, "密钥"), apiKey),
        el(
          "div",
          { class: "row" },
          el("label", { class: "row", style: "gap:8px" }, saveKey, el("span", { class: "muted" }, "保存 api_key 到 settings.json（不建议在共享电脑上勾选）")),
          apiSaveBtn,
          apiTestBtn
        ),
        el(
          "div",
          { class: "muted" },
          "提示：默认优先读取环境变量：SKILL_LLM_API_KEY / SKILL_LLM_BASE_URL / SKILL_LLM_MODEL（或 OPENAI_*）。你也可以手动填写并点击“测试连接”。"
        )
      )
    );
    root.appendChild(apiStatusBox);

    syncApiFromStatus().catch(() => {});
    return root;
  }

  function pageHelp() {
    renderHeader("新手教程", "目标：把句式写得更像范文 —— 并且每一步都有“范文证据”可追溯。");
    return el(
      "div",
      { class: "grid", style: "gap:18px" },
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "最快上手（4 步）"),
        el(
          "ol",
          null,
          el("li", null, "范文库页：选择同领域 PDF → 一键准备（离线生成范文片段/证据）。"),
          el("li", null, "全稿体检页：上传你的论文 PDF → 一键体检（对齐度低句子 + 范文证据 + 可复用句式）。"),
          el("li", null, "找差距页：粘贴正文 → 开始找差距 → 定位最不像范文的句子（带证据）。"),
          el("li", null, "对齐润色页：粘贴句子/段落 → 一键对齐润色（自动带证据；保守版/更像版）。"),
          el("li", null, "写作规范页：上传你的论文 PDF → 引用核查（避免曲解/张冠李戴；有原文证据）。")
        )
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "模型的作用在哪里？"),
        el("div", null, "找差距：不生成内容，只负责“对照证据”。"),
        el("div", null, "对齐润色：模型生成“哪里不像 + 怎么改更像 + 两种改法”，并引用本次用到的范文证据。"),
        el("div", { class: "muted" }, "提示：默认温度固定 0（尽量不发散），更像“受控润色/模板化”而不是自由发挥。")
      )
    );
  }

  async function render() {
    const my = ++renderSeq;
    const r = route();
    try {
      document.body.dataset.route = r;
    } catch {}
    try {
      const lbl = $("#libraryLabel");
      if (lbl) lbl.textContent = r === "norms" ? "参考文献库" : "范文专题库";
    } catch {}
    navActive(r);
    const page = $("#page");
    clear(page);

    try {
      await refreshLibraries();
      if (my !== renderSeq) return;
      await refreshLibraryStatus();
      if (my !== renderSeq) return;
      await refreshLLMStatus();
      if (my !== renderSeq) return;
    } catch (e) {
      toast(String(e.message || e), "bad", 6500);
    }

    if (my !== renderSeq) return;
    if (r === "home") page.appendChild(pageHome());
    else if (r === "library") page.appendChild(pageLibrary());
    else if (r === "scan") page.appendChild(pageScan());
    else if (r === "polish") page.appendChild(pagePolish());
    else if (r === "audit") page.appendChild(pageAudit());
    else if (r === "cite") page.appendChild(pageCite());
    else if (r === "norms") page.appendChild(pageNorms());
    else if (r === "llm") page.appendChild(pageLLM());
    else if (r === "help") page.appendChild(pageHelp());
    else page.appendChild(pageHome());
  }

  function bindEvents() {
    $$(".nav-item").forEach((b) => b.addEventListener("click", () => setRoute(b.dataset.route)));

    $("#librarySelect").addEventListener("change", async (e) => {
      state.library = e.target.value || "";
      const r = route();
      setSavedLibraryForRoute(r, state.library);
      toast(
        state.library ? `${r === "norms" ? "已切换参考文献库" : "已切换范文专题库"}：${state.library}` : r === "norms" ? "未选择参考文献库。" : "未选择范文专题库。",
        state.library ? "good" : "bad"
      );
      render().catch(() => {});
    });

    $("#refreshBtn").addEventListener("click", async () => {
      try {
        await refreshLibraries();
        await refreshLibraryStatus();
        await refreshLLMStatus();
        toast("已刷新。");
        // Only re-render on the library page to refresh the topic cards without wiping
        // in-progress text/results on other pages.
        try {
          if (route() === "library") window.setTimeout(() => render().catch(() => {}), 0);
        } catch {}
      } catch (e) {
        toast(String(e.message || e), "bad");
      }
    });

    $("#llmBadge").addEventListener("click", () => setRoute("llm"));

    $("#exitBtn").addEventListener("click", async () => {
      const ok = window.confirm("确定退出 TopHumanWriting 吗？\n\n这会停止本地服务（关闭网页后会自动退出）。");
      if (!ok) return;
      try {
        await apiPost("/api/app/exit", { reason: "user" });
        toast("已请求退出，本地服务将很快停止。");
        window.setTimeout(() => {
          try {
            window.close();
          } catch {}
        }, 350);
      } catch (e) {
        toast(String(e.message || e), "bad", 6500);
      }
    });

    $("#themeToggle").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || "light";
      setTheme(cur === "dark" ? "light" : "dark");
    });

    $("#accentSelect").addEventListener("change", (e) => setAccent(e.target.value || "teal"));

    $("#modalClose").addEventListener("click", closeModal);
    $("#modalBackdrop").addEventListener("click", (e) => {
      if (e.target && e.target.id === "modalBackdrop") closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });
  }

  function maybeShowFirstRunModal() {
    const key = "aiw.onboardSeen.v1";
    if (localStorage.getItem(key) === "1") return;
    if (route() !== "home") return;
    try {
      const open = !$("#modalBackdrop").classList.contains("hidden");
      if (open) return;
    } catch {}

    localStorage.setItem(key, "1");

    const body = el(
      "div",
      { class: "grid", style: "gap:12px" },
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "3 步上手（不需要懂模型）"),
        el(
          "ol",
          null,
          el("li", null, "准备范文专题库：选择同领域 PDF → 一键准备（离线生成范文片段/证据）。"),
          el("li", null, "粘贴你的文本：句子/段落/正文都可以（中英混合可）。"),
          el("li", null, "先用“找差距（离线）”定位最不像范文的句子；需要一键生成时再到“润色设置”测试连接。")
        )
      ),
      el(
        "div",
        { class: "row", style: "justify-content:flex-end" },
        el(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            onclick: () => {
              closeModal();
              openPrepWizard({ need: "rag" });
            },
          },
          "一键准备范文专题库"
        ),
        el(
          "button",
          {
            class: "btn",
            type: "button",
            onclick: () => {
              closeModal();
              setRoute("help");
            },
          },
          "先看新手教程"
        )
      )
    );

    openModal("欢迎使用 TopHumanWriting", body);
  }

  function init() {
    const theme = localStorage.getItem("aiw.theme") || "light";
    setTheme(theme);
    const accent = localStorage.getItem("aiw.accent") || "ocean";
    setAccent(accent);
    bindClientLifecycle();
    bindEvents();
    render();
    window.setTimeout(() => maybeShowFirstRunModal(), 480);
  }

  window.addEventListener("hashchange", () => {
    try {
      const back = $("#modalBackdrop");
      if (back && !back.classList.contains("hidden")) closeModal();
    } catch {}
    render();
  });
  init();
})();
