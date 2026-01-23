# AI Word Detector | 领域文本怪异度检测器（范例语料库 + 离线模型）

[English](#english) | [中文](#中文)

---

## English

AI Word Detector compares your text against **your own domain PDF library** (human-written exemplars) and highlights what looks *out-of-domain* or *AI-ish* from multiple angles:

- **Word rarity** (document frequency across your PDFs)
- **Phrase/word-order rarity** (bigrams)
- **Sentence diagnosis** (length/formatting + domain phrasing outliers)
- **Semantic outliers (offline ONNX model)** with **exemplar sentences** + **PDF source**
- **Syntax outliers (offline UDPipe, optional)** via POS-pattern statistics (requires rebuilding the library)

### Quick Start (Offline Package)

1. Unzip `release/AIWordDetector_*_offline.zip`
2. Run `AIWordDetector.exe`
3. Create/select a **Library**
4. Click **Load PDF** and choose your domain paper folder (recursive)
5. Paste text and click **Analyze**

### Using “Sentence Diagnosis”

- The diagnosis panel is **selectable/copyable** (drag to select → `Ctrl+C`)
- Right-click a diagnosis item to:
  - Copy sentence / diagnosis / reasons
  - **Show Exemplars** (semantic nearest neighbors)
  - Locate the sentence in results
- **Zoom**: `Ctrl + Mouse Wheel` works in Input / Results / Sentence Diagnosis
- **Sorting**: warnings first → more warnings → more important issue types (semantic > phrasing > syntax > …) → original order

### “Exemplar Sentences” with PDF Source

When a sentence is flagged as a semantic outlier, the app shows an **exemplar** from your PDF library. If the library was built with source tracking, the exemplar line includes:

`[relative/path/to/paper.pdf] exemplar sentence...`

In Sentence Diagnosis, the `PDF: ...` source part is highlighted in blue for quick scanning.

### Data & Cache Location (Portable)

By default, the app stores data next to the exe:

- `AIWordDetector_data/`
  - `settings.json`
  - `libraries/*.json` (library stats)
  - `libraries/<name>.sentences.json` (semantic sentence records, includes PDF source)
  - `libraries/<name>.embeddings.npy` (semantic embeddings)

Override with `AIWORDDETECTOR_DATA_DIR`.

### Notes

- If you replace the semantic model but keep an old index, results may look unchanged. The app will prompt to **rebuild the semantic index**.
- Syntax analysis is optional; put UDPipe models in `models/syntax/` and rebuild the library.

### Run from Source

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python ai_word_detector.py
```

### Build (Windows)

```bat
build.bat
```

---

## 中文

AI Word Detector 会把你的**领域 PDF 文档库**当作“人类范例语料库”，从多个维度找出你输入文本里“不像本领域写法/像 AI 的地方”，并解释原因：

- **词频（按文档频率 DF）**：某个词在多少篇 PDF 里出现
- **短语/语序（bigram）**：不常见搭配/不常见语序
- **句子诊断**：句子层面的异常（领域短语偏离、模板痕迹、格式问题等）
- **语义偏离（离线语义模型）**：给出最相似的**范句**并附带 **PDF 出处**
- **句式偏离（UDPipe 离线，可选）**：对比语料库 POS 模式，标记不常见句式（需要重建文献库）

### 离线包快速使用

1. 解压 `release/AIWordDetector_*_offline.zip`
2. 运行 `AIWordDetector.exe`
3. 新建/选择一个**文献库**
4. 点击 **加载PDF**，选择你的领域 PDF 文件夹（会递归扫描）
5. 粘贴文本，点击 **分析**

### 句子诊断怎么用

- 句子诊断面板支持**直接选择/复制**（拖拽选中 → `Ctrl+C`）
- 右键诊断：复制句子/诊断/原因、**查看范句**、在结果中定位
- **放大/缩小**：输入框/结果/句子诊断都支持 `Ctrl + 鼠标滚轮`
- **排序逻辑**：先 warning → warning 数量 → 问题类型权重（语义 > 短语 > 句式 > …）→ 原文顺序

### 范句（含 PDF 出处）

当句子被判定为“语义偏离”时，会展示来自你的 PDF 文档库的**范句**。若文献库带有出处信息，会显示：

`[相对路径/论文.pdf] 范句内容...`

在句子诊断里，`PDF：...` 这一段会用蓝色高亮，方便快速定位出处。

### 数据与缓存位置（便于清理）

默认放在 exe 同目录，便于一键删除清理：

- `AIWordDetector_data/`
  - `settings.json`
  - `libraries/*.json`
  - `libraries/<库名>.sentences.json`（范句记录，含 PDF 出处）
  - `libraries/<库名>.embeddings.npy`（语义向量）

可用环境变量 `AIWORDDETECTOR_DATA_DIR` 覆盖。

### 源码运行/打包

```bash
python -m venv venv
venv\\Scripts\\activate
pip install -r requirements.txt
python ai_word_detector.py
```

```bat
build.bat
```

---

## Project Structure | 项目结构

```
ai-word-detector/
├── ai_word_detector.py
├── i18n.py
├── version.py
├── requirements.txt
├── build.bat
├── setup_env.bat
├── run_dev.bat
├── locales/
├── word_lists/
├── models/              # offline models (in offline package)
├── AIWordDetector_data/ # portable data/cache (runtime)
└── README.md
```

## License

MIT License - See [LICENSE](LICENSE)
