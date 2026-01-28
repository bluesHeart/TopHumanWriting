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
    buildTaskId: null,
    buildPollTimer: null,
    citeTaskId: null,
    citePollTimer: null,
    polishDraft: localStorage.getItem("aiw.polishDraft") || "",
    clientId: sessionStorage.getItem("aiw.clientId") || "",
    clientHeartbeatTimer: null,
  };

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
      formatIndexChips(null);
      return;
    }
    const st = await apiGet(`/api/library/status?library=${encodeURIComponent(state.library)}`);
    formatIndexChips(st);
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
    const badge = $("#llmBadge");
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
    renderHeader("文献库", "先建库：PDF 文件夹 → 向量索引（FAISS）→ 范文可检索。");
    const root = el("div", { class: "grid", style: "gap:14px" });

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
            await apiPost("/api/libraries", { name });
            await refreshLibraries();
            await refreshLibraryStatus();
            toast("已创建文献库。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "创建文献库"
    );

    const folderInput = el("input", { class: "input", placeholder: "PDF 文件夹路径（支持递归）", style: "flex:1; min-width:360px" });
    const pickBtn = el(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: async () => {
          try {
            const r = await apiPost("/api/dialog/pick_folder", {});
            folderInput.value = (r && r.folder) || "";
            if (folderInput.value) toast("已选择文件夹。");
            else toast("未选择文件夹（也可手动粘贴路径）。", "bad");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "选择 PDF 文件夹…"
    );

    const buildBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
          const folder = (folderInput.value || "").trim();
          if (!folder) return toast("请先选择 PDF 文件夹。", "bad");
          try {
            const r = await apiPost("/api/library/build", { library: state.library, folder });
            state.buildTaskId = r.task_id;
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
        el("div", { class: "label" }, "新建文献库"),
        el("div", { class: "row" }, createName, createBtn),
        el("div", { class: "muted" }, "库会存到本机数据目录（TopHumanWriting_data/…；兼容旧 AIWordDetector_data/）。")
      )
    );

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "PDF → 索引（FAISS）"),
        el("div", { class: "row" }, folderInput, pickBtn),
        el("div", { class: "row" }, buildBtn, cancelBtn),
        progressBar,
        progressText,
        el("div", { class: "muted" }, "提示：第一次建库会占用 CPU；完成后“对齐扫描/润色”才会有范文对照。")
      )
    );

    return root;
  }

  function pageScan() {
    renderHeader("对齐扫描", "先找出“最不像范文”的句子（向量检索，不用 LLM）。");
    const root = el("div", { class: "grid", style: "gap:14px" });

    const text = el("textarea", { class: "textarea", placeholder: "粘贴你的正文（中英混合可）…" });
    const topk = el("input", { class: "input", value: "6", style: "width:100px", inputmode: "numeric" });
    const maxItems = el("input", { class: "input", value: "220", style: "width:110px", inputmode: "numeric" });

    const runBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          if (!state.library) return toast("请先选择文献库。", "bad");
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
            toast(String(e.message || e), "bad", 6500);
          } finally {
            runBtn.disabled = false;
            runBtn.textContent = "开始扫描";
          }
        },
      },
      "开始扫描"
    );

    const resultsBox = el("div", { class: "card" }, el("div", { class: "muted" }, "结果将在这里显示。"));

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

    root.appendChild(
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "输入文本"),
        text,
        el(
          "div",
          { class: "row" },
          el("span", { class: "label" }, "top_k"),
          topk,
          el("span", { class: "label" }, "max_items"),
          maxItems,
          runBtn,
          el("span", { class: "muted" }, "扫描仅做检索：不调用 LLM。")
        )
      )
    );
    root.appendChild(resultsBox);
    return root;
  }

  function pagePolish() {
    renderHeader("对齐润色", "白箱：范文对照 + 证据引用 + 受控改写（Qwen 3B）。");
    const root = el("div", { class: "grid", style: "gap:14px" });

    const selected = el("textarea", { class: "textarea", placeholder: "选中要润色的句子/段落…" });
    selected.value = state.polishDraft || "";
    selected.addEventListener("input", () => {
      state.polishDraft = selected.value || "";
      localStorage.setItem("aiw.polishDraft", state.polishDraft);
    });

    const topk = el("input", { class: "input", value: "8", style: "width:110px", inputmode: "numeric", title: "检索多少条范文片段作为证据（C1..Ck）" });
    const maxTok = el("input", { class: "input", value: "900", style: "width:120px", inputmode: "numeric", title: "输出长度上限（越大越慢）" });

    let advOpen = localStorage.getItem("aiw.polishAdv") === "1";
    const advRow = el(
      "div",
      { class: "row", style: `display:${advOpen ? "flex" : "none"}` },
      el("span", { class: "label" }, "输出长度"),
      maxTok,
      el("span", { class: "muted" }, "温度固定 0（尽量不发散）。")
    );

    const exemplarsBox = el("div", { class: "card" }, el("div", { class: "muted" }, "先获取范文对照。"));
    const outBox = el("div", { class: "card" }, el("div", { class: "muted" }, "生成结果将在这里显示。"));

    async function fetchExemplars() {
      if (!state.library) return toast("请先选择文献库。", "bad");
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
        toast(String(e.message || e), "bad", 6500);
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

      if (state.llm && state.llm.model_path) {
        outBox.appendChild(el("div", { class: "muted" }, `LLM：${String(state.llm.model_path).split(/[\\\\/]/).pop()}（llama.cpp）`));
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
          const txt = (selected.value || "").trim();
          if (txt.length < 8) return toast("选中文本太短。", "bad");

          let maxTokens = Number(maxTok.value || 900);
          if (!Number.isFinite(maxTokens) || maxTokens <= 0) maxTokens = 900;
          maxTokens = Math.max(64, Math.min(2048, Math.round(maxTokens)));
          if (maxTokens < 256) {
            maxTokens = 256;
            maxTok.value = String(maxTokens);
          }
          if (maxTokens < 600) {
            toast("输出长度太小可能导致生成失败（JSON 被截断）。建议 ≥ 900。", "bad", 4500);
          }

          genBtn.disabled = true;
          genBtn.textContent = "生成中…（首次会加载模型）";
          try {
            await refreshLLMStatus();
            const r = await apiPost("/api/align/polish", {
              library: state.library,
              selected_text: txt,
              top_k: Number(topk.value || 8),
              generate: true,
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
              msg = "生成结果格式不完整（常见原因：输出长度太小）。请打开“高级设置”，把输出长度调大（建议 900）后重试。";
            } else if (msg.includes("failed to start llama-server")) {
              msg = "启动本地模型失败：请到“本地 LLM”页点击“一键启动&测试”。";
            }
            toast(msg, "bad", 6500);
          } finally {
            genBtn.disabled = false;
            genBtn.textContent = "生成对齐润色（Qwen）";
          }
        },
      },
      "生成对齐润色（Qwen）"
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

    root.appendChild(
      el(
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
      )
    );

    root.appendChild(exemplarsBox);
    root.appendChild(outBox);
    return root;
  }

  function pageCite() {
    renderHeader("引用借鉴", "抽取“引用句子 + References”，构建可检索的范文句式库（白箱）。");
    const root = el("div", { class: "grid", style: "gap:14px" });

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
            toast(String(e.message || e), "bad", 6500);
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
    return root;
  }

  function pageLLM() {
    renderHeader("本地 LLM", "llama.cpp（llama-server.exe）+ GGUF（默认 Qwen 2.5 3B）。");
    const root = el("div", { class: "grid", style: "gap:14px" });

    const serverPath = el("input", { class: "input", style: "flex:1", placeholder: "llama-server.exe 路径" });
    const modelPath = el("input", { class: "input", style: "flex:1", placeholder: "GGUF 模型路径（例如 qwen2.5-3b…gguf）" });
    const ctx = el("input", { class: "input", style: "width:100px", value: "2048", inputmode: "numeric" });
    const threads = el("input", { class: "input", style: "width:100px", value: "4", inputmode: "numeric" });
    const ngl = el("input", { class: "input", style: "width:110px", value: "0", inputmode: "numeric" });
    const sleep = el("input", { class: "input", style: "width:130px", value: "300", inputmode: "numeric" });

    const statusBox = el("div", { class: "card" }, el("div", { class: "muted" }, "正在读取状态…"));

    async function syncFromStatus() {
      await refreshLLMStatus();
      const st = state.llm || {};
      serverPath.value = st.server_path || "";
      modelPath.value = st.model_path || "";
      clear(statusBox);
      const rows = [
        ["server_path", st.server_path || ""],
        ["model_path", st.model_path || ""],
        ["server_ok", String(!!st.server_ok)],
        ["model_ok", String(!!st.model_ok)],
        ["running", String(!!st.running)],
        ["base_url", st.base_url || ""],
      ];
      statusBox.appendChild(el("div", { class: "label" }, "状态"));
      statusBox.appendChild(
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

    const testBtn = el(
      "button",
      {
        class: "btn btn-primary",
        type: "button",
        onclick: async () => {
          testBtn.disabled = true;
          testBtn.textContent = "启动&测试中…";
          try {
            const r = await apiPost("/api/llm/test", {
              server_path: (serverPath.value || "").trim(),
              model_path: (modelPath.value || "").trim(),
              ctx_size: Number(ctx.value || 2048),
              threads: Number(threads.value || 4),
              n_gpu_layers: Number(ngl.value || 0),
              sleep_idle_seconds: Number(sleep.value || 300),
            });
            await syncFromStatus();
            toast(r.ok ? "LLM 测试通过。" : "LLM 测试失败。", r.ok ? "good" : "bad");
          } catch (e) {
            await syncFromStatus().catch(() => {});
            toast(String(e.message || e), "bad", 6500);
          } finally {
            testBtn.disabled = false;
            testBtn.textContent = "一键启动&测试";
          }
        },
      },
      "一键启动&测试"
    );

    const stopBtn = el(
      "button",
      {
        class: "btn btn-danger",
        type: "button",
        onclick: async () => {
          try {
            await apiPost("/api/llm/stop", {});
            await syncFromStatus();
            toast("已停止 LLM。");
          } catch (e) {
            toast(String(e.message || e), "bad");
          }
        },
      },
      "停止"
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
        el("div", { class: "label" }, "配置"),
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
        el("div", { class: "row" }, preset8g, testBtn, stopBtn, openDirBtn),
        el("div", { class: "muted" }, "说明：本页用于“确认 Qwen 是否真的在工作”。测试会启动 llama-server 并发出一次 JSON 请求。")
      )
    );

    root.appendChild(statusBox);

    syncFromStatus().catch((e) => toast(String(e.message || e), "bad"));
    return root;
  }

  function pageHelp() {
    renderHeader("使用帮助", "你要的是“像范文写法”的白箱过程：有范文背书，改法可追溯。");
    return el(
      "div",
      { class: "grid", style: "gap:14px" },
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "最快上手（3 步）"),
        el(
          "ol",
          null,
          el("li", null, "文献库页：创建库 → 选择 PDF 文件夹 → 开始建库（等待完成）。"),
          el("li", null, "对齐扫描页：粘贴正文 → 扫描 → 找到对齐度低的句子。"),
          el("li", null, "对齐润色页：点击“润色这个句子”或粘贴段落 → 获取范文对照 → 生成对齐润色。")
        )
      ),
      el(
        "div",
        { class: "card" },
        el("div", { class: "label" }, "Qwen 的作用在哪里？"),
        el("div", null, "扫描：只用向量检索（FAISS），不调用 LLM。"),
        el("div", null, "润色：调用本地 Qwen（llama.cpp）输出 JSON，包含：诊断 + 轻改/中改 + 引用证据。"),
        el("div", { class: "muted" }, "如果“对齐润色”页能看到带引用的诊断/改写，就说明 Qwen 已在工作。")
      )
    );
  }

  async function render() {
    const r = route();
    navActive(r);
    const page = $("#page");
    clear(page);

    try {
      await refreshLibraries();
      await refreshLibraryStatus();
      await refreshLLMStatus();
    } catch (e) {
      toast(String(e.message || e), "bad", 6500);
    }

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
      try {
        await refreshLibraryStatus();
        toast(state.library ? "已切换文献库。" : "未选择文献库。", state.library ? "good" : "bad");
      } catch (err) {
        toast(String(err.message || err), "bad");
      }
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
