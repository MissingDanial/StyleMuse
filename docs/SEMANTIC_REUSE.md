# 本地语义候选与人工复核

2026-10-14。第二阶段已接入独立句段索引、本地 BGE 候选召回、原文对齐和版本绑定的人工复核。当前示例作家已启用。候选相似分用于安排复核，不自动判定抄袭或保证原创。

## 当前流程

1. 完整原文、固定示例与指南切成原位置可追溯的句子和相邻句段。单段最多约 220 个字符，短片段不足 18 个有效字符不单独编码；保留上下文窗口。文讯等来源仍参与复用比对。
2. 生成与来源两侧使用同一个本地 `BAAI/bge-small-zh-v1.5` 直接编码，不加风格检索的 query 指令。向量归一化后计算余弦相似度。
3. 按生成句段召回最多五个不同来源，每个来源保留最匹配的原文窗口。相同生成区间的冗余对齐合并，每篇最多展示十二条语义候选。超出展示或处理范围会记录截断状态。
4. 对照实际生成片段、原文、标题、来源和位置，逐条记录具体表达复用、仅主题或物件相近、明确引用或尚无法判断，并填写复核依据。
5. 复核记录独立保存到 SQLite，绑定作品版本与证据哈希。修改意见追加历史，不改写生成文、原来的机器审稿或文字告警。刷新、重开版本后可读取最新意见。

BGE 模型卡说明，语义相似等任务不必使用检索 query 指令，阈值也应根据具体数据分布选择，而非套用一个通用数值：[官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)。风格 RAG 的 FAISS 距离阈值与本模块的余弦阈值属于不同配置。

## 校准与实际能力

`benchmarks/semantic_reuse/cases.json` 含十二个自主编写的来源家族、六十个构造样本，覆盖直接复制、特定事件改述、同主题独立写作、相同物件不同事件、明确引用。前六组用于开发，后六组用于留出检验；来源家族不跨集合。多来源拼接另有程序回归用例。

标签由构造过程确定，没有独立人工标注。它们测量候选来源召回，不是法律或真实散文的抄袭标签。明确引用也算来源复用，是否适当引用另行复核。

候选阈值 **0.683140** 只根据开发集选择，目标是尽量召回复用来源。留出结果如下：

| 项目 | 结果 |
|---|---:|
| 复用样本召回预期来源 | 18/18 |
| 其中，具体事件改述 | 6/6 |
| 独立写作触发候选 | 7/12 |
| 相同物件、不同事件触发候选 | 6/6 |
| 同主题独立写作触发候选 | 1/6 |

这组结果支持候选召回，也直接暴露了区分能力不足：保留物件、改变事件的负例仍容易被判为语义接近。BGE 单独不能可靠决定是否复用了特定表达，因此采用人工复核，所有候选保持待确认。小规模构造集和小候选池也不能代表更大的作者语料库；后续需要加入真实干扰来源、人工标注与语义精排。

新规则对已有 48 篇做了本地复查：1 篇仍为明确文字复用高风险，46 篇产生需复核候选，1 篇没有超过阈值的文字或语义候选。共保存 495 条已展示语义候选。46 篇不能解释为“46 篇抄袭”，也没有据此重算原消融实验的主观质量结论。来源句段索引包含 6,073 个窗口，与风格 RAG 的 678 个片段分开。

## 配置和缓存

作家 `config.json`：

```json
{
  "semantic_reuse_enabled": true,
  "semantic_reuse_profile": "benchmarks/semantic_reuse/profile.json"
}
```

全局默认关闭，当前 `liu_liangcheng` 已开启；未安装本地可选依赖的作家仍可使用文字检查。开启后只使用本地 CPU BGE 和已下载模型，写作期间不下载模型，也不回退到云端嵌入。

独立索引位于作家的 `cache/semantic-reuse`。缓存绑定来源内容、位置、句段版本及模型名称，保存为不加载 pickle 的 NumPy 数组，原子写入；来源变化后重建，损坏缓存可重建。首次建库需要编码完整来源，之后复用缓存；返回 `index_seconds`、`query_seconds`、`cache_hit` 便于观察耗时。更换模型或句段算法后需重新校准。

如果本地模型或校准配置不可用，状态进入需复核，不能因语义检查失败而显示通过。文字长段重合告警始终保留。

## 数据与接口

`plagiarism.semantic` 保存模型、阈值、来源与生成窗口数量、候选、缓存和截断状态。`methods.semantic=candidates_only` 表示完成候选召回；`semantic_adjudication=pending` 表示尚待判断。`structure` 当前仍为 `not_checked`，没有自动完成整篇情节结构判断。

每条候选有 `evidence_id`，由来源、双方实际片段与位置、模型及证据类型确定。位置口径延续文字检查：从零开始、右端不包含的 Unicode 字符区间。

保存人工意见：

```text
POST /api/versions/<version_id>/reuse-review
{
  "evidence_id": "该版本保存的证据哈希",
  "decision": "reuse | independent | quotation | uncertain",
  "note": "最多 2000 字的复核依据"
}
```

`GET /api/versions/<version_id>` 返回 `reuse_reviews` 与 `reuse_review_summary`。证据必须属于该版本；无效证据返回 409，输入无效返回 400。接口沿用访问密码与跨站写入检查。人工意见只覆盖已展示候选，即使全部填写也不能证明来源库外或整篇结构没有复用。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_semantic_reuse.py --output .stylemuse/semantic-evaluation/v1
.\.venv\Scripts\python.exe scripts/audit_originality.py --samples .stylemuse/ablations/rag-20261014/samples.json --output .stylemuse/ablations/rag-20261014/originality-v3-semantic --semantic
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/frontend.test.cjs tests/tasks.test.cjs
.\.venv\Scripts\python.exe tests/run_browser_qa.py
```

这些命令使用本地嵌入或模拟写作服务，没有新增付费生成或评审调用。原 80 次调用记录和原样本保持独立，复查输出写入新目录。

## 后续优先级

先补真实语料干扰下的人工标注与事件层面对齐/精排，降低候选误触发和复核工作量。然后在清洁语料上运行四条件 RAG 消融，并将质量、篇幅、文字复用、语义人工意见分别报告；当前候选相似分不能作为自动原创性评分。
