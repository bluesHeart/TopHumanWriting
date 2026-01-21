# AI Word Detector | AI词汇检测器

[English](#english) | [中文](#中文)

---

## English

### What is this?

AI Word Detector helps you identify **uncommon or unusual words** in your writing by comparing them against a corpus of academic papers (PDFs) you provide.

**Use case:** You've read 100+ papers in your field. Words that appear frequently across those papers are "normal" academic vocabulary. Words that *never* appear might be:
- AI-generated phrases
- Overly informal language
- Unusual word choices worth reviewing

### How it works

1. **Build your corpus**: Load a folder of PDF papers from your field
2. **Analyze text**: Paste any text to check
3. **See results**: Words are color-coded by how often they appear in your corpus
   - 🟢 **Common** (>50% of papers) - Standard vocabulary
   - ⚫ **Normal** (10-50%) - Acceptable usage
   - 🟠 **Rare** (<10%) - Worth checking
   - 🔴 **Unseen** (0%) - Never appeared in your corpus

### Quick Start Guide

**Step 1: Create a Library**
- Click the **[+]** button next to the Library dropdown
- Enter a name (e.g., "Finance", "Medical", "CS")
- Each library stores vocabulary from PDFs you add

**Step 2: Load PDFs**
- Click **[Load PDF]** button
- Select a folder containing your PDF papers
- Wait for processing (progress shown in status bar)
- More papers = better accuracy!

**Step 3: Analyze Text**
- Paste text in the left panel
- Click **[Analyze]**
- View highlighted results in the right panel
- Check the statistics table below for detailed word frequencies

**Managing Libraries**
- Switch between libraries using the dropdown
- Delete libraries with the **[-]** button
- Create separate libraries for different research fields

### Features

- **Document Frequency (DF) based analysis** - Measures how many papers contain each word, not just total occurrences
- **Bilingual UI** - English and Chinese interface
- **Light/Dark theme** - Toggle with ☾ button
- **Adjustable font size** - Ctrl+scroll or +/- buttons
- **Statistics table** - See all words sorted by rarity
- **Portable** - Single .exe file, no installation needed

### Installation

#### Option 1: Download Release (Recommended)
Download the latest `.exe` from [Releases](../../releases).

#### Option 2: Run from Source
```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/ai-word-detector.git
cd ai-word-detector

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run
python ai_word_detector.py
```

#### Option 3: Build Executable
```bash
# After setting up the environment
build.bat  # Windows
```

### Requirements

- Python 3.8+
- PyMuPDF (fitz) - PDF text extraction
- jieba - Chinese word segmentation

### Screenshots

*Coming soon*

### License

MIT License - See [LICENSE](LICENSE)

---

## 中文

### 这是什么？

AI词汇检测器帮助你通过与PDF论文语料库对比，识别文本中**不常见或异常的词汇**。

**使用场景：** 你已经阅读了本领域100+篇论文。在这些论文中频繁出现的词是"正常"的学术词汇。而那些*从未*出现过的词可能是：
- AI生成的短语
- 过于口语化的表达
- 值得检查的异常用词

### 工作原理

1. **构建语料库**：加载一个包含本领域PDF论文的文件夹
2. **分析文本**：粘贴任意文本进行检测
3. **查看结果**：词汇按照在语料库中的出现频率进行颜色标注
   - 🟢 **常见** (>50%的论文中出现) - 标准词汇
   - ⚫ **正常** (10-50%) - 可接受的用法
   - 🟠 **罕见** (<10%) - 值得检查
   - 🔴 **未见** (0%) - 从未在语料库中出现

### 快速入门指南

**第一步：创建文献库**
- 点击文献库下拉框旁边的 **[+]** 按钮
- 输入名称（例如："金融"、"医学"、"计算机"）
- 每个文献库独立存储您添加的PDF词汇

**第二步：加载PDF**
- 点击 **[加载PDF]** 按钮
- 选择包含PDF论文的文件夹
- 等待处理完成（状态栏显示进度）
- 论文越多，分析越准确！

**第三步：分析文本**
- 在左侧面板粘贴文本
- 点击 **[分析]**
- 在右侧面板查看高亮结果
- 查看下方统计表了解详细词频

**管理文献库**
- 使用下拉框切换不同文献库
- 点击 **[-]** 按钮删除文献库
- 为不同研究领域创建独立的文献库

### 功能特性

- **基于文档频率(DF)分析** - 统计包含该词的论文数量，而非简单的词频统计
- **双语界面** - 支持中英文切换
- **明暗主题** - 点击 ☾ 按钮切换
- **可调字号** - Ctrl+滚轮 或 +/- 按钮
- **统计表格** - 按稀有度排序展示所有词汇
- **绿色便携** - 单个exe文件，无需安装

### 安装方式

#### 方式一：下载发布版（推荐）
从 [Releases](../../releases) 下载最新的 `.exe` 文件。

#### 方式二：源码运行
```bash
# 克隆仓库
git clone https://github.com/YOUR_USERNAME/ai-word-detector.git
cd ai-word-detector

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt

# 运行
python ai_word_detector.py
```

#### 方式三：打包为exe
```bash
# 配置好环境后
build.bat  # Windows
```

### 依赖

- Python 3.8+
- PyMuPDF (fitz) - PDF文本提取
- jieba - 中文分词

### 截图

*即将添加*

### 开源协议

MIT License - 见 [LICENSE](LICENSE)

---

## Project Structure | 项目结构

```
ai-word-detector/
├── ai_word_detector.py   # Main application | 主程序
├── i18n.py               # Internationalization | 国际化模块
├── version.py            # Version info | 版本信息
├── requirements.txt      # Dependencies | 依赖列表
├── build.bat             # Build script | 打包脚本
├── setup_env.bat         # Environment setup | 环境配置
├── run_dev.bat           # Dev run script | 开发运行脚本
├── locales/
│   ├── en.json           # English UI text
│   └── zh_CN.json        # Chinese UI text
├── word_lists/
│   └── ai_words_zh.json  # Chinese AI-style words
├── LICENSE               # MIT License
└── README.md             # This file
```

## Contributing | 贡献

Issues and Pull Requests are welcome!

欢迎提交 Issue 和 Pull Request！
