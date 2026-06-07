# AI 产品技术简历 - StyleMuse 项目版

## 基本信息

姓名：XXX  
目标岗位：AI 产品经理 / AI 应用工程师 / AI 产品技术顾问  
项目方向：RAG 应用、LLM 工作流、AI 内容生成、Prompt Engineering、AI 产品原型落地  

## 个人简介

具备 AI 产品设计与工程实现能力，能够从用户场景出发，将大模型能力拆解为可运行的产品工作流。独立完成 StyleMuse 作家风格仿写系统，覆盖语料上传、文本解析、风格分析、向量检索、Prompt 编排、流式生成、模型配置、内容下载、Docker 部署与自动化测试，形成 Web、CLI、Python API 三种使用入口。

熟悉 LangChain、RAG、Embedding、FAISS、本地知识库构建、OpenAI 兼容模型接入、Flask API 设计、前端交互实现和 pytest 测试。项目关注 AI 生成内容的可控性、安全性与产品可用性，设计了反抄袭约束、生成后重复检测、路径安全校验和删除确认机制。

## 技术栈

Python、Flask、LangChain、FAISS、DashScope Embedding、OpenAI Compatible API、Anthropic Compatible API、BeautifulSoup、ebooklib、JavaScript、HTML/CSS、Docker、Docker Compose、pytest

## 项目经历

### StyleMuse - 基于 RAG 的作家风格学习与仿写系统

项目角色：AI 产品设计 / 后端开发 / RAG 工程实现 / Web 原型开发  
项目周期：个人项目  
项目定位：上传任意作家 txt 或 epub 语料，系统自动学习写作风格，构建向量索引，并生成原创仿写散文。

#### 核心功能

- 设计并实现作家工作区机制，每位作家拥有独立的 `config.json`、语料目录、风格指南、Few-shot 示例、FAISS 向量索引、生成结果目录。
- 实现 txt 与 epub 语料解析，支持中文文本清洗、章节抽取、元数据保存和缓存复用。
- 基于 LangChain `RecursiveCharacterTextSplitter` 设计碎片化分块策略，默认 100 字符 chunk、20 字符 overlap，降低生成时直接复制原文的风险。
- 接入 DashScope `text-embedding-v3` Embedding，并使用 FAISS 构建本地向量库，实现作家语料的相似片段检索。
- 实现 RAG 写作链路：主题输入 -> 检索相关片段 -> 注入风格指南 -> 构建系统 Prompt 和用户 Prompt -> 调用 LLM -> 保存生成文章。
- 支持 DeepSeek、通义千问、智谱 GLM、MiniMax、OpenAI 等 OpenAI/Anthropic 兼容接口，用户可在 Web 页面中配置模型名称、Base URL 和 API Key。
- 实现 Web 写作工坊、作家管理、模型配置三大页面，支持文件上传、在线生成、SSE 流式输出、复制、下载、自定义保存路径。
- 提供 CLI 命令，支持创建作家、生成文章、列出作家、查看详情、删除作家，便于批处理和脚本集成。
- 提供 Dockerfile 与 docker-compose 配置，支持 `.env`、`authors/`、`logs/` 的容器化挂载与持久化。

#### AI 产品与工程亮点

- 将“模仿作家风格”拆解为可执行产品链路：语料准备、风格分析、索引构建、检索增强、Prompt 编排、生成、检测、交付。
- 通过基础统计 + LLM 深度分析自动生成 `style_guide.md` 和 `few_shot.md`，把隐性的写作风格转化为可复用 Prompt 资产。
- 在生成链路中加入反抄袭机制：Prompt 约束、碎片化检索、随机打乱参考片段、最长公共子串检测、整体相似度检测。
- 设计多模型抽象层，支持不同供应商模型在同一套 UI 和 RAG 链路中切换，降低模型迁移成本。
- 对文件上传、保存路径、作家名称、下载文件名做安全校验，避免路径穿越和非法文件名问题。
- 为删除作家工作区增加显式确认与单次最多删除文件数限制，降低误删风险。
- 使用 pytest 覆盖 API、配置读写、模型列表、下载接口、文本统计、响应解析、路径安全等关键逻辑；本地验证结果为 52 passed。

#### 可量化结果

- 支持 Web、CLI、Python API 三种访问方式。
- 支持 txt、epub 两类语料输入。
- 支持至少 5 类主流大模型配置：DeepSeek、通义千问、智谱 GLM、MiniMax、OpenAI。
- 已实现 52 条自动化测试并通过。
- 支持 Docker Compose 一键部署。

## 代表性技术方案

### RAG 架构

输入语料先经过 txt/epub 解析和文本切分，生成带元数据的 LangChain Document；随后通过 DashScope Embedding 生成向量并持久化到 FAISS。写作时根据主题召回相关片段，将片段、风格指南、长度、语气要求组合为 Prompt，再调用大模型生成内容。

### Prompt 与风格资产

系统将作家风格拆成三类资产：基础统计、风格指南、Few-shot 示例。Prompt 中明确区分“可借鉴的风格手法”和“禁止复制的原文内容”，让模型学习句式、节奏、意象和修辞，而不是直接搬运句子。

### 生成安全

项目没有只依赖 Prompt 约束，而是在生成后继续执行重复检测：计算生成内容与原始语料的最长公共子串，并用 `SequenceMatcher` 检查片段相似度，超过阈值时返回警告结果，便于产品侧提示用户重新生成或人工复核。

## 可面试展开点

- 为什么 RAG 检索不宜直接塞入大段原文，而采用小 chunk、低 top_k 和随机打乱。
- 如何在多模型场景下设计 provider、model、base_url、api_key 的配置结构。
- 如何把文学风格分析产品化，避免 Prompt 只停留在人工描述。
- 如何平衡“像某位作家”与“不要复制原文”之间的产品边界。
- 如何通过 Web、CLI、API 三种入口提升 AI 应用的可用性和可集成性。

## 简历精简版项目描述

StyleMuse 是一个基于 RAG 的作家风格学习与仿写系统，支持上传 txt/epub 语料后自动解析文本、分析作家风格、构建 FAISS 向量库，并通过 LangChain 编排检索增强生成链路，输出原创仿写散文。项目支持 DeepSeek、通义千问、智谱、MiniMax、OpenAI 等 OpenAI/Anthropic 兼容模型，提供 Web、CLI、Python API 和 Docker Compose 部署方式。为降低照搬原文风险，系统实现了碎片化分块、Prompt 反抄袭约束、检索结果过滤与生成后最长公共子串检测，并通过 52 条 pytest 用例覆盖核心 API、配置读写、路径安全和文本处理逻辑。

