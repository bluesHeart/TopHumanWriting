# UX Spec (Web) | TopHumanWriting（范文对齐润色）

## 1) Product Snapshot
- 用户：研究者/学生/编辑（中英写作），有自己的“本地论文专题库（范文库）”
- 核心诉求：把稿子改得**更像范文写法**，并且是**白箱**（每条建议/改写都能追溯到范文证据）
- 主要约束：8GB Windows 笔记本；50–100 PDF；本地优先（检索在本地，润色生成走 OpenAI-compatible API）
- 成功指标：
  - 新用户 5 分钟内跑通：建库 → 扫描 → 选句 → 生成白箱润色
  - 每次润色都有“范文证据（pdf+页码+原文子串）”，便于背书
  - LLM 不可用时仍可做“范文对照”（不中断工作流）

## 2) IA & Navigation
- 导航模式：**左侧栏（Youdao-like）** + 顶部全局栏（库选择 + 索引状态 + 刷新）
- Page map：
  - `文献库`：创建库 / 选择 PDF 文件夹 / 建库进度 / 取消
  - `对齐扫描`：输入正文 → 找出对齐度低的句子（仅 FAISS 检索）
  - `对齐润色`：范文对照（C1..Ck）→ 生成白箱诊断 + 轻改/中改（API）
  - `API 设置`：base_url / model / api_key（可选保存）/ 一键测试
  - `使用帮助`：快速上手 + “模型的作用在哪里”

## 3) Key Flows
### Flow A: Build Library (PDF → Index)
- Entry：文献库页 → 选择 PDF 文件夹 → 开始建库
- Stages（UI 仅展示，后端实际阶段可更细）：
  - `pdf_extract`：逐个 PDF 处理（detail 显示相对路径）
  - `semantic_embed`：语义模型 embedding（可用于其他功能）
  - `rag_extract / rag_embed / rag_done`：RAG 节点抽取与 FAISS 持久化
- Recovery：
  - 可取消；失败/取消不应破坏已有索引（旧数据可继续用）

### Flow B: Align Scan (Find least-aligned)
- Entry：对齐扫描页 → 粘贴正文 → 开始扫描
- Output：按对齐度从低到高排序；每条可“查看范文/跳转润色”
- Constraint：扫描不调用 LLM（减少 8GB 场景等待与资源占用）

### Flow C: Align Polish (White-box)
- Entry：对齐润色页（来自扫描“润色这个句子”或手动粘贴）
- Steps：
  1) 获取范文对照（C1..Ck）
  2) 生成对齐润色（API）：输出 JSON → 解析展示
- Output：
  - 白箱诊断：每条必须包含 evidence（quote 是范文原文子串）
  - 两档润色：`light` / `medium`，并列出 changes + citations

### Flow D: API “Is it working?”
- Entry：API 设置页 → 填写 base_url/model/api_key → 一键测试
- Output：明确显示当前配置来源（env / settings）、HTTP 状态与错误摘要

## 4) Design Tokens
- Token 源：`uiux_tokens.json` + Web CSS 变量（`webapp/static/app.css`）
- 主题：Light/Dark
- 主题色（Accent presets）：Ocean / Teal / Violet / Slate / Crimson
- 反馈组件：Toast（成功/失败），Chip（状态），Progress（180ms ease-out）

## 5) Screen Specs（实现对齐）
- `文献库`
  - 组件：库名输入 + 创建；文件夹输入 + 选择；开始建库；进度条；取消
  - 状态：空库提示；建库中（running）；失败/取消提示
- `对齐扫描`
  - 组件：正文输入；top_k/max_items；开始扫描；结果列表（对齐度 badge + 操作）
  - 状态：空文本/未选库错误；扫描中按钮禁用；结果为空提示
- `对齐润色`
  - 组件：选中文本；获取范文对照；生成按钮；范文列表；诊断列表；轻改/中改卡片（复制/替换）
  - 状态：未选库/文本过短；LLM 不可用时报错；生成中 loading
- `API 设置`
  - 组件：base_url/model/api_key；保存；一键测试；状态表（含 source）
  - 状态：缺少 key/url/model；测试失败提示（401/403/429/超时等）

## 6) Accessibility Checklist
- 所有按钮/输入可键盘操作；Tab 顺序合理
- `:focus` 可见（使用统一 focus ring）
- 颜色对比：dark/light 都可读；状态不只靠颜色（文字也表意）

## 7) Performance Checklist
- 大任务（建库/模型加载）避免“无反馈”：立刻进入 running 状态 + 进度更新（1s 轮询）
- 8GB 默认：扫描不调用 LLM；生成时才发起 API 请求
- 未来（可选）：结果列表虚拟化、骨架屏、分段处理长文本

## 8) Acceptance Criteria（可验证）
- 文献库：点击开始建库后 1s 内能看到 `running` + 进度条开始变化；取消后状态变为 `canceled`
- 对齐扫描：输入正文后能返回按对齐度排序的列表；点击“润色这个句子”会带入对齐润色页
- 对齐润色：能看到 C1..Ck 范文对照；生成后能看到诊断/轻改/中改，并且每条都带引用证据
- API 设置：一键测试返回 OK；未配置时提示明确（缺 key/url/model），失败时有可读的错误摘要
