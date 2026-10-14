const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function context(fetcher) {
    const elements = new Map(), notices = [];
    const ctx = {TextDecoder, fetch: fetcher, notices, console, clearTimeout() {}, setTimeout() {return 1;},
        document: {querySelectorAll() {return [];}, addEventListener() {}, getElementById(id) {
            if (!elements.has(id)) elements.set(id, {value: '', checked: true, textContent: '', className: '',
                classList: {add() {}, remove() {}, toggle() {}}, disabled: false,
                querySelector(){return {setAttribute(){}};}});
            return elements.get(id);
        }}};
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), ctx);
    vm.runInContext('showToast = (msg, type) => notices.push({msg, type})', ctx);
    return ctx;
}

function streamResponse(events, splitBytes=false) {
    const raw = events.map(event => 'data: ' + JSON.stringify(event) + '\r\n\r\n').join('');
    const bytes = new TextEncoder().encode(raw);
    const buffers = splitBytes ? Array.from(bytes, byte => new Uint8Array([byte])) : [bytes];
    let index = 0;
    return {ok:true, body:{getReader(){return {
        async read(){return index < buffers.length ? {done:false,value:buffers[index++]} : {done:true};},
        async cancel(){}, releaseLock(){}
    };}}};
}

test('partial stream stays a draft and never reports success', async () => {
    const ctx = context(async () => streamResponse([{type:'chunk',content:'未完成草稿'}]));
    await assert.rejects(vm.runInContext('handleWriteStream({author:"writer",topic:"topic"})', ctx), /未收到完成确认/);
    assert.equal(vm.runInContext('lastArticle', ctx), '未完成草稿');
    assert.equal(vm.runInContext('lastAuthor', ctx), 'writer');
    assert.equal(ctx.notices.some(n => n.type === 'success'), false);
});

test('post-processing failure does not launch a second generation', async () => {
    const calls = [];
    const ctx = context(async url => {calls.push(url); return streamResponse([
        {type:'chunk',content:'draft'}, {type:'error',error:'保存失败'}]);});
    vm.runInContext('$("write-author").value="writer"; $("write-topic").value="topic";', ctx);
    await vm.runInContext('handleWrite()', ctx);
    assert.deepEqual(calls, ['/api/write-stream']);
    assert.equal(vm.runInContext('$("write-output").textContent', ctx), 'draft');
    assert.equal(vm.runInContext('$("btn-write").disabled', ctx), false);
    assert.equal(vm.runInContext('$("btn-rewrite").disabled', ctx), true);
});

test('HTTP errors do not launch a fallback generation', async () => {
    const calls = [];
    const ctx = context(async url => {calls.push(url); return {ok:false,status:400,async json(){return {error:'invalid'};}};});
    vm.runInContext('$("write-author").value="writer"; $("write-topic").value="topic";', ctx);
    await vm.runInContext('handleWrite()', ctx);
    assert.deepEqual(calls, ['/api/write-stream']);
    assert.equal(ctx.notices[0].msg, 'invalid');
});

test('split Unicode bytes and CRLF frames complete only on done', async () => {
    const ctx = context(async () => streamResponse([
        {type:'stage',stage:'generating'}, {type:'chunk',content:'正文🌙'}, {type:'stage',stage:'checking'},
        {type:'done',article:'正文🌙',author:'writer',topic:'topic',saved_path:'article.txt',plagiarism:{passed:null}}
    ], true));
    assert.equal(await vm.runInContext('handleWriteStream({author:"writer",topic:"topic"})', ctx), true);
    assert.equal(vm.runInContext('lastArticle', ctx), '正文🌙');
    assert.match(vm.runInContext('$("write-check").innerHTML', ctx), /检测未完成/);
    assert.equal(ctx.notices.filter(n => n.type === 'success').length, 1);
});

test('malformed events fail instead of silently reporting completion', async () => {
    const ctx = context(async () => ({ok:true,body:{getReader(){let done=false;return {
        async read(){if(done)return {done:true};done=true;return {done:false,value:new TextEncoder().encode('data: {broken\n\n')};},
        async cancel(){},releaseLock(){}
    };}}}));
    await assert.rejects(vm.runInContext('handleWriteStream({author:"writer",topic:"topic"})', ctx));
    assert.equal(ctx.notices.some(n => n.type === 'success'), false);
});

test('reuse evidence needs review and escapes article/source HTML', () => {
    const ctx = context();
    ctx.checked = {passed:null, originality_status:'needs_review', max_common:9,
        methods:{semantic:'not_checked', structure:'not_checked'}, coverage:{ratio:0.2},
        evidence:[{title:'<script>source</script>',source_type:'few_shot',kind:'near_text',
            generated:{text:'<img src=x onerror=alert(1)>'},source:{text:'<b>original</b>',line:3}}]};
    vm.runInContext('renderPlagiarismResult(checked)', ctx);
    const html = vm.runInContext('$("write-check").innerHTML', ctx);
    assert.match(html, /来源复用需要复核/);
    assert.match(html, /20\.0%/);
    assert.match(html, /固定示例/);
    assert.match(html, /&lt;img/);
    assert.doesNotMatch(html, /<script>|<img/);
    assert.match(html, /尚未检查/);
    assert.match(vm.runInContext('$("write-check").className', ctx), /warning/);
});

test('high quality score still displays failed length requirement', () => {
    const ctx = context();
    ctx.checked = {decision:'fail', score:99, requirement:{score:95,status:'fail'}, style:{score:99},plagiarism:{risk:'low'}};
    vm.runInContext('renderReviewResult(checked)', ctx);
    assert.match(vm.runInContext('$("write-review").innerHTML', ctx), /篇幅或空内容检查未通过/);
    assert.match(vm.runInContext('$("write-review").className', ctx), /warning/);
});

test('semantic candidate shows human controls and persisted escaped notes', () => {
    const ctx = context();
    ctx.hit = {evidence_id:'a'.repeat(64), kind:'semantic_candidate', similarity:0.8};
    ctx.checked = {version_id:'saved',reuse_reviews:[{evidence_id:'a'.repeat(64), decision:'independent', note:'<script>note</script>'}]};
    vm.runInContext('lastVersion=checked', ctx);
    const html = vm.runInContext('renderReuseReviewForm(hit)', ctx);
    assert.match(html, /人工意见/);
    assert.match(html, /value="independent" selected/);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
    assert.match(html, /不代表抄袭概率/);
});

test('review save is scoped to the selected immutable version', async () => {
    const calls = [];
    const ctx = context(async (url, options) => {
        calls.push({url, body:JSON.parse(options.body)});
        return {ok:true, async json(){return {ok:true,data:{reuse_reviews:[{evidence_id:'a'.repeat(64),decision:'independent'}],reuse_review_summary:{resolved:1,displayed_candidates:1}}};}};
    });
    ctx.hitId = 'a'.repeat(64);
    vm.runInContext('lastVersion={version_id:"saved",plagiarism:{passed:null}}; $("reuse-choice-"+hitId).value="independent"; $("reuse-note-"+hitId).value="事件不同";',ctx);
    await vm.runInContext('saveReuseReview(hitId)',ctx);
    assert.equal(calls[0].url, '/api/versions/saved/reuse-review');
    assert.equal(calls[0].body.note, '事件不同');
    assert.equal(vm.runInContext('lastVersion.reuse_reviews[0].decision',ctx), 'independent');
    assert.equal(ctx.notices[0].type, 'success');
});

test('a late review response cannot replace the newly selected version', async () => {
    let release;
    const ctx = context(() => new Promise(resolve => {release=resolve;}));
    ctx.hitId='a'.repeat(64);
    vm.runInContext('lastVersion={version_id:"first",plagiarism:{passed:null}}; $("reuse-choice-"+hitId).value="independent";',ctx);
    const saving=vm.runInContext('saveReuseReview(hitId)',ctx);
    vm.runInContext('lastVersion={version_id:"second"}',ctx);
    release({ok:true,async json(){return {ok:true,data:{reuse_reviews:[{decision:'independent'}]}};}});
    await saving;
    assert.equal(vm.runInContext('lastVersion.version_id',ctx),'second');
    assert.equal(vm.runInContext('lastVersion.reuse_reviews',ctx),undefined);
});
