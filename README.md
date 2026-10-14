# StyleMuse

StyleMuse 是一个作家风格辅助写作与来源复用检查工具。它从 TXT / EPUB 资料建立风格指南和示例，支持可选 RAG 检索、后台生成、审稿重写、版本管理，以及生成文与原文的证据对照。

当前推荐组合为 **DeepSeek 写作 + 本地中文 BGE 嵌入**。RAG 用于提供参考片段，语义检查用于召回复核候选；两者使用独立索引。系统不保证 RAG 一定改善质量，也不把相似分数作为抄袭判定。

## 当前功能

- **作家工作区**：上传 TXT / EPUB、分析风格、建立索引；归档和恢复作家资料。
- **写作与重写**：选择主题、语气和篇幅，开关 RAG；查看审稿意见后主动重写。
- **后台任务**：SQLite 保存写作、重写和导入任务，刷新后继续查询；完整正文已保存时，可继续检查或导出。
- **作品历史**：搜索、查看、比较和恢复版本；恢复生成新版本，保留原稿。
- **文字复用检查**：对照完整来源、指南、固定示例和本次检索片段，展示连续重合、近似片段、覆盖范围与原文位置。
- **语义候选与人工复核**：本地 BGE 召回句段来源，逐条保存“表达复用 / 独立写作 / 引用 / 不确定”及依据，绑定具体版本。
- **模型配置**：支持 OpenAI 兼容聊天接口、Anthropic 兼容接口；嵌入可选本地 BGE、DashScope 或 OpenAI 兼容嵌入服务。
- **CLI 与测试工具**：命令行写作，离线回归、真实模型测评、RAG 消融与语义候选校准脚本。

## 快速开始

推荐 Python **3.12** 和独立虚拟环境。首次安装需要网络下载依赖和公开模型；正常写作请求只读取已下载的嵌入模型。Node.js 仅用于前端测试。

### 1. 获取代码并安装

```bash
git clone https://github.com/MissingDanial/StyleMuse.git
cd StyleMuse
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -r requirements-local-embeddings.lock
Copy-Item .env.example .env
```

Linux / macOS：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -r requirements-local-embeddings.lock
cp .env.example .env
```

`requirements.lock` 固定应用依赖，`requirements-local-embeddings.lock` 固定本地嵌入依赖。对应 `.txt` 文件用于维护直接依赖。

### 2. 配置模型

编辑 `.env`，将 `LLM_API_KEY` 替换为自己的密钥；模型名以账户实际可用列表为准：

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your_api_key_here

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_CACHE_DIR=.stylemuse/models
EMBEDDING_LOCAL_ONLY=1
```

写作服务和嵌入服务分别配置。本地嵌入运行于 CPU，不需要嵌入 API 密钥；写作模型仍按其服务规则计费。作者 `config.json` 的覆盖字段优先于全局配置。把密钥放在本地 `.env`，不要写入随仓库发布的示例配置。

### 3. 下载模型并初始化演示索引

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/setup_local_embeddings.py --download --configure
.\.venv\Scripts\python.exe -X utf8 scripts/verify_rag.py
.\.venv\Scripts\python.exe -X utf8 app.py
```

Linux / macOS，在已激活的虚拟环境中：

```bash
python scripts/setup_local_embeddings.py --download --configure
python scripts/verify_rag.py
python app.py
```

打开 [http://localhost:5000](http://localhost:5000)。初始化和默认检索验证不调用付费写作模型。`--configure` 保存嵌入设置并保留已有写作配置；模型下载后，重复初始化可省略 `--download`。

内置 `liu_liangcheng` 演示配置已开启本地语义候选检查，因此首次写作还会建立独立句段索引，耗时高于后续请求。详细安装、云端替代方案和阈值说明见 [嵌入配置](docs/EMBEDDINGS.md)。

## 使用方式

### Web

1. 在“作家管理”导入有权使用的 TXT / EPUB，或选择内置演示作家。
2. 在写作页面选择主题、语气、篇幅和是否启用 RAG，提交后台任务。
3. 查看正文、篇幅检查、审稿意见及原文对照片段。
4. 在作品历史打开具体版本，逐条记录来源复用意见；需要修改时按审稿意见重写或恢复历史版本。

`short / medium / long` 默认范围分别是 400–500、800–1000、1500–2000 字；检查口径为去掉空白后的字符数，包含标点和标题。生成模型可能不满足范围，超出范围会单独显示失败，综合高分不能覆盖这一结果。

规则审稿默认开启，额外 LLM 审稿默认关闭，可在作者配置中设置 `review_llm_enabled=true`。关闭 RAG 后仍使用风格指南和固定示例，也仍执行来源检查。人工意见保存在独立记录中，不修改机器检查或原文。

修改模型配置后重启服务；采用独立 worker 时也需重启 worker。

### CLI

下文的 `python` 指虚拟环境解释器。Windows 未激活环境时，使用 `.\.venv\Scripts\python.exe` 替换。

```bash
# 查看作家
python main.py list
python main.py info --author liu_liangcheng

# 写作：会调用所配置的写作模型
python main.py write --author liu_liangcheng --topic "故乡的狗" --length short
python main.py write --author liu_liangcheng --topic "深夜的地铁" --no-retrieval

# 从自己的目录导入作家；默认执行模型风格分析和索引构建
python main.py create --name my_author --source ./my_texts/

# 归档作家，随后可用输出的编号恢复
python main.py delete --author my_author
python main.py restore --archive-id <归档编号>
```

正文及 `.review.json`、`.version.json` 保存到当前作家的 `output/`。`create --no-analyze --no-index` 可只导入资料。所有命令参数见 `python main.py --help`。

## 检查结果如何解读

| 状态 | 含义 |
|---|---|
| `low_risk` | 在已加载资料与已执行方法的范围内，没有超过告警阈值的证据 |
| `needs_review` | 发现近似片段、语义候选、多处短片段复用，或部分检查未完成，需要复核 |
| `high_risk` | 触发较长连续文字重合等工程告警，需要对照来源处理 |
| `not_checked` | 缺少可检查内容或检查未完成 |

当前连续重合阈值是工程参数；相似度和覆盖比例都不能换算成“抄袭概率”或“原创率”。检查范围为已加载的作家资料、指南与示例，不覆盖全网；整篇情节结构尚无自动判定。

语义模块采用对称编码的本地 BGE。构造留出集的 18 个复用样本都召回了预期来源，但 12 个独立写作样本中有 7 个也触发候选，说明它适合安排复核，区分能力仍需改进。小规模构造集没有独立人工标注，不能外推为真实文章准确率。

已有 RAG 消融测试包含 48 篇生成样本。在该作者、主题和配置范围内，没有观察到明确质量增益；这不证明 RAG 对其他任务冗余。当前保留 RAG 开关，后续在清洁语料上继续比较。方法、限制和复现方式见 [RAG 消融](docs/RAG_ABLATION.md)、[文字复用检查](docs/ORIGINALITY.md)、[语义候选与人工复核](docs/SEMANTIC_REUSE.md)。

## 部署与任务恢复

本机运行默认绑定 `127.0.0.1`。局域网、Docker、反向代理等共享部署需在 `.env` 配置独立的 `STYLEMUSE_ACCESS_TOKEN`，浏览器使用该密码登录，脚本可发送 `Authorization: Bearer <访问密码>`。HTTPS 反向代理部署设置 `STYLEMUSE_HTTPS=1`。

Waitress 启动：

```bash
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 --call app:create_app
```

Docker Compose 使用宿主机 `.env`、`authors/`、`.stylemuse/` 和 `logs/`。配置好 `.env` 和访问密码后：

```bash
docker compose build --build-arg INSTALL_LOCAL_EMBEDDINGS=1
# 首次部署下载模型；已有模型挂载时可跳过
# 容器 .env 只读，因此此处不使用 --configure
docker compose run --rm stylemuse python scripts/setup_local_embeddings.py --download
docker compose up -d
docker compose logs -f
```

容器配置变更需编辑宿主机 `.env` 并重新创建容器。Docker 使用固定依赖；本轮尚未实际验证镜像构建和容器运行。

默认 Web 进程启动后台任务线程。需要单独运行任务进程时，在 `.env` 设置 `STYLEMUSE_EXTERNAL_WORKER=1`，分别启动 Web 与 `python scripts/worker.py`。

进程中断后，任务在心跳超时后标记为中断，不自动重复生成正文。完整正文已保存时可继续检查或保存；不完整正文作为草稿保留。取消为协作式取消，正在执行的模型请求可能需等待返回。接口与恢复规则见 [后台任务和作品历史](docs/BACKGROUND_TASKS.md)。

## 测试与实验

以下检查不调用付费模型，不要求真实 API 密钥；前端测试需要 Node.js 22 或以上：

```bash
python -m pytest -q
node --test tests/frontend.test.cjs tests/tasks.test.cjs
python scripts/smoke_check.py
python scripts/smoke_check.py --production
```

GitHub Actions 执行同组检查。本地 BGE 初始化后可运行 `python scripts/verify_rag.py` 验证检索，或运行候选校准：

```bash
python scripts/evaluate_semantic_reuse.py --output .stylemuse/semantic-evaluation/local
```

浏览器 QA 使用模拟写作服务，需要安装 `requirements-browser.txt` 和浏览器；配置见 [浏览器测试](tests/README.md)。

真实模型测评会产生 API 费用，仅在显式运行相应脚本时执行：

| 工具 | 说明 |
|---|---|
| `scripts/live_deepseek_eval.py` | 业务模拟与质量盲评，默认最多 20 次 completion；见 [真实模型测试](docs/LIVE_TESTING.md) |
| `scripts/rag_ablation.py` | 48 篇生成 + 32 次顺序敏感性盲评，默认最多 80 次 completion；见 [实验协议](docs/RAG_ABLATION.md) |
| `scripts/verify_rag.py --live` | 单次真实 RAG 写作验证 |
| `scripts/audit_originality.py` | 对已有实验样本重新做本地来源检查 |

仓库保留实验脚本、自编校准样本和运行时所需的校准配置，原始响应、生成稿、索引与报告输出保留在本地。真实测评脚本使用原公开提交中的固定素材，需要完整 Git 历史；不能直接在仅下载的 ZIP 中运行。

## 目录与数据

```text
StyleMuse/
├── app.py / main.py              # Web 与 CLI 入口
├── skills/author_style/          # 写作、来源检查、索引、任务和版本模块
├── static/                      # 写作、任务和历史界面
├── authors/liu_liangcheng/       # 随仓库发布的演示资料
├── benchmarks/semantic_reuse/    # 自编测试集与运行时校准 profile
├── scripts/                     # 初始化、worker、验证和实验脚本
├── tests/                       # Python、JavaScript 与浏览器 QA
├── docs/                        # 配置、接口、实验和能力说明
├── requirements*.txt / *.lock   # 直接依赖与固定安装清单
└── .github/workflows/check.yml  # 不调用付费模型的 CI
```

| 本地路径 | 用途 | Git 行为 |
|---|---|---|
| `.env`、`env_test*.txt` | 本机密钥与配置 | 忽略，仅发布 `.env.example` |
| `.stylemuse/` | SQLite 状态、模型、测评与报告输出 | 忽略 |
| `authors/<自建作家>/` | 私人配置、上传资料、生成稿与索引 | 默认忽略 |
| `authors/liu_liangcheng/` | 演示配置、指南、示例和指定 TXT | 仅保留四个公开演示文件，其他文件忽略 |
| `logs/`、`*.log`、`.tmp/` | 日志与临时文件 | 忽略 |
| `.venv/`、`.uv-cache/`、测试缓存 | 依赖与可重建数据 | 忽略 |

Git 仓库不包含运行数据备份。迁移或备份应用时，另行保存 `.env`、完整 `authors/` 和 `.stylemuse/`；个人作家资料不会随普通 `git add` 上传。Docker 构建同样排除密钥、私有工作区和缓存，通过挂载读取运行数据。

## 文档导航

- [嵌入模型安装与阈值](docs/EMBEDDINGS.md)
- [任务、版本与恢复接口](docs/BACKGROUND_TASKS.md)
- [文字来源检查与篇幅约束](docs/ORIGINALITY.md)
- [语义召回、误触发与人工复核](docs/SEMANTIC_REUSE.md)
- [真实模型测试](docs/LIVE_TESTING.md) / [RAG 消融协议](docs/RAG_ABLATION.md)
- [此前代码优化记录](docs/OPTIMIZATIONS.md) / [早期 RAG 设计说明](docs/RAG_作家风格模仿流程.md)

## 许可与语料

原项目 README 标注代码采用 MIT 许可，仓库尚未提供独立 `LICENSE` 文件。内置文学作品及上传资料与代码许可分开；使用与再发布资料前需确认相应权限。演示汇总中的来源分类和作者归属标签仅用于初筛，不能代替来源审核。
