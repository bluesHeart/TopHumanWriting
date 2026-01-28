# 计划：把 AI Word Detector 升级为“对齐范文”的离线文档润色软件（8GB Windows / 50–100 PDF / 中英混合）

## 0. 一句话愿景
用户用“本地论文库（范文）”建索引；对选中段落一键获取**检索到的范文对照 + 带出处引用 + 受控改写（轻改/中改）**，尽可能“像范文写法”，并尽可能抑制 LLM 发散。

---

## 1. 约束与明确假设（按你的输入固定）
- 平台：Windows 笔记本，内存 8GB（CPU-only 作为默认场景）。
- 语料：50–100 篇 PDF，中文/英文/混合都可能出现。
- 交互：以“用户选中句子/段落”为主；不做整篇自动重写（后续可扩展）。
- 风格目标：**对齐（模仿范文写法）**，优先“同义改写 + 句式对齐 + 学术口吻”，尽量避免新观点/新事实/新引用。
- 分发：离线包允许较大体积；你希望离线包**自带 LLM 模型**，默认采用 **3B**。

---

## 2. 产品范围：MVP vs. 后续

### 2.1 MVP（必须做）
1) **本地论文库建库**：PDF 文件夹 → 按页抽取/清洗 → chunk → 向量索引（可增量、可取消、可恢复）。
2) **范文对照检索**：对选中段落检索 top-k 范文片段；展示 `PDF 路径 + 页码 + 片段原文`；可复制/打开来源。
3) **对齐润色（受控）**：
   - 输出两档：轻改 / 中改（默认轻改更保守）。
   - 每条改写都带“为什么这样改”（基于范文片段证据），并严格绑定引用。
4) **一键应用/撤销**：diff 预览 → 应用到输入框 → 可撤销。
5) **可打包离线**：解压即用的本地网页包（自带 python + 依赖 + `llama-server` + 3B GGUF + embedding 模型）。

### 2.2 后续（不阻塞 MVP）
- 整篇/章节级润色工作流（计划任务队列、批处理、导出报告）。
- DOCX/Markdown 导入导出（保留格式、批注）。
- 多风格 Profile（不同会议/期刊写法、不同导师偏好）。
- 自动抽取“范文 style guide”（句式、常用结构、连接词分布）。
- 评测：内置基准集、对齐度/保真度打分、回归测试。

---

## 3. 技术选型（与 8GB/离线/打包对齐）

### 3.1 编排框架：LlamaIndex
用途：ingestion（构建 Node/metadata）、检索编排、citation 输出结构。

建议采用模块化依赖（减少体积与冲突）：
- `llama-index-core`
- `llama-index-vector-stores-faiss`（默认）
- `llama-index-vector-stores-chroma`（可选）

### 3.2 向量库：FAISS 默认，Chroma 可选
- **FAISS（默认）**：Windows wheel 可用、依赖轻、性能好。
- **Chroma（可选）**：持久化/元数据友好，但依赖更重；在 8GB 上不作为默认路径。

### 3.3 本地 LLM：llama.cpp（GGUF）通过 `llama-server`
关键点：
- 不建议依赖 `llama-cpp-python`（Windows 下 PyPI 多为源码包，打包/编译不稳定）。
- 使用 `llama-server` 的 OpenAI 兼容 API（`/v1/chat/completions`）便于封装与调试。
- 利用 `--sleep-idle-seconds`（空闲自动卸载模型）降低 8GB 场景常驻内存压力。

### 3.4 Embedding：优先复用现有 ONNX（已在项目中）
现有语义模型（`models/semantic/`）已经支持中英句向量，建议复用：
- 统一 embedding 逻辑：既用于“异常检测/范句”，也用于“RAG 索引/检索”与“改写保真度约束”。
- 减少新模型依赖与下载复杂度。

---

## 4. 核心机制：如何“对齐”且“抑制发散”

### 4.1 生成策略（默认安全配置）
- `temperature=0.0~0.2`，`top_p` 可默认 1.0（或略低），限制 `max_tokens`（如 256–384）。
- 默认 `ctx=2048`（8GB 更稳），UI 提供“质量/省内存”档位切换。
- 输入长度硬上限：选中段落过长时先提示切段或自动切段（避免 prompt 爆 ctx）。

### 4.2 输出结构化 + 校验（强制）
LLM 必须输出严格 JSON（示例字段）：
```json
{
  "language": "zh|en|mixed",
  "variants": [
    {
      "level": "light|medium",
      "rewrite": "...",
      "changes": ["..."],
      "citations": [{"id":"C3","pdf":"...","page":12,"quote":"..."}]
    }
  ]
}
```
校验规则（不通过则自动要求“修复到合规”，否则回退到“无 LLM 建议”）：
- 引用 `id` 必须来自检索提供的 `C1..Ck`。
- `quote` 必须是对应 chunk 原文的子串。
- 禁止新增：数字/年份、括号引用（如 `[1]`、`(Smith, 2020)`）、新专有名词（简单规则：英文大写串/疑似人名机构模式等）。
- 保真度：改写与原文 embedding 相似度低于阈值则判失败（阈值可调）。

### 4.3 回退路径（8GB 必须有）
当 LLM 未启动/内存不足/输出不合规：
- 仍提供“范文对照 + 可编辑建议”（不依赖 LLM）。

---

## 5. 数据与持久化设计（按 Library 分隔，可增量）

### 5.1 建议目录结构（新）
默认仍遵循 `AIWORDDETECTOR_DATA_DIR`（便携/可清理）：
```
AIWordDetector_data/
  settings.json
  libraries/                  # 现有：统计与语义离群索引
    <name>.json
    <name>.sentences.json
    <name>.embeddings.npy
  rag/                        # 新增：RAG 索引与清单（每个库一套）
    <name>/
      manifest.json           # PDF 列表 + mtime/size/hash + 建库版本
      nodes.jsonl             # chunk 文本 + metadata（调试/回滚友好）
      faiss.index             # FAISS 向量索引（默认）
      storage/                # LlamaIndex storage（docstore/index store）
models/
  semantic/                   # 现有：离线语义模型（ONNX）
  syntax/                     # 现有：UDPipe 模型（可选）
  llm/                        # 新增：本地 LLM（llama.cpp）
    llama-server.exe          # 随包（或 download_llm_assets.bat 下载）
    qwen2.5-3b-instruct-q4_k_m.gguf  # 默认 3B（离线包自带；不建议提交到 repo）
```

### 5.2 增量更新策略
- `manifest.json` 记录每个 PDF 的 `path_rel, size, mtime, quick_hash`。
- 建库时只重算变化文件对应的 chunk，并更新索引（FAISS 可重建或增量；MVP 可先“变化即重建”，后续再做增量写入）。
- 建库全程可取消；采用“临时文件 + 原子替换（os.replace）”避免半成品破坏旧库。

---

## 6. 3B 模型与 8GB 预算（默认推荐值）

### 6.1 模型建议（满足中英混合、可分发）
- 默认：**Qwen2.5-3B-Instruct GGUF**（`Qwen/Qwen2.5-3B-Instruct-GGUF`，`qwen2.5-3b-instruct-q4_k_m.gguf`）。
- 量化：优先 `Q4_K_M`（质量/内存折中），备选 `Q4_0`（更小/更快但质量略差）。
- 注意：离线包自带权重前，需确认模型许可证允许再分发；若不确定，改成“离线包附下载脚本 + 首次运行导入”。

### 6.2 llama-server 默认启动参数（CPU-only）
- `--ctx-size 2048`
- `--threads`：默认物理核心数（UI 提供 1/2/4/8 快捷）
- `--n-predict`：按请求设置（不在 server 固定）
- `--sleep-idle-seconds 300`：空闲 5 分钟自动卸载（节省内存）

### 6.3 内存控制策略（避免 OOM）
- prompt 侧：限制检索片段长度（每条 300–500 字符）+ top-k 默认 6–8 + 选中段落硬上限。
- 生成侧：限制输出 tokens；优先轻改；中改需用户点击。
- 系统侧：当检测到内存压力（可用 psutil 可选）时：
  - 自动降低 top-k / ctx / max_tokens
  - 或直接切换到“无 LLM 建议模式”

---

## 7. 详细里程碑（按可交付/可验收拆解）

> 每个里程碑都要求：可运行、可回滚、可验证；不破坏现有“怪异度检测”主流程。

### M0：工程化准备（0.5–1 天）
- 新增模块目录（建议）：
  - `rag/`：ingestion、chunker、index、retriever
  - `llm/`：llama-server 进程管理、请求客户端、校验与重试
  - `polish/`：对齐建议、diff/apply/undo
- 配置项落盘（settings.json）：RAG/LLM 参数、默认模型路径、后端选择（faiss/chroma）。
- 依赖梳理：最小依赖集合 + 可选依赖集合。

验收：
- 程序可启动；新配置可保存；不影响现有分析功能。

### M1：PDF ingestion → chunk → nodes（1–2 天）
- 按页抽取文本（复用 PyMuPDF）。
- 清洗：页眉页脚、软换行、目录/参考文献降权或剔除。
- Chunk：段落/句组（中英分别断句，混合按段落优先）。
- 写出 `nodes.jsonl` 与 `manifest.json`（支持取消/原子替换）。

验收：
- 50–100 PDF 可建库；随机抽 10 条 node 可定位到 pdf+page 且文本干净。

### M2：Embedding + FAISS 索引与持久化（1–2 天）
- 复用现有 ONNX embedder 生成向量（批量、节流 UI 更新）。
- FAISS 建索引并落盘；LlamaIndex StorageContext 持久化（或先自管元数据，后续再深度整合）。
- 检索 API：输入 query → 输出 `[(score, chunk, pdf, page)]`。

验收：
- 选中段落 → 1 秒级返回 top-k（库规模约 50–100 PDF）。
- 引用信息稳定可用（路径/页码/片段）。

### M3：UI 集成“范文对照”（1–3 天）
- 新增面板：`范文对照`（列表 + 展开详情 + 复制/打开）。
- 右键菜单：`对齐范文（仅对照）`、`对齐范文（建议）`、`对齐范文（轻改）`、`对齐范文（中改）`。
- 长任务进度复用现有进度面板（阶段：extract → embed → index）。

验收：
- 用户无须启动 LLM 也能用“对照+建议”完成改写。

### M4：无 LLM 的对齐建议（2–4 天）
- 规则/统计建议（不改写原文，只给可编辑建议）：
  - 连接词、学术口吻替换建议（中英）
  - 句长对齐建议（基于范文片段与库基线）
  - 术语一致性提醒（从范文片段抽取高频术语）
- UI：建议可复制、可插入、可一键应用局部替换。

验收：
- 不启 LLM 仍有“可落地”的改写路径；建议能明显更像范文。

### M5：llama-server 接入与管理（1–2 天）
- 随包附带 `llama-server.exe`，由程序管理：
  - 启动/探活/停止/异常重启
  - 端口与日志显示
  - 空闲卸载（sleep-idle-seconds）
- 提供模型管理 UI：默认 3B GGUF 路径、切换模型、档位预设（省内存/平衡/质量）。

验收：
- 一键启动 LLM；能完成一次简单 JSON 输出请求。

### M6：受控改写（对齐润色）+ 校验回退（3–6 天）
- Prompt：输入=原文段落 + top-k 范文片段（带 `C1..Ck`）+ 禁止项；输出严格 JSON。
- 校验：结构/引用/禁止项/保真度；失败自动请求“修复”；仍失败则回退 M4。
- UI：diff 预览、应用/撤销、版本对比（轻改 vs 中改）。

验收：
- 输出稳定合规；不合规不会污染 UI（一定回退）。
- 改写不引入新事实/新引用（规则检测通过率高）。

### M7：发布（Web 离线包）（1–2 天）
- 放弃桌面 exe，改为“解压即用的本地网页”。
- 提供 `build_release_web.bat`：生成 `release/AIWordsWeb_<version>_offline.zip`。
- 离线包内容：`webapp/` + `aiwd/` + `models/` + `run_web.bat` + `setup_env.bat` + `requirements.txt`。
- 首次运行检测：在网页的 **本地 LLM** 页明确显示 server/model 是否存在，并提供“一键启动&测试”。

验收：
- 解压后双击 `run_web.bat` → 浏览器打开 → “建库→对齐扫描→对齐润色”可跑通。

---

## 8. 风险清单与应对
- **许可证/再分发风险**：优先选择允许再分发的 3B instruct；否则离线包改为“附下载脚本 + 导入”。
- **PDF 噪声影响检索**：页眉页脚/参考文献/表格必须清洗或降权；否则“对齐”会学到坏风格。
- **8GB OOM**：必须有回退模式；默认 ctx/top-k/片段长度都要保守；空闲卸载模型。
- **对齐质量不稳定**：引入严格校验与重试；提示词“只改写法不改事实”；轻改优先。

---

## 9. 推荐的先做顺序（最短路径）
1) M1+M2：先把“可引用的范文检索”做出来（不用 LLM 也有价值）。
2) M3+M4：把“对照 + 无 LLM 建议”做成可用产品。
3) M5+M6：再上受控改写（LLM），用校验与回退保证稳定。
4) M7：最后做离线打包与默认模型随包。

---

## 10. 待确认（实现前最后的“可选项”）
- 默认 3B 模型具体选择与量化档（建议 Q4_K_M）。
- 是否需要“打开 PDF 并定位页码”的深度集成（简单版：打开文件；高级版：定位页码/高亮需要额外工作）。
- 是否将 RAG 索引与现有 library 统计合并管理（同名库一键建两套索引）。
