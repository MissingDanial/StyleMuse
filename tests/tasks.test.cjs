const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function runtime(fetcher, persisted = new Map()) {
    const elements = new Map(), notices = [], timers = [];
    const context = {fetch:fetcher, TextDecoder, URLSearchParams, console, notices,
        crypto:{randomUUID(){return 'stable-submission-key';}},
        localStorage:{getItem(key){return persisted.get(key) || null;}, setItem(key,value){persisted.set(key,value);},removeItem(key){persisted.delete(key);}},
        setTimeout(callback){timers.push(callback);return timers.length;}, clearTimeout(){},
        document:{querySelectorAll(){return [];},querySelector(){return {click(){}};},addEventListener(){},
            getElementById(id){
                if(!elements.has(id)){
                    const classes=new Set();
                    elements.set(id,{value:'',textContent:'',innerHTML:'',checked:true,disabled:false,
                        classList:{add(c){classes.add(c);},remove(c){classes.delete(c);},toggle(c,enabled){enabled ? classes.add(c) : classes.delete(c);},contains(c){return classes.has(c);}}});
                }
                return elements.get(id);
            }}};
    vm.createContext(context);
    for(const file of ['app.js','tasks.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),context);
    vm.runInContext('showToast=(msg,type)=>notices.push({msg,type});',context);
    return {context, persisted, timers, evaluate:code=>vm.runInContext(code,context)};
}
function response(data){return {ok:true,async json(){return {ok:true,data};}};}
function job(extra={}){return {id:'job-id',kind:'write',status:'running',stage:'generating',author:'sample',topic:'topic',article:'草稿',payload:{},...extra};}
function version(extra={}){return {version_id:'version-id',article_id:'article-id',author:'sample',topic:'topic',article:'已保存正文',review:{decision:'pass'},plagiarism:{passed:true},request:{},...extra};}

test('opening a saved version restores the saved label without an active job',()=>{
    const rt=runtime();
    rt.context.saved=version();
    rt.evaluate('applySavedVersion(saved)');
    assert.match(rt.evaluate('$("write-saved-path").textContent'), /已保存/);
});

test('ambiguous submission retries with the same key and never falls back to legacy generation',async()=>{
    const calls=[];
    const rt=runtime(async(url,options)=>{
        calls.push({url,options});
        if(url==='/api/jobs' && calls.length===1) throw new Error('response lost');
        if(url==='/api/jobs') return response(job({status:'queued',stage:'queued'}));
        return response(job());
    });
    await rt.evaluate('submitGeneration({kind:"write",author:"sample",topic:"topic"})');
    assert.equal(rt.persisted.has('stylemuse.pendingSubmission'),true);
    await rt.evaluate('retryPendingSubmission()');
    const submitted=calls.filter(call=>call.url==='/api/jobs');
    assert.equal(submitted.length,2);
    assert.equal(submitted[0].options.headers['Idempotency-Key'],submitted[1].options.headers['Idempotency-Key']);
    assert.equal(calls.some(call=>call.url==='/api/write'||call.url==='/api/write-stream'),false);
    assert.equal(rt.persisted.has('stylemuse.pendingSubmission'),false);
});

test('page reload resumes a persisted job with GET only',async()=>{
    const persisted=new Map([['stylemuse.activeJob',JSON.stringify('job-id')]]),calls=[];
    const rt=runtime(async(url,options)=>{calls.push({url,options});return response(job());},persisted);
    await rt.evaluate('initializeTasks()');
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,'/api/jobs/job-id');
    assert.equal(calls[0].options.method,undefined);
    assert.equal(rt.evaluate('lastArticle'),'草稿');
    assert.equal(rt.evaluate('$("btn-write").disabled'),true);
});

test('lost submit response is recovered by key without a second POST',async()=>{
    const persisted=new Map([['stylemuse.pendingSubmission',JSON.stringify({key:'key',payload:{author:'sample',topic:'topic'}})]]),calls=[];
    const rt=runtime(async(url,options)=>{calls.push({url,options});return response(job());},persisted);
    await rt.evaluate('initializeTasks()');
    assert.equal(calls[0].url,'/api/jobs/by-key/key');
    assert.equal(calls.some(call=>call.options.method==='POST'),false);
    assert.equal(persisted.has('stylemuse.pendingSubmission'),false);
});

test('saving failure retains the draft and offers repair without enabling rewrite',()=>{
    const rt=runtime();
    rt.context.snapshot=job({status:'failed',stage:'saving',can_resume:true,error:'disk full'});
    rt.evaluate('renderJobSnapshot(snapshot)');
    assert.equal(rt.evaluate('$("write-output").textContent'),'草稿');
    assert.equal(rt.evaluate('$("btn-resume-job").classList.contains("hidden")'),false);
    assert.equal(rt.evaluate('$("btn-rewrite").disabled'),true);
});

test('completed task restores immutable version content and persisted last version',async()=>{
    const saved=version(),completed=job({status:'completed',stage:'completed',version_id:saved.version_id,article_id:saved.article_id,
        result:{version:saved},article:saved.article});
    const rt=runtime(async url=>response(url.startsWith('/api/articles/') ? {versions:[saved]} : url==='/api/jobs?limit=30' ? [] : completed));
    rt.context.completed=completed;
    await rt.evaluate('watchJob("job-id",completed)');
    assert.equal(rt.evaluate('lastVersion.version_id'),'version-id');
    assert.equal(JSON.parse(rt.persisted.get('stylemuse.lastVersion')),'version-id');
    assert.equal(rt.persisted.has('stylemuse.activeJob'),false);
});

test('rewrite uses the saved version instead of mutable article fields',async()=>{
    let submission;
    const rt=runtime(async(url,options)=>{
        if(url==='/api/jobs'){submission=JSON.parse(options.body);return response(job());}
        return response(job());
    });
    rt.context.saved=version();
    rt.evaluate('lastVersion=saved; lastArticle="tampered"; lastReview={decision:"pass"};');
    await rt.evaluate('startRewriteJob()');
    assert.equal(submission.version_id,'version-id');
    assert.equal(submission.article,undefined);
    assert.equal(submission.review,undefined);
});

test('network loss during polling preserves the task and schedules reconnect',async()=>{
    const rt=runtime(async()=>{throw new Error('offline');});
    await rt.evaluate('watchJob("job-id")');
    assert.equal(JSON.parse(rt.persisted.get('stylemuse.activeJob')),'job-id');
    assert.equal(rt.timers.length>0,true);
    assert.match(rt.evaluate('$("job-connection").textContent'),/后台任务仍保留/);
});

test('deleted task does not poll forever or keep the writing controls locked',async()=>{
    const persisted=new Map([['stylemuse.activeJob',JSON.stringify('missing')]]);
    const rt=runtime(async()=>({ok:false,status:404,async json(){return {ok:false,error:'missing'};}}),persisted);
    await rt.evaluate('initializeTasks()');
    assert.equal(persisted.has('stylemuse.activeJob'),false);
    assert.equal(rt.evaluate('$("btn-write").disabled'),false);
    assert.equal(rt.timers.length,0);
});
