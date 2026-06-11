"""
通用作家风格仿写 Skill - RAG Chain 实现

支持任意作家，通过 AuthorStyleSkill 类统一管理。
支持所有兼容 OpenAI API 的大模型。
"""

import json
import random
from pathlib import Path
from typing import List, Dict, Iterator

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from .config import (
    get_author_dir,
    load_author_config,
    get_llm,
    get_embeddings,
)
from .loader import load_all_chunks
from .style_prompt import build_system_prompt, build_user_prompt
from .logger import get_logger
from .llm_logger import invoke_with_logging, stream_with_logging
from .plagiarism import check_plagiarism
from .reviewer import review_article

log = get_logger(__name__)


def extract_text_from_response(content) -> str:
    """
    从 LLM 响应中提取纯文本内容。

    Args:
        content: response.content，可能是 str 或 list

    Returns:
        提取后的纯文本
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                text_parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts)

    return str(content)


class QwenEmbeddings(Embeddings):
    """千问 Embeddings 自定义实现（DashScope API）"""

    def __init__(self, api_key: str, model: str = "text-embedding-v3"):
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入文档"""
        from dashscope import TextEmbedding

        results = []
        batch_size = 10
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            call = TextEmbedding.call(model=self.model, api_key=self.api_key, input=batch)
            if call.status_code == 200:
                for item in call.output["embeddings"]:
                    results.append(item["embedding"])
            else:
                raise RuntimeError(f"Embedding failed: {call.message}")
        return results

    def embed_query(self, text: str) -> List[float]:
        """嵌入单个查询"""
        from dashscope import TextEmbedding

        call = TextEmbedding.call(model=self.model, api_key=self.api_key, input=text)
        if call.status_code == 200:
            return call.output["embeddings"][0]["embedding"]
        else:
            raise RuntimeError(f"Embedding failed: {call.message}")


def create_vector_store(documents: List[Document], embeddings: Embeddings, persist_dir: Path):
    """创建向量存储"""
    from langchain_community.vectorstores import FAISS

    persist_dir.mkdir(parents=True, exist_ok=True)
    vector_store = FAISS.from_documents(documents, embeddings)
    vector_store.save_local(str(persist_dir))
    return vector_store


def load_vector_store(embeddings: Embeddings, persist_dir: Path):
    """加载已有的向量存储"""
    from langchain_community.vectorstores import FAISS

    index_file = persist_dir / "index.faiss"
    if index_file.exists():
        return FAISS.load_local(str(persist_dir), embeddings, allow_dangerous_deserialization=True)
    return None


def retrieve_relevant_context(
    topic: str,
    vector_store,
    top_k: int = 3,
    similarity_threshold: float = 0.15,
    retrieval_multiplier: int = 3,
) -> str:
    """从向量库中检索相关上下文"""
    if vector_store is None:
        return ""

    try:
        results_with_scores = vector_store.similarity_search_with_score(topic, k=top_k * retrieval_multiplier)

        filtered = []
        for doc, score in results_with_scores:
            # FAISS returns a distance score here: lower means more relevant.
            if score <= similarity_threshold:
                filtered.append(doc)

        random.shuffle(filtered)
        final_results = filtered[:top_k]

        if not final_results:
            return ""

        contexts = []
        for i, doc in enumerate(final_results, 1):
            title = doc.metadata.get("title", f"片段 {i}")
            contexts.append(f"【{title}】\n{doc.page_content}")

        return "\n\n".join(contexts)
    except Exception as e:
        log.error(f"检索过程出现错误: {e}")
        return ""


class AuthorStyleSkill:
    """
    通用作家风格仿写 Skill

    支持任意作家，通过作家名称加载对应的工作空间配置。
    支持所有兼容 OpenAI API 的大模型。
    """

    def __init__(self, author_name: str, model: str = None, max_tokens: int = None, temperature: float = None):
        """
        初始化 Skill。

        Args:
            author_name: 作家名称（对应 authors/ 下的目录名）
            model: 模型名称（可选，覆盖作家配置）
            max_tokens: 最大生成长度（可选）
            temperature: 生成温度（可选）
        """
        self.author_name = author_name
        self.author_dir = get_author_dir(author_name)

        if not self.author_dir.exists():
            raise FileNotFoundError(f"作家 '{author_name}' 不存在，请先使用 create 命令创建")

        # 加载配置
        self.config = load_author_config(author_name)

        # 允许参数覆盖
        if model:
            self.config["llm_model"] = model
        if max_tokens:
            self.config["max_tokens"] = max_tokens
        if temperature is not None:
            self.config["temperature"] = temperature

        # 加载风格指南
        self.style_guide = self._load_style_guide()
        self.few_shot = self._load_few_shot()

        # 初始化 LLM（使用配置中的 provider）
        self.llm = get_llm(self.config)

        # 向量库（懒加载）
        self._vector_store = None
        self._embeddings = None
        self.last_plagiarism_result = None
        self.last_review_result = None

    def _load_style_guide(self) -> str:
        """加载风格指南"""
        path = self.author_dir / "style_guide.md"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _load_few_shot(self) -> str:
        """加载 Few-shot 示例"""
        path = self.author_dir / "few_shot.md"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _get_embeddings(self):
        """获取 Embedding 实例（懒加载）"""
        if self._embeddings is None:
            self._embeddings = get_embeddings(self.config)
        return self._embeddings

    def _get_vector_store(self):
        """获取向量库（懒加载，不存在则自动创建）"""
        if self._vector_store is None:
            embeddings = self._get_embeddings()
            cache_dir = self.author_dir / "cache" / "faiss"

            self._vector_store = load_vector_store(embeddings, cache_dir)
            if self._vector_store is None:
                log.info(f"向量库不存在，正在为 '{self.author_name}' 创建...")
                documents = load_all_chunks(
                    self.author_dir,
                    chunk_size=self.config["chunk_size"],
                    chunk_overlap=self.config["chunk_overlap"],
                )
                if documents:
                    self._vector_store = create_vector_store(documents, embeddings, cache_dir)
                    log.info(f"向量库创建完成: {len(documents)} 个文本块")
                else:
                    log.warning("警告: 无文档可索引")

        return self._vector_store

    def write(
        self,
        topic: str,
        tone: str = "default",
        length: str = "medium",
        include_retrieval: bool = True,
    ) -> str:
        """
        模仿作家风格写作。

        Args:
            topic: 写作主题
            tone: 风格选项
            length: 长度选项
            include_retrieval: 是否使用 RAG 检索

        Returns:
            生成的文章内容
        """
        tone_options = self.config.get("tone_options", {})
        length_options = self.config.get("length_options", {})

        tone_value = tone_options.get(tone, tone_options.get("default", tone))
        length_value = length_options.get(length, length_options.get("medium", length))

        # 检索相关上下文
        retrieved_context = ""
        if include_retrieval:
            try:
                vector_store = self._get_vector_store()
                if vector_store:
                    retrieved_context = retrieve_relevant_context(
                        topic,
                        vector_store,
                        top_k=self.config["retrieval_top_k"],
                        similarity_threshold=self.config["similarity_threshold"],
                        retrieval_multiplier=self.config["retrieval_multiplier"],
                    )
            except Exception as e:
                log.error(f"检索失败: {e}")

        # 构建 Prompt
        system_prompt = build_system_prompt(
            topic=topic,
            style_guide=self.style_guide,
            retrieved_context=retrieved_context,
            tone=tone_value,
            length=length_value,
        )

        user_prompt = build_user_prompt(
            topic=topic,
            tone=tone_value,
            length=length_value,
        )

        # 生成
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = invoke_with_logging(
            self.llm,
            messages,
            step="write",
            logger=log,
            metadata={
                "author": self.author_name,
                "topic": topic,
                "tone": tone,
                "length": length,
                "include_retrieval": include_retrieval,
                "retrieved_context_chars": len(retrieved_context),
                "llm_provider": self.config.get("llm_provider"),
                "llm_model": self.config.get("llm_model"),
            },
        )
        article = extract_text_from_response(response.content)
        self._run_post_generation_checks(article, topic, tone, length)
        return article

    def write_stream(
        self,
        topic: str,
        tone: str = "default",
        length: str = "medium",
        include_retrieval: bool = True,
    ) -> Iterator[str]:
        """
        模仿作家风格写作（流式输出）。

        Args:
            topic: 写作主题
            tone: 风格选项
            length: 长度选项
            include_retrieval: 是否使用 RAG 检索

        Yields:
            生成的文章内容片段
        """
        tone_options = self.config.get("tone_options", {})
        length_options = self.config.get("length_options", {})

        tone_value = tone_options.get(tone, tone_options.get("default", tone))
        length_value = length_options.get(length, length_options.get("medium", length))

        # 检索相关上下文
        retrieved_context = ""
        if include_retrieval:
            try:
                vector_store = self._get_vector_store()
                if vector_store:
                    retrieved_context = retrieve_relevant_context(
                        topic,
                        vector_store,
                        top_k=self.config["retrieval_top_k"],
                        similarity_threshold=self.config["similarity_threshold"],
                        retrieval_multiplier=self.config["retrieval_multiplier"],
                    )
            except Exception as e:
                log.error(f"检索失败: {e}")

        # 构建 Prompt
        system_prompt = build_system_prompt(
            topic=topic,
            style_guide=self.style_guide,
            retrieved_context=retrieved_context,
            tone=tone_value,
            length=length_value,
        )

        user_prompt = build_user_prompt(
            topic=topic,
            tone=tone_value,
            length=length_value,
        )

        # 流式生成
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        article_chunks = []
        for chunk in stream_with_logging(
            self.llm,
            messages,
            step="write_stream",
            logger=log,
            metadata={
                "author": self.author_name,
                "topic": topic,
                "tone": tone,
                "length": length,
                "include_retrieval": include_retrieval,
                "retrieved_context_chars": len(retrieved_context),
                "llm_provider": self.config.get("llm_provider"),
                "llm_model": self.config.get("llm_model"),
            },
        ):
            text = extract_text_from_response(chunk.content)
            if text:
                article_chunks.append(text)
                yield text
        self._run_post_generation_checks("".join(article_chunks), topic, tone, length)

    def rewrite(
        self,
        original_article: str,
        review: dict,
        topic: str,
        tone: str = "default",
        length: str = "medium",
        include_retrieval: bool = True,
    ) -> str:
        """Rewrite an article using review feedback while preserving author style."""
        tone_options = self.config.get("tone_options", {})
        length_options = self.config.get("length_options", {})
        tone_value = tone_options.get(tone, tone_options.get("default", tone))
        length_value = length_options.get(length, length_options.get("medium", length))

        retrieved_context = ""
        if include_retrieval:
            try:
                vector_store = self._get_vector_store()
                if vector_store:
                    retrieved_context = retrieve_relevant_context(
                        topic,
                        vector_store,
                        top_k=self.config["retrieval_top_k"],
                        similarity_threshold=self.config["similarity_threshold"],
                        retrieval_multiplier=self.config["retrieval_multiplier"],
                    )
            except Exception as e:
                log.error(f"检索失败: {e}")

        system_prompt = build_system_prompt(
            topic=topic,
            style_guide=self.style_guide,
            retrieved_context=retrieved_context,
            tone=tone_value,
            length=length_value,
        )

        review_json = json.dumps(review or {}, ensure_ascii=False, indent=2)
        user_prompt = f"""请根据审稿 Agent 的意见重写下面这篇文章。

要求：
1. 保持主题：{topic}
2. 保持语气：{tone_value}
3. 目标长度：{length_value}
4. 修正审稿意见指出的问题。
5. 避免照抄原文或作家语料中的连续句子。
6. 只输出重写后的正文，不要解释。

审稿意见：
{review_json[:4000]}

原文：
{original_article}
"""

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = invoke_with_logging(
            self.llm,
            messages,
            step="rewrite",
            logger=log,
            metadata={
                "author": self.author_name,
                "topic": topic,
                "tone": tone,
                "length": length,
                "include_retrieval": include_retrieval,
                "retrieved_context_chars": len(retrieved_context),
                "original_article_chars": len(original_article or ""),
                "review_decision": (review or {}).get("decision"),
                "review_score": (review or {}).get("score"),
                "llm_provider": self.config.get("llm_provider"),
                "llm_model": self.config.get("llm_model"),
            },
        )
        article = extract_text_from_response(response.content)
        self._run_post_generation_checks(article, topic, tone, length)
        return article

    def _load_source_documents(self) -> List[Document]:
        """Load author chunks once for post-generation checks."""
        return load_all_chunks(
            self.author_dir,
            chunk_size=self.config["chunk_size"],
            chunk_overlap=self.config["chunk_overlap"],
        )

    def _run_post_generation_checks(self, article: str, topic: str, tone: str, length: str):
        """Run plagiarism and review checks after generation."""
        try:
            source_documents = self._load_source_documents()
        except Exception as e:
            log.error(f"post-generation source loading failed: {e}")
            source_documents = []
        self.last_plagiarism_result = self._check_plagiarism(article, source_documents)
        self.last_review_result = self._review_article(
            article=article,
            topic=topic,
            tone=tone,
            length=length,
            source_documents=source_documents,
        )

    def _check_plagiarism(self, article: str, source_documents: List[Document] = None) -> dict:
        """Run best-effort duplicate-text detection against author chunks."""
        if not self.config.get("plagiarism_check_enabled", True):
            return None
        try:
            source_documents = source_documents if source_documents is not None else self._load_source_documents()
            result = check_plagiarism(
                generated=article,
                source_documents=source_documents,
                max_common_len=self.config.get("max_common_len", 15),
                similarity_threshold=self.config.get("plagiarism_similarity_threshold", 0.6),
            )
            if not result.get("passed"):
                log.warning(result.get("warning", "生成内容未通过重复检测"))
            return result
        except Exception as e:
            log.error(f"重复检测失败: {e}")
            return {"passed": None, "warning": f"重复检测失败: {e}"}

    def _review_article(
        self,
        article: str,
        topic: str,
        tone: str,
        length: str,
        source_documents: List[Document],
    ) -> dict:
        """Run the independent review agent after generation."""
        if not self.config.get("review_agent_enabled", True):
            return None
        try:
            return review_article(
                author_name=self.author_name,
                topic=topic,
                tone=tone,
                length=length,
                article=article,
                source_documents=source_documents,
                style_guide=self.style_guide,
                few_shot=self.few_shot,
                plagiarism_result=self.last_plagiarism_result,
                config=self.config,
                llm=self.llm,
            )
        except Exception as e:
            log.error(f"review agent failed: {e}")
            return {"passed": None, "decision": "unknown", "warning": f"review agent failed: {e}"}

    def write_batch(self, topics: List[str], **kwargs) -> Dict[str, str]:
        """
        批量生成多篇文章。

        Args:
            topics: 主题列表
            **kwargs: write 方法的其他参数

        Returns:
            {主题: 文章内容} 的字典
        """
        results = {}
        for topic in topics:
            results[topic] = self.write(topic, **kwargs)
        return results
