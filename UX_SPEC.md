# UX Spec (AI Word Detector)

## 1) Product Snapshot
- Users: 研究者/学生/编辑（中英文写作），需要快速发现“相对领域语料库的怪异点”
- Primary job (JTBD): 用户提供领域 PDF 文档库作为范例，输入一段文本，定位异常词/短语/句子并解释原因
- Success metrics:
  - 首次建库的可理解性（有明确阶段、进度、ETA、可取消）
  - 用户复制/导出诊断的效率（低摩擦）
  - 误报率降低（标题/章节名不被当成异常）
- Platforms & inputs: Windows 桌面端（鼠标/键盘），离线优先
- Constraints: Tkinter + 本地 ONNX 语义模型；安装包 ≤ 1GB
- Non-goals: 在线推理/云端依赖；复杂账号体系

## 2) IA & Navigation
- Page map:
  - 主窗口（输入/结果/统计/诊断）
  - 文献库管理菜单（重命名/信息/打开数据目录/清空/删除）
- Primary navigation pattern: 单窗信息密集型布局（三栏：输入/结果/词频；底部：句子诊断）
- Global command surface: 顶部工具栏（库选择、加载 PDF、查看词表、分析、复制）

## 3) Key Flows

### Flow A: Build Library (Load PDF)
- Entry: 点击 `Load PDF` → 选择包含 PDF 的文件夹
- Steps:
  - 立刻显示进度面板（标题+阶段+进度条+ETA+取消）
  - 阶段 1：Extracting PDFs（按 `PDF x/y` 更新）
  - 阶段 2：Embedding sentences（按 `sentences x/y` 更新；提示 CPU 高占用属正常）
  - 完成后进度面板回到“空闲态”（不消失），并弹出完成提示
- Errors & recovery:
  - 缺少语义模型：阻止继续并提示离线模型目录
  - 中途取消：停止处理并保持旧索引不被删除（原有数据不丢）
  - PDF 解析失败：跳过单个文件但继续整体进度
- Acceptance check:
  - 选择文件夹后 200ms 内出现进度 UI（0/x 起步）
  - embedding 阶段进度至少每 1s 更新一次（不卡 0）
  - 点击取消后 2s 内停止（或明显进入“正在取消”状态）
  - 取消/失败不会删除旧的 `*.sentences.json` / `*.embeddings.npy`

### Flow B: Analyze Text
- Entry: 粘贴文本 → 点击 `Analyze`
- Steps: 词/短语统计 + 句子诊断（按严重程度排序）+ 语义对照检索（缺索引则明确报错）
- Acceptance check:
  - 结果区域可选择复制
  - 诊断列表点击可复制“句子+原因”，右键可打开复制/定位菜单
  - 默认仅显示严重问题，可切换“显示轻微问题”

## 4) Design Tokens
- Source of truth: `ai_word_detector.py` 的 `Theme` 常量 + `ModernProgressBar` 动效参数
- Tokens file: `uiux_tokens.json`

## 5) Screen Specs
- Main Window: 主窗口（输入/结果/统计/诊断）
- Progress Panel: 顶部工具栏下方的任务进度面板（常驻；空闲态显示提示，长任务时显示进度/ETA/可取消）
- Resizable layout: 主区域与底部区域可拖拽分配高度；主区域“输入/结果/词频”可拖拽分配宽度

## 6) States & Errors (Global)
- Loading: 立刻显示任务面板（shell-first），避免“无反馈”
- Error: messagebox + 状态条简短提示
- Canceling: 取消按钮变为禁用状态并显示“正在取消…”
- Empty: 无库/无语料/无文本时显示引导文案

## 7) Motion & Micro-interactions
- Progress fill: 180ms ease-out（降低跳变焦虑）
- Update cadence: embedding 进度更新节流到 1s（避免 UI 抖动）
- Indeterminate: 当进度卡在 0% 时显示轻量“流动条”，明确“正在工作”

## 8) Accessibility Checklist
- 键盘：禁用状态不可触发；进度面板不抢焦点
- 对比度：进度条轨道与填充需在 light/dark 都可辨识

## 9) Performance Checklist
- 长任务永远在后台线程执行；UI 更新使用 `root.after`
- 语义 embedding 采用长度分桶减少动态 shape 开销
- 索引写入用临时文件 + `os.replace` 原子替换，避免半成品/数据丢失

## 10) Open Questions / Assumptions
- 默认语义索引上限当前为 10k 句，是否需要在 UI 中暴露一个“速度/精度”滑杆？
