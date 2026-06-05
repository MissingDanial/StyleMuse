"""
刘亮程风格写作 Skill - RAG Chain 实现
"""

import random
from typing import List, Dict

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from .config import (
    get_api_key,
    get_kwen_api_key,
    MINIMAX_BASE_URL,
    DEFAULT_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
    RETRIEVAL_TOP_K,
    VECTOR_STORE_PERSIST_DIR,
    SIMILARITY_THRESHOLD,
    RETRIEVAL_MULTIPLIER,
)
from .loader import load_all_chunks
from .style_prompt import build_system_prompt, build_user_prompt, TONE_OPTIONS, LENGTH_OPTIONS


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
            if hasattr(block, 'type') and block.type == 'text':
                text_parts.append(block.text)
            elif isinstance(block, dict) and block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
        return '\n'.join(text_parts)

    return str(content)


class QwenEmbeddings(Embeddings):
    """千问 Embeddings 自定义实现"""

    def __init__(self, api_key: str, model: str = "text-embedding-v3"):
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入文档"""
        from dashscope import TextEmbedding

        results = []
        # DashScope API 每次最多 10 条
        batch_size = 10
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
            else:
                raise RuntimeError(f"Embedding failed: {call.message}")
        return results

    def embed_query(self, text: str) -> List[float]:
        """嵌入单个查询"""
        from dashscope import TextEmbedding

        call = TextEmbedding.call(
            model=self.model,
            api_key=self.api_key,
            input=text
        )
        if call.status_code == 200:
            return call.output['embeddings'][0]['embedding']
        else:
            raise RuntimeError(f"Embedding failed: {call.message}")


def create_vector_store(documents: List[Document], kwen_api_key: str):
    """
    创建向量存储（使用 FAISS 持久化存储）

    Args:
        documents: Document 对象列表
        kwen_api_key: 千问 API Key

    Returns:
        VectorStore 对象
    """
    from langchain_community.vectorstores import FAISS
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    # 碎片化切分：每个 chunk 不超过 100 字
    splitter = RecursiveCharacterTextSplitter(
        separators=["\n", "。", "！", "？", "，", "、", "；", "："],
        chunk_size=100,
        chunk_overlap=20,
        length_function=len,
    )

    # 展平并切分所有文档
    all_texts = []
    all_metadatas = []
    for doc in documents:
        splits = splitter.split_text(doc.page_content)
        for split in splits:
            all_texts.append(split)
            all_metadatas.append(doc.metadata)

    # 创建新的文档列表
    split_docs = [Document(page_content=t, metadata=m) for t, m in zip(all_texts, all_metadatas)]

    embeddings = QwenEmbeddings(api_key=kwen_api_key)

    VECTOR_STORE_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    vector_store = FAISS.from_documents(split_docs, embeddings)
    vector_store.save_local(str(VECTOR_STORE_PERSIST_DIR))

    return vector_store


def load_vector_store(kwen_api_key: str):
    """
    加载或创建向量存储

    Args:
        kwen_api_key: 千问 API Key

    Returns:
        VectorStore 对象
    """
    from langchain_community.vectorstores import FAISS

    index_path = str(VECTOR_STORE_PERSIST_DIR)

    # 如果索引已存在，直接加载
    if (VECTOR_STORE_PERSIST_DIR / "index.faiss").exists():
        embeddings = QwenEmbeddings(api_key=kwen_api_key)
        return FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

    # 否则创建新索引
    documents = load_all_chunks()
    return create_vector_store(documents, kwen_api_key)


def retrieve_relevant_context(topic: str, top_k: int = RETRIEVAL_TOP_K) -> str:
    """
    检索与主题相关的上下文

    Args:
        topic: 写作主题
        top_k: 检索数量

    Returns:
        检索到的相关段落拼接成的字符串
    """

    kwen_api_key = get_kwen_api_key()

    if not kwen_api_key:
        print("警告: 未找到 KWEN_API，跳过 RAG 检索")
        return ""

    try:
        vector_store = load_vector_store(kwen_api_key)

        # 多取 RETRIEVAL_MULTIPLIER 倍结果，用于过滤和打乱
        results_with_scores = vector_store.similarity_search_with_score(topic, k=top_k * RETRIEVAL_MULTIPLIER)

        # 过滤高相似度片段（score >= SIMILARITY_THRESHOLD 表示相似度 <= 85%）
        filtered = []
        for doc, score in results_with_scores:
            if score >= SIMILARITY_THRESHOLD:
                filtered.append(doc)

        # 打乱顺序
        random.shuffle(filtered)

        # 取最终 top_k
        final_results = filtered[:top_k]

        if not final_results:
            return ""

        contexts = []
        for i, doc in enumerate(final_results, 1):
            title = doc.metadata.get('title', f'片段 {i}')
            contexts.append(f"【{title}】\n{doc.page_content}")

        return "\n\n".join(contexts)
    except Exception as e:
        print(f"检索过程出现错误: {e}")
        return ""


class LiuLiangchengSkill:
    """
    刘亮程风格写作 Skill

    提供基于 RAG 的刘亮程风格文章生成能力
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
        top_k: int = RETRIEVAL_TOP_K,
    ):
        """
        初始化 Skill

        Args:
            model: 使用的模型名称
            max_tokens: 最大生成长度
            temperature: 生成温度
            top_k: 检索返回数量
        """
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_k = top_k

        # 初始化 LLM
        from langchain_anthropic import ChatAnthropic

        api_key = get_api_key()
        if not api_key:
            raise ValueError("未找到 MINIMAX_API，请设置环境变量或在注册表中配置")

        self.llm = ChatAnthropic(
            model=model,
            api_key=api_key,
            base_url=MINIMAX_BASE_URL,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def write(
        self,
        topic: str,
        tone: str = "default",
        length: str = "medium",
        include_retrieval: bool = True,
    ) -> str:
        """
        模仿刘亮程风格写作

        Args:
            topic: 写作主题
            tone: 风格选项 ("default", "humorous", "philosophical", "poetic", "simple")
            length: 长度选项 ("short", "medium", "long")
            include_retrieval: 是否使用 RAG 检索

        Returns:
            生成的文章内容
        """
        # 获取 tone 和 length 的实际值
        tone_value = TONE_OPTIONS.get(tone, TONE_OPTIONS["default"])
        length_value = LENGTH_OPTIONS.get(length, LENGTH_OPTIONS["medium"])

        # 检索相关上下文
        retrieved_context = ""
        if include_retrieval:
            retrieved_context = retrieve_relevant_context(topic, self.top_k)

        # 构建 Prompt
        system_prompt = build_system_prompt(
            topic=topic,
            retrieved_context=retrieved_context,
            tone=tone_value,
            length=length_value
        )

        user_prompt = build_user_prompt(
            topic=topic,
            tone=tone_value,
            length=length_value
        )

        # 生成
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = self.llm.invoke(messages)
        return extract_text_from_response(response.content)

    def write_batch(
        self,
        topics: List[str],
        **kwargs
    ) -> Dict[str, str]:
        """
        批量生成多篇文章

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


# 便捷函数
def write(topic: str, **kwargs) -> str:
    """
    便捷函数：模仿刘亮程风格写作

    Args:
        topic: 写作主题
        **kwargs: LiuLiangchengSkill.write 的其他参数

    Returns:
        生成的文章内容
    """
    skill = LiuLiangchengSkill()
    return skill.write(topic, **kwargs)
