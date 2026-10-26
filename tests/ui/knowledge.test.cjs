const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const script = fs.readFileSync('static/js/app.js', 'utf8');
const template = fs.readFileSync('templates/dashboard.html', 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const ok = body => ({ ok: true, status: 200, json: async () => body });
const error = (status, message) => ({ ok: false, status, json: async () => ({error: message}) });
const item = { chunk_id:'one', material_id:1, title:'<script>bad()</script>', excerpt:'光合作用', location:{paragraph:1,chunk:1} };

async function setup(handler, role='teacher') {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => { if (!error.message.startsWith('Not implemented: navigation')) throw error; });
  const dom = new JSDOM(template, {url:'http://localhost/materials', runScripts:'outside-only', virtualConsole});
  const window = dom.window;
  window.Headers = Headers;
  window.TextDecoder = TextDecoder;
  window.sessionStorage.setItem('access_token', 'test-token');
  const requests=[];
  window.fetch = async (path, options) => {
    requests.push({path, options});
    if(path === '/api/me') return ok({user:{id:role==='teacher'?1:2,username:role==='teacher'?'a_teacher':'a_student',role,class_name:'A班'}});
    if(path === '/api/materials') return ok({materials:[]});
    return handler(path, options);
  };
  window.eval(script);
  await tick();
  const form=window.document.querySelector('[data-knowledge-form]');
  const submit=async (action='search', query='光合作用') => {
    form.elements.query.value=query;
    form.dispatchEvent(new window.SubmitEvent('submit',{bubbles:true,cancelable:true,
      submitter:form.querySelector(`button[value=${action}]`)}));
    await tick();
  };
  return {window, requests, submit, status:()=>window.document.querySelector('[data-knowledge-status]').textContent,
    results:()=>window.document.querySelector('[data-knowledge-results]')};
}

test('search defaults to hybrid with Bearer and safely renders source', async () => {
  const ui=await setup(async()=>ok({results:[item]}));
  await ui.submit();
  const request=ui.requests.find(x=>x.path==='/api/search');
  assert.equal(request.options.headers.get('Authorization'),'Bearer test-token');
  assert.equal(JSON.parse(request.options.body).mode,'hybrid');
  assert.equal(ui.results().querySelector('script'),null);
  assert.match(ui.results().textContent, /<script>bad/);
  ui.window.close();
});

test('answer citations load complete source through authenticated API', async () => {
  const ui=await setup(async path=>path.includes('/chunks/')?ok({...item,excerpt:'完整原文'}):
    ok({status:'answered',answer:'植物制造养分 [1]',citations:[{...item,number:1}]}));
  await ui.submit('ask');
  assert.match(ui.results().textContent,/植物制造养分 \[1\]/);
  ui.results().querySelector('button').click(); await tick();
  assert.match(ui.results().textContent,/完整原文/);
  assert.equal(ui.requests.at(-1).options.headers.get('Authorization'),'Bearer test-token');
  ui.window.close();
});

test('empty and insufficient evidence have explicit states', async()=>{
  let asking=false;
  const ui=await setup(async()=>ok(asking?{status:'insufficient_evidence'}:{results:[]}));
  await ui.submit(); assert.match(ui.status(),/暂无匹配/);
  asking=true; await ui.submit('ask'); assert.match(ui.status(),/暂无足够依据/);
  assert.equal(ui.results().children.length,0); ui.window.close();
});

for(const status of [409,502,503]) test(`HTTP ${status} is shown and a later request succeeds`,async()=>{
  let fail=true;
  const ui=await setup(async()=>fail?error(status,'请重试'):ok({results:[item]}));
  await ui.submit(); assert.equal(ui.status(),'请重试');
  fail=false; await ui.submit(); assert.match(ui.status(),/找到 1/); ui.window.close();
});

test('404 citation shows an unavailable notice',async()=>{
  const ui=await setup(async path=>path.includes('/chunks/')?error(404,'not found'):ok({results:[item]}));
  await ui.submit(); ui.results().querySelector('button').click(); await tick();
  assert.match(ui.window.document.querySelector('[data-notice]').textContent,/引用已失效/); ui.window.close();
});

test('late requests cannot overwrite newer results',async()=>{
  let resolveFirst;
  let count=0;
  const ui=await setup(async()=>++count===1?new Promise(resolve=>{resolveFirst=resolve;}):ok({results:[]}));
  await ui.submit(); await ui.submit();
  resolveFirst(ok({results:[item]})); await tick();
  assert.match(ui.status(),/暂无匹配/); assert.equal(ui.results().children.length,0); ui.window.close();
});

test('401 clears credential and visible evidence',async()=>{
  let expired=false;
  const ui=await setup(async()=>expired?error(401,'expired'):ok({results:[item]}));
  await ui.submit(); expired=true; await ui.submit();
  assert.equal(ui.window.sessionStorage.getItem('access_token'),null);
  assert.equal(ui.results().children.length,0); ui.window.close();
});

test('logout clears evidence before revocation completes',async()=>{
  const ui=await setup(async path=>path==='/api/auth/logout'?new Promise(()=>{}):ok({results:[item]}));
  await ui.submit();
  ui.window.document.querySelector('[data-logout]').click();
  assert.equal(ui.results().children.length,0); ui.window.close();
});

test('teacher coaching panel loads class prompt and protects required guidance skill',async()=>{
  let prompt='先询问学生思路';
  const skill={id:1,name:'解题引导',kind:'solution_guidance',instructions:'一步一步提示',enabled:1};
  const ui=await setup(async(path,options)=>{
    if(path==='/api/tutoring/config'&&options?.method==='PUT') { prompt=JSON.parse(options.body).teacher_prompt; return ok({}); }
    if(path==='/api/tutoring/config') return ok({teacher_prompt:prompt,skills:[skill]});
    if(path==='/api/tutoring/skills') return ok({skills:[skill]});
    return ok({results:[]});
  });
  const panel=ui.window.document.querySelector('[data-tutoring-teacher]');
  assert.equal(panel.hidden,true);
  ui.window.document.querySelector('[data-nav="settings"]').click();
  assert.equal(panel.hidden,false);
  assert.equal(panel.querySelector('[name=teacher_prompt]').value,prompt);
  assert.equal(panel.querySelectorAll('[data-skill-form]').length,1);
  panel.querySelector('[data-delete-skill]').click(); await tick();
  assert.match(panel.querySelector('[data-skill-status]').textContent,/至少保留一项/);
  assert.equal(ui.requests.some(request=>request.path==='/api/tutoring/skills/1'&&request.options.method==='DELETE'),false);
  ui.window.close();
});

test('student creates a private session and reads validated stream with bearer token',async()=>{
  let created=false;
  let answered=false;
  const ui=await setup(async(path,options)=>{
    if(path==='/api/tutoring/sessions'&&options?.method==='POST') { created=true; return ok({session:{id:7,title:'新会话'}}); }
    if(path==='/api/tutoring/sessions') return ok({sessions:created?[{id:7,title:'光合作用'}]:[]});
    if(path==='/api/tutoring/sessions/7') return ok({session:{id:7,title:'光合作用'},messages:answered?[
      {id:1,role:'user',content:'如何开始？',status:'completed'},
      {id:2,role:'assistant',content:'先说出你认为关键的一步。',status:'completed',citations:[]},
    ]:[]});
    if(path==='/api/tutoring/sessions/7/messages') {
      assert.equal(options.headers.Authorization,'Bearer test-token');
      const body='event: meta\ndata: {"message_id":3,"status":"completed"}\n\n'+
        'event: delta\ndata: {"text":"先说出你认为关键的一步。"}\n\n'+
        'event: citations\ndata: {"items":[]}\n\n'+
        'event: done\ndata: {"message_id":3}\n\n';
      answered=true;
      return new Response(body,{headers:{'Content-Type':'text/event-stream'}});
    }
    return ok({results:[]});
  },'student');
  const panel=ui.window.document.querySelector('[data-tutoring-student]');
  assert.equal(panel.hidden,false);
  panel.querySelector('[data-create-session]').click(); await tick(); await tick();
  const form=panel.querySelector('[data-tutoring-question-form]');
  assert.equal(form.hidden,false);
  form.elements.question.value='如何开始？';
  form.dispatchEvent(new ui.window.SubmitEvent('submit',{bubbles:true,cancelable:true}));
  await tick(); await tick(); await tick();
  assert.match(panel.querySelector('[data-session-messages]').textContent,/先说出你认为关键的一步/);
  assert.match(panel.querySelector('[data-tutoring-status]').textContent,/回答已完成/);
  ui.window.close();
});

test('student tutoring citation uses authenticated passage API and is not replaced if stale',async()=>{
  const citation={...item,number:1,source_url:'/api/knowledge/chunks/one'};
  const ui=await setup(async(path,options)=>{
    if(path==='/api/tutoring/sessions') return ok({sessions:[{id:9,title:'引用练习'}]});
    if(path==='/api/tutoring/sessions/9') return ok({session:{id:9,title:'引用练习'},messages:[
      {id:1,role:'user',content:'怎样理解？',status:'completed'},
      {id:2,role:'assistant',content:'先指出你观察到的条件。[1]',status:'completed',citations:[citation]},
    ]});
    if(path.includes('/chunks/')) {
      assert.equal(options.headers.get('Authorization'),'Bearer test-token');
      return error(404,'not found');
    }
    return ok({results:[]});
  },'student');
  const panel=ui.window.document.querySelector('[data-tutoring-student]');
  await tick(); await tick();
  const citationCard=panel.querySelector('.tutoring-citation');
  assert.ok(citationCard);
  citationCard.querySelector('button').click(); await tick();
  assert.match(citationCard.textContent,/引用已失效或不可访问/);
  assert.match(fs.readFileSync('static/css/app.css','utf8'),/@media \(max-width: 620px\)/);
  ui.window.close();
});

test('interrupted stream reloads only the complete persisted answer',async()=>{
  let sending=false;
  const ui=await setup(async(path,options)=>{
    if(path==='/api/tutoring/sessions') return ok({sessions:[{id:11,title:'恢复会话'}]});
    if(path==='/api/tutoring/sessions/11') return ok({session:{id:11,title:'恢复会话'},messages:sending?[
      {id:1,role:'user',content:'如何开始？',status:'completed'},
      {id:2,role:'assistant',content:'先检查题目给出的条件。',status:'completed',citations:[]},
    ]:[ ]});
    if(path==='/api/tutoring/sessions/11/messages') {
      sending=true;
      return new Response('event: meta\ndata: {"message_id":2}\n\nevent: delta\ndata: {"text":"局部"}\n\n',
        {headers:{'Content-Type':'text/event-stream'}});
    }
    return ok({results:[]});
  },'student');
  const panel=ui.window.document.querySelector('[data-tutoring-student]');
  await tick(); await tick();
  const form=panel.querySelector('[data-tutoring-question-form]');
  form.elements.question.value='如何开始？';
  form.dispatchEvent(new ui.window.SubmitEvent('submit',{bubbles:true,cancelable:true}));
  await tick(); await tick(); await tick();
  assert.match(panel.querySelector('[data-session-messages]').textContent,/先检查题目给出的条件/);
  assert.doesNotMatch(panel.querySelector('[data-session-messages]').textContent,/局部/);
  assert.match(panel.querySelector('[data-tutoring-status]').textContent,/连接中断/);
  ui.window.close();
});


test('role navigation isolates pages and keeps unsent work when switching',async()=>{
  for (const role of ['teacher','student']) {
    const ui=await setup(async(path)=>{
      if(path==='/api/tutoring/config') return ok({teacher_prompt:'本班辅导',skills:[]});
      if(path==='/api/tutoring/sessions') return ok({sessions:[]});
      return ok({results:[]});
    },role);
    const doc=ui.window.document;
    const visible=()=>[...doc.querySelectorAll('[data-view]')].filter(panel=>!panel.hidden).map(panel=>panel.dataset.view);
    assert.deepEqual(visible(),[role==='teacher'?'materials':'chat']);
    assert.equal(doc.querySelector(`[data-nav="${role==='teacher'?'chat':'settings'}"]`).hidden,true);
    doc.querySelector('[data-nav="knowledge"]').click();
    assert.deepEqual(visible(),['knowledge']);
    const query=doc.querySelector('[name="query"]'); query.value='保留尚未提交的问题';
    doc.querySelector('[data-nav="materials"]').click();
    assert.deepEqual(visible(),['materials']);
    assert.equal(doc.querySelector('[data-nav="materials"]').getAttribute('aria-current'),'page');
    doc.querySelector('[data-nav="knowledge"]').click();
    assert.equal(query.value,'保留尚未提交的问题');
    ui.window.location.hash=role==='teacher'?'chat':'settings';
    await tick();
    assert.deepEqual(visible(),[role==='teacher'?'materials':'chat']);
    ui.window.close();
  }
});

test('teacher saves class tools without exposing credentials or changing page drafts',async()=>{
  const ui=await setup(async(path,options)=>{
    if(path==='/api/integrations' && options?.method==='PUT') return ok({status:'saved'});
    if(path==='/api/integrations') return ok({tools:[{id:'builtin:calculator',name:'<script>工具</script>',
      source:'内置工具',description:'只读计算',enabled:false}],enabled_tools:[],errors:[],mcp_endpoint:'/mcp',model_support:true});
    if(path==='/api/tutoring/config') return ok({teacher_prompt:'',skills:[]});
    return ok({results:[]});
  });
  const doc=ui.window.document;
  doc.querySelector('[data-nav="integrations"]').click();
  const form=doc.querySelector('[data-integrations-form]');
  assert.equal(doc.querySelector('[data-view="integrations"]').hidden,false);
  assert.equal(form.querySelector('script'),null);
  assert.match(form.textContent,/<script>工具<\/script>/);
  assert.equal(doc.querySelector('[data-mcp-endpoint]').value,'http://localhost/mcp');
  form.querySelector('input').checked=true;
  form.dispatchEvent(new ui.window.SubmitEvent('submit',{bubbles:true,cancelable:true}));
  await tick();
  const saved=ui.requests.find(request=>request.path==='/api/integrations'&&request.options.method==='PUT');
  assert.deepEqual(JSON.parse(saved.options.body),{enabled_tools:['builtin:calculator']});
  assert.equal(saved.options.headers.get('Authorization'),'Bearer test-token');
  assert.match(doc.querySelector('[data-integrations-status]').textContent,/本班工具已保存/);
  ui.window.close();
});
