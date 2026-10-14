// Durable tasks and manuscript history. The server owns content and versions.
const taskLabels = {queued:"等待处理", retrieving:"准备风格与检索", generating:"正在生成正文", checking:"查重与审稿",
    saving:"正在保存", importing:"导入作品", analyzing:"分析风格", indexing:"构建索引", completed:"已完成",
    failed:"处理失败", interrupted:"服务中断", cancelled:"已取消", cancelling:"正在取消"};
const terminalJobs = new Set(["completed", "failed", "interrupted", "cancelled"]);
const versionLabels = {draft:"初稿", rewrite:"重写稿", restore:"恢复稿"};
let activeJobId = null, activeJob = null, jobPollTimer = null, jobListTimer = null, submissionBusy = false;
let pendingSubmission = null, historyItems = [], historyNextOffset = null, selectedHistory = null, historySearchTimer = null;

const taskStorage = {
    get(key) { try { return JSON.parse(localStorage.getItem("stylemuse." + key)); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem("stylemuse." + key, JSON.stringify(value)); } catch (_) {} },
    remove(key) { try { localStorage.removeItem("stylemuse." + key); } catch (_) {} },
};
function submissionKey() {
    return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : "job-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}
function taskNotice(message, retry = false) {
    $("job-connection").textContent = message;
    $("job-connection").classList.toggle("hidden", !message);
    $("btn-confirm-submit").classList.toggle("hidden", !retry);
}
function setJobBusy(busy) {
    $("btn-write").disabled = busy;
    $("btn-write").textContent = busy ? "任务处理中…" : "开始写作";
    setRewriteEnabled(!busy && Boolean(lastArticle && lastReview && lastVersion?.version_id));
}
function generationPayload() {
    return {author:$("write-author").value, topic:$("write-topic").value.trim(), tone:$("write-tone").value,
        length:$("write-length").value, include_retrieval:$("write-retrieval").checked, save_dir:$("write-save-dir").value.trim()};
}
async function submitGeneration(payload) {
    if (submissionBusy || (activeJob && !terminalJobs.has(activeJob.status))) return;
    submissionBusy = true;
    setJobBusy(true);
    const previous = pendingSubmission || taskStorage.get("pendingSubmission");
    const pending = previous && JSON.stringify(previous.payload) === JSON.stringify(payload) ? previous : {key:submissionKey(), payload};
    pendingSubmission = pending;
    taskStorage.set("pendingSubmission", pending);
    taskNotice("正在提交任务…");
    try {
        const job = await api("/api/jobs", {method:"POST", headers:{"Idempotency-Key":pending.key}, body:JSON.stringify(payload)});
        pendingSubmission = null;
        taskStorage.remove("pendingSubmission");
        taskNotice("");
        await watchJob(job.id, job);
    } catch (error) {
        // Reuse this key after an ambiguous network failure, never start a new generation automatically.
        taskNotice("提交结果尚未确认：" + error.message + "。可使用原提交编号确认状态。", true);
        setJobBusy(false);
    } finally {
        submissionBusy = false;
    }
}
async function startWriteJob() {
    const payload = generationPayload();
    if (!payload.author || !payload.topic) return showToast("请选择作家并填写主题", "error");
    await submitGeneration({kind:"write", ...payload});
}
async function startRewriteJob() {
    if (!lastVersion?.version_id) return showToast("请先选择已保存的作品版本", "error");
    await submitGeneration({kind:"rewrite", version_id:lastVersion.version_id, save_dir:$("write-save-dir").value.trim()});
}
async function retryPendingSubmission() {
    const pending = pendingSubmission || taskStorage.get("pendingSubmission");
    if (!pending) return;
    await submitGeneration(pending.payload);
}
async function watchJob(id, initial = null) {
    clearTimeout(jobPollTimer);
    activeJobId = id;
    taskStorage.set("activeJob", id);
    if (initial) renderJobSnapshot(initial);
    await pollActiveJob(id);
}
async function pollActiveJob(id) {
    if (id !== activeJobId) return;
    try {
        const job = await api("/api/jobs/" + encodeURIComponent(id));
        if (id !== activeJobId) return;
        taskNotice("");
        renderJobSnapshot(job);
        if (terminalJobs.has(job.status)) {
            if (job.status === "completed") {
                taskStorage.remove("activeJob");
                if (job.kind !== "import") {
                    taskStorage.set("lastVersion", job.version_id);
                    await loadWorkshopVersions(job.article_id);
                } else {
                    loadAuthors(); loadAuthorSelect();
                }
            }
            loadJobList();
            return;
        }
    } catch (error) {
        if (id !== activeJobId) return;
        if (error.status === 404) {
            activeJobId = null; activeJob = null;
            taskStorage.remove("activeJob");
            setJobBusy(false);
            taskNotice("原任务不存在，请从作品库选择已保存版本或重新提交。");
            return;
        }
        if (error.status === 401) {
            taskNotice("登录已失效，请重新登录后查看原任务。");
            await initializeAccess();
            return;
        }
        taskNotice("连接中断，后台任务仍保留。正在重新查询：" + error.message);
    }
    if (id === activeJobId) jobPollTimer = setTimeout(() => pollActiveJob(id), 900);
}
function renderJobSnapshot(job) {
    activeJob = job;
    const busy = !terminalJobs.has(job.status);
    setJobBusy(busy);
    $("btn-cancel-job").classList.toggle("hidden", !busy);
    $("btn-cancel-job").disabled = job.cancel_requested;
    $("btn-resume-job").classList.toggle("hidden", !job.can_resume);
    const label = job.cancel_requested && busy ? taskLabels.cancelling : taskLabels[job.stage] || taskLabels[job.status];
    if (job.kind === "import") {
        $("import-job-status").textContent = `${job.author} · ${label}${job.error ? " · " + job.error : ""}`;
        $("btn-cancel-import").classList.toggle("hidden", !busy);
        $("btn-cancel-import").disabled = job.cancel_requested;
        return;
    }
    $("write-result").classList.remove("hidden");
    $("write-loading").classList.toggle("hidden", !busy);
    lastArticle = job.article || "";
    lastAuthor = job.author;
    lastTopic = job.topic;
    $("write-author").value = job.author;
    $("write-topic").value = job.topic;
    $("write-output").textContent = lastArticle;
    const options = job.payload || {};
    if (options.tone) $("write-tone").value = options.tone;
    if (options.length) $("write-length").value = options.length;
    if (typeof options.include_retrieval === "boolean") $("write-retrieval").checked = options.include_retrieval;
    if (job.status === "completed" && job.result) {
        applySavedVersion(job.result.version);
        $("write-saved-path").textContent = "已保存 · " + job.result.version.version_id.slice(0, 8);
    } else {
        lastReview = null;
        lastVersion = null;
        $("write-review").classList.add("hidden");
        $("write-check").classList.add("hidden");
        $("write-versions").classList.add("hidden");
        $("write-saved-path").textContent = label + (job.error ? " · " + job.error : "") + (lastArticle && !busy ? " · 草稿已保留" : "");
        setRewriteEnabled(false);
    }
}
function applySavedVersion(version) {
    lastArticle = version.article;
    lastTopic = version.topic;
    lastAuthor = version.author;
    lastReview = version.review;
    lastVersion = version;
    $("write-saved-path").textContent = "已保存 · " + version.version_id.slice(0, 8);
    $("write-output").textContent = version.article;
    $("write-result").classList.remove("hidden");
    renderPlagiarismResult(version.plagiarism);
    renderReviewResult(version.review);
    setRewriteEnabled(Boolean(lastReview));
}
async function loadWorkshopVersions(articleId) {
    const previousReviews = JSON.stringify(lastVersion?.reuse_reviews || []);
    const article = await api("/api/articles/" + encodeURIComponent(articleId));
    if (lastVersion?.article_id !== articleId) return;
    versionHistory = article.versions;
    const current = versionHistory.find(version => version.version_id === lastVersion.version_id);
    if (current && previousReviews === JSON.stringify(lastVersion.reuse_reviews || [])) applySavedVersion(current);
    renderVersionHistory();
}
async function cancelActiveJob() {
    if (!activeJobId) return;
    try { renderJobSnapshot(await api(`/api/jobs/${activeJobId}/cancel`, {method:"POST"})); }
    catch (error) { showToast(error.message, "error"); }
}
async function resumeActiveJob() {
    if (!activeJobId) return;
    try {
        const job = await api(`/api/jobs/${activeJobId}/resume`, {method:"POST"});
        await watchJob(job.id, job);
    } catch (error) { showToast(error.message, "error"); }
}
async function openJob(id) {
    const job = await api("/api/jobs/" + encodeURIComponent(id));
    document.querySelector(`[data-tab="${job.kind === "import" ? "authors" : "write"}"]`).click();
    await watchJob(id, job);
}
async function loadJobList() {
    clearTimeout(jobListTimer);
    try {
        const jobs = await api("/api/jobs?limit=30");
        $("job-list").innerHTML = jobs.length ? jobs.map(job => `
            <div class="task-row"><div><b>${escapeHtml(job.kind === "import" ? "导入 · " + job.author : job.topic)}</b>
            <p>${escapeHtml(job.author)} · ${escapeHtml(new Date(job.created_at).toLocaleString("zh-CN"))}</p>
            ${job.error ? `<p class="task-error">${escapeHtml(job.error)}</p>` : ""}</div>
            <span class="task-state ${escapeHtml(job.status)}">${escapeHtml(job.cancel_requested && job.status === "running" ? taskLabels.cancelling : taskLabels[job.stage] || job.status)}</span>
            <button class="btn btn-small" data-job-id="${escapeHtml(job.id)}">${job.status === "completed" ? "查看结果" : "查看任务"}</button></div>
        `).join("") : '<div class="empty-state">暂无任务。开始写作后，进度和草稿会保留在这里。</div>';
        if (jobs.some(job => !terminalJobs.has(job.status))) jobListTimer = setTimeout(loadJobList, 2000);
    } catch (error) { $("job-list").textContent = "加载任务失败: " + error.message; }
}
async function startImportJob(event) {
    const name = $("create-name").value.trim(), files = [...$("create-files").files];
    if (!name || !files.length) return showToast("请填写作家名称并上传作品", "error");
    const button = event.currentTarget || event.target;
    button.disabled = true;
    const signature = JSON.stringify({name, files:files.map(file => [file.name,file.size,file.lastModified])});
    const previous = taskStorage.get("pendingImport");
    const pending = previous?.signature === signature ? previous : {key:submissionKey(), signature};
    taskStorage.set("pendingImport", pending);
    try {
        const body = new FormData();
        body.append("name", name);
        files.forEach(file => body.append("files", file));
        const response = await fetch("/api/import-jobs", {method:"POST", headers:{"Idempotency-Key":pending.key}, body});
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || "提交失败");
        taskStorage.remove("pendingImport");
        $("create-files").value = "";
        $("create-name").value = "";
        await watchJob(result.data.id, result.data);
        showToast("作品已提交，正在后台处理", "info");
    } catch (error) { $("import-job-status").textContent = "提交结果未确认，请保留所选文件后重试：" + error.message; }
    finally { button.disabled = false; }
}

async function initializeTasks() {
    const pending = taskStorage.get("pendingSubmission");
    if (pending) {
        pendingSubmission = pending;
        try {
            const job = await api("/api/jobs/by-key/" + encodeURIComponent(pending.key));
            pendingSubmission = null; taskStorage.remove("pendingSubmission");
            await watchJob(job.id, job);
            return;
        } catch (_) { taskNotice("上次提交状态尚未确认，可使用原提交编号查询或重试。", true); }
    }
    const pendingImport = taskStorage.get("pendingImport");
    if (pendingImport) {
        try {
            const job = await api("/api/jobs/by-key/" + encodeURIComponent(pendingImport.key));
            taskStorage.remove("pendingImport");
            await watchJob(job.id, job);
            return;
        } catch (_) { $("import-job-status").textContent = "上次导入未确认，请重新选择相同文件后提交。"; }
    }
    const active = taskStorage.get("activeJob");
    if (active) { await watchJob(active); return; }
    const version = taskStorage.get("lastVersion");
    if (version) {
        try {
            const saved = await api("/api/versions/" + encodeURIComponent(version));
            applySavedVersion(saved);
            $("write-author").value = saved.author;
            $("write-topic").value = saved.topic;
            await loadWorkshopVersions(saved.article_id);
        } catch (_) { taskStorage.remove("lastVersion"); }
    }
}

function scheduleHistorySearch() { clearTimeout(historySearchTimer); historySearchTimer = setTimeout(loadHistory, 250); }
async function loadHistory(more = false) {
    try {
        if (!more) {
            const selected = $("history-author").value;
            const authors = await api("/api/authors");
            $("history-author").innerHTML = '<option value="">全部作家</option>' + authors.map(author => `<option value="${escapeHtml(author.name)}">${escapeHtml(author.name)}</option>`).join("");
            $("history-author").value = selected;
        }
        const params = new URLSearchParams({author:$("history-author").value, search:$("history-search").value.trim(), offset:more ? historyNextOffset || 0 : 0});
        const data = await api("/api/articles?" + params);
        historyItems = more ? historyItems.concat(data.items) : data.items;
        historyNextOffset = data.next_offset;
        $("history-more").classList.toggle("hidden", historyNextOffset === null);
        $("history-list").innerHTML = historyItems.length ? historyItems.map(item => `
            <button class="manuscript-card ${item.id === selectedHistory?.id ? "selected" : ""}" data-article-id="${escapeHtml(item.id)}">
                <span class="eyebrow">${escapeHtml(item.author)}</span><b>${escapeHtml(item.topic)}</b>
                <span>${item.version_count} 稿 · ${item.article_chars} 字 · ${escapeHtml(item.review_summary.decision || "未评估")}</span>
                <small>${escapeHtml(new Date(item.updated_at).toLocaleString("zh-CN"))}</small></button>`).join("") : '<div class="empty-state">这里将保留你的每一稿作品。</div>';
    } catch (error) { $("history-list").textContent = "加载作品失败: " + error.message; }
}
let historySelectionToken = 0;
async function selectHistoryArticle(id) {
    const token = ++historySelectionToken;
    try {
        const article = await api("/api/articles/" + encodeURIComponent(id));
        if (token !== historySelectionToken) return;
        selectedHistory = article;
        const versions = selectedHistory.versions;
        const options = versions.map((version, index) => `<option value="${escapeHtml(version.version_id)}">第 ${index + 1} 稿 · ${escapeHtml(versionLabels[version.kind] || version.kind)} · ${version.article_chars} 字</option>`).join("");
        for (const control of ["history-version", "compare-left", "compare-right"]) $(control).innerHTML = options;
        $("history-version").value = selectedHistory.latest_version_id;
        $("compare-left").value = versions[Math.max(0, versions.length - 2)].version_id;
        $("compare-right").value = selectedHistory.latest_version_id;
        $("history-detail").classList.remove("hidden");
        $("history-comparison").classList.add("hidden");
        $("history-detail-title").textContent = selectedHistory.topic;
        $("history-detail-author").textContent = selectedHistory.author;
        selectHistoryVersion();
    } catch (error) { showToast(error.message, "error"); }
}
function selectedVersion() { return selectedHistory?.versions.find(version => version.version_id === $("history-version").value); }
function selectHistoryVersion() {
    const version = selectedVersion();
    if (!version) return;
    $("history-body").textContent = version.article;
    $("history-version-info").textContent = `${new Date(version.created_at).toLocaleString("zh-CN")} · ${version.review_summary.decision || "未评估"} · ${version.article_chars} 字`;
}
async function openHistoryInWorkshop() {
    const version = selectedVersion();
    if (!version || (activeJob && !terminalJobs.has(activeJob.status))) return showToast("请等待或取消当前任务", "info");
    document.querySelector('[data-tab="write"]').click();
    await loadAuthorSelect();
    applySavedVersion(version);
    $("write-author").value = version.author;
    $("write-topic").value = version.topic;
    for (const key of ["tone", "length"]) if (version.request[key]) $("write-" + key).value = version.request[key];
    versionHistory = selectedHistory.versions;
    renderVersionHistory();
    taskStorage.set("lastVersion", version.version_id);
}
async function restoreHistoryVersion() {
    const version = selectedVersion();
    if (!version) return;
    document.querySelector('[data-tab="write"]').click();
    await submitGeneration({kind:"restore", version_id:version.version_id});
}
async function compareHistoryVersions() {
    if (!selectedHistory) return;
    try {
        const params = new URLSearchParams({left:$("compare-left").value, right:$("compare-right").value});
        const data = await api(`/api/articles/${selectedHistory.id}/compare?${params}`);
        $("history-comparison").classList.remove("hidden");
        $("compare-left-body").textContent = data.left.article;
        $("compare-right-body").textContent = data.right.article;
        $("compare-diff").textContent = data.diff || "两个版本正文相同";
    } catch (error) { showToast(error.message, "error"); }
}

document.addEventListener("DOMContentLoaded", () => {
    $("job-list").addEventListener("click", event => {
        const button = event.target.closest("[data-job-id]");
        if (button) openJob(button.dataset.jobId).catch(error => showToast(error.message, "error"));
    });
    $("history-list").addEventListener("click", event => {
        const button = event.target.closest("[data-article-id]");
        if (button) selectHistoryArticle(button.dataset.articleId);
    });
});
