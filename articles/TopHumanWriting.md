# 把“AI 味”赶出论文——用 TopHumanWriting 做范文对齐式白箱体检

**如果你写过论文、研报或公文，你一定经历过这种崩溃：**

你明明把意思写清楚了，语法也没错，逻辑也说得通——但导师一句话就把你打回原形：

> “这段太像 AI 了。”  
> “不像顶刊的写法。”  
> “你这句为什么要这么说？范文不是这样写的。”  

最难受的不是被否定，而是**你不知道到底哪里不对**：  
是词不对？句式不对？段落推进不对？章节结构不对？还是引用句子根本不像同领域的人类写法？

于是你打开大模型，让它“润色一下”。它确实能写得更顺，但往往会带来新的灾难：

- 改得更“像 AI”了（过度圆滑、空泛、套话、过渡词密集）
- 语气变了（本来是 finance 的写法，被改成 generic 的写法）
- 引用语气变得危险（不确定的因果被改成肯定句）
- 你根本没法解释“为什么这么改”，更没法拿范文背书

**TopHumanWriting 解决的不是“写得更好看”，而是“写得更像范文”。**

它不是一个“黑箱改写器”，而是一套**范文对齐式、可审计、可回溯**的写作体检系统：

- 你提供一个同领域的 **PDF 范文库**（50–200 篇，中英混合可）
- 它把范文做成可复用的“专题库工件”（索引/范句/引用句式）
- 你提供要检测的 PDF
- 它输出一份白箱报告：**哪里不像范文、为什么不像、参考哪段范文（PDF+页码）**，以及“怎么改更像范文”的模板建议

你最终得到的不是“AI 给你改完的成稿”，而是一个可复盘的写作证据链：  
**有法可依（范文），有据可查（页码），有步骤可做（逐条修）。**

---

## 它到底在做什么？（从系统视角讲清楚）

TopHumanWriting 更像一个“数字化写作助教团队”，而不是一个聊天机器人：

- **输入**：
  - 待检测 PDF（仅支持可复制文字的文本型 PDF）
  - 范文 PDF 库（同领域越集中越好）
  - （可选）大模型 API（OpenAI-compatible）用于“分治体检”和“受控模板建议”
- **输出**：
  - `result.json`：机器可读的结构化问题清单（每条带证据）
  - `report.md`：人类可读的体检报告（可直接发给自己或组会用）
  - 可复用的库工件（一次性慢构建，后续复用）

它的核心原则只有一句话：

> **没有范文证据，不准下结论；能用程序做的，不浪费 token；需要“像人类审稿”时，用 LLM 分头行动。**

---

## 为什么它能“像范文”？（不是让模型自由发挥）

TopHumanWriting 的“像范文”，不是靠更大的模型或更玄学的 prompt，而是靠三件事把发散按死：

1) **检索先行（RAG）**  
   任何建议都先从范文库里检索出证据片段（带 PDF+页码）。  
   你的每一次修改，都有“同领域顶级人类文本”作为锚点。

2) **白箱输出**  
   每条问题都尽量结构化：
   - 问题是什么（issue）
   - 为什么像 AI / 为什么不像范文（diagnosis）
   - 参考哪段范文（evidence：pdf/page/quote）
   - 推荐怎么改（template / rewrite hints）

3) **受控生成（temperature=0 + token 预算）**  
   LLM 的角色是“模拟专业研究生审稿”，而不是“文风创作家”。  
   通过 `--max-llm-tokens` 给单次运行上限，避免无止境发散与成本失控。

---

## 它会检查哪些“不像范文”的点？

你可以把它理解成：派 N 个研究生从不同角度同时检查你的论文写作。

目前后端已经覆盖这些方向（并会继续扩展）：

- **句子层面**：对齐度低句（最不像范文的句子优先）
- **段落层面（LLM 分治）**：段落推进方式、topic sentence、证据句与结论句的比例
- **章节结构（LLM 分治）**：章节标题体系是否像同领域范文（缺什么段、顺序怪不怪）
- **引用风格（LLM 分治 + 证据）**：author-year 引用句的常见写法、措辞与风险点
- **引用核查（CiteCheck）**：你说“谁发现了什么”是否张冠李戴（给出证据段落）
- **词汇/表达习惯（程序 + 可选 LLM 解读）**：你使用的词是否偏离范文库常见用法、是否有典型 AI 过渡词密集

关键点是：它不会只告诉你“差”，而是告诉你“像谁”“怎么像”。

---

## 它怎么跑？（新手也能照抄）

### 1) 安装

最小安装：

```bash
pip install tophumanwriting
```

需要做范文检索（推荐）：

```bash
pip install "tophumanwriting[rag]"
```

或者使用 FAISS：

```bash
pip install "tophumanwriting[rag-faiss]"
```

### 2) 配置 LLM（可选，但强烈建议开）

只认这三个环境变量（OpenAI-compatible）：

- `TOPHUMANWRITING_LLM_API_KEY`（或 `SKILL_LLM_API_KEY`）
- `TOPHUMANWRITING_LLM_BASE_URL`（通常以 `/v1` 结尾）
- `TOPHUMANWRITING_LLM_MODEL`

PowerShell 示例：

```powershell
$env:TOPHUMANWRITING_LLM_API_KEY="sk-..."
$env:TOPHUMANWRITING_LLM_BASE_URL="https://your-provider.example/v1"
$env:TOPHUMANWRITING_LLM_MODEL="gemini-3-flash"
```

### 3) 一条命令端到端

```bash
thw run --paper main.pdf --exemplars reference_papers --max-llm-tokens 200000
```

### 4) 建库一次 → 反复体检（真实工作流推荐）

```bash
thw models download-semantic
thw library build --name reference_papers --pdf-root reference_papers
thw audit run --paper main.pdf --library reference_papers --max-llm-tokens 200000
```

---

## 为什么这份报告“更可信”？（我建议你这样用）

把它当成“可复核的审稿助理”，而不是“替你写论文的黑箱”：

1) 先看报告里“最不像范文”的句子/段落清单（它会优先排序）  
2) 点开对应的范文证据（PDF + 页码 + 引用片段）  
3) 按它给的模板建议改一轮  
4) 再跑下一轮体检（同一个 `series_id` 会尽量避免重复盯一个地方）  

你会看到一个很接近真实写作过程的现象：  
**问题会被逐轮清掉，风格会越来越像范文，而不是越来越像 AI。**

---

## 写在最后：我们不是“让你像 AI”，而是“让你更像人类范文”

TopHumanWriting 想做的事情很朴素：

- 让你在写作时能像人工对照一样，有范文背书、有证据链
- 让“润色”变成一个可审计、可迭代的过程（而不是一次性黑箱改写）
- 让你用 AI 的效率，但保留同领域顶级人类写作的“职业气味”

如果你也受够了“AI 味”的指责、受够了改到最后不知道自己写了什么，  
那就把你的范文库和你的 PDF 丢给它，让它先替你把“最不像范文”的地方揪出来。

---

**项目 GitHub： https://github.com/bluesHeart/TopHumanWriting**  
**PyPI： https://pypi.org/project/tophumanwriting/**

