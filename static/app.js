// ============================================================================
// 作家风格仿写 Skill - 前端逻辑
// ============================================================================

const API = "";

// ============================================================================
// 工具
// ============================================================================

async function api(url, opts = {}) {
    const resp = await fetch(API + url, {
        ...opts,
        headers: { "Content-Type": "application/json", ...opts.headers },
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
        const error = new Error(data.error || "请求失败");
        error.status = resp.status;
        throw error;
    }
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
        if (tab === "history" && typeof loadHistory === "function") loadHistory();
        if (tab === "jobs" && typeof loadJobList === "function") loadJobList();
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
        $("authors-list").textContent = "加载失败: " + e.message;
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

async function handleCreate(event) {
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
    if (!confirm(`确认将作家「${name}」移入归档？可通过命令行恢复。`)) return;
    try {
        await api(`/api/author/${encodeURIComponent(name)}`, {
            method: "DELETE",
            body: JSON.stringify({ confirm: true }),
        });
        showToast(`已移入归档`, "success");
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

    try {
        await handleWriteStream(payload);
    } catch (e) {
        $("write-saved-path").textContent = lastArticle ? "未完成草稿：尚未确认保存及检查" : "生成失败";
        if (!lastArticle) $("write-output").textContent = "生成失败: " + e.message;
        showToast(e.message, "error");
    } finally {
        $("write-loading").classList.add("hidden");
        btn.disabled = false; btn.textContent = "开始写作";
    }
}

async function handleWriteStream(payload) {
    const resp = await fetch(API + "/api/write-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    if (!resp.ok) {
        let details;
        try { details = await resp.json(); } catch (_) {}
        throw new Error(details?.error || "生成请求失败（HTTP " + resp.status + "）");
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let article = "";
    const applyEvent = (event) => {
        if (event.type === "chunk") {
            article += event.content;
            lastArticle = article;
            lastTopic = payload.topic;
            lastAuthor = payload.author;
            $("write-output").textContent = article;
            $("write-output").scrollTop = $("write-output").scrollHeight;
        } else if (event.type === "stage") {
            const labels = { generating: "正在生成…", checking: "正在查重和审稿…", saving: "正在保存…" };
            $("write-saved-path").textContent = labels[event.stage] || "正在处理…";
        } else if (event.type === "done") {
            lastArticle = event.article;
            lastTopic = event.topic;
            lastAuthor = event.author;
            lastReview = event.review || null;
            addVersion(event.version, event.version_path);
            $("write-output").textContent = event.article;
            $("write-saved-path").textContent = event.saved_path ? `已保存: ${event.saved_path}` : "生成完成";
            renderPlagiarismResult(event.plagiarism);
            renderReviewResult(event.review);
            setRewriteEnabled(Boolean(lastArticle && lastReview));
            showToast(event.retrieval_warning || "生成完成", event.retrieval_warning ? "info" : "success");
            return true;
        } else if (event.type === "error") {
            throw new Error(event.error || "生成失败，草稿已保留");
        }
        return false;
    };
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop();
        for (const frame of frames) {
            const jsonStr = frame.split(/\r?\n/).filter(line => line.startsWith("data:"))
                .map(line => line.slice(5).trimStart()).join("\n");
            if (jsonStr && applyEvent(JSON.parse(jsonStr))) return true;
        }
      }
      throw new Error("连接已中断，未收到完成确认；请保留草稿后再决定是否重新生成");
    } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
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
    const status = result.originality_status || (passed === false ? "high_risk" : passed === true ? "low_risk" : "not_checked");
    const stateClass = status === "high_risk" || status === "needs_review" ? "warning" : status === "low_risk" ? "ok" : "neutral";
    const title = {high_risk: "文字复用风险较高", needs_review: "来源复用需要复核", low_risk: "文字比对未见超限", not_checked: "检测未完成"}[status] || "检测未完成";
    const maxCommon = Number.isFinite(Number(result.max_common)) ? `最长连续重复 ${Number(result.max_common)} 字` : "无重复长度数据";
    const warning = result.warning || (passed === false ? "生成内容与原文存在较长连续重复" : "未发现超限连续重复");

    el.className = `quality-strip ${stateClass}`;
    el.innerHTML = `
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(maxCommon)}</span>
        <span>${escapeHtml(warning)}</span>
        ${renderReuseEvidence(result)}
    `;
}

function renderReuseEvidence(result) {
    const evidence = Array.isArray(result.evidence) ? result.evidence : [];
    const ratio = Number(result.coverage?.ratio);
    const coverage = Number.isFinite(ratio) ? `疑似重合片段覆盖 ${(ratio * 100).toFixed(1)}%` : "";
    const methods = result.methods || {};
    const sourceCount = Number(result.scope?.source_count);
    const sourceSummary = Number.isFinite(sourceCount) ? `比对 ${sourceCount} 份已加载来源。` : "";
    const scope = methods.semantic === "candidates_only" ? "已提供语义候选供逐条复核；情节结构尚未自动检查。"
        : methods.semantic === "error" ? "语义检查未完成，请检查本地模型与校准配置。"
        : methods.structure === "not_checked" ? "仅比较已加载来源的文字；语义与情节结构尚未检查。" : "";
    const kinds = {exact: "连续文字重合", punctuation_insensitive: "忽略标点后重合", near_text: "近似文字片段", semantic_candidate: "语义候选"};
    const sourceTypes = {work: "作品", reference: "参考资料", style_guide: "风格指南", few_shot: "固定示例", retrieval: "本次检索参考"};
    const rows = evidence.slice(0, 20).map(hit => `
        <li class="reuse-evidence">
            <p><b>${escapeHtml(hit.title || "未知来源")}</b> · ${escapeHtml(sourceTypes[hit.source_type] || "来源资料")} · ${escapeHtml(kinds[hit.kind] || "文字重合")}</p>
            <p class="muted">${escapeHtml(hit.filename || "参考片段")}${hit.source?.line ? ` · 原文第 ${Number(hit.source.line)} 行` : ""}</p>
            <div class="reuse-pair">
                <div><b>生成文</b><blockquote>${escapeHtml((hit.generated?.text || "").slice(0, 800))}</blockquote></div>
                <div><b>对应原文</b><blockquote>${escapeHtml((hit.source?.text || "").slice(0, 800))}</blockquote></div>
            </div>
            ${hit.kind === "semantic_candidate" ? renderReuseReviewForm(hit) : ""}
        </li>`).join("");
    const summary = lastVersion?.reuse_review_summary;
    const humanSummary = summary?.displayed_candidates ? `人工复核已完成 ${summary.resolved} / ${summary.displayed_candidates} 条已展示候选。` : "";
    return `<div class="reuse-summary">${sourceSummary ? `<span>${escapeHtml(sourceSummary)}</span>` : ""}${coverage ? `<span>${escapeHtml(coverage)}，用于定位复用片段。</span>` : ""}${scope ? `<span>${escapeHtml(scope)}</span>` : ""}</div>
        ${humanSummary ? `<p class="reuse-summary">${escapeHtml(humanSummary)}</p>` : ""}
        ${rows ? `<details class="reuse-details"><summary>查看来源证据（显示 ${Math.min(evidence.length, 20)} / ${Number(result.evidence_count) || evidence.length} 条）</summary><ol>${rows}</ol></details>` : ""}`;
}

function renderReuseReviewForm(hit) {
    const id = hit.evidence_id;
    if (typeof id !== "string" || !/^[a-f0-9]{64}$/.test(id)) return "";
    const previous = (lastVersion?.reuse_reviews || []).find(row => row.evidence_id === id);
    const labels = {uncertain:"尚无法判断", reuse:"存在具体表达复用", independent:"仅主题、物件或风格相近", quotation:"明确引用，另核对引用方式"};
    return `<p class="muted">候选相似分 ${Number(hit.similarity).toFixed(3)}，不代表抄袭概率。</p>
        <div class="reuse-review-form">
            <label for="reuse-choice-${id}">人工意见</label>
            <select id="reuse-choice-${id}">${Object.entries(labels).map(([value,label]) => `<option value="${value}"${(previous?.decision || "uncertain") === value ? " selected" : ""}>${label}</option>`).join("")}</select>
            <label for="reuse-note-${id}">复核依据</label>
            <textarea id="reuse-note-${id}" maxlength="2000" rows="2" placeholder="记录对应的事件、比喻，以及实质差异">${escapeHtml(previous?.note || "")}</textarea>
            <button class="btn btn-small" id="reuse-save-${id}" onclick="saveReuseReview('${id}')"${lastVersion?.version_id ? "" : " disabled"}>保存复核意见</button>
            <span>${previous ? "已保存人工意见" : "等待人工复核"}</span>
        </div>`;
}

async function saveReuseReview(id) {
    if (!lastVersion?.version_id || !/^[a-f0-9]{64}$/.test(id)) return;
    const versionId = lastVersion.version_id;
    const button = $("reuse-save-" + id);
    button.disabled = true;
    try {
        const data = await api(`/api/versions/${encodeURIComponent(versionId)}/reuse-review`, {method:"POST", body:JSON.stringify({
            evidence_id:id, decision:$("reuse-choice-" + id).value, note:$("reuse-note-" + id).value})});
        if (lastVersion?.version_id === versionId) {
            Object.assign(lastVersion, data);
            renderPlagiarismResult(lastVersion.plagiarism);
            $("write-check").querySelector("details")?.setAttribute("open", "");
        }
        showToast("复核意见已保存", "success");
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        button.disabled = false;
    }
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
            <strong>${result.agent === "rule+llm" ? "规则与模型审稿" : "规则审稿"}</strong>
            <span>结论: ${escapeHtml(decision)}</span>
            <span>总分: ${Number(result.score) || 0}</span>
        </div>
        <div class="review-grid">
            <div>
                <b>要求符合度</b>
                <span>${Number(requirement.score) || 0}</span>
                ${requirement.status ? `<span>${requirement.status === "pass" ? "篇幅符合要求" : "篇幅或空内容检查未通过"}</span>` : ""}
                ${renderIssueList(requirement.issues)}
            </div>
            <div>
                <b>风格相似度</b>
                <span>${Number(style.score) || 0}</span>
                ${renderIssueList([...matched, ...missing])}
            </div>
            <div>
                <b>来源复用风险</b>
                <span>${escapeHtml(plagiarism.risk || "unknown")}</span>
                ${renderIssueList(plagiarism.issues)}
            </div>
        </div>
        <div class="review-suggestions">
            <b>修改建议</b>
            ${renderIssueList(suggestions)}
        </div>
        ${result.llm_review ? `<div class="review-suggestions"><b>模型审稿</b><p>${escapeHtml(result.llm_review.ok ? `结论: ${result.llm_review.data.decision} · 得分: ${result.llm_review.data.score}` : "未完成，请人工复核")}</p></div>` : ""}
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
    $("btn-write").disabled = true;
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
        $("btn-write").disabled = false;
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
    const kind = item.kind === "restore" ? "恢复稿" : item.kind === "rewrite" ? "重写稿" : "初稿";
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
                <span>来源复用风险: ${escapeHtml(risk)}</span>
                ${summary.requirement_status ? `<span>篇幅要求: ${summary.requirement_status === "pass" ? "符合" : "未通过"}</span>` : ""}
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
        $("cfg-emb-url").value = cfg.EMBEDDING_BASE_URL || "";
        $("cfg-emb-key").value = "";
        const localEmbedding = cfg.EMBEDDING_PROVIDER === "local";
        $("cfg-emb-key-status").className = `badge ${localEmbedding || cfg.EMBEDDING_API_KEY_SET ? "badge-ok" : "badge-no"}`;
        $("cfg-emb-key-status").textContent = localEmbedding ? "无需密钥" : (cfg.EMBEDDING_API_KEY_SET ? "已配置" : "未配置");
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
        EMBEDDING_BASE_URL: $("cfg-emb-url").value.trim() || "__CLEAR__",
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
    initializeAccess();
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

async function initializeAccess() {
    try {
        const access = await api("/api/access");
        $("access-panel").classList.toggle("hidden", access.authorized);
        $("app-content").classList.toggle("hidden", !access.authorized);
        if (access.authorized) {
            await loadAuthorSelect();
            if (typeof initializeTasks === "function") await initializeTasks();
        }
    } catch (error) {
        showToast("连接服务失败: " + error.message, "error");
    }
}

async function handleLogin(event) {
    event.preventDefault();
    try {
        await api("/api/session", {method: "POST", body: JSON.stringify({password: $("access-password").value})});
        $("access-password").value = "";
        await initializeAccess();
    } catch (error) {
        showToast(error.message, "error");
    }
}
