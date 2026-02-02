# TopHumanWriting | 顶级范文对齐写作（后端 Python 包：CLI + SDK）

[English](#english) | [中文](#中文)

---

## English

TopHumanWriting is a **backend-only** Python package (CLI + library) for “exemplar-alignment writing audit”:

- Build a local **PDF exemplar library** (50–200 PDFs, zh/en/mixed)
- Audit a target PDF and output **white-box** results:
  - what looks unlike exemplars, where, and why
  - exemplar evidence (**PDF + page**)
  - optional “rewrite templates” (controlled, temperature=0)
- Optional **CiteCheck**: author-year citation accuracy with evidence paragraphs

This PyPI distribution intentionally **does not ship the web UI** (to keep the package lean).

### Install

- Minimal: `pip install tophumanwriting`
- With RAG (required for exemplar retrieval):
  - `pip install "tophumanwriting[rag]"` (default: Chroma)
  - or `pip install "tophumanwriting[rag-faiss]"` (FAISS)
- With optional syntax checks: `pip install "tophumanwriting[syntax]"`
- Everything for backend: `pip install "tophumanwriting[all]"`

### Quickstart (CLI)

1. (Optional) Configure OpenAI-compatible LLM API (used by LLM review + CiteCheck):

   - `TOPHUMANWRITING_LLM_API_KEY` (fallback: `SKILL_LLM_API_KEY`, `OPENAI_API_KEY`)
   - `TOPHUMANWRITING_LLM_BASE_URL` (fallback: `SKILL_LLM_BASE_URL`, `OPENAI_BASE_URL`, usually ends with `/v1`)
   - `TOPHUMANWRITING_LLM_MODEL` (fallback: `SKILL_LLM_MODEL`, `OPENAI_MODEL`)

2. Run end-to-end (build once if needed → audit):

   - `thw run --paper main.pdf --exemplars reference_papers --max-llm-tokens 200000`

3. Output: the CLI prints an export folder that contains `result.json` + `report.md`.

Budgeting:
- Use `--max-llm-tokens` to hard-cap total LLM usage per run (LLM review + CiteCheck).
- (Optional) Provide `--cost-per-1m-tokens` and `--max-cost` to show an approximate cost in reports (unitless; depends on your pricing).

### Build Once → Audit Many (recommended for real workflows)

1. Download the semantic embedder model (once):
   - `thw models download-semantic`
2. Build exemplar library artifacts (slow, one-time):
   - `thw library build --name reference_papers --pdf-root reference_papers`
3. Run audits repeatedly (fast reuse):
   - `thw audit run --paper main.pdf --library reference_papers --max-llm-tokens 200000`

### Quickstart (Python)

```python
from tophumanwriting import TopHumanWriting

thw = TopHumanWriting(exemplars="reference_papers")  # folder with PDFs
export = thw.run("main.pdf", max_llm_tokens=200000)  # fit if needed + audit
print(export.report_md_path)
```

### Data & Cache Location

TopHumanWriting stores reusable artifacts under a writable data directory:

- `TopHumanWriting_data/`
  - `settings.json` (optional LLM config)
  - `libraries/*.json` (library manifests / stats)
  - `libraries/<name>.sentences.json` + `libraries/<name>.embeddings.npy`
  - `rag/<library>/` (RAG index)
  - `cite/<library>/` (citation bank)
  - `audit/exports/` (export bundles)

Override with `TOPHUMANWRITING_DATA_DIR` (legacy `AIWORDDETECTOR_DATA_DIR` also works).

### Limitations / Notes

- Only **text-based** PDFs are supported (scanned PDFs are out of scope).
- If you change the semantic model but keep an old index, rebuild the library to see changes.

### Publishing to PyPI (maintainers)

1. Bump version in `pyproject.toml`
2. Build:
   - `python -m build`
3. Verify:
   - `python -m twine check dist/*`
4. Upload:
   - `python -m twine upload dist/tophumanwriting-<version>*`

---

## 中文

TopHumanWriting 是一个**后端 Python 包**（CLI + SDK），用于“模仿同领域顶级范文写法”的对照式白箱体检：

- 你提供本地 PDF **范文库**（50–200 篇，中英混合可）
- 对待检测 PDF 做**端到端体检**并输出白箱结果：
  - 哪里不像范文、为什么不像、参考哪段范文（PDF+页码）
  - 可选：给出“可复用的改写模板/句式骨架”（温度=0，尽量不发散）
- 可选：**引用核查（CiteCheck）**，核查 author-year 引用是否准确/是否张冠李戴（附证据段落）

本 PyPI 包为了更轻量，**不包含前端网页**。

### 安装

- 最小安装：`pip install tophumanwriting`
- 安装检索编排（做范文检索必需）：
  - `pip install "tophumanwriting[rag]"`（默认：Chroma）
  - 或 `pip install "tophumanwriting[rag-faiss]"`（FAISS）
- 可选句法检查：`pip install "tophumanwriting[syntax]"`
- 一次装齐后端所有依赖：`pip install "tophumanwriting[all]"`

### 快速开始（CLI）

1. （可选）配置 OpenAI 兼容大模型 API（用于 LLM 分治体检 + CiteCheck）：

   - `TOPHUMANWRITING_LLM_API_KEY`（fallback: `SKILL_LLM_API_KEY`, `OPENAI_API_KEY`）
   - `TOPHUMANWRITING_LLM_BASE_URL`（fallback: `SKILL_LLM_BASE_URL`, `OPENAI_BASE_URL`，通常以 `/v1` 结尾）
   - `TOPHUMANWRITING_LLM_MODEL`（fallback: `SKILL_LLM_MODEL`, `OPENAI_MODEL`）

2. 一条命令端到端运行（必要时会自动建库）：

   - `thw run --paper main.pdf --exemplars reference_papers --max-llm-tokens 200000`

3. 输出：命令行会打印导出目录，里面包含 `result.json` + `report.md`。

预算说明：
- 用 `--max-llm-tokens` 硬限制单次运行的 LLM 总 tokens（同时覆盖 LLM 分治体检 + 引用核查）。
- （可选）用 `--cost-per-1m-tokens` + `--max-cost` 仅用于在报告里展示估算成本（单位自定）。

### 建库一次 → 反复体检（推荐）

1. 下载一次语义模型：`thw models download-semantic`
2. 建范文库工件（慢，一次性）：`thw library build --name reference_papers --pdf-root reference_papers`
3. 反复体检（复用索引）：`thw audit run --paper main.pdf --library reference_papers --max-llm-tokens 200000`

### 快速开始（Python）

```python
from tophumanwriting import TopHumanWriting

thw = TopHumanWriting(exemplars="reference_papers")
export = thw.run("main.pdf", max_llm_tokens=200000)
print(export.report_md_path)
```

### 数据与缓存位置

TopHumanWriting 会把可复用的工件写到数据目录：

- `TopHumanWriting_data/`
  - `settings.json`
  - `libraries/*.json`
  - `libraries/<库名>.sentences.json` + `libraries/<库名>.embeddings.npy`
  - `rag/<库名>/`（检索索引）
  - `cite/<库名>/`（引用句式库）
  - `audit/exports/`（导出包）

可用环境变量 `TOPHUMANWRITING_DATA_DIR` 覆盖（旧的 `AIWORDDETECTOR_DATA_DIR` 也兼容）。

### 注意事项

- 仅支持**可复制文字**的文本型 PDF；扫描版不考虑。
- 更换语义模型后建议重建范文库工件，否则效果可能看起来没变化。

---

## Project Structure | 项目结构

```
TopHumanWriting/
├── tophumanwriting/       # PyPI package (CLI + sklearn-style API)
│   ├── api.py             # TopHumanWriting.fit/audit/run
│   ├── cli.py             # `thw` entrypoint
│   ├── models.py          # semantic model download/status
│   ├── _version.py
│   └── locales/
├── aiwd/                  # audit core (RAG/citecheck/LLM reviews)
├── ai_word_detector.py    # legacy module (kept for compatibility)
├── pyproject.toml
├── MANIFEST.in
└── README.md
```

## License

MIT License - See [LICENSE](LICENSE)
