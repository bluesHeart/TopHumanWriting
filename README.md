# TopHumanWriting | 顶级范文对齐写作（本地文档库 + 白箱证据 + 离线模型）

[English](#english) | [中文](#中文)

---

## English

TopHumanWriting is an **offline local web app** for “aligning to top human exemplars”:

- Build a local **PDF exemplar library** (50–100 PDFs, zh/en/mixed)
- Retrieve top-k exemplar excerpts with **PDF + page** (FAISS, local)
- Generate **white-box** polish: diagnosis + controlled rewrites + evidence quotes (Qwen 3B via llama.cpp)
- Build a **citation pattern bank**: in-text citation sentences + references (white-box, searchable)

### Quick Start (Web)

**Offline Release (unzip & run):**

1. Download the latest `TopHumanWriting_<version>_offline.zip` from GitHub Releases
2. Unzip it
3. Run `TopHumanWriting.vbs` (silent, recommended) or `run_web.bat` (debug)
4. Your browser opens `http://127.0.0.1:7860` (default; auto-switch if occupied)

If it stays on the startup page, open `TopHumanWriting_data/logs/launch.log` (or run `run_web.bat` to see errors).

**From source (dev):**

1. Run `setup_env.bat` (once)
2. Run `run_web.bat`

Main pages:
- **Library**: create library → pick PDF folder → build index
- **Align Scan**: find least-aligned sentences (retrieval only, no LLM)
- **Align Polish**: show exemplars (C1..Ck) → generate white-box polish (Qwen via llama.cpp)
- **Citations**: build/search citation sentence patterns + open PDFs + view References
- **Local LLM**: one-click start & test (Preset: 8GB)

### Offline LLM Assets

Expected paths:
- `models/llm/llama-server.exe`
- `models/llm/qwen2.5-3b-instruct-q4_k_m.gguf`

From source you can download them with `download_llm_assets.bat`.

### Optional: OpenAI-compatible LLM API

If you want to use a remote LLM (instead of local llama.cpp), configure it in **LLM Settings** or set env vars:
- `SKILL_LLM_API_KEY` (or `OPENAI_API_KEY`)
- `SKILL_LLM_BASE_URL` (or `OPENAI_BASE_URL`, usually ends with `/v1`)
- `SKILL_LLM_MODEL` (or `OPENAI_MODEL`)

### Data & Cache Location (Portable)

By default, the app stores data next to the project folder:

- `TopHumanWriting_data/` (compatible with old `AIWordDetector_data/`)
  - `settings.json`
  - `libraries/*.json` (library stats)
  - `libraries/<name>.sentences.json` (semantic sentence records, includes PDF source)
  - `libraries/<name>.embeddings.npy` (semantic embeddings)
  - `rag/<library>/` (RAG index)
  - `cite/<library>/` (citation bank)

Override with `TOPHUMANWRITING_DATA_DIR` (or legacy `AIWORDDETECTOR_DATA_DIR`).

### Notes

- If you replace the semantic model but keep an old index, results may look unchanged. The app will prompt to **rebuild the semantic index**.
- Syntax analysis is optional; put UDPipe models in `models/syntax/` and rebuild the library.

### Build Release Zip (Web)

Run `build_release_web.bat` to generate `release/TopHumanWriting_<version>_offline.zip`.

---

## 中文

TopHumanWriting 是一个**离线本地网页**，用于“模仿顶级人类范文写法”的白箱写作：

- 你提供本地 PDF 范文库（50–100 篇，中英混合可）
- 检索 top-k 范文片段并展示 **PDF + 页码**
- 用本地 Qwen（llama.cpp）生成 **白箱** 输出：诊断 + 轻改/中改 + 范文证据引用
- 从范文库抽取 **引用句式库**：正文 author-year 引用句子 + References，可检索可追溯

### 快速开始（网页）

**离线发布包（解压即用）：**

1. 在 GitHub Releases 下载最新的 `TopHumanWriting_<version>_offline.zip`
2. 解压
3. 双击 `TopHumanWriting.vbs`（推荐：不弹黑窗口）或 `run_web.bat`（调试用）
4. 浏览器会自动打开 `http://127.0.0.1:7860`（默认端口；若被占用会自动换端口）

如果一直停在启动页：打开 `TopHumanWriting_data/logs/launch.log`（或用 `run_web.bat` 看报错）。

**源码运行（开发）：**

1. 运行 `setup_env.bat`（首次一次）
2. 运行 `run_web.bat`

主要页面：
- **文献库**：建库/选 PDF 文件夹/建索引
- **对齐扫描**：找出最不像范文的句子（仅检索，不调用 LLM）
- **对齐润色**：展示范文证据（C1..Ck）→ 生成白箱润色（Qwen + llama.cpp）
- **引用借鉴**：抽取/检索引用句式库 + 打开原 PDF + 查看 References
- **本地 LLM**：一键启动&测试（推荐 8GB 预设）

### 本地 LLM 资产

默认读取：
- `models/llm/llama-server.exe`
- `models/llm/qwen2.5-3b-instruct-q4_k_m.gguf`

源码模式可用 `download_llm_assets.bat` 自动下载。

### 可选：大模型 API（OpenAI 兼容）

如果你想用“云端 API”替代本地 llama.cpp，可在 **LLM 设置** 页面配置，或设置环境变量：
- `SKILL_LLM_API_KEY`（或 `OPENAI_API_KEY`）
- `SKILL_LLM_BASE_URL`（或 `OPENAI_BASE_URL`，通常以 `/v1` 结尾）
- `SKILL_LLM_MODEL`（或 `OPENAI_MODEL`）

### 数据与缓存位置（便于清理）

默认放在项目目录旁的可携带数据目录：

- `TopHumanWriting_data/`（兼容旧 `AIWordDetector_data/`）
  - `settings.json`
  - `libraries/*.json`
  - `libraries/<库名>.sentences.json`（范句记录，含 PDF 出处）
  - `libraries/<库名>.embeddings.npy`（语义向量）
  - `rag/<库名>/`（RAG 检索索引）
  - `cite/<库名>/`（引用句式库）

可用环境变量 `TOPHUMANWRITING_DATA_DIR` 覆盖（旧的 `AIWORDDETECTOR_DATA_DIR` 也兼容）。

### 生成 Release Zip（网页版）

运行 `build_release_web.bat` 会生成 `release/TopHumanWriting_<version>_offline.zip`。

---

## Project Structure | 项目结构

```
TopHumanWriting/
├── webapp/              # FastAPI backend + static frontend
├── aiwd/                # RAG / llama-server / polish core
├── ai_word_detector.py
├── i18n.py
├── version.py
├── requirements.txt
├── setup_env.bat
├── run_web.bat
├── build_release_web.bat
├── locales/
├── word_lists/
├── models/              # offline models (llama.cpp + gguf + onnx)
├── TopHumanWriting_data/ # portable data/cache (runtime)
└── README.md
```

## License

MIT License - See [LICENSE](LICENSE)
