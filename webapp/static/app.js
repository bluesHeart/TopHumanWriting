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
    llm: null,
    llmApi: null,
    libraryStatus: null,
    buildTaskId: localStorage.getItem("aiw.buildTaskId") || "",
    buildPollTimer: null,
    citeTaskId: localStorage.getItem("aiw.citeTaskId") || "",
    citePollTimer: null,
    pdfFolder: localStorage.getItem("aiw.pdfFolder") || "",
    scanDraft: localStorage.getItem("aiw.scanDraft") || "",
    polishDraft: localStorage.getItem("aiw.polishDraft") || "",
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
    location.hash = name;
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
    const libs = state.libraries || [];
    sel.appendChild(el("option", { value: "" }, "— 选择范文库 —"));
    for (const lib of libs) {
      const name = (lib && lib.name) || "";
      if (!name) continue;
      sel.appendChild(el("option", { value: name }, name));
    }
    sel.value = state.library || "";
    localStorage.setItem("aiw.library", state.library || "");
  }

  async function refreshLibraries() {
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

    const names = libraryNames();
    if (state.library && !names.includes(state.library)) state.library = "";

    // If user hasn't picked one yet, auto-select a ready demo library.
    const hasExplicit = !!localStorage.getItem("aiw.library");
    if (!hasExplicit) {
      const ready = (state.libraries || []).find((x) => x && x.rag_index);
      if (ready && ready.name) state.library = ready.name;
    }
    if (!state.library && names.length) state.library = names[0];
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
      title = "范文证据库（用于对照）";
      desc = "用于“找差距/模仿改写”的范文段落检索（离线），给你可追溯的范文证据。";
      need = "找差距/模仿改写都需要它。";
      nextRoute = "library";
      nextBtn = "去准备";
    } else if (k === "cite") {
      title = "引用证据库（引用写法）";
      desc = "从范文中抽取“引用句子 + 参考文献”，用于检索可借鉴的引用写法。";
      need = "引用写法需要它；通常在准备范文库后再构建。";
      nextRoute = "cite";
      nextBtn = "去引用写法";
    }

    const badge = el("span", { class: "badge " + (ok ? "good" : "bad") }, ok ? "已就绪" : "未就绪");
    const body = el(
      "div",
      { class: "grid", style: "gap:10px" },
      el("div", { class: "row" }, badge, el("span", { class: "muted" }, state.library ? `当前范文库：${state.library}` : "未选择范文库")),
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
    const need = String(opts.need || "rag").trim().toLowerCase() || "rag"; // "rag" | "cite"
    const resume = opts.resume && typeof opts.resume === "object" ? opts.resume : null;
    const presetLibrary = String(opts.library || "").trim();
    const lockLibrary = !!opts.lockLibrary;

    const title = "准备范文库（第一次使用）";

    let selectedFiles = [];
    let importing = false;
    let importCanceled = false;
    let currentTaskId = "";
    let pollTimer = null;

    const pdfInput = el("input", { type: "file", multiple: true, accept: ".pdf,application/pdf", style: "display:none" });
    pdfInput.setAttribute("webkitdirectory", "");
    pdfInput.setAttribute("directory", "");
    const pdfInputFiles = el("input", { type: "file", multiple: true, accept: ".pdf,application/pdf", style: "display:none" });

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
        cite_extract: "抽取引用信息",
        cite_embed: "构建引用证据",
        cite_index: "整理引用证据",
        cite_done: "引用证据完成",
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
    const libName = el("input", { class: "input", placeholder: "给范文库起个名字（例如：finance_2026）", style: "flex:1; min-width:220px; display:none" });
    const libCreateBtn = el("button", { class: "btn btn-primary", type: "button", style: "display:none" }, "创建");
    const libNewBtn = el("button", { class: "btn", type: "button" }, "新建范文库");
    const libHint = el("div", { class: "muted" }, "范文库就是：你收集的同领域顶级 PDF。只要准备一次，之后扫描/润色都会直接有“范文证据”。");

    if (presetLibrary) {
      state.library = presetLibrary;
      localStorage.setItem("aiw.library", state.library);
      updateGlobalLibraryUI();
    }

    function syncLibSelOptions() {
      clear(libSel);
      libSel.appendChild(el("option", { value: "" }, "— 选择范文库 —"));
      for (const lib of state.libraries || []) {
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
      if (!name) return toast("请输入范文库名字。", "bad");
      libCreateBtn.disabled = true;
      libCreateBtn.textContent = "创建中…";
      try {
        const r = await apiPost("/api/libraries", { name });
        let safe = name;
        try {
          const p = String((r && r.path) || "");
          const base = p.split(/[\\/]/).pop() || "";
          if (base.toLowerCase().endsWith(".json")) safe = base.slice(0, -5) || safe;
        } catch {}
        state.library = safe;
        localStorage.setItem("aiw.library", state.library);
        await refreshLibraries();
        await refreshLibraryStatus();
        updateGlobalLibraryUI();
        syncLibSelOptions();
        await syncImportedCount();
        toast("已创建范文库。");
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
      localStorage.setItem("aiw.library", state.library);
      updateGlobalLibraryUI();
      await refreshLibraryStatus().catch(() => {});
      await syncImportedCount().catch(() => {});
    });

    // Step 1: pick folder (optional when evidence already exists)
    const selectedInfo = el("div", { class: "muted mono" }, "（可选）新增范文：优先选择“PDF 文件夹”。若无反应，可用“选择多个 PDF”。");
    const pickBtn = el("button", { class: "btn", type: "button" }, "选择 PDF 文件夹…");
    const pickFilesBtn = el("button", { class: "btn btn-ghost", type: "button" }, "选择多个 PDF…");
    pickBtn.onclick = () => pdfInput.click();
    pickFilesBtn.onclick = () => pdfInputFiles.click();

    function updateSelectedInfo() {
      const pdfs = selectedFiles.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) {
        selectedInfo.textContent = "（可选）新增范文：优先选择“PDF 文件夹”。若无反应，可用“选择多个 PDF”。";
        return;
      }
      const rel0 = String(pdfs[0].webkitRelativePath || "");
      const folder = rel0 && rel0.includes("/") ? rel0.split("/")[0] : "";
      selectedInfo.textContent = folder
        ? `已选择：${fmtCount(pdfs.length)} 个 PDF · 文件夹：${folder}`
        : `已选择：${fmtCount(pdfs.length)} 个 PDF`;
    }

    pdfInput.addEventListener("change", () => {
      selectedFiles = Array.from(pdfInput.files || []);
      updateSelectedInfo();
      if (selectedFiles.length) toast("已选择 PDF 文件夹。");
    });

    pdfInputFiles.addEventListener("change", () => {
      selectedFiles = Array.from(pdfInputFiles.files || []);
      updateSelectedInfo();
      if (selectedFiles.length) toast("已选择 PDF 文件。");
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
      const ragRoot = lib && lib.rag_pdf_root ? String(lib.rag_pdf_root) : "";
      const importN = lib && lib.pdf_import_count != null ? Number(lib.pdf_import_count) : null;
      const importRoot = lib && lib.pdf_import_root ? String(lib.pdf_import_root) : "";

      if (ragOk) {
        const n = Number.isFinite(ragN) ? ragN : 0;
        importedInfo.textContent = `已准备：${fmtCount(n)} 篇范文（证据库）${ragRoot ? ` · 来源：${ragRoot}` : ""}`;
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
    const startBtn = el("button", { class: "btn btn-primary", type: "button" }, "一键准备（导入 + 生成证据）");
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
      if (!resume) return;
      const cur = route();
      if (resume.autoKey) localStorage.setItem(String(resume.autoKey), String(resume.autoValue || "1"));
      if (resume.route && resume.route !== cur) setRoute(resume.route);
      window.setTimeout(() => render().catch(() => {}), 80);
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
      if (!state.library) return toast("请先选择/创建范文库。", "bad", 4500);

      startBtn.disabled = true;
      pickBtn.disabled = true;
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
        if (!hasSelection && (!Number.isFinite(importedN) || importedN <= 0) && !ragOk0) {
          toast("请先选择包含 PDF 的文件夹。", "bad", 4500);
          return;
        }

        let didImport = false;
        if (hasSelection) {
          await runImport();
          didImport = true;
        } else {
          if (ragOk0) setBars(100, `跳过导入：已存在范文证据（${fmtCount(importedN)} 篇）`, null, null);
          else setBars(100, `跳过导入：已检测到本地已导入 ${fmtCount(importedN)} 个 PDF`, null, null);
        }

        if (!ragOk0 || didImport) {
          setBars(null, null, 0, "正在生成范文对照证据…（首次可能较慢）");
          await runBuildLibrary();
        } else {
          setBars(null, null, 100, "已就绪：范文对照证据已存在");
        }

        await refreshLibraryStatus().catch(() => {});
        const st1 = state.libraryStatus || {};
        if (!st1.rag_index) throw new Error("范文对照证据未就绪（请重试）。");

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
        pickBtn.disabled = false;
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

    const needLabel = need === "cite" ? "要用“引用写法”需要先准备引用证据（可选项会更慢）。" : "准备完成后，“找差距/模仿改写”会自动出现范文证据。";

    function syncReadyHint() {
      const st = state.libraryStatus || {};
      const ragOk = !!st.rag_index;
      if (!state.library) {
        readyHint.textContent = "请先选择/创建范文库。";
        goBtn.style.display = "none";
        startBtn.textContent = "一键准备（导入 + 生成证据）";
        return;
      }
      if (ragOk) {
        readyHint.textContent = "✅ 已检测到范文证据：你可以直接开始写作（找差距/模仿改写）。如要新增范文，再选择文件夹并点击“更新证据”。";
        goBtn.style.display = "";
        startBtn.textContent = "更新证据（可选）";
      } else {
        readyHint.textContent = "第一次需要：导入同领域 PDF → 生成范文证据。完成后写作过程会显示“参考哪段范文/哪里不像/怎么改更像”。";
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
      el("div", { class: "muted" }, "你只需要把同领域的范文 PDF 选进来。软件会在本地生成“可引用的范文证据”，之后写作过程就能白箱对照。"),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "1) 选择范文库"),
        el("div", { class: "row" }, libSel, libNewBtn, libName, libCreateBtn),
        libHint
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "2) 导入范文 PDF（可选：新增范文时）"),
        el("div", { class: "row" }, pickBtn, pickFilesBtn, selectedInfo),
        el("div", { class: "muted" }, "建议：50–100 篇 PDF，尽量同领域/同期刊/同风格。越“同风格”，对齐越像。")
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "3) 一键准备（离线）"),
        readyHint,
        el(
          "div",
          { class: "row" },
          goBtn,
          startBtn,
          el("label", { class: "row", style: "gap:8px" }, includeCite, el("span", { class: "muted" }, "同时准备引用证据（可选）")),
          cancelBtn
        ),
        el("div", { class: "muted" }, needLabel),
        importedInfo,
        el("div", { class: "label", style: "margin-top:10px" }, "导入进度"),
        importBar,
        importText,
        el("div", { class: "label", style: "margin-top:10px" }, "准备进度"),
        buildBar,
        buildText
      ),
      pdfInput,
      pdfInputFiles
    );

    syncLibSelOptions();
    // If no library selected but we have exactly one existing library, auto-select it.
    if (!state.library) {
      const names = libraryNames();
      if (names.length === 1) {
        state.library = names[0];
        localStorage.setItem("aiw.library", state.library);
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
      libHint.textContent = "你正在准备当前范文库：导入范文 PDF → 生成证据 → 在写作里随时引用（白箱可追溯）。";
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
        try {
          pdfInputFiles.remove();
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
      rag_extract: "切分范文片段",
      rag_embed: "构建范文对照证据",
      rag_done: "范文对照证据完成",
      cite_extract: "抽取引用信息",
      cite_embed: "构建引用证据",
      cite_index: "整理引用证据",
      cite_done: "引用证据完成",
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
          title: ragOk ? "范文库已准备好" : "点击一键准备范文库",
          onclick: () => openPrepWizard({ need: "rag" }),
        },
        ragOk ? "✅ 范文库就绪" : "⚠️ 准备范文库"
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
    $("#modalTitle").textContent = title;
    const body = $("#modalBody");
    clear(body);
    body.appendChild(bodyNode);
    $("#modalBackdrop").classList.remove("hidden");
  }

  function closeModal() {
    const onClose = state.modalOnClose;
    state.modalOnClose = null;
    $("#modalBackdrop").classList.add("hidden");
    $("#modalTitle").textContent = "—";
    clear($("#modalBody"));
    try {
      if (typeof onClose === "function") onClose();
    } catch {}
  }

  async function refreshLLMStatus() {
    const st = await apiGet("/api/llm/status");
    state.llm = st;

    try {
      state.llmApi = await apiGet("/api/llm/api/status");
    } catch {
      state.llmApi = null;
    }

    const badge = $("#llmBadge");
    const provider = localStorage.getItem("aiw.llmProvider") || "local";

    if (provider === "api" && state.llmApi) {
      const api = state.llmApi || {};
      const hasKey = !!api.api_key_present;
      const hasUrl = !!String(api.base_url || "").trim();
      const hasModel = !!String(api.model || "").trim();

      let text = "模型: API ";
      if (!hasKey) text += "缺少 Key";
      else if (!hasUrl) text += "缺少 URL";
      else if (!hasModel) text += "缺少模型";
      else text += "已配置";

      badge.textContent = text;
      badge.classList.remove("ok", "warn", "bad");
      badge.classList.add(hasKey && hasUrl && hasModel ? "ok" : "bad");
      return;
    }

    const modelOk = !!st.model_ok;
    const serverOk = !!st.server_ok;
    const running = !!st.running;
    let text = "模型: ";
    if (!serverOk) text += "缺少 llama-server";
    else if (!modelOk) text += "缺少模型";
    else if (running) text += "运行中";
    else text += "未启动";
    badge.textContent = text;
    badge.classList.remove("ok", "warn", "bad");
    badge.classList.add(running ? "ok" : serverOk && modelOk ? "warn" : "bad");
  }

  async function copyText(s) {
    try {
      await navigator.clipboard.writeText(String(s || ""));
      toast("已复制到剪贴板。");
    } catch {
      toast("复制失败：浏览器不允许（可手动复制）。", "bad");
    }
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
      el("div", { class: "diff-panel" }, el("div", { class: "muted" }, "改写"), el("div", { class: "diff-text" }, right))
    );
  }

  function exemplarList(exemplars, opts = {}) {
    const { library } = opts;
    const list = el("div", { class: "list" });
    if (!exemplars || !exemplars.length) {
      list.appendChild(el("div", { class: "muted" }, "没有检索到范文片段。"));
      return list;
    }
    for (const ex of exemplars) {
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
                  onclick: async () => {
                    try {
                      await apiPost("/api/library/open_pdf", { library, pdf: ex.pdf });
                    } catch (e) {
                      toast(String(e.message || e), "bad");
                    }
                  },
                },
                "打开"
              )
            : null
        )
      );
      list.appendChild(el("div", { class: "item" }, head, el("div", { class: "quote" }, ex.text || "")));
    }
    return list;
  }

  const HOME_SAMPLE_TEXT =
    "This paper studies how risk premia vary with market conditions. We document strong cross-sectional dispersion and show that a parsimonious factor model explains most of the variation.";

  function pageHome() {
    renderHeader("开始", "把你的段落写得更像顶级范文：先对照证据，再受控改写（白箱可追溯）。");

    const root = el("div", { class: "home" });
    const inner = el("div", { class: "home-inner" });

    const hero = el(
      "div",
      { class: "home-hero" },
      el("div", { class: "home-title" }, "TopHumanWriting"),
      el("div", { class: "home-sub" }, "模仿同领域顶级人类范文写法 · 避免 AI 味 · 证据可追溯"),
      el(
        "div",
        { class: "home-kicker" },
        "三步上手：① 准备范文库（只做一次）② 粘贴段落 ③ 找差距 / 模仿改写（每条建议都有范文证据背书）。"
      )
    );

    const modeKey = "aiw.homeMode";
    const savedMode = localStorage.getItem(modeKey) || "scan";
    let mode = savedMode === "polish" || savedMode === "cite" ? savedMode : "scan";

    const text = el("textarea", { class: "textarea home-textarea", placeholder: "粘贴你要改的句子/段落（中英混合可）…" });
    const homeDraftKey = "aiw.homeDraft";
    text.value = localStorage.getItem(homeDraftKey) || "";
    text.addEventListener("input", () => localStorage.setItem(homeDraftKey, text.value || ""));

    function modeChip(id, label, desc) {
      const b = el(
        "button",
        {
          class: "pill",
          type: "button",
          "data-mode": id,
          onclick: () => {
            mode = id;
            localStorage.setItem(modeKey, mode);
            renderModeUI();
            renderStatus();
          },
        },
        label
      );
      b.title = desc || "";
      return b;
    }

    const modeRow = el(
      "div",
      { class: "home-modes" },
      modeChip("scan", "🧭 找差距（哪里不像）", "只做范文对照，找出最不像范文的句子"),
      modeChip("polish", "✨ 模仿改写（更像范文）", "白箱输出：诊断 + 可选改法 + 范文证据"),
      modeChip("cite", "🔖 引用写法（可借鉴）", "检索范文里的引用表达与参考文献")
    );

    const hint = el("div", { class: "home-hint" });
    const primaryBtn = el("button", { class: "btn btn-primary home-primary", type: "button" }, "开始");
    const secondaryBtn = el("button", { class: "btn home-secondary", type: "button" }, "准备范文库…");
    const sampleBtn = el("button", { class: "btn btn-ghost", type: "button" }, "填入示例");
    const clearBtn = el("button", { class: "btn btn-ghost", type: "button" }, "清空");

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

    secondaryBtn.onclick = () => openPrepWizard({ need: "rag" });

    primaryBtn.onclick = () => {
      const raw = (text.value || "").trim();
      if (!raw) return toast("请先粘贴文本。", "bad");

      if (mode === "scan") {
        state.scanDraft = raw;
        localStorage.setItem("aiw.scanDraft", state.scanDraft);
        if (!state.library || !(state.libraryStatus && state.libraryStatus.rag_index)) {
          openPrepWizard({ need: "rag", resume: { route: "scan", autoKey: "aiw.scanAutoRun", autoValue: "1" } });
          return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
        }
        localStorage.setItem("aiw.scanAutoRun", "1");
        return setRoute("scan");
      }
      if (mode === "polish") {
        state.polishDraft = raw;
        localStorage.setItem("aiw.polishDraft", state.polishDraft);
        if (!state.library || !(state.libraryStatus && state.libraryStatus.rag_index)) {
          openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
          return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
        }
        localStorage.setItem("aiw.polishAutoRun", "generate");
        return setRoute("polish");
      }
      if (mode === "cite") {
        localStorage.setItem("aiw.citeQueryDraft", raw);
        if (!state.library || !(state.libraryStatus && state.libraryStatus.cite_index)) {
          openPrepWizard({ need: "cite", resume: { route: "cite", autoKey: "aiw.citeAutoRun", autoValue: "1" } });
          return toast("先准备范文库（可选：同时准备引用写法）。", "bad", 4500);
        }
        localStorage.setItem("aiw.citeAutoRun", "1");
        return setRoute("cite");
      }
    };

    text.addEventListener("keydown", (e) => {
      if (!e) return;
      // Ctrl+Enter / Cmd+Enter to run.
      const isMac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || "");
      const hot = isMac ? e.metaKey && e.key === "Enter" : e.ctrlKey && e.key === "Enter";
      if (hot) {
        e.preventDefault();
        primaryBtn.click();
      }
    });

    const statusRow = el("div", { class: "home-status" });

    function statusPill(label, ok, onClick) {
      if (typeof onClick !== "function") {
        return el("span", { class: "pill small static " + (ok ? "ok" : "bad") }, label);
      }
      const b = el("button", { class: "pill small " + (ok ? "ok" : "bad"), type: "button" }, label);
      b.onclick = onClick;
      return b;
    }

    function renderStatus() {
      clear(statusRow);
      const st = state.libraryStatus || {};
      const ragOk = !!st.rag_index;
      const citeOk = !!st.cite_index;

      statusRow.appendChild(statusPill(state.library ? `📚 当前范文库：${state.library}` : "📚 未选择范文库", !!state.library, () => openPrepWizard({ need: "rag" })));

      if (mode === "cite") {
        statusRow.appendChild(statusPill(citeOk ? "✅ 引用写法已准备" : "＋ 准备引用写法（可选）", citeOk, () => openPrepWizard({ need: "cite" })));
        statusRow.appendChild(statusPill("🧠 本模式不需要模型", true, () => setRoute("llm")));
        return;
      }

      statusRow.appendChild(statusPill(ragOk ? "✅ 范文库已准备" : "⚠️ 范文库未准备", ragOk, () => openPrepWizard({ need: "rag" })));

      if (mode !== "polish") {
        statusRow.appendChild(statusPill("🧠 本模式不需要模型", true, () => setRoute("llm")));
        return;
      }

      const provider = localStorage.getItem("aiw.llmProvider") || "local";
      let llmOk = false;
      let llmLabel = "";
      if (provider === "api") {
        const api = state.llmApi || {};
        llmOk = !!(api.api_key_present && String(api.base_url || "").trim() && String(api.model || "").trim());
        llmLabel = llmOk ? "🧠 API 已配置（用于改写）" : "⚠️ API 未配置（可用本地模型）";
      } else {
        const ls = state.llm || {};
        const hasAssets = !!(ls.server_ok && ls.model_ok);
        llmOk = hasAssets;
        llmLabel = ls.running ? "🧠 本地模型运行中（用于改写）" : hasAssets ? "🧠 本地模型已安装（用于改写）" : "⚠️ 本地模型缺失";
      }
      statusRow.appendChild(statusPill(llmLabel, llmOk, () => setRoute("llm")));
    }

    function renderModeUI() {
      $$(".pill[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
      if (mode === "scan") {
        hint.textContent = "把正文拆成句子，找出最不像范文的句子，并给出“对应范文证据”（可一键继续模仿改写）。";
        primaryBtn.textContent = "开始找差距";
      } else if (mode === "polish") {
        hint.textContent = "先对照范文证据，再给出“哪里不像 + 怎么改更像 + 两种改法（保守/更像）”。每条建议都可追溯到范文。";
        primaryBtn.textContent = "生成模仿改写";
      } else if (mode === "cite") {
        hint.textContent = "检索范文里常见的引用句式（正文引用 + 参考文献），可直接借鉴到你的写作里。";
        primaryBtn.textContent = "检索引用写法";
      }
    }

    const inputCard = el(
      "div",
      { class: "card home-card" },
      modeRow,
      text,
      el("div", { class: "home-actions" }, primaryBtn),
      el("div", { class: "home-subactions" }, secondaryBtn, sampleBtn, clearBtn),
      el("div", { class: "muted" }, hint)
    );

    const onboarding = el("div", { class: "home-onboard" });
    function renderOnboarding() {
      clear(onboarding);
      const st = state.libraryStatus || {};
      if (!state.library) {
        onboarding.appendChild(
          el(
            "div",
            { class: "card" },
            el("div", { class: "label" }, "第一次使用：先准备范文库（只做一次）"),
            el("div", { class: "muted" }, "把同领域顶级 PDF 放进来，软件会生成“可引用的范文证据”。之后你每次写作都能白箱对照。"),
            el(
              "div",
              { class: "row" },
              el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "一键准备范文库"),
              el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
            )
          )
        );
        return;
      }

      if (!st.rag_index) {
        onboarding.appendChild(
          el(
            "div",
            { class: "card" },
            el("div", { class: "label" }, "范文库未准备好：还不能找差距/模仿改写"),
            el("div", { class: "muted" }, "第一次需要导入 PDF，并在本地生成“范文证据库”。完成后这里会自动变得可用。"),
            el(
              "div",
              { class: "row" },
              el("button", { class: "btn btn-primary", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "一键准备范文库"),
              el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去范文库页")
            )
          )
        );
      }
    }

    inner.appendChild(hero);
    inner.appendChild(inputCard);
    inner.appendChild(statusRow);
    inner.appendChild(onboarding);
    root.appendChild(inner);

    renderModeUI();
    renderStatus();
    renderOnboarding();

    return root;
  }

  function pageLibrary() {
    renderHeader("范文库", "像专题库一样管理：每个范文库=同领域顶级文档集合。准备一次，写作全程白箱对齐。");
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
      localStorage.setItem("aiw.library", state.library);
      updateGlobalLibraryUI();
      await refreshLibraryStatus().catch(() => {});
      toast(`已切换范文库：${libName}`);
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
              const r = await apiPost("/api/libraries", { name });
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
        el("div", { class: "muted" }, "范文库=你的“专题库”。把同领域顶级 PDF 放进来，之后写作就能逐句对照、可追溯。"),
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
      const builtAt = (lib && lib.rag_built_at_iso) || "";
      const bits = [`范文 ${fmtCount(pdfCount)} 篇`];
      bits.push(ragOk ? "已准备" : "未准备");
      bits.push(citeOk ? "含引用写法" : "引用可选");
      if (builtAt) bits.push(`构建：${builtAt}`);
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
          disabled: active,
          onclick: async () => {
            await useLibrary(name, "home");
          },
        },
        active ? "正在使用" : "进入写作"
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
        ragOk ? "更新/重建" : "一键准备"
      );

      const citeBtn = el(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          onclick: async () => {
            await useLibrary(name);
            if (citeOk) return setRoute("cite");
            openPrepWizard({ need: "cite", library: name, lockLibrary: true });
          },
        },
        citeOk ? "打开引用写法" : "准备引用写法"
      );

      const copyBtn = el(
        "button",
        {
          class: "chip",
          type: "button",
          title: "复制来源路径",
          onclick: () => {
            const ragOk2 = !!(lib && lib.rag_index);
            const src = ragOk2 ? String(lib.rag_pdf_root || "") : String(lib.pdf_import_root || "");
            if (!src) return toast("暂无来源路径。", "bad", 3500);
            copyText(src);
            toast("已复制来源路径。");
          },
        },
        "复制路径"
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
        el("div", { class: "topic-path muted mono", title: librarySourceLine(lib) }, librarySourceLine(lib)),
        el("div", { class: "topic-actions" }, primaryBtn, prepBtn, citeBtn, copyBtn)
      );
    }

    const search = el("input", { class: "input", placeholder: "搜索范文库…", style: "flex:1; min-width:240px" });
    search.value = localStorage.getItem("aiw.libraryQuery") || "";

    const createBtn = el("button", { class: "btn btn-primary", type: "button", onclick: () => openCreateLibraryModal() }, "新建范文库");
    const prepBtn = el("button", { class: "btn", type: "button", onclick: () => openPrepWizard({ need: "rag" }) }, "一键准备（向导）");

    const details = el(
      "details",
      { class: "details" },
      el("summary", { class: "label" }, "这是什么？（点开查看）"),
      el("div", { class: "muted" }, "范文库=你的“专题库”：放入同领域顶级 PDF，准备一次，后续写作就能逐句对照（白箱可追溯）。"),
      el(
        "ol",
        null,
        el("li", null, "找差距：不生成内容，只做对照，定位哪里不像范文。"),
        el("li", null, "模仿改写：引用范文证据，给出“哪里不像 + 怎么改更像 + 两种改法”。"),
        el("li", null, "引用写法：检索范文里常见的引用句式与参考文献（可选）。")
      )
    );

    root.appendChild(el("div", { class: "card" }, el("div", { class: "row" }, search, createBtn, prepBtn), details));

    const libs = Array.isArray(state.libraries) ? state.libraries.slice() : [];
    libs.sort((a, b) => {
      const aOk = a && a.rag_index ? 1 : 0;
      const bOk = b && b.rag_index ? 1 : 0;
      if (aOk !== bOk) return bOk - aOk;
      return String((a && a.name) || "").localeCompare(String((b && b.name) || ""), "zh-Hans-CN");
    });

    const grid = el("div", { class: "topic-grid" });

    const createCard = el(
      "div",
      { class: "card topic-card topic-create" },
      el("div", { class: "topic-icon big", "aria-hidden": "true" }, "＋"),
      el("div", { class: "topic-name" }, "新建范文库"),
      el("div", { class: "topic-sub" }, "按领域建立你的“专题库”，准备一次即可长期复用。"),
      el("button", { class: "btn btn-primary", type: "button", onclick: openCreateLibraryModal }, "新建")
    );

    function renderGrid() {
      clear(grid);
      grid.appendChild(createCard);

      const q = String(search.value || "").trim().toLowerCase();
      const shown = q ? libs.filter((x) => String((x && x.name) || "").toLowerCase().includes(q)) : libs;

      for (const lib of shown) {
        const c = topicCard(lib);
        if (c) grid.appendChild(c);
      }

      if (!shown.length) {
        grid.appendChild(el("div", { class: "card" }, el("div", { class: "muted" }, "没有匹配的范文库。你可以新建一个，或清空搜索关键字。")));
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
    renderHeader("找差距", "先找出“最不像范文”的句子，并给出对应的范文证据（可继续一键模仿改写）。");
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
            });
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
        resultsBox.appendChild(el("div", { class: "muted" }, "第一次需要导入同领域 PDF，并在本地生成“范文证据库”。完成后才能对白箱对照。"));
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
      const list = el("div", { class: "list" });
        for (const it of items) {
        const pct = Number(it.pct || 0);
        const badgeCls = pct >= 80 ? "badge good" : pct >= 60 ? "badge" : "badge bad";
        const sent = String(it.text || "");
          const head = el(
            "div",
            { class: "item-header" },
            el(
              "div",
              null,
              el("span", { class: badgeCls }, `${pct}%`),
              " ",
              el("span", null, sent.slice(0, 220) + (sent.length > 220 ? "…" : ""))
            ),
            el(
              "div",
              { class: "actions-col" },
              el(
                "button",
                {
                  class: "btn btn-small",
                  type: "button",
                  onclick: () => openModal("范文对照（Top-K）", exemplarList(it.exemplars || [], { library: state.library })),
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
                "润色这个句子"
              )
            )
          );
          list.appendChild(el("div", { class: "item" }, head));
        }
        resultsBox.appendChild(list);
      }

    const inputCard = el(
      "div",
      { class: "card" },
      el("div", { class: "label" }, "输入文本"),
      text,
      el(
        "div",
        { class: "row" },
        el("span", { class: "label" }, "每句范文数"),
        topk,
        el("span", { class: "label" }, "最多扫描句子"),
        maxItems,
        runBtn,
        el("span", { class: "muted" }, "找差距只做对照：不生成内容。")
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
    renderHeader("模仿改写", "白箱：参考哪段范文 → 哪里不像 → 怎么改更像（默认离线本地模型，可选 API）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const selected = el("textarea", { class: "textarea", placeholder: "选中要润色的句子/段落…" });
    selected.value = state.polishDraft || "";
    selected.addEventListener("input", () => {
      state.polishDraft = selected.value || "";
      localStorage.setItem("aiw.polishDraft", state.polishDraft);
    });

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
    const providerDefault = localStorage.getItem("aiw.llmProvider") || "local";
    const maxTokDefault = Number.isFinite(storedMaxTok) && storedMaxTok > 0 ? storedMaxTok : providerDefault === "api" ? 4096 : 650;
    const maxTok = el("input", { class: "input", value: String(Math.round(maxTokDefault)), style: "width:120px", inputmode: "numeric", title: "输出长度上限（越大越慢）" });
    maxTok.addEventListener("change", () => {
      const v = Number((maxTok.value || "").trim() || 0);
      if (Number.isFinite(v) && v > 0) localStorage.setItem("aiw.polishMaxTokens", String(Math.round(v)));
    });

    const providerSel = el(
      "select",
      { class: "select", style: "width:220px" },
      el("option", { value: "local" }, "本地 Qwen（离线）"),
      el("option", { value: "api" }, "大模型 API（OpenAI兼容）")
    );
    providerSel.value = localStorage.getItem("aiw.llmProvider") || "local";
    providerSel.addEventListener("change", () => {
      localStorage.setItem("aiw.llmProvider", providerSel.value || "local");
      refreshLLMStatus().catch(() => {});
      if ((providerSel.value || "") === "api") {
        const cur = Number((maxTok.value || "").trim() || 0);
        if (!Number.isFinite(cur) || cur < 2048) {
          maxTok.value = "4096";
          localStorage.setItem("aiw.polishMaxTokens", "4096");
          toast("已切换到 API：默认输出长度已调大（4096）。");
        }
      }
    });

    let advOpen = localStorage.getItem("aiw.polishAdv") === "1";
    const advRow = el(
      "div",
      { class: "row", style: `display:${advOpen ? "flex" : "none"}` },
      el("span", { class: "label" }, "模型"),
      providerSel,
      el("span", { class: "label" }, "输出长度"),
      maxTok,
      el("span", { class: "muted" }, "温度固定 0（尽量不发散）。API 需先在“模型设置”配置。")
    );

    const exemplarsBox = el("div", { class: "card" });
    const outBox = el("div", { class: "card" });

    function renderExemplars(exs, title = "范文对照（将作为证据引用）") {
      clear(exemplarsBox);
      exemplarsBox.appendChild(el("div", { class: "label" }, title));
      exemplarsBox.appendChild(exemplarList(exs || [], { library: state.library }));
    }

    function renderExemplarsEmpty() {
      clear(exemplarsBox);
      if (!state.library) {
        exemplarsBox.appendChild(el("div", { class: "label" }, "还不能开始：请先准备范文库"));
        exemplarsBox.appendChild(el("div", { class: "muted" }, "准备一次后，润色会显示：参考哪段范文、哪里不像、怎么改更像。"));
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
        exemplarsBox.appendChild(el("div", { class: "muted" }, "第一次需要导入同领域 PDF，并在本地生成“范文证据库”。完成后才能白箱对照。"));
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
      exemplarsBox.appendChild(el("div", { class: "muted" }, "先获取范文对照（证据），再生成模仿改写。"));
    }

    function renderOutEmpty() {
      clear(outBox);
      outBox.appendChild(el("div", { class: "label" }, "白箱输出将在这里展示"));
      outBox.appendChild(el("div", { class: "muted" }, "包含：对齐度对比（原文/轻改/中改） + 诊断（带证据） + 改写（带引用）。"));
      outBox.appendChild(el("div", { class: "muted" }, "建议流程：先点“获取范文对照”确认证据 → 再点“生成模仿改写”。"));
    }

    let genUiTimer = null;
    function stopGenUiTimer() {
      if (genUiTimer) window.clearInterval(genUiTimer);
      genUiTimer = null;
    }

    function renderOutGenerating(provider, onCancel) {
      stopGenUiTimer();
      clear(outBox);
      const p = String(provider || "").toLowerCase();
      const title = p === "api" ? "生成中…（API 请求中）" : "生成中…（本地模型运行中）";

      const stage = el("div", { class: "muted" }, "阶段：准备中…");
      const timeEl = el("div", { class: "muted mono" }, "耗时：0s");
      const bar = el("div", { class: "progress" }, el("div"));

      const stages = [
        "检索范文证据（可追溯）",
        "生成诊断（哪里不像 + 句式模板）",
        "生成改写（轻改/中改）",
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
          el("div", { class: "muted" }, "提示：若失败，多数是输出长度太小导致 JSON 截断；API 建议 ≥ 4096。")
        )
      );

      genUiTimer = window.setInterval(() => {
        const elapsed = Date.now() - start;
        timeEl.textContent = `耗时：${Math.floor(elapsed / 1000)}s`;

        // Pseudo progress: keep moving but never "complete" before the response returns.
        pct = Math.min(92, pct + (p === "api" ? 0.35 : 0.55));
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
          el("button", { class: "btn", type: "button", onclick: () => setRoute("llm") }, "去模型设置")
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
        toast("已获取范文对照。");
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

      outBox.appendChild(
        el("div", { class: "label" }, `输出语言：${result.language || "mixed"} · 诊断 ${diag.length} 条 · 改写 ${vars.length} 条`)
      );

      const llmInfo = (r && r.llm) || null;
      if (llmInfo && llmInfo.provider === "api") {
        outBox.appendChild(el("div", { class: "muted" }, `模型：API · ${llmInfo.model || "—"} · ${llmInfo.base_url || "—"}`));
      } else if (llmInfo && llmInfo.provider === "local") {
        const mp = String(llmInfo.model_path || "");
        outBox.appendChild(el("div", { class: "muted" }, `模型：${mp ? mp.split(/[\\\\/]/).pop() : "—"}（本地）`));
      } else if (state.llm && state.llm.model_path) {
        outBox.appendChild(el("div", { class: "muted" }, `模型：${String(state.llm.model_path).split(/[\\\\/]/).pop()}（本地）`));
      }

      // White-box alignment score before/after (retrieval-only, no LLM).
      const al = (r && r.alignment) || null;
      if (al && al.selected) {
        const rows = [];
        rows.push({ name: "原文", pack: al.selected });
        const vs = Array.isArray(al.variants) ? al.variants : [];
        for (const v of vs) {
          const lvl = String(v.level || "").toLowerCase();
          const name = lvl === "light" ? "轻改" : lvl === "medium" ? "中改" : lvl || "改写";
          rows.push({ name, pack: v });
        }

        const wrap = el("div", { class: "list" });
        for (const it of rows) {
          const pack = it.pack || {};
          const pct = Number(pack.pct || 0);
          const badgeCls = pct >= 80 ? "badge good" : pct >= 60 ? "badge" : "badge bad";
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
                el("div", null, el("span", { class: badgeCls }, `${Math.round(pct)}%`), " ", el("span", null, `${it.name} 对齐度`)),
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
                          onclick: () => openModal(`${it.name} · 对齐范文（Top-K）`, exemplarList(exs, { library: state.library })),
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
        outBox.appendChild(el("div", { class: "label" }, "对齐度（检索得分，越高越像范文）"));
        outBox.appendChild(el("div", { class: "muted" }, "说明：该分数来自离线检索（不生成内容），用于量化“改写后是否更像范文”。"));
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
          const evNodes = ev.map((c) =>
            el(
              "div",
              { class: "quote" },
              el("div", { class: "muted mono" }, `${c.id || ""} · ${c.pdf || ""}#p${c.page || 0}`),
              el("div", null, scaffolds.length ? highlightNeedles(c.quote || "", scaffolds) : String(c.quote || ""))
            )
          );
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
                el("span", { class: "muted" }, "本改写用到"),
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
                ...cits.map((c) =>
                  el("div", { class: "quote" }, el("div", { class: "muted mono" }, `${c.id} · ${c.pdf}#p${c.page}`), c.quote)
                )
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
                  toast("已替换到输入框（可继续润色）。");
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

    const exBtn = el("button", { class: "btn", type: "button", onclick: fetchExemplars }, "获取范文对照");
    const genBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) {
            openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
            return toast("先准备范文库（第一次需要导入 PDF）。", "bad", 4500);
          }
          if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
            openPrepWizard({ need: "rag", resume: { route: "polish", autoKey: "aiw.polishAutoRun", autoValue: "generate" } });
            return toast("范文库还没准备好：先完成一次“导入 + 准备”。", "bad", 4500);
          }
          const txt = (selected.value || "").trim();
          if (txt.length < 8) return toast("选中文本太短。", "bad");

          const provider = providerSel.value || localStorage.getItem("aiw.llmProvider") || "local";

          let maxTokens = Number(maxTok.value || 650);
          if (!Number.isFinite(maxTokens) || maxTokens <= 0) maxTokens = 650;
          const cap = provider === "api" ? 8192 : 2048;
          maxTokens = Math.max(64, Math.min(cap, Math.round(maxTokens)));
          if (maxTokens < 256) {
            maxTokens = 256;
            maxTok.value = String(maxTokens);
            localStorage.setItem("aiw.polishMaxTokens", String(maxTokens));
          }
          if (provider === "api") {
            if (maxTokens < 2048) {
              maxTokens = Math.min(cap, 4096);
              maxTok.value = String(maxTokens);
              localStorage.setItem("aiw.polishMaxTokens", String(maxTokens));
              toast("API 输出长度已自动调大（4096）以避免 JSON 截断。");
            }
          } else {
            if (maxTokens < 450) toast("输出长度太小可能导致生成失败（JSON 被截断）。建议 ≥ 650。", "bad", 4500);
          }

          genBtn.disabled = true;
          genBtn.textContent = provider === "api" ? "生成中…（API 请求中）" : "生成中…（本地模型运行中）";

          // Allow cancel: abort the HTTP request; for local model also stop llama-server.
          const abort = new AbortController();
          let canceled = false;
          const onCancel = async () => {
            if (canceled) return;
            canceled = true;
            try {
              abort.abort();
            } catch {}
            if (provider !== "api") {
              try {
                await apiPost("/api/llm/stop", {});
              } catch {}
              await refreshLLMStatus().catch(() => {});
            }
            toast("已取消生成。", "bad", 4500);
          };

          renderOutGenerating(provider, onCancel);
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
                provider,
                temperature: 0.0,
                max_tokens: maxTokens,
                retries: 2,
              },
              { signal: abort.signal }
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
            if (msg.includes("LLM output invalid") && msg.includes("bad json")) {
              msg =
                "生成结果格式不完整（常见原因：输出长度太小或 API 推理占用大量 tokens）。请打开“高级设置”，把输出长度调大（本地建议 ≥ 650；API 建议 ≥ 4096）后重试。";
            } else if (msg.includes("failed to start llama-server")) {
              msg = "启动本地模型失败：请到“模型设置”页点击“一键启动&测试”。";
            } else if (maybeOpenIndexModalForError(msg)) {
              msg = "";
            } else if (msg.includes("missing api key")) {
              msg = "未配置大模型 API：请到“模型设置”页填写/测试，或设置环境变量 SKILL_LLM_API_KEY / OPENAI_API_KEY。";
            } else if (msg.includes("missing base_url")) {
              msg = "未配置 API URL：请到“模型设置”页填写 base_url（通常以 /v1 结尾），或设置 SKILL_LLM_BASE_URL / OPENAI_BASE_URL。";
            } else if (msg.includes("missing model")) {
              msg = "未配置 API 模型名：请到“模型设置”页填写 model，或设置 SKILL_LLM_MODEL / OPENAI_MODEL。";
            } else if (msg.includes("api request failed") && msg.includes("http 401")) {
              msg = "API 鉴权失败（401）：请检查 api_key 是否正确，或到“模型设置”页先点“测试 API”。";
            } else if (msg.includes("api request failed") && msg.includes("http 403")) {
              msg = "API 拒绝访问（403）：可能是 key/权限不足、白名单限制或网关不支持 /v1/chat/completions。请到“模型设置”页先点“测试 API”。";
            } else if (msg.includes("api request failed") && msg.includes("http 429")) {
              msg = "API 触发限流（429）：请稍后重试，或降低频率/更换模型。";
            }
            if (msg) {
              toast(msg, "bad", 6500);
              renderOutError(msg);
            }
          } finally {
            genBtn.disabled = false;
            genBtn.textContent = "生成模仿改写";
          }
        },
      },
      "生成模仿改写"
    );

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
      el("div", { class: "label" }, "选中要润色的文本"),
      selected,
      el(
        "div",
        { class: "row" },
        el("span", { class: "label" }, "范文数量"),
        topk,
        exBtn,
        genBtn,
        advBtn
      ),
      advRow,
      el("div", { class: "muted" }, "提示：先“获取范文对照”再生成，能更清楚看到使用了哪些范文证据。")
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
      placeholder: "max_pages（可选）",
      style: "width:150px",
      inputmode: "numeric",
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
                        onclick: async () => {
                          try {
                            await apiPost("/api/library/open_pdf", { library: state.library, pdf: pdfRel });
                          } catch (e) {
                            toast(String(e.message || e), "bad");
                          }
                        },
                      },
                      "打开"
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

  function pageLLM() {
    renderHeader("模型设置", "可选：用本地模型离线生成（推荐），或切换到你自己的大模型 API。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const providerSel = el(
      "select",
      { class: "select", style: "width:220px" },
      el("option", { value: "local" }, "默认用本地模型（离线）"),
      el("option", { value: "api" }, "默认用大模型 API（可选）")
    );
    providerSel.value = localStorage.getItem("aiw.llmProvider") || "local";
    providerSel.addEventListener("change", () => {
      localStorage.setItem("aiw.llmProvider", providerSel.value || "local");
      refreshLLMStatus().catch(() => {});
      toast("已更新默认模型。");
    });

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "模仿改写默认使用"),
        el("div", { class: "row" }, providerSel, el("span", { class: "muted" }, "也可在“模仿改写 → 高级设置”临时切换。"))
      )
    );

    // Local model (offline)
    const serverPath = el("input", { class: "input", style: "flex:1", placeholder: "llama-server.exe 路径" });
    const modelPath = el("input", { class: "input", style: "flex:1", placeholder: "GGUF 模型路径（例如 qwen2.5-3b…gguf）" });
    const ctx = el("input", { class: "input", style: "width:100px", value: "2048", inputmode: "numeric" });
    const threads = el("input", { class: "input", style: "width:100px", value: "4", inputmode: "numeric" });
    const ngl = el("input", { class: "input", style: "width:110px", value: "0", inputmode: "numeric" });
    const sleep = el("input", { class: "input", style: "width:130px", value: "300", inputmode: "numeric" });

    const localStatusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取本地模型状态…"));

    async function syncLocalFromStatus() {
      await refreshLLMStatus();
      const st = state.llm || {};
      serverPath.value = st.server_path || "";
      modelPath.value = st.model_path || "";
      clear(localStatusBox);
      const rows = [
        ["server_path", st.server_path || ""],
        ["model_path", st.model_path || ""],
        ["server_ok", String(!!st.server_ok)],
        ["model_ok", String(!!st.model_ok)],
        ["running", String(!!st.running)],
        ["base_url", st.base_url || ""],
      ];
      localStatusBox.appendChild(el("div", { class: "label" }, "本地模型状态"));
      localStatusBox.appendChild(
        el(
          "div",
          { class: "list" },
          ...rows.map(([k, v]) =>
            el(
              "div",
              { class: "item" },
              el("div", { class: "item-header" }, el("span", { class: "badge mono" }, k), el("span", { class: "muted mono" }, v))
            )
          )
        )
      );
    }

    const preset8g = el(
      "button",
      {
        class: "btn btn-small",
        type: "button",
        onclick: () => {
          ctx.value = "2048";
          threads.value = "4";
          ngl.value = "0";
          sleep.value = "300";
          toast("已应用 8GB 预设。");
        },
      },
      "8GB 预设"
    );

    const localTestBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          localTestBtn.disabled = true;
          localTestBtn.textContent = "启动&测试中…";
          try {
            const r = await apiPost("/api/llm/test", {
              server_path: (serverPath.value || "").trim(),
              model_path: (modelPath.value || "").trim(),
              ctx_size: Number(ctx.value || 2048),
              threads: Number(threads.value || 4),
              n_gpu_layers: Number(ngl.value || 0),
              sleep_idle_seconds: Number(sleep.value || 300),
            });
            await syncLocalFromStatus();
            toast(r.ok ? "本地模型测试通过。" : "本地模型测试失败。", r.ok ? "good" : "bad");
          } catch (e) {
            await syncLocalFromStatus().catch(() => {});
            toast(String(e.message || e), "bad", 6500);
          } finally {
            localTestBtn.disabled = false;
            localTestBtn.textContent = "一键启动&测试";
          }
        },
      },
      "一键启动&测试"
    );

    const localStopBtn = el(
      "button",
      {
        class: "btn btn-danger",
        type: "button",
        onclick: async () => {
          try {
            await apiPost("/api/llm/stop", {});
            await syncLocalFromStatus();
            toast("已停止本地模型。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "停止本地"
    );

    const openDirBtn = el(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: async () => {
          try {
            const st = state.llm || {};
            const p = st.model_path || st.server_path || "";
            if (!p) return toast("没有路径可打开。", "bad");
            const dir = String(p).replace(/[\\\\/][^\\\\/]+$/, "");
            await apiPost("/api/open", { path: dir });
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "打开模型目录"
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "本地模型（离线）"),
        el("div", { class: "row" }, el("span", { class: "label" }, "server"), serverPath),
        el("div", { class: "row" }, el("span", { class: "label" }, "model"), modelPath),
        el(
          "div",
          { class: "row" },
          el("span", { class: "label" }, "ctx"),
          ctx,
          el("span", { class: "label" }, "threads"),
          threads,
          el("span", { class: "label" }, "n_gpu_layers"),
          ngl,
          el("span", { class: "label" }, "sleep"),
          sleep
        ),
        el("div", { class: "row" }, preset8g, localTestBtn, localStopBtn, openDirBtn),
        el("div", { class: "muted" }, "说明：用于离线生成（默认 Qwen 2.5 3B GGUF）。测试会启动 llama-server 并发出一次 JSON 请求。")
      )
    );
    root.appendChild(localStatusBox);

    // OpenAI-compatible API (optional)
    const apiBaseUrl = el("input", { class: "input", style: "flex:1", placeholder: "base_url（OpenAI 兼容，通常以 /v1 结尾）" });
    const apiModel = el("input", { class: "input", style: "flex:1", placeholder: "model（例如 gpt-4o-mini / deepseek-chat / qwen-…）" });
    const apiKey = el("input", { class: "input", style: "flex:1", type: "password", placeholder: "api_key（不显示；可从环境变量读取）" });
    const saveKey = el("input", { type: "checkbox" });

    const apiStatusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取 API 状态…"));

    function renderApiStatus() {
      const st = state.llmApi || {};
      clear(apiStatusBox);
      apiStatusBox.appendChild(el("div", { class: "label" }, "API 状态（OpenAI-compatible）"));
      const rows = [
        ["base_url", st.base_url || ""],
        ["model", st.model || ""],
        ["api_key_present", String(!!st.api_key_present)],
        ["api_key_masked", st.api_key_masked || ""],
        ["source.base_url", (st.source && st.source.base_url) || ""],
        ["source.model", (st.source && st.source.model) || ""],
        ["source.api_key", (st.source && st.source.api_key) || ""],
      ];
      apiStatusBox.appendChild(
        el(
          "div",
          { class: "list" },
          ...rows.map(([k, v]) =>
            el(
              "div",
              { class: "item" },
              el("div", { class: "item-header" }, el("span", { class: "badge mono" }, k), el("span", { class: "muted mono" }, v))
            )
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
            toast("已保存 API 设置。");
          } catch (e) {
            await syncApiFromStatus().catch(() => {});
            toast(String(e.message || e), "bad", 6500);
          } finally {
            apiSaveBtn.disabled = false;
            apiSaveBtn.textContent = "保存 API 设置";
          }
        },
      },
      "保存 API 设置"
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
            if (r.ok) {
              toast(`API 测试通过：${r.model}`, "good");
            } else {
              let msg = `API 测试失败（HTTP ${r.http}）`;
              if (r.error) msg += `：${String(r.error).slice(0, 160)}`;
              toast(msg, "bad", 6500);
            }
          } catch (e) {
            toast(String(e.message || e), "bad", 6500);
          } finally {
            apiTestBtn.disabled = false;
            apiTestBtn.textContent = "测试 API";
          }
        },
      },
      "测试 API"
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "大模型 API（OpenAI-compatible，可选）"),
        el("div", { class: "row" }, el("span", { class: "label" }, "base_url"), apiBaseUrl),
        el("div", { class: "row" }, el("span", { class: "label" }, "model"), apiModel),
        el("div", { class: "row" }, el("span", { class: "label" }, "api_key"), apiKey),
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
          "提示：默认优先读取环境变量 SKILL_LLM_API_KEY / SKILL_LLM_BASE_URL / SKILL_LLM_MODEL（或 OPENAI_*）。"
        )
      )
    );
    root.appendChild(apiStatusBox);

    syncLocalFromStatus().catch((e) => toast(String(e.message || e), "bad"));
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
        el("div", { class: "label" }, "最快上手（3 步）"),
        el(
          "ol",
          null,
          el("li", null, "范文库页：选择同领域 PDF → 一键准备（离线生成范文证据）。"),
          el("li", null, "找差距页：粘贴正文 → 开始找差距 → 定位最不像范文的句子（带证据）。"),
          el("li", null, "模仿改写页：粘贴句子/段落 → 获取范文对照 → 生成模仿改写（保守版/更像版）。")
        )
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "模型的作用在哪里？"),
        el("div", null, "找差距：不生成内容，只负责“对照证据”。"),
        el("div", null, "模仿改写：模型生成“哪里不像 + 怎么改更像 + 两种改法”，并引用本次用到的范文证据。"),
        el("div", { class: "muted" }, "提示：默认温度固定 0（尽量不发散），更像“受控改写/模板化”而不是自由发挥。")
      )
    );
  }

  async function render() {
    const my = ++renderSeq;
    const r = route();
    try {
      document.body.dataset.route = r;
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
    else if (r === "cite") page.appendChild(pageCite());
    else if (r === "llm") page.appendChild(pageLLM());
    else if (r === "help") page.appendChild(pageHelp());
    else page.appendChild(pageHome());
  }

  function bindEvents() {
    $$(".nav-item").forEach((b) => b.addEventListener("click", () => setRoute(b.dataset.route)));

    $("#librarySelect").addEventListener("change", async (e) => {
      state.library = e.target.value || "";
      localStorage.setItem("aiw.library", state.library);
      toast(state.library ? `已切换范文库：${state.library}` : "未选择范文库。", state.library ? "good" : "bad");
      render().catch(() => {});
    });

    $("#refreshBtn").addEventListener("click", async () => {
      try {
        await refreshLibraries();
        await refreshLibraryStatus();
        await refreshLLMStatus();
        toast("已刷新。");
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

  function init() {
    const theme = localStorage.getItem("aiw.theme") || "light";
    setTheme(theme);
    const accent = localStorage.getItem("aiw.accent") || "teal";
    setAccent(accent);
    bindClientLifecycle();
    bindEvents();
    render();
  }

  window.addEventListener("hashchange", render);
  init();
})();
