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

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    }[char]));
}

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
        $("authors-list").innerHTML = '<div class="empty-state">暂无作家，请在上方创建</div>';
        return;
    }
    $("authors-list").innerHTML = authors.map((a) => `
        <div class="author-card">
            <div class="author-info">
                <h4>${escapeHtml(a.name)}</h4>
                <div class="meta">
                    <span class="meta-chip ${a.has_style_guide ? "badge-ok" : "badge-no"}">风格指南</span>
                    <span class="meta-chip ${a.has_vector_store ? "badge-ok" : "badge-no"}">向量库</span>
                    <span class="meta-chip">${Number(a.txt_files) || 0} txt / ${Number(a.epub_files) || 0} epub</span>
                </div>
            </div>
            <div class="author-actions">
                <button class="btn btn-small" data-author-action="write" data-author-name="${escapeHtml(a.name)}">写作</button>
                <button class="btn btn-small btn-danger" data-author-action="delete" data-author-name="${escapeHtml(a.name)}">删除</button>
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
        await api(`/api/author/${name}`, {
            method: "DELETE",
            body: JSON.stringify({ confirm: true }),
        });
        showToast(`已删除`, "success");
        loadAuthors(); loadAuthorSelect();
    } catch (e) { showToast("删除失败: " + e.message, "error"); }
}

// ============================================================================
// 写作工坊
// ============================================================================

let lastArticle = "";
let lastTopic = "";
let lastAuthor = "";
let lastReview = null;
let lastVersion = null;
let versionHistory = [];

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
    $("write-check").classList.add("hidden");
    $("write-review").classList.add("hidden");
    $("write-versions").classList.add("hidden");
    $("write-output").textContent = "";
    $("write-saved-path").textContent = "";
    setRewriteEnabled(false);
    lastArticle = "";
    lastTopic = "";
    lastAuthor = "";
    lastReview = null;
    lastVersion = null;
    versionHistory = [];

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
            lastAuthor = data.author;
            lastReview = data.review || null;
            addVersion(data.version, data.version_path);
            $("write-output").textContent = data.article;
            $("write-saved-path").textContent = data.saved_path ? `已保存: ${data.saved_path}` : "";
            renderPlagiarismResult(data.plagiarism);
            renderReviewResult(data.review);
            setRewriteEnabled(Boolean(lastArticle && lastReview));
            showToast("生成完成", "success");
        } catch (e) {
            $("write-output").textContent = "生成失败: " + e.message;
            $("write-check").classList.add("hidden");
            $("write-review").classList.add("hidden");
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

            let event;
            try {
                event = JSON.parse(jsonStr);
            } catch (parseErr) {
                console.warn("SSE parse error:", parseErr);
                continue;
            }

            if (event.type === "chunk") {
                article += event.content;
                $("write-output").textContent = article;
                // Auto-scroll to bottom
                $("write-output").scrollTop = $("write-output").scrollHeight;
            } else if (event.type === "done") {
                lastArticle = event.article;
                lastTopic = event.topic;
                lastAuthor = event.author;
                lastReview = event.review || null;
                addVersion(event.version, event.version_path);
                $("write-output").textContent = event.article;
                $("write-saved-path").textContent = event.saved_path ? `已保存: ${event.saved_path}` : "";
                renderPlagiarismResult(event.plagiarism);
                renderReviewResult(event.review);
                setRewriteEnabled(Boolean(lastArticle && lastReview));
                showToast("生成完成", "success");
                succeeded = true;
            } else if (event.type === "error") {
                throw new Error(event.error || "stream failed");
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

function renderPlagiarismResult(result) {
    const el = $("write-check");
    if (!el) return;
    if (!result) {
        el.className = "quality-strip neutral";
        el.innerHTML = "<strong>重复检测</strong><span>未启用</span><span>当前生成未返回检测结果</span>";
        return;
    }

    const passed = result.passed;
    const stateClass = passed === false ? "warning" : passed === null ? "neutral" : "ok";
    const title = passed === false ? "需要复核" : passed === null ? "检测未完成" : "检测通过";
    const maxCommon = Number.isFinite(Number(result.max_common)) ? `最长连续重复 ${Number(result.max_common)} 字` : "无重复长度数据";
    const warning = result.warning || (passed === false ? "生成内容与原文存在较长连续重复" : "未发现超限连续重复");

    el.className = `quality-strip ${stateClass}`;
    el.innerHTML = `
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(maxCommon)}</span>
        <span>${escapeHtml(warning)}</span>
    `;
}

function renderReviewResult(result) {
    const el = $("write-review");
    if (!el) return;
    if (!result) {
        el.classList.add("hidden");
        return;
    }

    const decision = result.decision || "unknown";
    const stateClass = decision === "fail" ? "warning" : decision === "pass" ? "ok" : "neutral";
    const requirement = result.requirement || {};
    const style = result.style || {};
    const plagiarism = result.plagiarism || {};
    const suggestions = Array.isArray(result.suggestions) ? result.suggestions : [];
    const matched = Array.isArray(style.matched) ? style.matched : [];
    const missing = Array.isArray(style.missing) ? style.missing : [];

    el.className = `review-panel ${stateClass}`;
    el.innerHTML = `
        <div class="review-head">
            <strong>审稿 Agent</strong>
            <span>结论: ${escapeHtml(decision)}</span>
            <span>总分: ${Number(result.score) || 0}</span>
        </div>
        <div class="review-grid">
            <div>
                <b>要求符合度</b>
                <span>${Number(requirement.score) || 0}</span>
                ${renderIssueList(requirement.issues)}
            </div>
            <div>
                <b>风格相似度</b>
                <span>${Number(style.score) || 0}</span>
                ${renderIssueList([...matched, ...missing])}
            </div>
            <div>
                <b>抄袭风险</b>
                <span>${escapeHtml(plagiarism.risk || "unknown")}</span>
                ${renderIssueList(plagiarism.issues)}
            </div>
        </div>
        <div class="review-suggestions">
            <b>修改建议</b>
            ${renderIssueList(suggestions)}
        </div>
    `;
}

function renderIssueList(items) {
    const list = Array.isArray(items) ? items.filter(Boolean).slice(0, 5) : [];
    if (!list.length) return '<p class="muted">暂无明显问题</p>';
    return `<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function setRewriteEnabled(enabled) {
    const btn = $("btn-rewrite");
    if (btn) btn.disabled = !enabled;
}

async function handleRewrite() {
    if (!lastArticle || !lastReview) {
        showToast("暂无可重写的审稿结果", "error");
        return;
    }

    const payload = {
        author: lastAuthor || $("write-author").value,
        topic: lastTopic || $("write-topic").value.trim(),
        article: lastArticle,
        review: lastReview,
        parent_version: lastVersion?.version_id || lastVersion?.version_path || "",
        tone: $("write-tone").value,
        length: $("write-length").value,
        include_retrieval: $("write-retrieval").checked,
        save_dir: $("write-save-dir").value.trim(),
    };

    const btn = $("btn-rewrite");
    btn.disabled = true;
    $("write-loading").classList.remove("hidden");

    try {
        const data = await api("/api/rewrite", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        lastArticle = data.article;
        lastTopic = data.topic;
        lastAuthor = data.author;
        lastReview = data.review || null;
        addVersion(data.version, data.version_path);
        $("write-output").textContent = data.article;
        $("write-saved-path").textContent = data.saved_path ? `已保存: ${data.saved_path}` : "";
        renderPlagiarismResult(data.plagiarism);
        renderReviewResult(data.review);
        showToast("重写完成", "success");
    } catch (e) {
        showToast("重写失败: " + e.message, "error");
    } finally {
        $("write-loading").classList.add("hidden");
        setRewriteEnabled(Boolean(lastArticle && lastReview));
    }
}

function addVersion(version, versionPath) {
    if (!version) {
        renderVersionHistory();
        return;
    }
    const normalized = {
        ...version,
        version_path: versionPath || version.version_path || "",
    };
    lastVersion = normalized;
    versionHistory.push(normalized);
    renderVersionHistory();
}

function renderVersionHistory() {
    const el = $("write-versions");
    if (!el) return;
    if (!versionHistory.length) {
        el.classList.add("hidden");
        el.innerHTML = "";
        return;
    }

    el.className = "version-panel";
    el.innerHTML = `
        <div class="version-head">
            <strong>作品版本链</strong>
            <span>${versionHistory.length} 个版本</span>
        </div>
        <div class="version-list">
            ${versionHistory.map((item, index) => renderVersionItem(item, index)).join("")}
        </div>
    `;
}

function renderVersionItem(item, index) {
    const summary = item.review_summary || {};
    const previous = index > 0 ? versionHistory[index - 1]?.review_summary || {} : {};
    const score = Number(summary.score);
    const previousScore = Number(previous.score);
    const hasScore = Number.isFinite(score);
    const delta = hasScore && Number.isFinite(previousScore) ? score - previousScore : null;
    const deltaText = delta === null ? "" : `${delta >= 0 ? "+" : ""}${delta}`;
    const kind = item.kind === "rewrite" ? "重写稿" : "初稿";
    const decision = summary.decision || "unknown";
    const risk = summary.plagiarism_risk
        || (item.plagiarism_summary?.passed === false ? "high" : "unknown");

    return `
        <div class="version-item ${index === versionHistory.length - 1 ? "active" : ""}">
            <div>
                <b>v${index + 1} · ${escapeHtml(kind)}</b>
                <span>${escapeHtml(decision)} · ${hasScore ? score : "-"}${deltaText ? ` (${escapeHtml(deltaText)})` : ""}</span>
            </div>
            <div>
                <span>${Number(item.article_chars) || 0} 字</span>
                <span>抄袭风险: ${escapeHtml(risk)}</span>
            </div>
        </div>
    `;
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
        $("models-list").innerHTML = '<div class="empty-state">暂无模型，点击上方添加</div>';
        return;
    }
    $("models-list").innerHTML = models.map((m, i) => `
        <div class="model-card" data-model-index="${i}" title="点击填入配置">
            <div class="model-info">
                <strong>${escapeHtml(m.label || m.name)}</strong>
                <code>${escapeHtml(`${m.provider || ""} / ${m.name || ""}${m.base_url ? " / " + m.base_url : ""}`)}</code>
            </div>
            <div class="model-actions">
                <button class="btn btn-small btn-danger" data-model-action="delete" data-model-index="${i}">删除</button>
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
        await api("/api/models", {
            method: "POST",
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
        await api("/api/models", {
            method: "POST",
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
    $("authors-list")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-author-action]");
        if (!button) return;
        const name = button.dataset.authorName;
        if (button.dataset.authorAction === "write") selectAuthorForWrite(name);
        if (button.dataset.authorAction === "delete") handleDelete(name);
    });
    $("models-list")?.addEventListener("click", (event) => {
        const deleteButton = event.target.closest("[data-model-action='delete']");
        if (deleteButton) {
            event.stopPropagation();
            handleDeleteModel(Number(deleteButton.dataset.modelIndex));
            return;
        }
        const card = event.target.closest("[data-model-index]");
        if (card) applyModel(Number(card.dataset.modelIndex));
    });
});
