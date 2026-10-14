# 写作模型与本地嵌入配置

本项目推荐 DeepSeek 聊天接口配合本地 `BAAI/bge-small-zh-v1.5`：DeepSeek 负责写作，本地模型负责 RAG 和语义候选召回。两种服务分别配置，不能把聊天接口的地址和密钥直接当成嵌入配置。

本地嵌入采用 [BGE 模型](https://huggingface.co/BAAI/bge-small-zh-v1.5) 的 [FastEmbed ONNX 实现](https://qdrant.github.io/fastembed/examples/Supported_Models/)，运行于 CPU，输出归一化的 512 维向量。嵌入计算不上传语料，不需要云端嵌入密钥；首次安装会下载公开依赖和模型权重。写作请求仍会把提示和其中引用的资料发送给所配置的写作服务。

## 新环境安装

从项目根目录执行。Windows PowerShell：

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

只在新环境复制模板，不覆盖自己已经配置的 `.env`。填入写作模型的 `LLM_API_KEY`，确认模型名称和地址与服务商账户匹配。本地嵌入配置如下：

```dotenv
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_CACHE_DIR=.stylemuse/models
EMBEDDING_LOCAL_ONLY=1
```

Windows 初始化与验证：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/setup_local_embeddings.py --download --configure
.\.venv\Scripts\python.exe -X utf8 scripts/verify_rag.py
```

Linux / macOS 在已激活的虚拟环境中使用相同脚本：

```bash
python scripts/setup_local_embeddings.py --download --configure
python scripts/verify_rag.py
```

初始化会验证中文向量、建立演示作家的真实 FAISS 索引，再验证离线加载，不调用聊天模型。`--configure` 更新 `.env` 和指定作家的嵌入覆盖设置，保留原有写作配置。为其他已存在的作家初始化时加 `--author <目录名>`。

只有显式 `--download` 允许初始化脚本下载模型。下载完成后可省略该参数；写作期间始终只读本地文件，模型缺失会报错。配置变更后重启 Web 和独立 worker。

## 作者覆盖与云端替代方案

作者级 `config.json` 优先于 `.env`。内置 `liu_liangcheng` 的嵌入已配置为本地 BGE，并开启语义候选。切换云端 RAG 时应同时更改该作家的 `embedding_provider`、`embedding_model`、`embedding_base_url` 和 `embedding_api_key` 覆盖字段；个人密钥优先放在本地 `.env`，不要写入随仓库发布的示例文件。

RAG 嵌入支持 `dashscope` 或 `openai` 兼容服务，必须提供该嵌入服务自己的模型、地址与密钥。项目拒绝将官方 DeepSeek 聊天地址误设为嵌入服务。支持聊天 API 格式，不代表同时支持 `/embeddings`。

只使用云端 RAG 时，核心依赖可单独安装 `requirements.lock`。语义候选检查仍使用本地 BGE；需要启用它时仍须安装本地依赖和下载权重。若希望只使用文字检查，在作者配置设置 `"semantic_reuse_enabled": false`。关闭 RAG 开关不会关闭语义候选检查。

## RAG 阈值与缓存

FAISS 使用单位向量的**平方 L2 距离**，值越小越相似；它不是余弦相似度：

```text
distance = 2 - 2 × cosine
```

示例作家的 `similarity_threshold=1.0` 对应余弦至少 0.5，是少量主题校准得到的检索阈值，不能照搬到所有作者或领域。距离超限的候选会被过滤；没有合格片段时，写作会提示并使用指南和固定示例。

RAG 文档不加指令，短主题查询使用中文检索指令。模型、查询指令、语料内容或来源元数据变化会使旧索引失效，索引完整构建后才更新活动版本。缓存位于 `authors/<作家>/cache/faiss/`，可以重建，不进入 Git 或 Docker 镜像。

语料整理后的演示汇总拆为 35 条记录，其中 29 条通过风格检索分类初筛，得到 678 个 RAG 片段。数量随语料和分块规则变化，以初始化报告为准；来源分类不能替代作者归属审核。

语义候选模块使用独立句段索引和双侧无指令编码，其余弦阈值来自 `benchmarks/semantic_reuse/profile.json`。该文件是运行时配置，随代码提交；两种阈值不能混用。能力和误触发见 [语义候选与人工复核](SEMANTIC_REUSE.md)。

## 验证与报告

```bash
# 本地检索与负例检查，不调用写作模型
python scripts/verify_rag.py

# 显式付费选项：最多一次真实写作、零重试、关闭额外 LLM 审稿
python scripts/verify_rag.py --live
```

Windows 使用虚拟环境解释器代替上述 `python`。初始化报告写入 `.stylemuse/embedding-setup.json`，检索报告写入 `.stylemuse/rag-verification.json`。真实调用另存正文、用量、查重和审稿资料。输出保留在本地，不随 Git 提交。

检索接入验证不等于写作质量提升；质量比较见 [RAG 消融协议](RAG_ABLATION.md)。普通 pytest、CI 和默认验证不执行付费调用。

## Docker

在宿主机准备好 `.env` 和访问密码。容器 `.env` 为只读，本地模型与索引通过 Compose 挂载持久化：

```bash
docker compose build --build-arg INSTALL_LOCAL_EMBEDDINGS=1
docker compose run --rm stylemuse python scripts/setup_local_embeddings.py --download
docker compose up -d
```

已有模型和有效索引时可跳过初始化。不要在只读配置容器中使用 `--configure`；需要改设置时编辑宿主机 `.env` 和对应作者覆盖字段，然后重新创建容器。本轮没有实际验证 Docker 构建与运行。
