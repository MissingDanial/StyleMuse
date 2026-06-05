// ============================================================================
// 作家风格仿写 Skill - 前端逻辑
// ============================================================================

const API = "";

// ============================================================================
// 工具
// ============================================================================

async function api(url, opts = {}) {
    const resp = await fetch(API + url, {
        headers: { "Content-Type": "application/json", ...opts.headers },
        ...opts,
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "请求失败");
    return data.data;
}

function showToast(msg, type = "info") {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = `toast ${type}`;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.add("hidden"), 3000);
}

const $ = (id) => document.getElementById(id);

// ============================================================================
// Tab 切换
// ============================================================================

document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
        const tab = item.dataset.tab;
        document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
        item.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        $(`tab-${tab}`).classList.add("active");

        if (tab === "authors") loadAuthors();
        if (tab === "settings") { loadConfig(); loadModels(); }
        if (tab === "write") loadAuthorSelect();
    });
});

// ============================================================================
// 作家管理
// ============================================================================

let authorsCache = [];

async function loadAuthors() {
    try {
        const authors = await api("/api/authors");
        authorsCache = authors;
        renderAuthors(authors);
    } catch (e) {
        $("authors-list").innerHTML = `<div class="loading">加载失败: ${e.message}</div>`;
    }
}

function renderAuthors(authors) {
    if (!authors.length) {
        $("authors-list").innerHTML = '<div class="loading">暂无作家，请在上方创建</div>';
        return;
    }
    $("authors-list").innerHTML = authors.map((a) => `
        <div class="author-card">
            <div class="author-info">
                <h4>${a.name}</h4>
                <div class="meta">
                    <span class="${a.has_style_guide ? "badge-ok" : "badge-no"}"></span>
                    <span class="${a.has_vector_store ? "badge-ok" : "badge-no"}"> 向量库</span>
                    <span>${a.txt_files} txt / ${a.epub_files} epub</span>
                </div>
            </div>
            <div class="author-actions">
                <button class="btn btn-small" onclick="selectAuthorForWrite('${a.name}')">写作</button>
                <button class="btn btn-small btn-danger" onclick="handleDelete('${a.name}')">删除</button>
            </div>
        </div>
    `).join("");
}

function selectAuthorForWrite(name) {
    document.querySelector('[data-tab="write"]').click();
    loadAuthorSelect().then(() => { $("write-author").value = name; });
}

async function handleCreate() {
    const name = $("create-name").value.trim();
    const files = $("create-files").files;
    if (!name) return showToast("请输入作家名称", "error");
    if (!files.length) return showToast("请上传至少一个文件", "error");

    const btn = event.target;
    btn.disabled = true; btn.textContent = "创建中...";

    try {
        const fd = new FormData();
        fd.append("name", name);
        for (const f of files) fd.append("files", f);

        const resp = await fetch(API + "/api/author", { method: "POST", body: fd });
        const data = await resp.json();
        if (data.ok) {
            showToast(`作家「${name}」创建成功`, "success");
            $("create-name").value = ""; $("create-files").value = "";
            loadAuthors(); loadAuthorSelect();
        } else {
            showToast(data.error, "error");
        }
    } catch (e) {
        showToast("创建失败: " + e.message, "error");
    } finally {
        btn.disabled = false; btn.textContent = "创建作家";
    }
}

async function handleDelete(name) {
    if (!confirm(`确认删除作家「${name}」及其所有数据？`)) return;
    try {
        await api(`/api/author/${name}`, { method: "DELETE" });
        showToast(`已删除`, "success");
        loadAuthors(); loadAuthorSelect();
    } catch (e) { showToast("删除失败: " + e.message, "error"); }
}

// ============================================================================
// 写作工坊
// ============================================================================

let lastArticle = "";
let lastTopic = "";

async function loadAuthorSelect() {
    try {
        const authors = await api("/api/authors");
        const sel = $("write-author"), cur = sel.value;
        sel.innerHTML = '<option value="">-- 请选择 --</option>';
        authors.forEach((a) => { const o = document.createElement("option"); o.value = a.name; o.textContent = a.name; sel.appendChild(o); });
        if (cur) sel.value = cur;
    } catch (e) {}
}

async function handleWrite() {
    const author = $("write-author").value;
    const topic = $("write-topic").value.trim();
    if (!author) return showToast("请选择作家", "error");
    if (!topic) return showToast("请输入写作主题", "error");

    $("write-result").classList.remove("hidden");
    $("write-loading").classList.remove("hidden");
    $("write-output").textContent = "";
    $("write-saved-path").textContent = "";

    const btn = $("btn-write");
    btn.disabled = true; btn.textContent = "生成中...";

    const payload = {
        author, topic,
        tone: $("write-tone").value,
        length: $("write-length").value,
        include_retrieval: $("write-retrieval").checked,
        save_dir: $("write-save-dir").value.trim(),
    };

    // Try streaming first, fall back to non-streaming on failure
    let streamingSucceeded = false;
    try {
        streamingSucceeded = await handleWriteStream(payload);
    } catch (e) {
        console.warn("Streaming failed, falling back to non-streaming:", e);
    }

    if (!streamingSucceeded) {
        try {
            const data = await api("/api/write", { method: "POST", body: JSON.stringify(payload) });
            lastArticle = data.article;
            lastTopic = data.topic;
            $("write-output").textContent = data.article;
            $("write-saved-path").textContent = data.saved_path ? `已保存: ${data.saved_path}` : "";
            showToast("生成完成", "success");
        } catch (e) {
            $("write-output").textContent = "生成失败: " + e.message;
            showToast("生成失败", "error");
        }
    }

    $("write-loading").classList.add("hidden");
    btn.disabled = false; btn.textContent = "开始写作";
}

async function handleWriteStream(payload) {
    const resp = await fetch(API + "/api/write-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    if (!resp.ok) {
        throw new Error("Stream endpoint returned " + resp.status);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let article = "";
    let succeeded = false;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete line in buffer

        for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const jsonStr = line.slice(6).trim();
            if (!jsonStr) continue;

            try {
                const event = JSON.parse(jsonStr);
                if (event.type === "chunk") {
                    article += event.content;
                    $("write-output").textContent = article;
                    // Auto-scroll to bottom
                    $("write-output").scrollTop = $("write-output").scrollHeight;
                } else if (event.type === "done") {
                    lastArticle = event.article;
                    lastTopic = event.topic;
                    $("write-output").textContent = event.article;
                    $("write-saved-path").textContent = event.saved_path ? `已保存: ${event.saved_path}` : "";
                    showToast("生成完成", "success");
                    succeeded = true;
                } else if (event.type === "error") {
                    throw new Error(event.error);
                }
            } catch (parseErr) {
                console.warn("SSE parse error:", parseErr);
            }
        }
    }

    if (!succeeded && article) {
        // Stream ended without a "done" event but we got content
        lastArticle = article;
        lastTopic = payload.topic;
        showToast("生成完成", "success");
        succeeded = true;
    }

    return succeeded;
}

function copyArticle() {
    const text = $("write-output").textContent;
    if (!text) return;
    navigator.clipboard.writeText(text)
        .then(() => showToast("已复制", "success"))
        .catch(() => showToast("复制失败", "error"));
}

function downloadArticle() {
    if (!lastArticle) return showToast("没有可下载的内容", "error");
    const blob = new Blob([lastArticle], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${lastTopic || "article"}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    showToast("下载完成", "success");
}

async function handleBrowseFolder() {
    try {
        const data = await api("/api/browse-folder");
        if (data && data.path) {
            $("write-save-dir").value = data.path;
        }
    } catch (e) {
        // 用户取消选择时不提示
        if (!e.message.includes("未选择")) {
            showToast("选择文件夹失败: " + e.message, "error");
        }
    }
}

// ============================================================================
// 模型配置
// ============================================================================

async function loadConfig() {
    try {
        const cfg = await api("/api/config");
        $("cfg-llm-provider").value = cfg.LLM_PROVIDER || "openai";
        $("cfg-llm-model").value = cfg.LLM_MODEL || "";
        $("cfg-llm-url").value = cfg.LLM_BASE_URL || "";
        $("cfg-llm-key").value = "";
        $("cfg-llm-key-status").className = `badge ${cfg.LLM_API_KEY_SET ? "badge-ok" : "badge-no"}`;

        $("cfg-emb-provider").value = cfg.EMBEDDING_PROVIDER || "dashscope";
        $("cfg-emb-model").value = cfg.EMBEDDING_MODEL || "";
        $("cfg-emb-key").value = "";
        $("cfg-emb-key-status").className = `badge ${cfg.EMBEDDING_API_KEY_SET ? "badge-ok" : "badge-no"}`;
    } catch (e) {
        showToast("加载配置失败", "error");
    }
}

async function handleSaveConfig() {
    const payload = {
        LLM_PROVIDER: $("cfg-llm-provider").value,
        LLM_MODEL: $("cfg-llm-model").value.trim(),
        LLM_BASE_URL: $("cfg-llm-url").value.trim(),
        EMBEDDING_PROVIDER: $("cfg-emb-provider").value,
        EMBEDDING_MODEL: $("cfg-emb-model").value.trim(),
    };

    const llmKey = $("cfg-llm-key").value.trim();
    const embKey = $("cfg-emb-key").value.trim();
    if (llmKey) payload.LLM_API_KEY = llmKey;
    if (embKey) payload.EMBEDDING_API_KEY = embKey;

    try {
        const resp = await fetch(API + "/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast("配置已保存，重启服务后生效", "success");
            $("cfg-llm-key").value = "";
            $("cfg-emb-key").value = "";
            loadConfig();
        } else {
            showToast(data.error, "error");
        }
    } catch (e) {
        showToast("保存失败: " + e.message, "error");
    }
}

// ============================================================================
// 自定义模型列表
// ============================================================================

let modelsCache = [];

async function loadModels() {
    try {
        const models = await api("/api/models");
        modelsCache = models;
        renderModels(models);
    } catch (e) {
        $("models-list").innerHTML = `<div class="loading">加载失败</div>`;
    }
}

function renderModels(models) {
    if (!models.length) {
        $("models-list").innerHTML = '<div class="loading">暂无模型，点击上方添加</div>';
        return;
    }
    $("models-list").innerHTML = models.map((m, i) => `
        <div class="model-card" onclick="applyModel(${i})" title="点击填入配置">
            <div class="model-info">
                <strong>${m.label || m.name}</strong>
                <code>${m.provider} / ${m.name}${m.base_url ? " / " + m.base_url : ""}</code>
            </div>
            <div class="model-actions">
                <button class="btn btn-small btn-danger" onclick="event.stopPropagation(); handleDeleteModel(${i})">删除</button>
            </div>
        </div>
    `).join("");
}

function applyModel(index) {
    const m = modelsCache[index];
    if (!m) return;
    $("cfg-llm-provider").value = m.provider || "openai";
    $("cfg-llm-model").value = m.name || "";
    $("cfg-llm-url").value = m.base_url || "";
    showToast(`已填入「${m.label || m.name}」的配置`, "success");
}

function handleAddModel() {
    $("model-form").classList.remove("hidden");
    $("model-label").value = "";
    $("model-provider").value = "openai";
    $("model-name").value = "";
    $("model-url").value = "";
}

function handleCancelModel() {
    $("model-form").classList.add("hidden");
}

async function handleSaveModel() {
    const label = $("model-label").value.trim();
    const provider = $("model-provider").value;
    const name = $("model-name").value.trim();
    const url = $("model-url").value.trim();

    if (!name) return showToast("请输入模型名称", "error");

    modelsCache.push({ provider, name, base_url: url, label: label || name });

    try {
        await fetch(API + "/api/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ models: modelsCache }),
        });
        renderModels(modelsCache);
        $("model-form").classList.add("hidden");
        showToast("模型已添加", "success");
    } catch (e) {
        showToast("保存失败: " + e.message, "error");
    }
}

async function handleDeleteModel(index) {
    if (!confirm("确认删除此模型？")) return;
    modelsCache.splice(index, 1);
    try {
        await fetch(API + "/api/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ models: modelsCache }),
        });
        renderModels(modelsCache);
        showToast("已删除", "success");
    } catch (e) {
        showToast("删除失败", "error");
    }
}

// ============================================================================
// 初始化
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
    loadAuthorSelect();
});
