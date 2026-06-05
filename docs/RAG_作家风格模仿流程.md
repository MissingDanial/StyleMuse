# RAG 作家风格模仿流程

## 概述

本流程用于构建一个 RAG 系统，模仿任意作家的写作风格生成新文章。

**输入**: 作家的 epub/txt 语料文件
**输出**: 可调用的风格写作 Skill，持续生成该作家风格的文章

---

## 流程总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        阶段一：语料准备                          │
│  epub/txt 文件 → 解析 → 碎片化切分 → 清洗 → 存储                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        阶段二：向量库构建                         │
│  文本 → Embedding API → 向量 → FAISS 索引 → 持久化               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        阶段三：Prompt 设计                        │
│  系统 Prompt → 反抄袭约束 → 风格指南 → User Prompt                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        阶段四：Skill 封装                        │
│  RAG Chain → 检索逻辑 → 生成逻辑 → 对外接口                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        阶段五：验证调优                          │
│  生成测试 → 复制检测 → 参数调优 → 交付                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 阶段一：语料准备

### 步骤 1.1：准备原始语料

**任务**: 收集目标作家的作品文件

**支持格式**:
- `.txt` 纯文本文件（UTF-8 编码）
- `.epub` 电子书文件

**目录结构建议**:
```
PROJECT_ROOT/
└── corpus/
    ├── 作者名.txt          # 汇总的纯文本
    └── epub_data/          # epub 文件目录
        ├── 书名1.epub
        ├── 书名2.epub
        └── 书名3.epub
```

**注意事项**:
- epub 文件需包含完整的章节内容
- 版权问题：仅用于学习研究

---

### 步骤 1.2：解析 epub 文件

**任务**: 将 epub 解析为纯文本

**实现方式**: 使用 `BeautifulSoup` 配合 XML 解析器

**关键代码模式**:

```python
from bs4 import BeautifulSoup
import zipfile
import re

def parse_epub(epub_path: str) -> str:
    """解析 epub 文件为纯文本"""
    with zipfile.ZipFile(epub_path, 'r') as z:
        # epub 本质是 zip 文件
        html_files = [f for f in z.namelist() if f.endswith('.xhtml') or f.endswith('.html')]
        texts = []
        for html_file in html_files:
            content = z.read(html_file)
            soup = BeautifulSoup(content, 'xml')  # 注意：用 xml 解析器
            # 提取 <p> 标签内的文本
            for p in soup.find_all('p'):
                text = p.get_text().strip()
                if text:
                    texts.append(text)
        return '\n'.join(texts)
```

**输出**: 解析后的纯文本内容

---

### 步骤 1.3：文本清洗

**任务**: 去除噪音，保留正文

**常见噪音**:
- 章节编号（"第一章"、"Chapter 1"）
- 脚注、尾注
- 空行、多余空格
- 特殊字符

**示例清洗逻辑**:

```python
def clean_text(text: str) -> str:
    # 去除多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去除首尾空白
    text = text.strip()
    # 过滤过短的片段（可能是注释）
    lines = [line for line in text.split('\n') if len(line) > 20]
    return '\n'.join(lines)
```

---

### 步骤 1.4：碎片化切分

**任务**: 将长文本切分为小 chunk

**关键参数**:

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `chunk_size` | 100 字符 | 越小越碎片化，复制风险越低 |
| `chunk_overlap` | 20 字符 | 相邻 chunk 重叠，保持上下文 |
| `separators` | `["\n", "。", "！", "？", "，"]` | 按句子/段落切分 |

**实现方式**: 使用 LangChain `RecursiveCharacterTextSplitter`

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    separators=["\n", "。", "！", "？", "，", "、", "；", "："],
    chunk_size=100,
    chunk_overlap=20,
    length_function=len,
)
chunks = splitter.split_text(text)
```

**输出**: `List[str]`，每个元素是一个 100 字符左右的碎片

---

## 阶段二：向量库构建

### 步骤 2.1：选择 Embedding 模型

**推荐模型**:

| 模型 | 提供商 | 维度 | 说明 |
|------|--------|------|------|
| `text-embedding-v3` | 阿里千问 | 1024 | 推荐，支持批量 |
| `text-embedding-v2` | 阿里千问 | 768 | 已deprecated |
| `embedding-3-large` | OpenAI | 3072 | 效果好但收费 |

**API 获取地址**:
- 千问: https://dashscope.console.aliyun.com/
- OpenAI: https://platform.openai.com/

---

### 步骤 2.2：调用 Embedding API

**批量嵌入**:

```python
class QwenEmbeddings:
    def __init__(self, api_key: str, model: str = "text-embedding-v3"):
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        from dashscope import TextEmbedding
        results = []
        batch_size = 10  # API 限制每批最多 10 条
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            call = TextEmbedding.call(
                model=self.model,
                api_key=self.api_key,
                input=batch
            )
            if call.status_code == 200:
                for item in call.output['embeddings']:
                    results.append(item['embedding'])
        return results
```

---

### 步骤 2.3：构建向量索引

**推荐使用 FAISS**（支持本地持久化）

```python
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# 准备文档
split_docs = [
    Document(page_content=chunk, metadata={"source": "书名", "title": "片段标题"})
    for chunk in chunks
]

# 创建向量库
embeddings = QwenEmbeddings(api_key=API_KEY)
vector_store = FAISS.from_documents(split_docs, embeddings)

# 持久化到本地
vector_store.save_local("data/embeddings")
```

---

### 步骤 2.4：向量库持久化结构

**FAISS 目录结构**:

```
data/embeddings/
├── index.faiss    # FAISS 索引文件（向量）
└── index.pkl     # 元数据（原始文本 + metadata）
```

**加载向量库**:

```python
def load_vector_store(api_key: str):
    embeddings = QwenEmbeddings(api_key=api_key)
    if os.path.exists("data/embeddings/index.faiss"):
        return FAISS.load_local("data/embeddings", embeddings,
                                 allow_dangerous_deserialization=True)
    else:
        raise FileNotFoundError("向量库不存在，请先构建")
```

---

## 阶段三：Prompt 设计

### 步骤 3.1：设计系统 Prompt

**核心组成部分**:

```
1. 角色定义：你是一位XX风格的作家

2. 风格特点：
   - 幽默哲学化
   - 第一人称沉浸
   - 朴实诗意
   - 拟人化万物
   - 时间感

3. 叙事技巧：
   - 从具体到抽象
   - 长句铺陈
   - 重复与排比
   - 留白与节制

4. 写作禁忌：
   - 过于学术化
   - 直白情感宣泄
   - 生硬哲理说教

5. 参考片段（RAG 检索结果）

6. 【关键】反抄袭约束：
   - 严禁复制参考资料的任何完整句子或完整段落
   - 必须原创改写
   - 生成内容与任一参考片段的连续重复字符不得超过 15 个
```

---

### 步骤 3.2：设计用户 Prompt

**模板**:

```
请用XX的风格，写一篇关于「{topic}」的散文。

风格要求：{tone}
字数要求：约 {length} 字

请学习XX的风格手法，原创改写，严禁复制任何完整句子。
```

---

### 步骤 3.3：Few-shot 示例（可选）

**提供 2-3 个高质量示例**，帮助模型更好理解风格

```markdown
## 示例

【示例1：主题"故乡的狗"】
开头：故乡的狗都认得回家的路...
结尾：...

【示例2：主题"春天的风"】
开头：...
结尾：...
```

---

## 阶段四：Skill 封装

### 步骤 4.1：RAG 检索逻辑

**核心流程**:

```python
def retrieve_relevant_context(topic: str, top_k: int = 3) -> str:
    """
    1. 加载向量库
    2. 相似度搜索（多取 3 倍）
    3. 过滤高相似度（>85% 相似度过滤）
    4. 打乱顺序
    5. 返回 top_k 条
    """
    vector_store = load_vector_store(api_key)
    results = vector_store.similarity_search_with_score(topic, k=top_k * 3)

    # 过滤高相似度
    filtered = [doc for doc, score in results if score >= 0.15]

    # 打乱
    import random
    random.shuffle(filtered)

    # 返回
    return "\n\n".join([doc.page_content for doc in filtered[:top_k]])
```

---

### 步骤 4.2：生成逻辑

```python
class WritingSkill:
    def __init__(self, model: str = "MiniMax-M2.7",
                 temperature: float = 1.0,
                 max_tokens: int = 2500):
        from langchain_anthropic import ChatAnthropic
        self.llm = ChatAnthropic(
            model=model,
            api_key=API_KEY,
            base_url=BASE_URL,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def write(self, topic: str, tone: str = "default",
              length: str = "medium") -> str:
        # 1. 检索相关上下文
        context = retrieve_relevant_context(topic)

        # 2. 构建 Prompt
        system_prompt = build_system_prompt(topic, context, tone, length)
        user_prompt = build_user_prompt(topic, tone, length)

        # 3. 调用 LLM
        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        # 4. 处理响应
        return response.content
```

---

### 步骤 4.3：对外接口

**封装为可复用 Skill**:

```python
from skills.author_name import WritingSkill

# 初始化
skill = WritingSkill()

# 单篇生成
article = skill.write(
    topic="故乡的狗",
    tone="humorous",      # 风格选项
    length="medium"       # 长度选项
)

# 批量生成
articles = skill.write_batch(
    topics=["故乡的狗", "春天的风", "老家的树"]
)
```

---

## 阶段五：验证调优

### 步骤 5.1：生成测试

```bash
python main.py --topic "测试主题" --tone default --length medium
```

---

### 步骤 5.2：复制检测

**人工检测方法**:
- 对比生成文章与原始语料的相似段落
- 检查是否存在连续 15+ 字符重复

**辅助检测脚本**:

```python
def check_plagiarism(generated: str, corpus_files: List[str]) -> float:
    """检测生成内容与语料的重复率"""
    import difflib
    # ... 实现略
    return similarity_score
```

---

### 步骤 5.3：参数调优

**关键调优参数**:

| 参数 | 调整方向 | 影响 |
|------|----------|------|
| `chunk_size` | 减小 → | 更碎片化，复制更少 |
| `SIMILARITY_THRESHOLD` | 增大 → | 过滤更多高相似度 |
| `RETRIEVAL_TOP_K` | 减小 → | 更少上下文，原创更多 |
| `TEMPERATURE` | 增大 → | 更有创意，更少复制 |

---

## 文件结构模板

```
PROJECT_ROOT/
├── skills/
│   └── author_name/              # 以作家名命名
│       ├── __init__.py
│       ├── config.py             # 所有配置参数
│       ├── epub_loader.py        # epub 解析
│       ├── loader.py             # 文本加载 + 分块
│       ├── rag_chain.py          # RAG 核心逻辑
│       ├── style_prompt.py       # Prompt 模板
│       └── writer.py             # Skill 封装
├── corpus/                       # 原始语料
│   ├── 作者名.txt
│   └── epub_data/
│       └── *.epub
├── data/
│   ├── chunks/                   # 切分后的 chunk 缓存
│   │   └── chunks.json
│   └── embeddings/               # FAISS 向量库
│       ├── index.faiss
│       └── index.pkl
├── prompts/                      # Prompt 模板文件
│   ├── style_guide.md
│   └── few_shot_examples.md
├── main.py                       # 命令行入口
└── .env                          # API 密钥
```

---

## 配置文件模板 (config.py)

```python
"""
【必改参数】

API 密钥（必须设置）:
- MINIMAX_API: 大模型 API Key
- KWEN_API: Embedding API Key

【可改参数】

模型参数:
- DEFAULT_MODEL: 生成模型 (默认 MiniMax-M2.7)
- EMBEDDING_MODEL: Embedding 模型 (默认 text-embedding-v3)
- TEMPERATURE: 生成温度 (默认 1.0)
- MAX_TOKENS: 最大生成长度 (默认 2500)

检索参数:
- CHUNK_SIZE: 碎片大小 (默认 100)
- CHUNK_OVERLAP: 重叠大小 (默认 20)
- RETRIEVAL_TOP_K: 检索数量 (默认 3)
- SIMILARITY_THRESHOLD: 相似度阈值 (默认 0.15)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ============ API 密钥 ============
MINIMAX_API = os.environ.get("MINIMAX_API")
KWEN_API = os.environ.get("KWEN_API")
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"

# ============ 模型参数 ============
DEFAULT_MODEL = "MiniMax-M2.7"
EMBEDDING_MODEL = "text-embedding-v3"
EMBEDDING_DIM = 1024
MAX_TOKENS = 2500
TEMPERATURE = 1.0

# ============ 检索参数 ============
CHUNK_SIZE = 100
CHUNK_OVERLAP = 20
RETRIEVAL_TOP_K = 3
SIMILARITY_THRESHOLD = 0.15
RETRIEVAL_MULTIPLIER = 3

# ============ 路径 ============
VECTOR_STORE_PERSIST_DIR = PROJECT_ROOT / "data" / "embeddings"
CORPUS_FILE = PROJECT_ROOT / "corpus" / "作者名.txt"
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
```

---

## 快速开始 checklist

```
[ ] 1. 准备语料：corpus/ 目录下放入 epub 或 txt 文件
[ ] 2. 配置密钥：在 .env 中设置 MINIMAX_API 和 KWEN_API
[ ] 3. 修改 config.py：确认 CORPUS_FILE 路径正确
[ ] 4. 构建向量库：首次运行自动创建（耗时约 30 分钟）
[ ] 5. 测试生成：python main.py --topic "测试主题"
[ ] 6. 验证质量：检查是否复制原文
[ ] 7. 调优参数：根据需要调整 chunk_size、threshold 等
```

---

## 常见问题

**Q: 向量库构建太慢？**
A: 15,294 个 chunks 约需 30 分钟（Qwen API 限速）。可以减小 `CHUNK_SIZE` 减少数量，或联系 API 服务商提升配额。

**Q: 生成仍然复制原文？**
A: 1) 减小 `CHUNK_SIZE` 到 50；2) 增大 `SIMILARITY_THRESHOLD` 到 0.2；3) 增大 `TEMPERATURE` 到 1.2

**Q: 如何更换作家？**
A: 1) 替换 corpus/ 目录下的语料文件；2) 修改 `config.py` 中的 `CORPUS_FILE`；3) 删除 `data/embeddings/` 重建向量库；4) 更新 `style_prompt.py` 中的作家风格描述
