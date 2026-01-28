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

  async function api(method, url, body) {
    const init = { method, headers: {} };
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

  const apiGet = (url) => api("GET", url);
  const apiPost = (url, body) => api("POST", url, body);

  function maybeOpenIndexModalForError(msg) {
    const m = String(msg || "").toLowerCase();
    if (m.includes("rag index missing") || m.includes("build library first")) {
      openIndexModal("rag", state.libraryStatus || {});
      return true;
    }
    if (m.includes("citation bank missing") || m.includes("cite index missing") || m.includes("build it first")) {
      openIndexModal("cite", state.libraryStatus || {});
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
    return h || "library";
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
        out.push({ ...it, name });
      } else {
        const name = String(it).trim();
        if (!name) continue;
        out.push({ name });
      }
    }
    return out;
  }

  function libraryNames() {
    return (state.libraries || []).map((x) => x && x.name).filter(Boolean);
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function updateGlobalLibraryUI() {
    const sel = $("#librarySelect");
    clear(sel);
    const libs = state.libraries || [];
    sel.appendChild(el("option", { value: "" }, "— 选择文献库 —"));
    for (const lib of libs) {
      const name = (lib && lib.name) || "";
      if (!name) continue;
      sel.appendChild(el("option", { value: name }, name));
    }
    sel.value = state.library || "";
    localStorage.setItem("aiw.library", state.library || "");
  }

  async function refreshLibraries() {
    const data = await apiGet("/api/libraries");
    state.libraries = normalizeLibraries((data && data.libraries) || []);
    const names = libraryNames();
    if (state.library && !names.includes(state.library)) state.library = "";
    if (!state.library && names.length) state.library = names[0];
    updateGlobalLibraryUI();
  }

  function openIndexModal(kind, status) {
    const st = status || {};
    const k = String(kind || "").toLowerCase();
    const ok = k === "semantic" ? !!st.semantic_index : k === "rag" ? !!st.rag_index : k === "cite" ? !!st.cite_index : false;

    let title = "索引状态";
    let desc = "";
    let need = "";
    let nextRoute = "";
    let nextBtn = "";

    if (k === "semantic") {
      title = "Semantic（语义索引）";
      desc = "用于语义向量化（句子 embeddings）。主要由“建库”阶段生成。";
      need = "不是所有功能都依赖它，但建议建库时一并生成。";
      nextRoute = "library";
      nextBtn = "去文献库";
    } else if (k === "rag") {
      title = "RAG（范文检索索引）";
      desc = "用于“对齐扫描/对齐润色”的范文段落检索（FAISS）。";
      need = "对齐扫描/对齐润色需要它。";
      nextRoute = "library";
      nextBtn = "去建库（RAG）";
    } else if (k === "cite") {
      title = "Cite（引用句式库）";
      desc = "用于“引用借鉴”：抽取 author-year 引用句子 + References，并做可检索的句式库。";
      need = "引用借鉴需要它；与 RAG 独立，可单独构建。";
      nextRoute = "cite";
      nextBtn = "去引用借鉴";
    }

    const badge = el("span", { class: "badge " + (ok ? "good" : "bad") }, ok ? "已就绪" : "未就绪");
    const body = el(
      "div",
      { class: "grid", style: "gap:10px" },
      el("div", { class: "row" }, badge, el("span", { class: "muted" }, state.library ? `当前库：${state.library}` : "未选择文献库")),
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
      semantic_embed: "语义向量化（Semantic）",
      rag_extract: "切分范文段落（RAG）",
      rag_embed: "向量化范文段落（RAG）",
      rag_done: "RAG 完成",
      cite_extract: "抽取引用句子/References（Cite）",
      cite_embed: "向量化引用句子（Cite）",
      cite_index: "构建引用检索（Cite）",
      cite_done: "Cite 完成",
    };
    return map[s] || String(stage || "—");
  }

  function formatIndexChips(status) {
    const box = $("#indexChips");
    clear(box);
    if (!status) return;
    box.appendChild(
      el(
        "button",
        {
          class: "chip " + (status.semantic_index ? "ok" : "warn"),
          type: "button",
          title: "Semantic（语义索引）状态与说明",
          onclick: () => openIndexModal("semantic", status),
        },
        "Semantic"
      )
    );
    box.appendChild(
      el(
        "button",
        {
          class: "chip " + (status.rag_index ? "ok" : "bad"),
          type: "button",
          title: "RAG（范文检索索引）状态与说明",
          onclick: () => openIndexModal("rag", status),
        },
        "RAG"
      )
    );
    box.appendChild(
      el(
        "button",
        {
          class: "chip " + (status.cite_index ? "ok" : "warn"),
          type: "button",
          title: "Cite（引用句式库）状态与说明",
          onclick: () => openIndexModal("cite", status),
        },
        "Cite"
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
    $("#modalTitle").textContent = title;
    const body = $("#modalBody");
    clear(body);
    body.appendChild(bodyNode);
    $("#modalBackdrop").classList.remove("hidden");
  }

  function closeModal() {
    $("#modalBackdrop").classList.add("hidden");
    $("#modalTitle").textContent = "—";
    clear($("#modalBody"));
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

      let text = "LLM: API ";
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
    let text = "LLM: ";
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

  function pageLibrary() {
    renderHeader("文献库", "导入同领域顶级 PDF → 建索引（FAISS/LlamaIndex）→ 用于白箱对齐写作。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const createName = el("input", { class: "input", placeholder: "新建库名（例如：finance_2026）", style: "flex:1; min-width:320px" });
    const createBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          const name = (createName.value || "").trim();
          if (!name) return toast("请输入库名。", "bad");
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
            await syncPdfRoot();
            createName.value = "";
            toast("已创建文献库。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "创建文献库"
    );

    // Browser-native folder picker (no PowerShell / no tkinter).
    const pdfInput = el("input", { type: "file", multiple: true, accept: ".pdf,application/pdf", style: "display:none" });
    pdfInput.setAttribute("webkitdirectory", "");
    pdfInput.setAttribute("directory", "");

    let selectedFiles = [];
    const selectedInfo = el("div", { class: "muted mono" }, "未选择文件夹。");
    const importProgressBar = el("div", { class: "progress" }, el("div"));
    const importProgressText = el("div", { class: "muted mono" }, "—");

    function fmtCount(n) {
      const x = Number(n || 0);
      if (!Number.isFinite(x)) return "0";
      return String(Math.max(0, Math.round(x)));
    }

    function updateSelectedInfo() {
      const pdfs = selectedFiles.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) {
        selectedInfo.textContent = "未选择文件夹。";
        return;
      }
      const rel0 = String(pdfs[0].webkitRelativePath || pdfs[0].name || "");
      const folder = rel0.includes("/") ? rel0.split("/")[0] : "PDF_Folder";
      selectedInfo.textContent = `已选择：${fmtCount(pdfs.length)} 个 PDF · 文件夹：${folder}`;
    }

    pdfInput.addEventListener("change", () => {
      selectedFiles = Array.from(pdfInput.files || []);
      updateSelectedInfo();
      if (selectedFiles.length) toast("已选择文件夹（待导入）。");
    });

    const pickBtn = el(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: () => pdfInput.click(),
      },
      "选择 PDF 文件夹…"
    );

    const clearImportBtn = el(
      "button",
      {
        class: "btn btn-danger btn-small",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
          const ok = window.confirm("确定清空此库已导入的 PDF 吗？\n\n这不会删除你的原始文件夹，只会清空 TopHumanWriting_data 里的拷贝。");
          if (!ok) return;
          try {
            await apiPost("/api/library/import/clear", { library: state.library });
            await syncPdfRoot();
            toast("已清空导入的 PDF。");
          } catch (e) {
            toast(String(e.message || e), "bad", 6500);
          }
        },
      },
      "清空已导入"
    );

    async function syncPdfRoot() {
      if (!state.library) {
        state.pdfFolder = "";
        localStorage.setItem("aiw.pdfFolder", "");
        return null;
      }
      try {
        const r = await apiGet(`/api/library/pdf_root?library=${encodeURIComponent(state.library)}`);
        state.pdfFolder = (r && r.pdf_root) || "";
        localStorage.setItem("aiw.pdfFolder", state.pdfFolder || "");
        const n = (r && r.pdf_count) != null ? Number(r.pdf_count) : null;
        if (state.pdfFolder) {
          importProgressText.textContent = `已导入：${n == null ? "—" : fmtCount(n)} 个 PDF · 存储：${state.pdfFolder}`;
        }
        return r || null;
      } catch {
        // ignore
        return null;
      }
    }

    const importBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
          const pdfs = selectedFiles.filter((f) => String(f && f.name ? f.name : "").toLowerCase().endsWith(".pdf"));
          if (!pdfs.length) return toast("请先选择包含 PDF 的文件夹。", "bad");

          importBtn.disabled = true;
          pickBtn.disabled = true;
          clearImportBtn.disabled = true;
          let canceled = false;
          const cancelBtn = el(
            "button",
            {
              class: "btn btn-danger btn-small",
              type: "button",
              onclick: () => {
                canceled = true;
                toast("已请求取消导入（会在当前文件完成后停止）。", "bad");
              },
            },
            "取消导入"
          );
          importBtn.parentElement && importBtn.parentElement.appendChild(cancelBtn);

          try {
            importProgressBar.firstChild.style.width = "0%";
            importProgressText.textContent = `导入中… 0/${fmtCount(pdfs.length)}`;

            for (let i = 0; i < pdfs.length; i++) {
              if (canceled) break;
              const f = pdfs[i];
              const rel = String(f.webkitRelativePath || f.name || "");
              importProgressText.textContent = `导入中… ${fmtCount(i + 1)}/${fmtCount(pdfs.length)} · ${rel}`;
              importProgressBar.firstChild.style.width = `${Math.round(((i + 1) / pdfs.length) * 100)}%`;

              const fd = new FormData();
              fd.append("library", state.library);
              fd.append("overwrite", "0");
              fd.append("file", f, rel || f.name || `file_${i + 1}.pdf`);
              await apiFormPost("/api/library/upload_pdf", fd);
            }

            await syncPdfRoot();
            if (canceled) toast("导入已取消（部分文件可能已导入）。", "bad", 4500);
            else toast("导入完成。");
          } catch (e) {
            await syncPdfRoot().catch(() => {});
            toast("导入失败：" + String(e.message || e), "bad", 6500);
          } finally {
            importBtn.disabled = false;
            pickBtn.disabled = false;
            clearImportBtn.disabled = false;
            try {
              cancelBtn.remove();
            } catch {}
          }
        },
      },
      "导入到本地库"
    );

    const buildBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
          const r0 = await syncPdfRoot().catch(() => null);
          const n0 = r0 && r0.pdf_count != null ? Number(r0.pdf_count) : null;
          if (n0 !== null && n0 <= 0) return toast("此库还没有导入 PDF。请先“选择 PDF 文件夹 → 导入到本地库”。", "bad");
          try {
            const r = await apiPost("/api/library/build", { library: state.library, folder: state.pdfFolder || "" });
            state.buildTaskId = r.task_id;
            localStorage.setItem("aiw.buildTaskId", state.buildTaskId || "");
            startBuildPolling();
            toast("已开始建库（后台进行）。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "开始建库"
    );

    const progressBar = el("div", { class: "progress" }, el("div"));
    const progressText = el("div", { class: "muted mono" }, "—");
    const cancelBtn = el(
      "button",
      {
        class: "btn btn-danger btn-small",
        type: "button",
        onclick: async () => {
          if (!state.buildTaskId) return;
          try {
            await apiPost(`/api/tasks/${encodeURIComponent(state.buildTaskId)}/cancel`, {});
            toast("已请求取消。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "取消"
    );

    function updateProgressUI(t) {
      if (!t) return;
      const done = Number(t.done || 0);
      const total = Number(t.total || 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
      progressBar.firstChild.style.width = `${pct}%`;
      const stage = humanTaskStage(t.stage);
      const status = humanTaskStatus(t.status);
      const detail = String(t.detail || "");
      progressText.textContent = `${status} · ${stage} · ${done}/${total} ${detail ? "· " + detail : ""}`;
    }

    async function pollOnce() {
      if (!state.buildTaskId) return;
      if (!document.body.contains(progressText)) return stopBuildPolling();
      try {
        const t = await apiGet(`/api/tasks/${encodeURIComponent(state.buildTaskId)}`);
        updateProgressUI(t);
        if (t.status !== "running") {
          stopBuildPolling();
          state.buildTaskId = "";
          localStorage.setItem("aiw.buildTaskId", "");
          await refreshLibraryStatus();
          if (t.status === "done") toast("建库完成。");
          else if (t.status === "canceled") toast("建库已取消。", "bad");
          else toast("建库失败：" + (t.error || ""), "bad", 6500);
        }
      } catch (e) {
        stopBuildPolling();
        toast(String(e.message || e), "bad");
      }
    }

    function startBuildPolling() {
      stopBuildPolling();
      pollOnce();
      state.buildPollTimer = window.setInterval(pollOnce, 1000);
    }

    function stopBuildPolling() {
      if (state.buildPollTimer) window.clearInterval(state.buildPollTimer);
      state.buildPollTimer = null;
    }

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "1) 创建文献库"),
        el("div", { class: "row" }, createName, createBtn),
        el("div", { class: "muted" }, "创建后会自动切换到该库。库会存到本机数据目录（TopHumanWriting_data/…；兼容旧 AIWordDetector_data/）。")
      )
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "2) 导入范文 PDF（浏览器选文件夹）"),
        el("div", { class: "row" }, pickBtn, importBtn, clearImportBtn),
        selectedInfo,
        importProgressBar,
        importProgressText,
        el("div", { class: "muted" }, "说明：会把你选择的 PDF 拷贝进 TopHumanWriting_data/pdfs/（用于离线检索、打开来源）。")
      )
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "3) 建索引（用于扫描/润色）"),
        el("div", { class: "row" }, buildBtn, cancelBtn),
        progressBar,
        progressText,
        el("div", { class: "muted" }, "提示：首次建库会占用 CPU；完成后“对齐扫描/润色/引用借鉴”才会有范文对照。")
      )
    );

    root.appendChild(pdfInput);

    // Initialize status when opening this page.
    syncPdfRoot().catch(() => {});
    if (state.buildTaskId) startBuildPolling();

    return root;
  }

  function pageScan() {
    renderHeader("对齐扫描", "先找出“最不像范文”的句子（向量检索，不用 LLM）。");
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
          if (!state.library) return toast("请先选择文献库。", "bad");
          if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
            toast("缺少 RAG（范文检索索引）：请先到“文献库”导入 PDF 并建索引。", "bad", 4500);
            return openIndexModal("rag", state.libraryStatus || {});
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
            runBtn.textContent = "开始扫描";
          }
        },
      },
      "开始扫描"
    );

    const resultsBox = el("div", { class: "card" });
    function renderEmptyResultsHint() {
      clear(resultsBox);
      if (!state.library) {
        resultsBox.appendChild(el("div", { class: "muted" }, "请先在顶部选择文献库。"));
        return;
      }
      const ragOk = !!(state.libraryStatus && state.libraryStatus.rag_index);
      if (!ragOk) {
        resultsBox.appendChild(el("div", { class: "label" }, "还不能扫描：缺少 RAG（范文检索索引）"));
        resultsBox.appendChild(el("div", { class: "muted" }, "先到“文献库”完成：导入 PDF → 建索引。完成后再回来扫描。"));
        resultsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              {
                class: "btn btn-primary",
                type: "button",
                onclick: () => openIndexModal("rag", state.libraryStatus || {}),
              },
              "查看如何建索引"
            ),
            el(
              "button",
              {
                class: "btn",
                type: "button",
                onclick: () => setRoute("library"),
              },
              "去文献库"
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
            { class: "row" },
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
        el("span", { class: "muted" }, "扫描仅做检索：不调用 LLM。")
      )
    );
    root.appendChild(
      el(
        "div",
        { class: "grid two", style: "gap:14px; align-items:start; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr)" },
        inputCard,
        resultsBox
      )
    );
    return root;
  }

  function pagePolish() {
    renderHeader("对齐润色", "白箱：范文对照 + 证据引用 + 受控改写（默认本地 Qwen，可切换 API）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const selected = el("textarea", { class: "textarea", placeholder: "选中要润色的句子/段落…" });
    selected.value = state.polishDraft || "";
    selected.addEventListener("input", () => {
      state.polishDraft = selected.value || "";
      localStorage.setItem("aiw.polishDraft", state.polishDraft);
    });

    const topk = el("input", { class: "input", value: "8", style: "width:110px", inputmode: "numeric", title: "检索多少条范文片段作为证据（C1..Ck）" });
    const maxTok = el("input", { class: "input", value: "650", style: "width:120px", inputmode: "numeric", title: "输出长度上限（越大越慢）" });

    const providerSel = el(
      "select",
      { class: "select", style: "width:220px" },
      el("option", { value: "local" }, "本地 Qwen（llama.cpp）"),
      el("option", { value: "api" }, "大模型 API（OpenAI兼容）")
    );
    providerSel.value = localStorage.getItem("aiw.llmProvider") || "local";
    providerSel.addEventListener("change", () => {
      localStorage.setItem("aiw.llmProvider", providerSel.value || "local");
      refreshLLMStatus().catch(() => {});
    });

    let advOpen = localStorage.getItem("aiw.polishAdv") === "1";
    const advRow = el(
      "div",
      { class: "row", style: `display:${advOpen ? "flex" : "none"}` },
      el("span", { class: "label" }, "LLM"),
      providerSel,
      el("span", { class: "label" }, "输出长度"),
      maxTok,
      el("span", { class: "muted" }, "温度固定 0（尽量不发散）。API 需先在“LLM 设置”配置。")
    );

    const exemplarsBox = el("div", { class: "card" });
    const outBox = el("div", { class: "card" });

    function renderExemplarsEmpty() {
      clear(exemplarsBox);
      if (!state.library) {
        exemplarsBox.appendChild(el("div", { class: "muted" }, "请先在顶部选择文献库。"));
        return;
      }
      const ragOk = !!(state.libraryStatus && state.libraryStatus.rag_index);
      if (!ragOk) {
        exemplarsBox.appendChild(el("div", { class: "label" }, "缺少 RAG（范文检索索引）"));
        exemplarsBox.appendChild(el("div", { class: "muted" }, "先到“文献库”完成：导入 PDF → 建索引。完成后再回来润色。"));
        exemplarsBox.appendChild(
          el(
            "div",
            { class: "row" },
            el(
              "button",
              { class: "btn btn-primary", type: "button", onclick: () => openIndexModal("rag", state.libraryStatus || {}) },
              "查看如何建索引"
            ),
            el("button", { class: "btn", type: "button", onclick: () => setRoute("library") }, "去文献库")
          )
        );
        return;
      }
      exemplarsBox.appendChild(el("div", { class: "muted" }, "先获取范文对照（C1..Ck），再生成白箱润色。"));
    }

    function renderOutEmpty() {
      clear(outBox);
      outBox.appendChild(el("div", { class: "label" }, "白箱输出将在这里展示"));
      outBox.appendChild(el("div", { class: "muted" }, "包含：对齐度对比（原文/轻改/中改） + 诊断（带证据） + 改写（带引用）。"));
      outBox.appendChild(el("div", { class: "muted" }, "建议流程：先点“获取范文对照”确认证据 → 再点“生成对齐润色”。"));
    }

    renderExemplarsEmpty();
    renderOutEmpty();

    async function fetchExemplars() {
      if (!state.library) return toast("请先选择文献库。", "bad");
      if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
        toast("缺少 RAG（范文检索索引）：请先到“文献库”导入 PDF 并建索引。", "bad", 4500);
        openIndexModal("rag", state.libraryStatus || {});
        throw new Error("rag index missing (build library first)");
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
        clear(exemplarsBox);
        exemplarsBox.appendChild(el("div", { class: "label" }, "范文对照（将被引用为 C1..Ck）"));
        exemplarsBox.appendChild(exemplarList(r.exemplars || [], { library: state.library }));
        toast("已获取范文对照。");
        return r;
      } catch (e) {
        const msg = String(e.message || e);
        if (!maybeOpenIndexModalForError(msg)) toast(msg, "bad", 6500);
        throw e;
      }
    }

    function renderPolishResult(r) {
      clear(outBox);
      const result = r && r.result;
      if (!result) {
        outBox.appendChild(el("div", { class: "muted" }, "未生成结果。"));
        return;
      }

      const diag = result.diagnosis || [];
      const vars = result.variants || [];

      outBox.appendChild(
        el("div", { class: "label" }, `输出语言：${result.language || "mixed"} · 诊断 ${diag.length} 条 · 改写 ${vars.length} 条`)
      );

      const llmInfo = (r && r.llm) || null;
      if (llmInfo && llmInfo.provider === "api") {
        outBox.appendChild(el("div", { class: "muted" }, `LLM：API · ${llmInfo.model || "—"} · ${llmInfo.base_url || "—"}`));
      } else if (llmInfo && llmInfo.provider === "local") {
        const mp = String(llmInfo.model_path || "");
        outBox.appendChild(el("div", { class: "muted" }, `LLM：${mp ? mp.split(/[\\\\/]/).pop() : "—"}（llama.cpp）`));
      } else if (state.llm && state.llm.model_path) {
        outBox.appendChild(el("div", { class: "muted" }, `LLM：${String(state.llm.model_path).split(/[\\\\/]/).pop()}（llama.cpp）`));
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
        outBox.appendChild(el("div", { class: "muted" }, "说明：该分数来自向量检索（不调用 LLM），用于量化“改写后是否更像范文”。"));
        outBox.appendChild(wrap);
      }

      if (diag.length) {
        outBox.appendChild(el("div", { class: "hr" }));
        outBox.appendChild(el("div", { class: "label" }, "白箱诊断（每条都有范文证据）"));
        const list = el("div", { class: "list" });
        for (const d of diag) {
          const ev = d.evidence || [];
          const evNodes = ev.map((c) =>
            el(
              "div",
              { class: "quote" },
              el("div", { class: "muted mono" }, `${c.id || ""} · ${c.pdf || ""}#p${c.page || 0}`),
              el("div", null, c.quote || "")
            )
          );
          list.appendChild(
            el(
              "div",
              { class: "item" },
              el("div", { class: "item-header" }, el("div", null, el("span", { class: "badge mono" }, d.title || "Diagnosis"))),
              el("div", null, el("div", { class: "muted" }, d.problem || ""), el("div", null, d.suggestion || "")),
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
        return el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "item-header" },
            el("div", { class: "label" }, title),
            el(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: () => copyText(rewrite),
              },
              "复制"
            )
          ),
          el("div", { class: "quote" }, rewrite),
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
          if (!state.library) return toast("请先选择文献库。", "bad");
          if (!(state.libraryStatus && state.libraryStatus.rag_index)) {
            toast("缺少 RAG（范文检索索引）：请先到“文献库”导入 PDF 并建索引。", "bad", 4500);
            return openIndexModal("rag", state.libraryStatus || {});
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
          }
          if (provider === "api") {
            if (maxTokens < 1200) toast("API 模型可能需要更大输出长度（建议 ≥ 4096）以避免 JSON 被截断。", "bad", 4500);
          } else {
            if (maxTokens < 450) toast("输出长度太小可能导致生成失败（JSON 被截断）。建议 ≥ 650。", "bad", 4500);
          }

          genBtn.disabled = true;
          genBtn.textContent = provider === "api" ? "生成中…（API 请求中）" : "生成中…（首次会加载模型）";
          try {
            await refreshLLMStatus();
            const r = await apiPost("/api/align/polish", {
              library: state.library,
              selected_text: txt,
              top_k: Number(topk.value || 8),
              generate: true,
              provider,
              temperature: 0.0,
              max_tokens: maxTokens,
              retries: 2,
            });
            await refreshLLMStatus();
            renderPolishResult(r);
            toast("生成完成。");
          } catch (e) {
            await refreshLLMStatus().catch(() => {});
            let msg = String(e && (e.message || e) ? e.message || e : e);
            if (msg.includes("LLM output invalid") && msg.includes("bad json")) {
              msg =
                "生成结果格式不完整（常见原因：输出长度太小或 API 推理占用大量 tokens）。请打开“高级设置”，把输出长度调大（本地建议 ≥ 650；API 建议 ≥ 4096）后重试。";
            } else if (msg.includes("failed to start llama-server")) {
              msg = "启动本地模型失败：请到“LLM 设置”页点击“一键启动&测试”。";
            } else if (maybeOpenIndexModalForError(msg)) {
              msg = "";
            } else if (msg.includes("missing api key")) {
              msg = "未配置大模型 API：请到“LLM 设置”页填写/测试，或设置环境变量 SKILL_LLM_API_KEY / OPENAI_API_KEY。";
            } else if (msg.includes("missing base_url")) {
              msg = "未配置 API URL：请到“LLM 设置”页填写 base_url（通常以 /v1 结尾），或设置 SKILL_LLM_BASE_URL / OPENAI_BASE_URL。";
            } else if (msg.includes("missing model")) {
              msg = "未配置 API 模型名：请到“LLM 设置”页填写 model，或设置 SKILL_LLM_MODEL / OPENAI_MODEL。";
            } else if (msg.includes("api request failed") && msg.includes("http 401")) {
              msg = "API 鉴权失败（401）：请检查 api_key 是否正确，或到“LLM 设置”页先点“测试 API”。";
            } else if (msg.includes("api request failed") && msg.includes("http 403")) {
              msg = "API 拒绝访问（403）：可能是 key/权限不足、白名单限制或网关不支持 /v1/chat/completions。请到“LLM 设置”页先点“测试 API”。";
            } else if (msg.includes("api request failed") && msg.includes("http 429")) {
              msg = "API 触发限流（429）：请稍后重试，或降低频率/更换模型。";
            }
            if (msg) toast(msg, "bad", 6500);
          } finally {
            genBtn.disabled = false;
            genBtn.textContent = "生成对齐润色";
          }
        },
      },
      "生成对齐润色"
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
      el("div", { class: "muted" }, "提示：先“获取范文对照”再生成，能更清楚看到 C1..Ck 是哪些证据。")
    );

    const topGrid = el(
      "div",
      { class: "grid two", style: "gap:14px; align-items:start; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr)" },
      inputCard,
      exemplarsBox
    );
    root.appendChild(topGrid);
    root.appendChild(outBox);
    return root;
  }

  function pageCite() {
    renderHeader("引用借鉴", "抽取“引用句子 + References”，构建可检索的范文句式库（白箱）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const statusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取引用库状态…"));

    async function syncStatus() {
      if (!state.library) {
        clear(statusBox);
        statusBox.appendChild(el("div", { class: "muted" }, "请先选择文献库。"));
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
          statusBox.appendChild(el("div", { class: "muted" }, "提示：先建库（RAG）后，再在本页抽取引用句子会更顺。"));
        }
      } catch (e) {
        clear(statusBox);
        statusBox.appendChild(el("div", { class: "muted" }, "无法读取引用库状态（可先建库）。"));
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
          if (!state.library) return toast("请先选择文献库。", "bad");
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
            buildBtn.textContent = "抽取引用句子（建引用库）";
          }
        },
      },
      "抽取引用句子（建引用库）"
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
    const topk = el("input", { class: "input", value: "10", style: "width:100px", inputmode: "numeric" });
    const searchBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
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
            searchBtn.textContent = "检索引用句式";
          }
        },
      },
      "检索引用句式"
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
        openModal(`References · ${pdfRel}`, body);
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
        el("div", { class: "label" }, "构建引用库（一次即可，离线保存）"),
        el("div", { class: "row" }, maxPages, buildBtn, citeCancelBtn),
        citeProgress,
        citeProgressText,
        el("div", { class: "muted" }, "说明：仅抽取 author-year 引用句子（如 Smith (2020) / (Smith, 2020; …)）与 References。")
      )
    );

    root.appendChild(statusBox);

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "检索范文引用句式"),
        el("div", { class: "row" }, query, el("span", { class: "label" }, "top_k"), topk, searchBtn),
        el("div", { class: "muted" }, "用途：找“顶级论文怎么写这句话/怎么引文”，并复制句式（白箱可追溯）。")
      )
    );

    root.appendChild(resultsBox);

    syncStatus().catch(() => {});
    if (state.citeTaskId) startCitePolling();
    return root;
  }

  function pageLLM() {
    renderHeader("LLM 设置", "支持：本地 llama.cpp（离线） / OpenAI-compatible API（可选）。");
    const root = el("div", { class: "grid", style: "gap:18px" });

    const providerSel = el(
      "select",
      { class: "select", style: "width:220px" },
      el("option", { value: "local" }, "默认用本地 Qwen（离线）"),
      el("option", { value: "api" }, "默认用大模型 API")
    );
    providerSel.value = localStorage.getItem("aiw.llmProvider") || "local";
    providerSel.addEventListener("change", () => {
      localStorage.setItem("aiw.llmProvider", providerSel.value || "local");
      refreshLLMStatus().catch(() => {});
      toast("已更新润色默认 LLM。");
    });

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "润色默认使用"),
        el("div", { class: "row" }, providerSel, el("span", { class: "muted" }, "也可在“对齐润色 → 高级设置”临时切换。"))
      )
    );

    // Local llama.cpp (offline)
    const serverPath = el("input", { class: "input", style: "flex:1", placeholder: "llama-server.exe 路径" });
    const modelPath = el("input", { class: "input", style: "flex:1", placeholder: "GGUF 模型路径（例如 qwen2.5-3b…gguf）" });
    const ctx = el("input", { class: "input", style: "width:100px", value: "2048", inputmode: "numeric" });
    const threads = el("input", { class: "input", style: "width:100px", value: "4", inputmode: "numeric" });
    const ngl = el("input", { class: "input", style: "width:110px", value: "0", inputmode: "numeric" });
    const sleep = el("input", { class: "input", style: "width:130px", value: "300", inputmode: "numeric" });

    const localStatusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取本地 LLM 状态…"));

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
      localStatusBox.appendChild(el("div", { class: "label" }, "本地 llama.cpp 状态"));
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
            toast(r.ok ? "本地 LLM 测试通过。" : "本地 LLM 测试失败。", r.ok ? "good" : "bad");
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
            toast("已停止本地 LLM。");
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
        el("div", { class: "label" }, "本地 llama.cpp（离线）"),
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
    renderHeader("使用帮助", "你要的是“像范文写法”的白箱过程：有范文背书，改法可追溯。");
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
          el("li", null, "文献库页：创建库 → 选择 PDF 文件夹 → 导入到本地库 → 开始建库（等待完成）。"),
          el("li", null, "对齐扫描页：粘贴正文 → 扫描 → 找到对齐度低的句子。"),
          el("li", null, "对齐润色页：点击“润色这个句子”或粘贴段落 → 获取范文对照 → 生成对齐润色。")
        )
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "Qwen 的作用在哪里？"),
        el("div", null, "扫描：只用向量检索（FAISS），不调用 LLM。"),
        el("div", null, "润色：默认调用本地 Qwen（llama.cpp）输出 JSON（诊断 + 轻改/中改 + 引用证据）。也可切换到大模型 API（OpenAI 兼容）。"),
        el("div", { class: "muted" }, "如何确认：对齐润色结果顶部会显示“LLM：…”；并展示“对齐度（检索得分）”对比原文/轻改/中改。")
      )
    );
  }

  async function render() {
    const my = ++renderSeq;
    const r = route();
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
    if (r === "scan") page.appendChild(pageScan());
    else if (r === "polish") page.appendChild(pagePolish());
    else if (r === "cite") page.appendChild(pageCite());
    else if (r === "llm") page.appendChild(pageLLM());
    else if (r === "help") page.appendChild(pageHelp());
    else page.appendChild(pageLibrary());
  }

  function bindEvents() {
    $$(".nav-item").forEach((b) => b.addEventListener("click", () => setRoute(b.dataset.route)));

    $("#librarySelect").addEventListener("change", async (e) => {
      state.library = e.target.value || "";
      localStorage.setItem("aiw.library", state.library);
      toast(state.library ? `已切换文献库：${state.library}` : "未选择文献库。", state.library ? "good" : "bad");
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
