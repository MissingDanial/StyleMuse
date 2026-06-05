# StyleMuse ✍️

> 上传任意作家的作品，AI 自动学习其写作风格，生成原创仿写散文。

StyleMuse 是一个基于 RAG（检索增强生成）的作家风格仿写系统。只需上传 epub 或 txt 文件，系统会自动分析写作风格、构建向量索引，然后模仿该作家的笔触生成原创文章。

支持所有兼容 OpenAI API 的大模型（DeepSeek、通义千问、智谱、MiniMax、OpenAI 等），模型列表完全由用户自定义。

## ✨ 功能特性

- **一键创建作家 Skill** — 上传 epub/txt，自动分析风格、构建向量索引
- **风格分析** — 基础统计 + LLM 深度分析，自动生成风格指南和示例片段
- **RAG 检索增强** — 从作家作品中检索相关片段，辅助生成更地道的仿写
- **防抄袭机制** — 碎片化分块 + 相似度过滤 + Prompt 约束，确保原创性
- **多模型支持** — DeepSeek / 通义千问 / 智谱 / MiniMax / OpenAI 等，用户可自由配置
- **Web 界面** — 可视化操作，支持文件上传、在线写作、模型配置、文件下载
- **CLI 命令行** — 适合批量生成和脚本集成
- **Python API** — 可作为库集成到其他项目

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/<your-username>/stylemuse.git
cd stylemuse
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置模型

复制配置模板并填入你的 API 密钥：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# 大语言模型（以 DeepSeek 为例）
LLM_PROVIDER=openai
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx

# Embedding 模型（千问）
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_API_KEY=sk-xxxxxxxxxxxxxxxx
```

也可以启动后在 Web 界面的「模型配置」页面直接编辑。

### 4. 启动

```bash
# Web 界面
python app.py
# 浏览器访问 http://localhost:5000

# 或使用 CLI
python main.py write --author liu_liangcheng --topic "故乡的狗"
```

## 📖 使用方式

### Web 界面

启动 `python app.py` 后访问 http://localhost:5000：

**写作工坊** — 选择作家 → 输入主题 → 选择风格/长度 → 点击写作 → 复制或下载

**作家管理** — 上传 epub/txt 文件创建新作家，查看已有作家列表

**模型配置** — 编辑 `.env` 配置，管理自定义模型列表（点击模型可快速填入配置）

### CLI

```bash
# 创建作家（自动分析风格 + 构建向量索引）
python main.py create --name "鲁迅" --source ./luxun_files/

# 仿写
python main.py write --author "鲁迅" --topic "故乡" --tone philosophical --length long

# 列出所有作家
python main.py list

# 查看作家详情
python main.py info --author liu_liangcheng

# 删除作家
python main.py delete --author "鲁迅"
```

### Python API

```python
from skills.author_style import AuthorStyleSkill, create_author

# 创建新作家
create_author("鲁迅", source_path="./luxun_files/")

# 加载并写作
skill = AuthorStyleSkill("鲁迅")
article = skill.write(topic="故乡", tone="philosophical", length="medium")

# 批量生成
results = skill.write_batch(topics=["故乡", "风筝", "药"], tone="sharp")
```

## 🏗️ 项目结构

```
stylemuse/
├── app.py                           # Flask Web 服务
├── main.py                          # CLI 入口
├── requirements.txt
├── .env.example                     # 配置模板
│
├── static/                          # 前端
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── skills/
│   └── author_style/                # 核心 Skill 包
│       ├── config.py                # 配置（多模型支持）
│       ├── loader.py                # 文本加载与分块
│       ├── epub_loader.py           # EPUB 解析
│       ├── analyzer.py              # 风格分析器
│       ├── rag_chain.py             # RAG 链 + LLM
│       ├── style_prompt.py          # Prompt 模板
│       └── author_manager.py        # 作家管理
│
├── authors/                         # 作家工作空间
│   └── liu_liangcheng/              # 演示案例
│
└── docs/                            # 技术文档
```

每位作家拥有独立工作空间：

```
authors/<name>/
├── config.json          # 作家配置
├── style_guide.md       # 风格指南
├── few_shot.md          # 示例片段
├── works/               # txt 语料
├── epub/                # epub 书籍
├── cache/               # 向量索引 + 缓存
└── output/              # 生成的文章
```

## ⚙️ 支持的模型

所有兼容 OpenAI API 的模型均可使用。默认预置以下模型（可在 Web 界面自由增删）：

| 模型 | Provider | Model Name | Base URL |
|------|----------|------------|----------|
| DeepSeek | openai | deepseek-chat | https://api.deepseek.com/v1 |
| 通义千问 | openai | qwen-turbo | https://dashscope.aliyuncs.com/compatible-mode/v1 |
| 智谱 GLM | openai | glm-4-flash | https://open.bigmodel.cn/api/paas/v4 |
| MiniMax | anthropic | MiniMax-M2.7 | https://api.minimaxi.com/anthropic |
| OpenAI | openai | gpt-4o | (默认) |

在 Web 界面的「模型配置」页面可以：
- 点击模型卡片快速填入配置
- 添加自定义模型（任意 OpenAI 兼容接口）
- 删除不需要的模型
- 直接编辑 `.env` 中的 API 密钥等配置

## 🛡️ 防抄袭机制

1. **碎片化分块** — 每个文本块仅 100 字，避免大段复制
2. **相似度过滤** — 过滤掉相似度 > 85% 的片段
3. **随机打乱** — 打乱检索结果顺序，降低模仿痕迹
4. **Prompt 约束** — 明确要求 LLM 严禁整句照搬，连续重复字符不超过 15 个

## 🎯 演示案例

内置刘亮程（中国当代散文家）的完整数据作为演示：

```bash
python main.py write --author liu_liangcheng --topic "故乡的狗"
python main.py write --author liu_liangcheng --topic "春天的风" --tone humorous
python main.py write --author liu_liangcheng --topic "老家的树" --length long
```

## 📄 License

MIT
