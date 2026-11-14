// Real browser driver. Writes only via visible page controls; GETs inspect receipts.
// The server must be launched using validation/real_guard.py with an isolated ADW_DATA_DIR.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.ADW_REAL_URL || 'http://127.0.0.1:18789';
const root = process.env.ADW_REAL_EVIDENCE || '/private/tmp/adw-unified-real-20261110';
const action = process.argv[2] || 'inspect';
async function main() {
  fs.mkdirSync(root, {recursive:true});
  // Chrome owns the visible window; each test only attaches to its isolated profile.
  const profile=path.join(root,'browser-profile');
  const portFile=path.join(profile,'DevToolsActivePort');
  async function connect(){
    try {
      const [port,route]=fs.readFileSync(portFile,'utf8').trim().split(/\r?\n/);
      if(!/^\d+$/.test(port)||!route.startsWith('/devtools/browser/'))return null;
      return await chromium.connectOverCDP('ws://127.0.0.1:'+port+route,{timeout:2000});
    } catch {return null;}
  }
  let browser=await connect();
  if(!browser){
    const child=require('node:child_process').spawn(process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
      '--user-data-dir='+profile,'--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
      '--no-first-run','--no-default-browser-check','--disable-sync','--window-size=1500,1100',base+'/'
    ],{detached:true,stdio:'ignore'});
    child.unref();
    for(let i=0;i<50&&!browser;i++){await new Promise(r=>setTimeout(r,200));browser=await connect();}
  }
  assert(browser,'Could not attach to the visible workbench test browser');
  const context=browser.contexts()[0];
  const page=context.pages().find(p=>p.url().startsWith(base+'/')) || await context.newPage();
  await page.bringToFront();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {if(response.url().includes('/api/') && response.status() >= 500) errors.push(response.status()+' '+response.url());});
  const query = async (name,args={}) => {
    const response = await page.request.get(base+'/api/q/'+name,{params:{args:JSON.stringify(args)}});
    assert.equal(response.status(),200,await response.text());
    return response.json();
  };
  async function saveAuthorizedCreator(menu){
    for(const [label,value] of [['创作执行器','codex'],['创作型号','gpt-6.1-sol'],['创作推理强度','high']]){
      await menu.getByLabel(label,{exact:true}).selectOption(value);
    }
    const save=menu.getByRole('button',{name:'保存创作设置'});
    if(await save.isEnabled()){
      const response=page.waitForResponse(r=>r.url().endsWith('/cmd/save_stage_executor'));
      await save.click();assert.equal((await response).status(),200);
    }
  }
  try {
    await context.tracing.start({screenshots:true,snapshots:true});
    const home=['existing-topic','series-style','create-original','create-locked'].includes(action);
    if(home||process.env.ADW_REAL_HASH||!page.url().startsWith(base+'/'))await page.goto(base+'/'+(process.env.ADW_REAL_HASH || ''));
    await page.waitForLoadState('networkidle');
    const storage = await query('storage_status');
    assert.equal(storage.dataDir,path.join(root,'data'));
    const models = await query('model_catalog');
    assert(models.text.codex.models.find(m=>m.id==='gpt-6.1-sol').efforts.includes('high'));
    assert(models.image['codex-native']);
    if(action==='executor-settings'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const counts=async()=>{const view=await query('episode_view',{episode_id:episodeId});
        return {runs:view.artifacts.flatMap(a=>a.tasks.flatMap(t=>t.runs.map(r=>r.id))).sort(),
          batches:(await query('episode_batches',{episode_id:episodeId})).map(b=>b.id).sort()};};
      const before=await counts();
      const records=[];
      const writes=[];
      page.on('request',r=>{if(r.method()==='POST' && r.url().includes('/api/'))writes.push(new URL(r.url()).pathname);});
      await page.goto(base+'/#/e/'+episodeId+'/prompts');await page.reload();
      const menu=page.locator('details.executor-settings:visible');
      async function openMenu(){await menu.waitFor();if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();}
      async function read(role){
        const field=menu.locator('fieldset').filter({has:page.getByText(role,{exact:true})});
        const provider=await field.getByLabel(role+'执行器').inputValue();
        const model=await field.getByLabel(role+'型号').inputValue();
        const effort=field.getByLabel(role+'推理强度');
        return {provider,model:model||null,effort:await effort.count()?(await effort.inputValue()||null):null};
      }
      async function save(role,slot){
        await menu.getByLabel(role+'执行器').selectOption(slot.provider);
        await menu.getByLabel(role+'型号').selectOption(slot.model??'');
        if(slot.effort)await menu.getByLabel(role+'推理强度').selectOption(slot.effort);
        const button=menu.getByRole('button',{name:'保存'+role+'设置'});
        if(await button.isEnabled()){
          const response=page.waitForResponse(r=>r.url().endsWith('/cmd/save_stage_executor'));
          await button.click();const res=await response;assert.equal(res.status(),200,await res.text());
          assert.deepEqual(await res.json(),slot);
        }
      }
      async function reopen(){await page.reload();await openMenu();}
      await openMenu();
      const providers=Object.keys(models.text);
      for(const role of ['创作','独审'])assert.deepEqual(await menu.getByLabel(role+'执行器').locator('option').evaluateAll(nodes=>nodes.map(n=>n.value)),providers);
      assert(providers.includes('claude') && providers.includes('antigravity'));
      const initialReview=await read('独审');
      const create={provider:'codex',model:'gpt-6.1-sol',effort:'low'};
      const review={provider:'antigravity',model:models.text.antigravity.models.find(m=>m.id)?.id??null,effort:null};
      await save('创作',create);await reopen();
      assert.deepEqual(await read('创作'),create);assert.deepEqual(await read('独审'),initialReview);
      await save('独审',review);await reopen();
      assert.deepEqual(await read('独审'),review);assert.deepEqual(await read('创作'),create);
      records.push({create,review});
      await page.goto(base+'/#/e/'+episodeId+'/review');await page.reload();
      assert.equal(await page.getByLabel('提示词独审执行器').inputValue(),review.provider);
      assert.equal(await page.getByLabel('提示词独审型号').inputValue(),review.model??'');
      await page.goto(base+'/#/e/'+episodeId+'/prompts');await reopen();
      const authorized={provider:'codex',model:'gpt-6.1-sol',effort:'high'};
      await save('独审',authorized);await reopen();
      assert.deepEqual(await read('创作'),create);assert.deepEqual(await read('独审'),authorized);
      await save('创作',authorized);await reopen();
      for(const role of ['独审','创作'])assert.deepEqual(await read(role),authorized);
      const after=await counts();assert.deepEqual(after,before);
      assert(writes.every(url=>['/api/cmd/save_stage_executor','/api/cmd/visit_project'].includes(url)),JSON.stringify(writes));
      const evidence={providers,records,restored:authorized,reviewPageMatched:true,runCount:after.runs.length,batchCount:after.batches.length,writes};
      await menu.screenshot({path:path.join(root,'executor-settings.png')});
      fs.writeFileSync(path.join(root,'executor-settings.json'),JSON.stringify(evidence,null,2));
      console.log('SETTINGS_PASSED',JSON.stringify(evidence));
    }
    if(action==='existing-topic'){
      const series=JSON.parse(fs.readFileSync(path.join(root,'series-style.json')));
      const existing=await query('episode_view',{episode_id:series.episodeId});
      const pid=existing.episode.project_id;
      const saved=path.join(root,'existing-topic.json');
      assert(!fs.existsSync(saved),'Topic already started');
      await page.locator('.home-controls button').first().click();
      await page.locator('.pop').getByRole('button',{name:'PUGGY ROOM',exact:true}).click();
      await page.getByRole('button',{name:'选题',exact:true}).click();
      const code='UI-雨后脚印';
      const topics=await query('topics',{project_id:pid});
      if(topics.some(t=>t.code===code))await page.getByRole('button',{name:new RegExp(code+' .*选用')}).click();
      else{
        await page.getByPlaceholder('选题编号').fill(code);
        await page.getByPlaceholder('故事前提').fill('雨后门口的小泥脚印越来越多，MILO跟着痕迹寻找来客，却发现是自己的尾巴一路扫过泥水留下的痕迹。');
        await page.getByRole('button',{name:'新建并选用',exact:true}).click();
      }
      await page.getByRole('button',{name:/规格预设/}).click();
      await page.getByRole('button',{name:/调整完整开工设置/}).click();
      await page.getByRole('button',{name:'本集调整',exact:true}).click();
      for(const role of ['故事 写','审']){
        for(const [suffix,value] of [['执行器','codex'],['型号','gpt-6.1-sol'],['推理强度','high']]){
          const field=page.getByLabel(role+suffix,{exact:true});
          await field.selectOption(value);
          await page.waitForFunction(({label,value})=>document.querySelector('[aria-label="'+label+'"]').value===value,{label:role+suffix,value});
        }
      }
      await page.getByRole('button',{name:'开工并开始创作',exact:true}).click();
      await page.waitForURL(/#\/e\/[^/]+\/story/);
      const eid=page.url().split('/').at(-2);
      const v=await query('episode_view',{episode_id:eid});
      assert.equal(v.episode.project_id,pid);
      assert.equal(v.kickoff.source_type,'topic');
      const topic=(await query('topics',{project_id:pid})).find(t=>t.id===v.kickoff.topic_id);
      assert.equal(topic.code,code);
      fs.writeFileSync(saved,JSON.stringify({eid,topic,workflow:v.workflow,kickoff:v.kickoff},null,2));
      await page.goto(base+'/');
      await page.getByRole('button',{name:/修改进行中的一段/}).click();
      await page.waitForURL(url=>url.hash==='#/e/'+eid+'/story');
      console.log('RETURNED_TO_TOPIC',eid);
    }
    if (action === 'series-style') {
      const saved = path.join(root,'series-style.json');
      assert(!fs.existsSync(saved),'This episode already has a kickoff receipt');
      const style = '阴天窗边自然光，保留橘猫暖色毛发，蓝灰地砖，镜头保持在猫的视线高度。';
      await page.locator('.home-controls button').first().click();
      await page.getByText('用模板新建项目',{exact:true}).click();
      await page.getByRole('button',{name:/新项目（PUGGY ROOM 配置）/}).click();
      await page.getByRole('button',{name:'风格',exact:true}).click();
      await page.getByLabel('本集风格说明').fill(style);
      await page.getByRole('button',{name:'应用到本集',exact:true}).click();
      await page.locator('textarea').first().fill('MILO发现一辆装着面包的手推车慢慢滑向水坑，尝试用现场已有的物件让它停下。写清动作、失败和最后停下的原因，保持项目角色设定。');
      await page.getByRole('button',{name:/规格预设/}).click();
      await page.getByRole('button',{name:/调整完整开工设置/}).click();
      await page.getByRole('button',{name:'本集调整',exact:true}).click();
      for(const role of ['故事 写','审']) {
        for(const [suffix,value] of [['执行器','codex'],['型号','gpt-6.1-sol'],['推理强度','high']]) {
          const field=page.getByLabel(role+suffix,{exact:true});
          await field.selectOption(value);
          await page.waitForFunction(({label,value})=>document.querySelector('[aria-label="'+label+'"]').value===value,{label:role+suffix,value});
        }
      }
      await page.screenshot({path:path.join(root,'series-model-style-kickoff.png')});
      await page.getByRole('button',{name:'开工并开始创作',exact:true}).click();
      await page.waitForURL(/#\/e\/[^/]+\/story/);
      const episodeId=page.url().split('/').at(-2);
      const view=await query('episode_view',{episode_id:episodeId});
      fs.writeFileSync(saved,JSON.stringify({episodeId,kickoff:view.kickoff,workflow:view.workflow},null,2));
      assert.equal(view.kickoff.spec.style,style);
      assert.equal(view.workflow.method,'unified-film@draft');
      console.log('SERIES_STYLE_STARTED',episodeId);
    }
    if(action==='edit-locked-portrait'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const source=JSON.parse(fs.readFileSync(path.join(root,'baseline-C02.json'))).assetVersionId;
      const previous=(await query('episode_batches',{episode_id:episodeId})).find(b=>b.operation==='edit' && b.spec.items[0].inputs.includes(source));
      await page.goto(base+'/#/e/'+episodeId+'/assets');
      await page.getByRole('button',{name:'本轮出图',exact:true}).click();
      let batchId=previous?.id;
      if(!batchId){
        for (const mode of ['灰度','手机尺寸','并排']) {
          await page.getByRole('button',{name:mode,exact:true}).click();
          assert(await page.locator('.candidate-layout img').count()>0);
        }
        const side=page.getByLabel('左侧比较图');
        await side.selectOption(source);
        const card=page.locator('.candidate-layout > div').filter({has:side});
        await card.getByRole('button',{name:'带图修改',exact:true}).click();
        await page.getByLabel('图像任务创作型号').selectOption('gpt-6.1-sol');
        assert.equal(await page.getByLabel('图像任务推理强度').inputValue(),'high');
        await page.getByLabel('绘图要求').fill('保留单人全身照中同一位老太太的脸、发型、米色外套、裤鞋、站姿、背景和黄昏暖灰光线。只移除她的眼镜，眼睛真实清晰，不拿任何东西。这是同一角色戴眼镜之前的状态 C02-S01。');
        const made=page.waitForResponse(r=>r.url().endsWith('/cmd/create_batch'));
        await page.getByRole('button',{name:'生成摘要，去确认',exact:true}).click();
        batchId=(await (await made).json()).batchId;
        assert(batchId);
        const frozen=await query('batch_view',{batch_id:batchId});
        assert.deepEqual(frozen.spec.items[0].inputs,[source]);
        await page.getByRole('button',{name:'确认授权 1 张',exact:true}).click();
        await page.getByRole('button',{name:'发送已授权的 1 张',exact:true}).click();
      }
      const receipt=await query('batch_view',{batch_id:batchId});
      fs.writeFileSync(path.join(root,'locked-edit-portrait.json'),JSON.stringify(receipt,null,2));
      if(receipt.state==='completed'){
        await page.getByRole('button',{name:'并排',exact:true}).click();
        await page.getByLabel('左侧比较图').selectOption(source);
        await page.getByLabel('右侧比较图').selectOption(receipt.requests[0].asset_version_id);
        const shots=page.locator('.candidate-layout .candidate-preview img');
        assert.equal(await shots.count(),2);
        assert.notEqual(await shots.first().getAttribute('src'),await shots.last().getAttribute('src'));
        await page.screenshot({path:path.join(root,'locked-edit-before-after.png')});
      }
      console.log('EDIT_BATCH',batchId,receipt.state);
    }
    if(action==='locked-segment-cast'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const view=await query('episode_view',{episode_id:episodeId});
      const artifact=view.artifacts.find(a=>a.stage==='plan');
      assert(artifact.adopted_version_id,'Adopt the checked plan before binding segments');
      const plan=artifact.versions.find(v=>v.id===artifact.adopted_version_id).content;
      await page.goto(base+'/#/e/'+episodeId+'/assets');
      await page.getByRole('link',{name:'角色出镜与身份覆盖',exact:true}).click();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      for(const seg of plan.segments){
        const visible=new Set(plan.shots.filter(s=>s.segment===seg.id).flatMap(s=>s.visibleRoles));
        for(const role of ['C01','C02']){
          const field=page.getByLabel(seg.id+' '+role+' 身份图',{exact:true});
          const cell=field.locator('xpath=ancestor::td[1]');
          const status=visible.has(role)?'身份清楚':'未出镜';
          if(visible.has(role)){
            const base=JSON.parse(fs.readFileSync(path.join(root,'baseline-'+role+'.json')));
            await field.selectOption(base.assetVersionId);
            await cell.getByLabel('对照观察').fill('使用本集已核对的身份原图。'+base.appearance);
          }
          const request=page.waitForResponse(r=>r.url().endsWith('/cmd/set_cast'));
          await cell.getByRole('button',{name:status,exact:true}).click();
          const response=await request;
          assert.equal(response.status(),200,await response.text());
        }
      }
      await page.reload();
      const matrix=await query('cast_matrix',{episode_id:episodeId});
      for(const row of matrix.rows)for(const role of ['C01','C02'])assert(['absent','ok'].includes(row.cells[role]?.status));
      fs.writeFileSync(path.join(root,'locked-segment-cast.json'),JSON.stringify(matrix,null,2));
      await page.screenshot({path:path.join(root,'locked-segment-cast.png')});
    }
    if(action==='rebuild-locked-table'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/plan');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const plan=async()=>(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage==='plan').versions.at(-1);
      const old=await plan();
      assert(old.gate.formatErrors.some(e=>e.includes('联合表')));
      await page.getByText('镜头充分性与节奏核对',{exact:true}).click();
      const response=page.waitForResponse(r=>r.url().endsWith('/cmd/save_manual'));
      await page.getByRole('button',{name:'由镜头字段重建',exact:true}).click();
      assert.equal((await response).status(),200);
      const current=await plan();
      assert.deepEqual(current.content.shots,old.content.shots);
      assert.deepEqual(current.content.segments,old.content.segments);
      fs.writeFileSync(path.join(root,'locked-rebuilt-plan.json'),JSON.stringify(current,null,2));
      console.log('REBUILT',JSON.stringify(current.gate));
      assert(current.gate.adoptable);
    }
    if(action==='palette-inheritance'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      const pid=(await query('episode_view',{episode_id:episodeId})).episode.project_id;
      const name='黄昏暖灰';
      await page.goto(base+'/#/palette?project='+pid);
      await page.getByLabel('搜索资料').fill(name);
      await page.getByRole('button',{name:'搜索',exact:true}).click();
      const card=page.locator('.director-card').filter({hasText:name});
      const enabled=card.getByLabel('用于光色与构图');
      await enabled.waitFor();
      if(!await enabled.isChecked()){
        await enabled.check();
        const written=page.waitForResponse(r=>r.url().endsWith('/cmd/save_palettes'));
        await page.getByRole('button',{name:'用于本项目',exact:true}).click();
        assert.equal((await written).status(),200);
      }
      await page.goto(base+'/#/e/'+episodeId+'/assets');
      const evidence=[];
      for(const mode of ['inherit','off','custom','inherit']){
        await page.getByRole('button',{name:'本集色卡',exact:true}).click();
        await page.getByLabel('本集色卡使用方式').selectOption(mode);
        if(mode==='custom'){
          await page.getByLabel('搜索本阶段色卡').fill(name);
          await page.getByLabel(name,{exact:true}).check();
        }
        await page.getByRole('button',{name:'保存本集选择',exact:true}).click();
        await page.getByLabel('本集色卡使用方式').waitFor({state:'hidden'});
        await page.reload();
        const state=await query('episode_palettes',{episode_id:episodeId,stage:'assets'});
        assert.equal(state.mode,mode);
        assert.equal(state.cards.some(c=>c.name===name),mode!=='off');
        evidence.push({mode:state.mode,cards:state.cards.map(c=>c.name)});
      }
      const other=await query('episode_palettes',{episode_id:episodeId,stage:'prompts'});
      assert.equal(other.mode,'inherit');
      fs.writeFileSync(path.join(root,'palette-inheritance.json'),JSON.stringify({episodeId,evidence,otherStage:other.mode},null,2));
    }
    if(action==='locked-observations'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const matrix=JSON.parse(fs.readFileSync(path.join(root,'locked-segment-cast.json')));
      const latest=async()=>(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage==='prompts').versions.at(-1);
      await page.goto(base+'/#/e/'+episodeId+'/prompts');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      for(const row of matrix.rows){
        await page.getByRole('button',{name:new RegExp('^'+row.segment+' ·')}).click();
        for(const role of Object.keys(row.cells)){
          await page.getByRole('button',{name:new RegExp('^'+row.segment+' ·')}).click();
          await page.getByLabel(row.segment+' 提示词正文',{exact:true}).waitFor();
          const v=await latest();
          const base=matrix.roster[role];
          const ref=v.content.segments[row.segment].refs.find(r=>r.assetVersionId===base.assetVersionId);
          assert(ref,'missing bound '+role+' '+row.segment);
          if(ref.visualReviewId)continue;
          const summary=page.locator('summary').filter({hasText:new RegExp('^'+ref.label+' · .*看图与作用镜头')});
          await summary.click();
          const box=summary.locator('..');
          const img=box.locator('img');
          await img.waitFor();
          assert(await img.evaluate(n=>n.complete&&n.naturalWidth>0),'image not loaded');
          await box.getByLabel('看图观察').fill(base.appearance+(role==='C02'?'中性灰背景，全身正面站立，双手自然下垂，未持手机或饭盒。眼镜的佩戴动作由剧本逐镜决定。':'中性灰背景，全身正面站立，两手空着，未持手机或送餐包。'));
          if(role==='C02')await box.getByLabel('显眼道具').fill('p04=仅外观');
          const resp=page.waitForResponse(r=>r.url().endsWith('/cmd/save_shot_observation'));
          await box.getByRole('button',{name:'保存看图记录为提示词新版'}).click();
          const saved=await resp;assert.equal(saved.status(),200,await saved.text());
          const {versionId}=await saved.json();
          await page.waitForFunction(id=>document.querySelector('textarea[data-version]')?.dataset.version===id,versionId);
        }
      }
      const v=await latest();
      fs.writeFileSync(path.join(root,'locked-observed-prompts.json'),JSON.stringify(v,null,2));
      console.log('OBSERVED_GATE',JSON.stringify(v.gate));
    }
    if(action==='locked-folder'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const {delivery}=JSON.parse(fs.readFileSync(path.join(root,'export-full.json')));
      const episode=await query('episode_view',{episode_id:episodeId});
      const project=(await query('list_projects')).find(p=>p.id===episode.episode.project_id);
      const folder=path.join(root,'独立项目验收',delivery.deliveryId);
      await page.goto(base+'/#/projects');
      if(!fs.existsSync(path.join(folder,'导演工作台项目.json'))){
        await page.getByRole('button',{name:'管理项目 '+project.name,exact:true}).click();
        await page.getByRole('button',{name:'另存独立项目',exact:true}).click();
        await page.getByLabel('新项目文件夹绝对路径').fill(folder);
        const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/cmd/export_project_folder')),page.getByRole('button',{name:'另存项目',exact:true}).click()]);
        assert.equal(response.status(),200,await response.text());
        await page.getByRole('status').filter({hasText:folder}).waitFor();
        await page.getByRole('button',{name:'关闭',exact:true}).click();
      }
      await page.getByText('打开项目文件夹',{exact:true}).click();
      await page.getByLabel('项目文件夹绝对路径',{exact:true}).fill(folder);
      await page.getByRole('button',{name:'打开目录',exact:true}).click();
      const enter=page.getByRole('link',{name:'进入独立项目',exact:true});
      await enter.waitFor();
      const [external]=await Promise.all([context.waitForEvent('page'),enter.click()]);
      await external.getByText('独立项目 · 保存到 '+folder,{exact:true}).waitFor();
      const origin=new URL(external.url()).origin;
      assert.equal(new URL(origin).hostname,'127.0.0.1');
      await external.goto(origin+'/#/e/'+episodeId+'/package?delivery='+delivery.deliveryId);
      await external.getByText('整集图文已交付 · 5 段',{exact:true}).waitFor();
      await external.getByRole('button',{name:'预览所选段',exact:true}).click();
      await external.getByRole('heading',{name:'出包预览 · 5 段',exact:true}).waitFor();
      const display=await external.getByRole('region',{name:'出包预览'}).innerText();
      assert(['G01','G02','G03','G04','G05'].every(id=>display.includes(id)));
      await external.screenshot({path:path.join(root,'independent-full-preview.png')});
      fs.writeFileSync(path.join(root,'independent-full-preview.json'),JSON.stringify({episodeId,deliveryId:delivery.deliveryId,folder,display},null,2));
      await page.bringToFront();
    }
    if(action==='locked-decisions'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/review');
      await page.reload();
      await page.getByRole('heading',{name:'两遍独审',exact:true}).waitFor();
      const reason='导演选择按当前提示词交付。两遍审查记录保留；角色身份照只能核对外观，实际轴线、站位及动作衔接在生成视频后复核。';
      for(const seg of ['G01','G03','G04','G05']){
        const card=page.locator('.card:visible').filter({has:page.locator('b').filter({hasText:new RegExp('^'+seg+'$')})});
        await card.waitFor();
        if(await card.getByText('已放行：'+reason,{exact:false}).count())continue;
        const field=card.getByPlaceholder('写理由放行（绑定本段输入，改动后失效）');
        await field.fill(reason);
        const [r]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/cmd/waive_segment_issue')),card.getByRole('button',{name:'放行',exact:true}).click()]);assert.equal(r.status(),200,await r.text());
        await card.getByText('已放行：'+reason,{exact:false}).waitFor();
      }
      await page.screenshot({path:path.join(root,'locked-director-decisions.png'),fullPage:true});
    }
    if(action==='read-palette-candidate'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const {runId}=JSON.parse(fs.readFileSync(path.join(root,'prompt-source-write.json')));
      const art=(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage==='prompts');
      const v=art.versions.find(v=>v.source_run_id===runId);
      const adopted=art.versions.find(v=>v.id===art.adopted_version_id);
      assert(v && v.id!==adopted.id);
      const evidence={candidate:v.id,adopted:adopted.id,gate:v.gate,segments:{}};
      await page.goto(base+'/#/e/'+episodeId+'/prompts?v='+v.id+'&segment=G02');await page.reload();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      for(const id of ['G01','G03','G04','G05','G02']){
        await page.getByRole('button',{name:new RegExp('^'+id+' ·')}).click();
        const field=page.getByRole('textbox',{name:id+' 提示词正文',exact:true});
        await page.waitForFunction(([label,txt])=>[...document.querySelectorAll('textarea')].some(n=>n.getAttribute('aria-label')===label && n.value===txt),[id+' 提示词正文',v.content.segments[id].txt]);
        const txt=await field.inputValue();
        for(const term of ['#23454B','#E6C99F','#74746D','稳定自然曝光'])assert(txt.includes(term));
        assert.deepEqual(v.content.segments[id].refs,adopted.content.segments[id].refs);
        const details=page.locator('details:visible').filter({has:page.getByText('本段制作备注与核对依据',{exact:true})});
        if(!await details.evaluate(n=>n.open))await details.locator('summary').click();
        assert.equal(await details.locator('p').textContent(),v.content.segments[id].notes);
        evidence.segments[id]={color:txt.slice(txt.indexOf('黄昏暖灰')-3,txt.indexOf('黄昏暖灰')+125),refs:v.content.segments[id].refs,notes:v.content.segments[id].notes};
      }
      await page.screenshot({path:path.join(root,'palette-candidate-ui.png')});
      fs.writeFileSync(path.join(root,'palette-candidate-ui.json'),JSON.stringify(evidence,null,2));
    }
    if(action==='asset-choice-roundtrip'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const args={episode_id:episodeId};
      const before=await query('episode_assets',args);
      const prompt=(await query('episode_view',args)).artifacts.find(a=>a.stage==='prompts');
      const used=Object.values(prompt.versions.find(v=>v.id===prompt.adopted_version_id).content.segments)
        .flatMap(s=>s.refs.map(r=>r.assetVersionId));
      const target=before.find(a=>a.kind==='image' && !a.marks.length && !used.includes(a.id));
      assert(target,'Requires an unused candidate');
      async function selectCandidate(tab){
        await page.getByRole('button',{name:tab,exact:true}).click();
        await page.getByRole('button',{name:'并排',exact:true}).click();
        const side=page.getByLabel('左侧比较图');
        await side.selectOption(target.id);
        return page.locator('.candidate-layout > div').filter({has:side});
      }
      await page.goto(base+'/#/e/'+episodeId+'/assets');await page.reload();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      let card=await selectCandidate('本轮出图');
      await card.getByRole('button',{name:'标必用'}).click();
      const save=page.getByRole('button',{name:'保存必用要求'});
      assert(await save.isDisabled());
      await page.getByLabel('选择理由').fill('仅核对G02中人物服装轮廓');
      await page.getByLabel('G02',{exact:true}).check();
      await page.getByLabel('用途适配').selectOption('limited');
      assert(await save.isDisabled());
      await page.getByLabel('用途限制或缺陷范围').fill('身份图无法证明取手机的位置');
      await save.click();await card.getByRole('button',{name:'编辑必用要求'}).waitFor();
      await page.reload();card=await selectCandidate('本轮出图');
      await card.getByRole('button',{name:'编辑必用要求'}).click();
      assert(await page.getByLabel('G02',{exact:true}).isChecked());
      assert.equal(await page.getByLabel('用途限制或缺陷范围').inputValue(),'身份图无法证明取手机的位置');
      const applied=(await query('episode_assets',args)).find(a=>a.id===target.id);
      assert.deepEqual(applied.requirement.targetSegments,['G02']);
      await page.getByRole('button',{name:'取消必用'}).click();
      await card.getByRole('button',{name:'标必用'}).waitFor();
      await card.getByRole('button',{name:'补选',exact:true}).click();
      await page.waitForFunction(id=>![...document.querySelectorAll('[aria-label="左侧比较图"] option')].some(o=>o.value===id),target.id);
      card=await selectCandidate('补选区');
      await card.getByRole('button',{name:'取消补选'}).waitFor();
      await page.reload();card=await selectCandidate('补选区');
      await card.getByRole('button',{name:'取消补选'}).click();
      card=await selectCandidate('本轮出图');await card.getByRole('button',{name:'补选',exact:true}).waitFor();
      const restored=(await query('episode_assets',args)).find(a=>a.id===target.id);
      assert.deepEqual(restored.marks,target.marks);
      const current=(await query('episode_view',args)).artifacts.find(a=>a.stage==='prompts');
      assert.equal(current.adopted_version_id,prompt.adopted_version_id);
      fs.writeFileSync(path.join(root,'asset-choice-roundtrip.json'),JSON.stringify({
        episodeId,asset:target.id,name:target.name,requirement:applied.requirement,
        restoredMarks:restored.marks,adoptedPrompt:current.adopted_version_id},null,2));
    }
    if(action==='native-grid' || action==='native-grid-edit'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const edit=action==='native-grid-edit';
      const name='G03 公交站牌细节九宫格';
      const args={episode_id:episodeId};
      const batches=await query('episode_batches',args);
      const batch=batches.find(b=>b.operation===(edit?'edit':'generate') && b.spec.items[0].target?.name===name);
      await page.goto(base+'/#/e/'+episodeId+'/assets');await page.reload();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      let id=batch?.id;
      if(!batch){
        if(edit){
          const baseAsset=(await query('episode_assets',args)).find(a=>a.grid && a.name===name && a.version_no===1);
          assert(baseAsset);
          await page.getByRole('button',{name:'并排',exact:true}).click();
          const side=page.getByLabel('左侧比较图');
          await side.selectOption(baseAsset.id);
          await page.locator('.candidate-layout > div').filter({has:side}).getByRole('button',{name:'修一格'}).click();
        } else {
          await page.getByRole('button',{name:'出图',exact:true}).click();
          await page.getByLabel('素材名称').fill(name);
          await page.getByLabel('素材分类').selectOption('grid');
        }
        await page.getByLabel('图像任务创作型号').selectOption('gpt-6.1-sol');
        assert.equal(await page.getByLabel('图像任务推理强度').inputValue(),'high');
        await page.getByLabel('绘图要求').fill(edit?'第五格的站牌号码换成清楚的大号12，其余格完全保留。':'同一个黄昏公交站，九个分别取景的站牌道具特写。中心格拍医院方向的12路号码，其他格分别表现线路图、站牌边框、支柱底脚、金属扣、玻璃反光、雨滴、方向箭头、路沿。写实，无人物。');
        await page.getByPlaceholder(/例：G04/).fill('G03');
        const response=page.waitForResponse(r=>r.url().endsWith('/cmd/'+(edit?'redraw_cells':'create_batch')));
        await page.getByRole('button',{name:'生成摘要，去确认'}).click();
        id=(await (await response).json()).batchId;assert(id);
        const receipt=await query('batch_view',{batch_id:id});
        assert.equal(receipt.provider,'codex-native');assert.equal(receipt.model,'gpt-6.1-sol');
        assert.equal(receipt.spec.items[0].target.grid,true);
        if(edit)assert.deepEqual(receipt.spec.items[0].cells,[5]);
        await page.getByRole('button',{name:'确认授权 1 张'}).click();
        await page.getByRole('button',{name:'发送已授权的 1 张'}).click();
      }
      const status=await query('batch_view',{batch_id:id});
      fs.writeFileSync(path.join(root,action+'.json'),JSON.stringify(status,null,2));
      console.log('GRID_BATCH',id,status.state,status.requests[0].state);
    }
    if(action==='optional-and-modes'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/world');await page.reload();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const view=()=>query('episode_view',{episode_id:episodeId});
      const original=await view();
      for(const value of ['beginner','expert',original.cooperationMode]){
        const select=page.locator('.catalog:visible').getByRole('combobox',{name:/合作档位/});
        if(await select.inputValue()!==value){
          const [r]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/cmd/set_cooperation_mode')),select.selectOption(value)]);
          assert.equal(r.status(),200,await r.text());
        }
        await page.reload();
        assert.equal((await view()).cooperationMode,value);
      }
      const recorded=[];
      for(const stage of ['world','acting']){
        const adopted=original.artifacts.find(a=>a.stage===stage).adopted_version_id;
        await page.goto(base+'/#/e/'+episodeId+'/'+stage);
        await page.getByRole('button',{name:'跳过这一步',exact:true}).click();
        assert(await page.getByRole('button',{name:'确认跳过',exact:true}).isDisabled());
        await page.getByLabel('跳过原因').fill('本次验证跳过状态的保存与恢复，保留原场次和已有稿件。');
        await page.getByRole('button',{name:'确认跳过',exact:true}).click();
        await page.getByRole('button',{name:'恢复这一步',exact:true}).waitFor();
        await page.reload();
        const skipped=await view();assert(skipped.skippedStages[stage]);
        await page.getByRole('button',{name:'恢复这一步',exact:true}).click();
        await page.getByRole('button',{name:'跳过这一步',exact:true}).waitFor();
        const restored=await view();assert(!restored.skippedStages[stage]);
        assert.equal(restored.artifacts.find(a=>a.stage===stage).adopted_version_id,adopted);
        recorded.push({stage,reason:skipped.skippedStages[stage],adopted});
      }
      fs.writeFileSync(path.join(root,'optional-and-modes.json'),JSON.stringify({episodeId,restoredMode:original.cooperationMode,recorded},null,2));
    }
    if(action==='stage-checklists'){
      const original=JSON.parse(fs.readFileSync(path.join(root,'original-state.json'))).episodeId;
      const locked=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json'))).episodeId;
      const reports={};
      const seenCommands=[];
      page.on('request',r=>{if(r.url().includes('/cmd/'))seenCommands.push(r.url().split('/').at(-1));});
      for(const stage of ['story','characters','world','treatment','script','stress','asset_design','acting','plan','prompts','review','delivery','package']){
        const eid=['plan','prompts','review','delivery','package'].includes(stage)?locked:original;
        await page.goto(base+'/#/e/'+eid+'/'+stage);
        await page.getByRole('button',{name:'过关清单',exact:true}).click();
        const modal=page.locator('.modal:visible');
        await modal.locator('.stage-check-list li').first().waitFor();
        const rows=await modal.locator('.stage-check-list li').allTextContents();
        reports[stage]={eid,rows};
        await modal.getByRole('button',{name:'关闭清单',exact:true}).click();
      }
      const pid=(await query('episode_view',{episode_id:locked})).episode.project_id;
      await page.getByRole('button',{name:'执行日志',exact:true}).click();
      const log=page.locator('.modal:visible');
      await log.getByLabel('日志项目').selectOption(pid);
      await log.locator('.activity-row').first().waitFor();
      await log.getByRole('button',{name:'刷新日志',exact:true}).click();
      const actual=(await query('activity_log',{project_id:pid,offset:0})).items;
      assert(actual.length>0);
      await log.getByText(new RegExp(actual[0].id.slice(0,8))).waitFor();
      await log.getByRole('button',{name:'关闭日志',exact:true}).click();
      assert(!seenCommands.some(c=>['create_task','post_message','start_prompt_review_batch','retry_run'].includes(c)));
      fs.writeFileSync(path.join(root,'stage-checklists.json'),JSON.stringify({reports,latestLog:actual[0].id,seenCommands},null,2));
    }
    if(action==='method-retry'){
      const {episodeId,versions}=JSON.parse(fs.readFileSync(path.join(root,'custom-method-editions.json')));
      const receipt=path.join(root,'method-retry.json');
      assert(!fs.existsSync(receipt));
      await page.goto(base+'/#/e/'+episodeId+'/story');await page.reload();
      await page.getByRole('button',{name:'创作对话',exact:true}).click();
      const menu=page.locator('details.executor-settings:visible');
      if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      await page.getByPlaceholder('说明希望怎样起草或修改').fill('按本阶段已选用的方法打磨修鞋师核对木偶鞋子的动作。沿用故事和无对白形式，另存完整候选。');
      async function press(label,pattern){
        const [resp]=await Promise.all([page.waitForResponse(r=>pattern.test(r.url())),page.getByRole('button',{name:label,exact:true}).click()]);
        assert.equal(resp.status(),200,await resp.text());return resp.json();
      }
      const initial=await press('发送消息',/\/cmd\/(post_message|create_task)$/);
      const stopped=await press('停止',/\/cmd\/stop_run$/);
      assert.equal(stopped.runId,initial.runId);
      const retry=await press('用原输入重试',/\/cmd\/retry_run$/);
      assert.notEqual(retry.runId,initial.runId);
      const before=(await query('run_manifest',{run_id:initial.runId})).manifest;
      const after=(await query('run_manifest',{run_id:retry.runId})).manifest;
      assert.deepEqual(before.inputs,after.inputs);
      assert(after.inputs.some(e=>e.role==='project_method' && e.name==='上传技能 · 木偶手艺动作核对 · R2'));
      fs.writeFileSync(receipt,JSON.stringify({initial,stopped,retry,methodInputs:after.inputs.filter(e=>e.role.includes('method'))},null,2));
    }
    if(action==='custom-method-editions'){
      const {episodeId,projectId}=JSON.parse(fs.readFileSync(path.join(root,'adapt-import.json')));
      const stage='story', title='木偶手艺动作核对';
      await page.goto(base+'/#/e/'+episodeId+'/story');
      await page.getByRole('button',{name:'制作方法',exact:true}).click();
      const modal=page.locator('.modal:visible');
      const versions=[];
      for(const [n,body] of [[1,'检查木偶的鞋如何成对。'],[2,'用修鞋师两指轻捏鞋底、让两只鞋跟并齐的可见动作交代木偶靴已成对，保留街市误认与揭晓。']]){
        if(n===1)await modal.getByLabel('技能名称',{exact:true}).fill(title);
        await modal.getByLabel('技能 Markdown 文件',{exact:true}).setInputFiles({name:'SKILL.md',mimeType:'text/markdown',buffer:Buffer.from('# '+title+'\n\n'+body)});
        const [r]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/upload/custom-method')),modal.getByRole('button',{name:'上传 SKILL.md',exact:true}).click()]);
        assert.equal(r.status(),200,await r.text());versions.push(await r.json());
        await modal.getByRole('status').filter({hasText:'已收录 R'+n}).waitFor();
      }
      const picker=modal.getByLabel(title+'采用版本',{exact:true});
      assert.equal(await picker.inputValue(),'');
      await picker.selectOption(versions[0].versionId);
      await modal.getByRole('button',{name:'撤销未保存选择',exact:true}).click();
      assert.equal(await picker.inputValue(),'');
      await picker.selectOption(versions[1].versionId);
      await modal.getByRole('button',{name:'保存自定义技能选择',exact:true}).click();
      await modal.getByText(/已保存，下次在本阶段起草/).waitFor();
      assert.deepEqual((await query('custom_methods',{project_id:projectId,episode_id:episodeId,stage})).selected,[versions[1].versionId]);
      assert.deepEqual((await query('custom_methods',{project_id:projectId,episode_id:episodeId,stage:'script'})).selected,[]);
      await modal.getByRole('button',{name:'关闭',exact:true}).click();
      fs.writeFileSync(path.join(root,'custom-method-editions.json'),JSON.stringify({episodeId,projectId,stage,versions},null,2));
    }
    if(action==='adapt-import'){
      const receipt=path.join(root,'adapt-import.json');
      assert(!fs.existsSync(receipt));
      const original=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      const pid=(await query('episode_view',{episode_id:original.episodeId})).episode.project_id;
      const project=(await query('list_projects')).find(p=>p.id===pid);
      await page.goto(base+'/');await page.reload();
      await page.locator('.home-controls > button').first().click();
      await page.locator('.pop').getByRole('button',{name:project.name,exact:true}).click();
      await page.getByRole('button',{name:'导入故事稿 / 剧本',exact:true}).click();
      const f=page.locator('input[type=file][accept=".md,.txt,.docx,.pdf"]');
      await f.setInputFiles({name:'空白验证稿.txt',mimeType:'text/plain',buffer:Buffer.from('   ')});
      await page.getByRole('button',{name:'继续',exact:true}).click();
      await page.getByText(/文件为空或没有可提取的正文/).waitFor();
      assert(await page.getByRole('button',{name:/^去开工 /}).isDisabled());
      assert.equal((await query('list_projects')).find(p=>p.id===pid).episodes,project.episodes);
      const source='收摊时，一位修鞋师发现门前留下一只小红靴。他以为是顾客遗忘，沿街寻找，最后看见另一个孩子正在把一只木偶的鞋补成一双。';
      await f.setInputFiles({name:'小红靴_改编来源.txt',mimeType:'text/plain',buffer:Buffer.from(source)});
      await page.getByPlaceholder('补充本集要求（选填）').fill('改编成雨天街市的无对白短片，保留误认小红靴主人和木偶揭晓。');
      await page.getByRole('button',{name:'继续',exact:true}).click();
      await page.getByText('查看提取正文（前 800 字符）',{exact:true}).click();
      await page.getByText(source,{exact:true}).waitFor();
      await page.getByRole('button',{name:/^去开工 /}).click();
      await page.getByLabel('导入稿用途').selectOption('adapt');
      await page.getByRole('button',{name:'本集调整',exact:true}).click();
      for(const role of ['故事 写','审']){
        for(const [suffix,value] of [['执行器','codex'],['型号','gpt-6.1-sol'],['推理强度','high']]){
          await page.getByLabel(role+suffix,{exact:true}).selectOption(value);
        }
      }
      await page.getByRole('button',{name:'开工并开始创作',exact:true}).click();
      await page.waitForURL(/#\/e\/[^/]+\/story/);
      const episodeId=page.url().split('/').at(-2);
      const v=await query('episode_view',{episode_id:episodeId});
      assert.equal(v.kickoff.sourceIntent,'adapt');
      assert(!v.artifacts.some(a=>a.adopted_version_id));
      fs.writeFileSync(receipt,JSON.stringify({source,episodeId,projectId:pid,kickoff:v.kickoff,emptyFileBlocked:true},null,2));
    }
    if(action==='handbag-plan'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const receipt=path.join(root,'handbag-plan.json');
      assert(!fs.existsSync(receipt));
      await page.goto(base+'/#/e/'+episodeId+'/plan');await page.reload();
      const edit=page.getByRole('button',{name:'修改本阶段',exact:true});
      if(await edit.isVisible())await edit.click();
      const menu=page.locator('details.executor-settings:visible');
      if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      const direction='导演已确认按05采用剧本场02的手袋处理老太太的旧手机。以已采用的R03完整镜头方案为底稿，仅调整老太太旧手机的连续性：G01旧手机已放在手袋内；G02 SH04老太太左手从手袋取出旧手机，黑屏，接回原镜头。逐项同步相关镜头正文、道具状态、时间窗动作锚点、联合表和备注。保留饭盒原位置和持物逻辑。G05阿成把自己的手机收进他自己口袋的动作原样保留。保留16镜、五段65秒、原场号、参考图责任和其他镜头内容；交付完整可采用的结构化镜头方案。';
      await page.getByPlaceholder('说明希望怎样起草或修改').fill(direction);
      const [resp]=await Promise.all([page.waitForResponse(r=>/\/cmd\/(post_message|create_task)$/.test(r.url())),page.getByRole('button',{name:'发送消息',exact:true}).click()]);
      assert.equal(resp.status(),200,await resp.text());
      const result=await resp.json();assert(result.runId);
      fs.writeFileSync(receipt,JSON.stringify({episodeId,direction,...result},null,2));
      console.log('PLAN_REVISION',result.runId);
    }
    if(action==='prompt-source-write'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const receipt=path.join(root,'prompt-source-write.json');
      assert(!fs.existsSync(receipt));
      await page.goto(base+'/#/e/'+episodeId+'/prompts?segment=G02');
      await page.reload();
      await page.getByRole('button',{name:'本集色卡',exact:true}).click();
      await page.getByLabel('本集色卡使用方式').selectOption('custom');
      await page.getByLabel('搜索本阶段色卡').fill('黄昏暖灰');
      await page.getByLabel('黄昏暖灰',{exact:true}).check();
      await page.getByRole('button',{name:'保存本集选择',exact:true}).click();
      await page.getByLabel('本集色卡使用方式').waitFor({state:'hidden'});
      const edit=page.getByRole('button',{name:'修改本阶段',exact:true});
      if(await edit.isVisible())await edit.click();
      const menu=page.locator('details.executor-settings:visible');
      if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      await page.getByPlaceholder('说明希望怎样起草或修改').fill('对照已采用剧本场次原文、现有五段和已选视频色卡，为本集另存五段完整候选提示词。保留镜号、时间、参考素材编号、镜头适用范围、看图回执和声音契约。逐段核对动作、对白和物件；若剧本与镜头方案不一致，在备注中引用两边的原文和场号，单列待导演处理的问题。');
      const [resp]=await Promise.all([page.waitForResponse(r=>/\/cmd\/(post_message|create_task)$/.test(r.url())),page.getByRole('button',{name:'发送消息',exact:true}).click()]);
      assert.equal(resp.status(),200,await resp.text());
      const result=await resp.json();assert(result.runId);
      fs.writeFileSync(receipt,JSON.stringify(result,null,2));
      const {manifest}=await query('run_manifest',{run_id:result.runId});
      assert.equal(manifest.inputs.filter(e=>e.role==='adopted_plan').length,1);
      assert(manifest.inputs.some(e=>e.role==='script_scene_source'));
      assert(manifest.inputs.some(e=>e.role==='visual_reference' && e.name==='黄昏暖灰'));
    }
    if(action==='upstream-ui-audit'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      const view=await query('episode_view',{episode_id:episodeId});
      const result={};
      for(const stage of ['characters','world','treatment','script','stress','asset_design','acting']){
        const art=view.artifacts.find(a=>a.stage===stage);
        const version=art.versions.find(v=>v.id===art.adopted_version_id) || art.versions.at(-1);
        await page.goto(base+'/#/e/'+episodeId+'/'+stage+'?v='+version.id);
        await page.getByRole('button',{name:'版本来源',exact:true}).click();
        const dialog=page.locator('.modal:visible');
        await dialog.getByText(/来源 .*运行/).waitFor();
        const manifest=(await query('run_manifest',{run_id:version.source_run_id})).manifest;
        result[stage]={version:version.id,run:version.source_run_id,upstream:manifest.inputs.filter(e=>e.role.startsWith('adopted_')).map(e=>({role:e.role,name:e.name})),displayed:await dialog.locator('details > summary').allTextContents()};
        await dialog.getByRole('button',{name:'关闭',exact:true}).click();
      }
      fs.writeFileSync(path.join(root,'upstream-ui-audit-'+(process.argv[3]||'initial')+'.json'),JSON.stringify(result,null,2));
    }
    if(action==='review-ui-evidence'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/review');
      await page.reload();
      const card=page.locator('.card:visible').filter({has:page.locator('b').filter({hasText:/^G02$/})});
      const summary=card.getByText(/^第二遍完整观察/);
      await summary.click();
      await card.getByText(/剧本版本.*omit写有/).waitFor();
      await card.getByText('第二遍送审文件',{exact:true}).click();
      await card.getByRole('link',{name:'G02-剧本原文.json',exact:true}).waitFor();
      fs.writeFileSync(path.join(root,'G02-source-conflict-ui.txt'),await card.innerText());
      await card.screenshot({path:path.join(root,'G02-source-conflict-ui.png')});
    }
    if(action==='locked-scene-source'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const view=await query('episode_view',{episode_id:episodeId});
      const script=view.artifacts.find(a=>a.stage==='script');
      const version=script.versions.find(v=>v.id===script.adopted_version_id);
      const scene=version.content.scenes.find(s=>s.id==='场02');
      const shots=[];
      for(const seg of ['G02','G03']){
        await page.goto(base+'/#/e/'+episodeId+'/prompts?segment='+seg);
        await page.reload();
        const section=page.getByRole('region',{name:'场景拍摄简述'});
        await section.getByText(scene.action,{exact:true}).waitFor();
        const source=section.getByRole('link',{name:/核对.*剧本.*场02 原文/});
        const href=await source.getAttribute('href');
        assert(href.includes(version.id));
        shots.push(await section.locator('span.small').innerText());
        await page.screenshot({path:path.join(root,'scene-trace-'+seg+'.png')});
        await source.click();
        await page.locator('[data-script-scene="场02"]:visible').waitFor();
        assert(page.url().includes(version.id));
      }
      assert.notEqual(shots[0],shots[1]);
      fs.writeFileSync(path.join(root,'scene-trace.json'),JSON.stringify({episodeId,version:version.id,scene:scene.id,action:scene.action,shots},null,2));
    }
    if(action==='locked-prompt-read'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const art=(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage==='prompts');
      const version=art.versions.find(v=>v.id===art.adopted_version_id);
      await page.goto(base+'/#/e/'+episodeId+'/prompts?v='+version.id);
      await page.reload();
      const doc=page.getByRole('button',{name:'成果文档',exact:true});if(await doc.isVisible())await doc.click();
      for(const seg of ['G05','G04','G03','G02','G01']){
        await page.getByRole('button',{name:new RegExp('^'+seg+' ·')}).click();
        await page.getByRole('heading',{name:seg+' 提示词正文',exact:true}).waitFor();
        const editor=page.getByRole('textbox',{name:seg+' 提示词正文',exact:true});
        await page.waitForFunction(([label,txt])=>Array.from(document.querySelectorAll('textarea')).some(e=>e.getAttribute('aria-label')===label && e.value===txt),[seg+' 提示词正文',version.content.segments[seg].txt]);
        console.log('PROMPT_READ',seg,(await editor.inputValue()).length);
      }
      await page.getByRole('heading',{name:'G01 提示词正文'}).scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(root,'prompt-body-first.png')});
    }
    if(action==='locked-export'){
      const scope=process.argv[3];
      assert(['full','trial'].includes(scope));
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const args={episode_id:episodeId,scope,segments:scope==='full'?null:['G02']};
      await page.goto(base+'/#/e/'+episodeId+'/delivery');
      await page.getByRole('heading',{name:'交付核验',exact:true}).waitFor();
      if(scope==='trial'){
        await page.getByRole('button',{name:'限定试验范围',exact:true}).click();
        await page.getByRole('button',{name:'G02',exact:true}).click();
      }
      const [problems]=await query('delivery_problems',args);
      assert.deepEqual(problems,[]);
      async function press(label,command){
        const response=page.waitForResponse(r=>r.url().endsWith('/cmd/'+command));
        await page.getByRole('button',{name:label,exact:true}).click();
        const r=await response;assert.equal(r.status(),200,await r.text());return r.json();
      }
      const delivery=await press('生成图文交付','deliver');
      assert(delivery.deliveryId);
      await page.goto(base+'/#/e/'+episodeId+'/package?delivery='+delivery.deliveryId);
      const preview=await press('预览所选段','package_preview');
      assert.equal(preview.segments.length,scope==='full'?5:1);
      const pack=await press('下载 H3 包','package');
      await page.getByText('已导出',{exact:true}).waitFor();
      fs.writeFileSync(path.join(root,'export-'+scope+'.json'),JSON.stringify({delivery,preview,pack},null,2));
      console.log('EXPORTED',pack.path);
    }
    if(action==='locked-review'){
      const pass=process.argv[3];
      assert(['cold_read','comparison'].includes(pass));
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/review');
      await page.reload();
      await page.getByRole('heading',{name:'两遍独审',exact:true}).waitFor();
      for(const [suffix,value] of [['执行器','codex'],['型号','gpt-6.1-sol'],['推理强度','high']]){
        await page.getByLabel('提示词独审'+suffix,{exact:true}).selectOption(value);
        await page.waitForFunction(({suffix,value})=>document.querySelector('[aria-label="提示词独审'+suffix+'"]').value===value,{suffix,value});
      }
      const submitted=page.waitForResponse(r=>r.url().endsWith('/cmd/start_prompt_review_batch'));
      await page.getByRole('button',{name:pass==='cold_read'?'送审材料（第一遍）':'开始第二遍',exact:true}).click();
      const response=await submitted;
      assert.equal(response.status(),200,await response.text());
      const receipt=await response.json();
      fs.writeFileSync(path.join(root,'review-'+pass+'-'+receipt.runIds[0]+'.json'),JSON.stringify(receipt,null,2));
      console.log('REVIEW_JOBS',JSON.stringify(receipt));
    }
    if(action==='canvas-cancel'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/assets');
      await page.getByRole('button',{name:'画布',exact:true}).click();
      const board=page.locator('.canvas-board');
      await board.getByRole('button',{name:'＋ 生成',exact:true}).click();
      const model=board.getByLabel('图像任务创作型号',{exact:false});
      await model.selectOption('gpt-6.1-sol');
      await board.getByPlaceholder('对这个生成节点写要求，例：正面全身，中性站姿…').fill('渡口候船处的生日蛋糕盒，暖灰黄昏侧光。');
      const response=page.waitForResponse(r=>r.url().endsWith('/cmd/create_batch'));
      await board.getByRole('button',{name:/生成 2 张/}).click();
      const made=await response;assert.equal(made.status(),200,await made.text());
      const {batchId}=await made.json();
      await page.getByRole('button',{name:'取消批次',exact:true}).click();
      const state=async()=>await query('batch_view',{batch_id:batchId});
      for(let i=0;i<50&&(await state()).state!=='cancelled';i++) await page.waitForTimeout(100);
      const batch=await state();assert.equal(batch.state,'cancelled');
      assert.equal(batch.model,'gpt-6.1-sol');assert.equal(batch.requests.filter(r=>r.asset_version_id).length,0);
      await page.reload();
      await page.getByRole('button',{name:'画布',exact:true}).click();
      const drawing=await query('canvas_view',{episode_id:episodeId});
      assert(drawing.doc.nodes.find(n=>n.data.batchId===batchId));
      fs.writeFileSync(path.join(root,'canvas-cancel.json'),JSON.stringify({batchId,state:batch.state,model:batch.model,canvas:drawing},null,2));
    }
    if(action==='series-step-reference'){
      const {eid}=JSON.parse(fs.readFileSync(path.join(root,'existing-topic.json')));
      await page.goto(base+'/#/e/'+eid+'/story');
      await page.getByRole('button',{name:'本步需求与参考',exact:true}).click();
      await page.getByLabel(/^本步目标/).fill('搜神记');
      await page.getByLabel('参考与知识库').selectOption('match');
      await page.getByRole('button',{name:'选择参考片段'}).click();
      await page.locator('.script-list button').filter({has:page.getByText('搜神记',{exact:true})}).click();
      await page.getByRole('button',{name:'下一段',exact:true}).click();
      await page.getByText(/^2401–/).waitFor();
      await page.getByRole('button',{name:'引用此段'}).click();
      await page.getByLabel(/^本步目标/).fill('保持雨后脚印的误会与揭晓；知识库选段仅供比较叙述方式。');
      await page.getByLabel('显示全部阶段的方法').check();
      await page.getByRole('checkbox',{name:/00-A ·/}).check();
      await page.getByRole('button',{name:'保存本步配置',exact:true}).click();
      await page.getByRole('heading',{name:'本步需求',exact:true}).waitFor({state:'hidden'});
      await page.reload();
      await page.getByRole('button',{name:'本步需求与参考',exact:true}).click();
      const config=await query('step_config',{episode_id:eid,stage:'story'});
      assert.deepEqual(config.enhancements,['00-A']);
      assert(config.references.some(r=>r.title==='搜神记' && r.offset===2400));
      fs.writeFileSync(path.join(root,'series-story-step-config.json'),JSON.stringify(config,null,2));
      await page.screenshot({path:path.join(root,'series-stage-method-source.png')});
    }
    if(action==='locked-sound-format'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/prompts');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      for(const sid of ['G01','G02','G03','G04','G05']){
        await page.getByRole('button',{name:new RegExp('^'+sid+' ·')}).click();
        const editor=page.getByLabel(sid+' 提示词正文',{exact:true});
        await editor.waitFor();
        const txt=await editor.inputValue();
        if(txt.endsWith('non_diegetic_music: N/A'))continue;
        assert(txt.endsWith('non_diegetic_music\nN/A'));
        await editor.fill(txt.replace('non_diegetic_music\nN/A','non_diegetic_music: N/A'));
        const saved=page.waitForResponse(r=>r.url().endsWith('/cmd/save_manual'));
        await page.getByRole('button',{name:'保存本段',exact:true}).click();
        const res=await saved;assert.equal(res.status(),200);
        const {versionId}=await res.json();
        await page.waitForFunction(id=>document.querySelector('textarea[data-version]')?.dataset.version===id,versionId);
      }
    }
    if(action==='model-avatar'){
      await page.reload();
      const {eid}=JSON.parse(fs.readFileSync(path.join(root,'existing-topic.json')));
      assert(page.url().includes(eid));
      await page.getByRole('button',{name:'创作对话',exact:true}).click();
      const reply=page.locator('.model-reply').filter({has:page.locator('.chat-prose').filter({hasText:'验脚底'})}).last();
      await reply.waitFor();
      assert.match(await reply.locator('.model-byline').innerText(),/Codex · gpt-6.1-sol · high/i);
      assert.equal(await reply.locator('.model-portrait').innerText(),'6.1');
      await reply.scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(root,'model-avatar-real-reply.png')});
    }
    if(action==='original-stage-requirement'){
      const stage=process.argv[3];
      const cases={
        story:['让观众从蛋糕盒上的取货单先读到七十岁，再看到红伞线索。','保留交付蛋糕与共享蛋糕的结局。'],
        characters:['给小雨一个反复可见的护盒动作：转身前先用拇指按住盒盖。','小雨是送蛋糕的学徒，老人是寿星。'],
        world:['写清末班船只能从老人所在岸开向小雨所在岸，明确观众辨认两岸的视觉线索。','保持同一黄昏渡口。'],
        treatment:['用一句话分别写明五场结束时蛋糕盒在谁手中。','沿用已采用的五场稳定编号。'],
        script:['小雨认错人后，先将蛋糕盒收回胸前，再抬眼看向对岸。','全片无口头对白。'],
        stress:['单独检验第一场十秒是否足够辨读取货单和两把红伞，给出可实拍的验证方法。','诊断只记录问题及修改选项。'],
        asset_design:['为两把红伞设计可区分的伞柄形状，并登记各自的持有人。','两把伞从远处仍应可能被误认。'],
        acting:['在老人决定分享蛋糕前，先看小雨手里的两把叉子，再回望小雨，两次视线变化都要写在动作正文。','本集零口头对白。']
      };
      const [goal,keep]=cases[stage];
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'original-state.json')));
      const lookup=async()=>(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage===stage);
      const prior=await lookup();
      assert(!prior?.tasks.some(t=>t.runs.some(r=>['queued','running'].includes(r.status))));
      const receipt=path.join(root,'original-requirement-'+stage+'.json');
      assert(!fs.existsSync(receipt),'Inspect the existing receipt before sending another request');
      await page.goto(base+'/#/e/'+episodeId+'/'+stage);
      await page.getByRole('button',{name:'本步需求与参考',exact:true}).click();
      await page.getByLabel(/^本步目标/).fill(goal);
      await page.getByLabel(/^必须保留的内容/).fill(keep);
      await page.getByRole('button',{name:'保存本步配置',exact:true}).click();
      await page.getByRole('heading',{name:'本步需求',exact:true}).waitFor({state:'hidden'});
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const open=page.getByRole('button',{name:'打开本阶段',exact:true});
      if(await open.isVisible())await open.click();
      const edit=page.getByRole('button',{name:'修改本阶段',exact:true});
      if(await edit.isVisible())await edit.click();
      const menu=page.locator('details.executor-settings:visible');
      if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      await page.getByPlaceholder('说明希望怎样起草或修改').fill('完成这一阶段的完整候选成果。落实刚在本步配置里保存的目标和保留项，沿用已采用上游的稳定编号和拍摄规格。');
      const sent=page.waitForResponse(r=>/\/cmd\/(post_message|create_task)$/.test(r.url()));
      await page.getByRole('button',{name:'发送消息',exact:true}).click();
      const resp=await sent;assert.equal(resp.status(),200,await resp.text());
      const {runId}=await resp.json();assert(runId);
      const {manifest}=await query('run_manifest',{run_id:runId});
      const input=manifest.inputs.find(v=>v.role==='step_requirements');
      assert(input);
      fs.writeFileSync(receipt,JSON.stringify({episodeId,stage,goal,keep,runId,input},null,2));
    }
    if(action==='visible-dialogue'){
      const turn=Number(process.argv[3]);assert([1,2,3].includes(turn));
      const {eid}=JSON.parse(fs.readFileSync(path.join(root,'existing-topic.json')));
      const story=async()=>(await query('episode_view',{episode_id:eid})).artifacts.find(a=>a.stage==='story');
      const before=await story();
      const modal=page.locator('.modal').last();
      if(await modal.isVisible())await modal.getByRole('button',{name:'关闭',exact:true}).last().click();
      if(!page.url().includes('/'+eid+'/story'))await page.goto(base+'/#/e/'+eid+'/story');
      await page.getByRole('button',{name:'创作对话',exact:true}).click();
      const menu=page.locator('details.executor-settings:visible');
      if(!await menu.evaluate(n=>n.open))await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      const write=turn===3;
      const messages=[
        '先聊需求，暂时别改稿。我想让观众在揭晓前和 MILO 一样相信有陌生来客，但不能靠对白解释。就现有门口泥印的故事，给我两种能拍出来的核验动作，并说各自会让观众先看到什么。',
        '我选你说的第一种“验脚底”，不用白巾。再保留一个具体要求：让 MILO 先跨过一条干燥的蓝色门垫，再回头看到门垫中央多了一道弧形泥痕。结合你上面提的办法，怎么拍才能让观众自己推断是尾巴造成的？先用两三句话确认，我们接着再改稿。',
        '按刚才两轮讨论确认的动作和线索修改现有故事，落实到正文；保留原来的结局。'];
      assert(!before.tasks.some(t=>t.runs.some(r=>['queued','running'].includes(r.status))));
      await page.locator('.assistant .composer textarea').fill(messages[turn-1]);
      const sent=page.waitForResponse(r=>/\/cmd\/(post_message|create_task)$/.test(r.url()));
      await page.getByRole('button',{name:'发送消息',exact:true}).click();
      const res=await sent;assert.equal(res.status(),200,await res.text());
      const {runId}=await res.json();assert(runId);
      const receipt=path.join(root,'visible-dialogue-'+turn+'.json');
      fs.writeFileSync(receipt,JSON.stringify({runId,turn,versionCount:before.versions.length,request:messages[turn-1]},null,2));
      console.log('VISIBLE_TURN',turn,runId);
      await page.screenshot({path:path.join(root,'visible-dialogue-'+turn+'-sent.png')});
    }
    if(action==='verify-step-input'){
      const {eid}=JSON.parse(fs.readFileSync(path.join(root,'existing-topic.json')));
      const runs=async()=>(await query('episode_view',{episode_id:eid})).artifacts.find(a=>a.stage==='story').tasks.flatMap(t=>t.runs);
      const before=await runs();
      assert(!before.some(r=>['running','queued'].includes(r.status)));
      await page.goto(base+'/#/e/'+eid+'/story');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const menu=page.locator('details.executor-settings:visible');
      await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      await page.getByPlaceholder('说明希望怎样起草或修改').fill('依据已保存的本步目标、所选增强方法和知识库选段，打磨现有故事。保留雨后脚印误会及尾巴揭晓。');
      await page.getByRole('button',{name:'发送消息',exact:true}).click();
      let run;
      for(let i=0;i<40&&!run;i++){
        run=(await runs()).find(r=>!before.some(old=>old.id===r.id));
        if(!run)await page.waitForTimeout(200);
      }
      assert(run);
      const {manifest}=await query('run_manifest',{run_id:run.id});
      const selected=(await query('run_methods',{run_id:run.id})).sections.filter(s=>s.source?.enhancementIds);
      assert(selected.length && selected.every(s=>JSON.stringify(s.source.enhancementIds)==='["00-A"]'));
      const citation=manifest.inputs.find(i=>i.role==='citation' && i.name==='搜神记');
      const goal=manifest.inputs.find(i=>i.role==='step_requirements');
      assert(citation && goal);
      fs.writeFileSync(path.join(root,'series-actual-step-input.json'),JSON.stringify({run:run.id,model:manifest.requestedModel,effort:manifest.requestedEffort,citation,goal,methods:selected.map(s=>s.source.enhancementIds)},null,2));
    }
    if(action==='repair-locked-plan'){
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/plan');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const art=(await query('episode_view',{episode_id:episodeId})).artifacts.find(a=>a.stage==='plan');
      assert.equal(art.versions.length,1,'Inspect the latest plan first');
      assert(!art.tasks.some(t=>t.runs.some(r=>['queued','running'].includes(r.status))));
      const menu=page.locator('details.executor-settings:visible');
      await menu.locator('summary').first().click();
      await saveAuthorizedCreator(menu);
      await menu.getByRole('button',{name:'收起',exact:true}).click();
      await page.getByPlaceholder('说明希望怎样起草或修改').fill('修复当前16镜5段65秒镜头方案的采用格式错误。所有镜头的propStates保持小写道具ID，状态值保留具体可见事实，把内部中英文分号改为逗号，禁止竖线或换行。rhythmTable字段省略或填空字符串，不填字符串“[]”。保留16个动作、镜号、段号、65秒预算和三个原句对白。沿用C01、C02真实角色绑定。沿用当前稿完整字段，确保长镜有持续观看理由，切换或连续机位准确。');
      await page.getByRole('button',{name:'发送消息',exact:true}).click();
      await page.getByText(/Codex 正在/).first().waitFor();
      console.log('REVISION_SENT',art.versions[0].id);
    }
    if(action==='bind-locked-role'){
      const role=process.argv[3];
      assert(['C01','C02'].includes(role));
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      await page.goto(base+'/#/e/'+episodeId+'/cast');
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const picker=page.getByLabel('角色',{exact:true});
      await picker.waitFor();
      await picker.selectOption(role);
      const field=page.getByLabel('独立身份原图',{exact:true});
      let source=role==='C01'?'C01-S01 阿成身份参考.png':'C02-S02 老太太戴镜身份参考';
      let option=field.locator('option').filter({hasText:source});
      if(role==='C01'&&!(await option.count())){
        const original=JSON.parse(fs.readFileSync(path.join(root,'ui-native-image-result.json')));
        const bytes=fs.readFileSync(path.join(root,'data','codex-images',original.requests[0].local_request_id,'result.png'));
        await page.getByLabel('上传身份原图').setInputFiles({name:source,mimeType:'image/png',buffer:bytes});
      }
      await option.waitFor({state:'attached'});
      const value=await option.first().getAttribute('value');
      await field.selectOption(value);
      const appearance=role==='C01'?'年轻男性，短黑发，深青色连帽外套，深灰长裤，黑色系带鞋。':process.argv[4];
      assert(appearance,'inspect the real picture before describing it');
      await page.getByLabel('外观短描述').fill(appearance);
      const response=page.waitForResponse(r=>r.url().endsWith('/cmd/set_role_baseline'));
      await page.getByRole('button',{name:'保存本集身份基准',exact:true}).click();
      assert.equal((await response).status(),200);
      await page.reload();
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      await picker.selectOption(role);
      await page.waitForFunction(v=>document.querySelector('[aria-label="独立身份原图"]')?.value===v,value);
      const m=await query('cast_matrix',{episode_id:episodeId});
      assert.equal(m.roster[role].appearance,appearance);
      fs.writeFileSync(path.join(root,'baseline-'+role+'.json'),JSON.stringify(m.roster[role],null,2));
      await page.screenshot({path:path.join(root,'identity-bound-'+role+'.png')});
    }
    if (action === 'locked-identity') {
      const id=process.argv[3];
      assert(['C01-S01','C02-S02'].includes(id));
      const {episodeId}=JSON.parse(fs.readFileSync(path.join(root,'locked-state.json')));
      const view=await query('episode_view',{episode_id:episodeId});
      const art=view.artifacts.find(a=>a.stage==='asset_design');
      const design=art.versions.find(v=>v.id===art.adopted_version_id).content.assets.find(a=>a.id===id);
      const name=id+' '+(id.startsWith('C01')?'阿成':'老太太戴镜')+'身份参考';
      await page.goto(base+'/#/e/'+episodeId+'/assets');
      const batches=await query('episode_batches',{episode_id:episodeId});
      const prior=batches.find(b=>b.spec?.items?.[0]?.target?.name===name);
      let batchId=prior?.id;
      if(!batchId){
        await page.getByRole('button',{name:'出图',exact:true}).click();
        await page.getByLabel('素材名称').fill(name);
        await page.getByLabel('素材分类').selectOption('identity');
        await page.getByLabel('图像任务创作型号').selectOption('gpt-6.1-sol');
        assert.equal(await page.getByLabel('图像任务推理强度').inputValue(),'high');
        await page.getByLabel('绘图要求').fill(design.imagePrompt+' 只画一张完整的单人身份参考照，便于后续镜头保持同一人物。');
        const response=page.waitForResponse(r=>r.url().endsWith('/cmd/create_batch'));
        await page.getByRole('button',{name:'生成摘要，去确认',exact:true}).click();
        batchId=(await (await response).json()).batchId;
        assert(batchId);
        await page.getByRole('button',{name:'确认授权 1 张',exact:true}).click();
        await page.getByRole('button',{name:'发送已授权的 1 张',exact:true}).click();
      }
      for(let i=0;i<720;i++){
        const b=await query('batch_view',{batch_id:batchId});
        if(b.state==='completed'){
          assert.equal(b.spec.items[0].target.category,'identity');
          fs.writeFileSync(path.join(root,id+'-batch.json'),JSON.stringify(b,null,2));
          await page.reload();
          await page.getByRole('button',{name:'本轮出图',exact:true}).click();
          await page.getByText(name,{exact:false}).first().waitFor();
          console.log('IDENTITY_COMPLETED',id,batchId);
          break;
        }
        assert(['running','authorized','sending','submitted','in_progress','partial'].includes(b.state),b.state);
        if(i===719)throw new Error('Image is still pending '+batchId);
        await page.waitForTimeout(1000);
      }
    }
    if (action === 'palette-image-locked') {
      const state = JSON.parse(fs.readFileSync(path.join(root,'locked-state.json'),'utf8'));
      const paletteName = '黄昏暖灰';
      await page.goto(base+'/#/palette');
      const existingPalettes=await query('browse_palettes',{query:paletteName});
      if (!existingPalettes.rows.some(c=>c.name===paletteName)) {
      await page.getByRole('button',{name:'添加我的色卡',exact:true}).click();
      const modal=page.locator('.modal').last();
      await modal.getByLabel('色卡名称',{exact:true}).fill(paletteName);
      await modal.getByLabel('光影、色彩与构图',{exact:true}).fill('暗青服装、暖米肤色、灰色背景，柔和侧光，写实日常电影质感。');
      await modal.getByLabel('图像提示词',{exact:true}).fill('黄昏暖灰：暗青与暖米色的克制配色，柔和定向光，写实皮肤与布料，避免霓虹和过度磨皮。');
      await modal.getByLabel('视频提示词',{exact:true}).fill('延续黄昏暖灰配色，柔和侧光与稳定自然曝光，保留表演中的真实肌肤和布料纹理。');
      await modal.getByLabel('来源与许可',{exact:true}).fill('本次系统验收自拟配色，无外部作品引用。');
      await modal.getByLabel('主色值',{exact:true}).fill('#23454B, #E6C99F, #74746D');
      await modal.getByRole('button',{name:'保存色卡',exact:true}).click();
      await page.getByRole('button',{name:'返回色卡库',exact:true}).waitFor();
      } else {
        await page.getByLabel('搜索资料',{exact:true}).fill(paletteName);
        await page.getByRole('button',{name:'搜索',exact:true}).click();
        await page.locator('.director-open').filter({hasText:paletteName}).click();
        await page.getByRole('button',{name:'返回色卡库',exact:true}).waitFor();
      }
      await page.screenshot({path:path.join(root,'ui-custom-palette.png'),fullPage:true});
      await page.goto(base+'/#/e/'+state.episodeId+'/assets');
      await page.getByRole('button',{name:'本集色卡',exact:true}).click();
      await page.getByLabel('本集色卡使用方式').selectOption('custom');
      await page.getByLabel('搜索本阶段色卡').fill(paletteName);
      await page.getByLabel(paletteName,{exact:true}).check();
      await page.getByRole('button',{name:'保存本集选择',exact:true}).click();
      await page.getByRole('heading',{name:'本集图像资产色卡',exact:true}).waitFor({state:'hidden'});
      await page.reload();
      await page.getByRole('button',{name:'本集色卡',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('[aria-label="本集色卡使用方式"]')?.value==='custom');
      assert.equal(await page.getByLabel('本集色卡使用方式').inputValue(),'custom');
      await page.getByLabel('搜索本阶段色卡').fill(paletteName);
      assert.equal(await page.getByLabel(paletteName,{exact:true}).isChecked(),true);
      await page.getByRole('button',{name:'保存本集选择',exact:true}).click();
      await page.getByRole('heading',{name:'本集图像资产色卡',exact:true}).waitFor({state:'hidden'});
      const priorBatches=await query('episode_batches',{episode_id:state.episodeId});
      let receipt;
      const prior=priorBatches.find(b=>b.provider==='codex-native');
      if (prior) {
        receipt={batchId:prior.id};
        await page.locator('.asset-stage').getByRole('button',{name:'打开',exact:true}).first().click();
      } else {
      await page.getByRole('button',{name:'出图',exact:true}).click();
      await page.getByLabel('图像任务创作型号').selectOption('gpt-6.1-sol');
      assert.equal(await page.getByLabel('图像任务推理强度').inputValue(),'high');
      const view=await query('episode_view',{episode_id:state.episodeId});
      const design=view.artifacts.find(a=>a.stage==='asset_design');
      const content=design.versions.find(v=>v.id===design.adopted_version_id).content;
      const asset=content.assets.find(a=>a.id==='C01-S01');
      await page.getByLabel('绘图要求').fill(asset.imagePrompt+' 仅一张单人参考，尺寸1024x1536。');
      const made=page.waitForResponse(r=>r.url().includes('/api/cmd/create_batch')&&r.request().method()==='POST');
      await page.getByRole('button',{name:'生成摘要，去确认',exact:true}).click();
      const response=await made;
      assert.equal(response.status(),200,await response.text());
      receipt=await response.json();
      }
      fs.writeFileSync(path.join(root,'ui-native-image-state.json'),JSON.stringify({episodeId:state.episodeId,batchId:receipt.batchId},null,2));
      const frozen=await query('batch_view',{batch_id:receipt.batchId});
      assert(frozen.spec.items[0].prompt.includes(paletteName),'selected palette was not frozen into image prompt');
      assert.equal(frozen.model,'gpt-6.1-sol');
      assert.equal(frozen.spec.billingMode,'subscription_usage');
      if (frozen.state==='awaiting_authorization') await page.getByRole('button',{name:'确认授权 1 张',exact:true}).click();
      if (['awaiting_authorization','authorized','partial'].includes(frozen.state)) await page.getByRole('button',{name:'发送已授权的 1 张',exact:true}).click();
      let result;
      for(let i=0;i<900;i++) {
        const b=await query('batch_view',{batch_id:receipt.batchId});
        if(['failed','pending_reconcile','partially_succeeded'].includes(b.state))throw new Error(JSON.stringify(b.requests.map(r=>({state:r.state,error:r.error}))));
        if(b.state==='completed'){result=b;break;}
        if(i%30===0)console.log('UI_NATIVE_IMAGE_WAIT',receipt.batchId,b.state);
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
      assert(result,'native image did not complete');
      fs.writeFileSync(path.join(root,'ui-native-image-result.json'),JSON.stringify(result,null,2));
      await page.getByRole('button',{name:'关闭',exact:true}).last().click();
      await page.reload();
      await page.getByRole('button',{name:'本轮出图',exact:true}).click();
      await page.locator('.asset-stage img').first().waitFor();
      await page.screenshot({path:path.join(root,'ui-native-image-in-assets.png'),fullPage:true});
      console.log('UI_NATIVE_IMAGE_COMPLETE',receipt.batchId,result.requests.map(r=>({id:r.asset_version_id,sha256:r.sha256})));
    }
    if (action === 'create-locked') {
      assert(!fs.existsSync(path.join(root,'locked-state.json')),'use resume-locked for an existing validation episode');
      await page.locator('.home-controls > button').first().click();
      await page.getByRole('button',{name:'＋ 新项目（设定为空）',exact:true}).click();
      await page.getByRole('button',{name:'导入故事稿 / 剧本',exact:true}).click();
      const source = '# 《最后一格电》定稿剧本\n目标：65秒，16:9，写实温暖喜剧。仅两名主要角色，同一公交站。\n场01 外景 公交站 黄昏（0–15秒）\n送餐员阿成的手机只剩1%电。他把手机递给候车的老太太：奶奶，帮我看一眼，去医院坐哪辆车？老太太戴上眼镜，手机忽然黑屏。阿成愣住。\n场02 同场（15–35秒）\n阿成着急按电源键。老太太也拿出自己的旧手机，一样黑屏。两人对着两块黑屏叹气。老太太抬头，看见站牌，伸手拉住还在按键的阿成，把他转向站牌上清楚标出的医院和12路。\n场03 同场（35–50秒）\n12路缓缓进站。阿成笑着道谢：谢谢，您也去医院？老太太摇头，从手袋拿出饭盒：我给司机送饭。车门打开，司机只伸手接盒，不增加有台词角色。\n场04 同场（50–65秒）\n阿成上车前把外卖保温袋挂稳，伸手想帮老太太拿回手机。老太太按亮屏幕，显示电量100%，又按黑，朝站牌点点。阿成明白被她轻轻教了一课，笑着收起自己的手机上车。老太太挥手，公交车从站牌前驶离。\n保留以上情节、对白、四个场号和结局。只整理结构，不重写为另一故事。实际时长待视频生成验证。';
      await page.locator('input[type=file][accept=".md,.txt,.docx,.pdf"]').setInputFiles({name:'最后一格电_定稿.txt',mimeType:'text/plain',buffer:Buffer.from(source)});
      await page.locator('select').filter({has:page.locator('option[value="script_source"]')}).selectOption('script_source');
      await page.getByPlaceholder('补充本集要求（选填）').fill('这是完整定稿，仅整理05，保留原情节与准确对白；65秒，16:9。');
      await page.getByRole('button',{name:'继续',exact:true}).click();
      await page.getByRole('button',{name:'去开工 E01',exact:true}).click();
      await page.getByLabel('导入稿用途').selectOption('locked_screenplay');
      await page.getByLabel('本集画幅覆盖').selectOption('16:9');
      await page.getByRole('button',{name:'开工并开始创作',exact:true}).click();
      await page.waitForURL(/#\/e\/[^/]+\/script/,{timeout:45000});
      const eid = page.url().match(/#\/e\/([^/]+)/)[1];
      fs.writeFileSync(path.join(root,'locked-state.json'),JSON.stringify({episodeId:eid,url:page.url(),source},null,2));
      console.log('REAL_LOCKED_STARTED',eid,page.url());
    }
    if (/^(resume|adopt|skip)-(original|locked)$/.test(action)) {
      const route = action.split('-')[1];
      const state = JSON.parse(fs.readFileSync(path.join(root,route+'-state.json'),'utf8'));
      const stage = process.argv[3] || 'story';
      await page.goto(base+'/#/e/'+state.episodeId+'/'+stage);
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      const view = await query('episode_view',{episode_id:state.episodeId});
      const art = view.artifacts.find(a=>a.stage===stage);
      if (action.startsWith('skip-')) {
        await page.getByRole('button',{name:'跳过这一步',exact:true}).click();
        await page.getByLabel('跳过原因').fill('本次导入完整定稿保持情节与对白，暂跳过可选诊断；继续按同一流程制作，未执行不记为通过。');
        await page.getByRole('button',{name:'确认跳过',exact:true}).click();
        await page.getByText('本步已跳过',{exact:false}).waitFor();
        await page.getByRole('link',{name:'继续下一阶段',exact:true}).click();
        console.log('REAL_SKIPPED',stage,page.url());
      } else if (action.startsWith('adopt-')) {
        assert(art?.versions.length,'review the saved content before adoption');
        await page.getByRole('button',{name:'采用并继续',exact:true}).click();
        await page.waitForURL(url=>!url.hash.endsWith('/'+stage));
        console.log('REAL_ADOPTED',stage,page.url());
      } else if (!art?.versions.length) {
        if (!art) await page.getByRole('button',{name:'打开本阶段',exact:true}).click();
        if (!art?.tasks.some(t=>t.runs.some(r=>['queued','running'].includes(r.status)))) {
        const menu = page.locator('details.executor-settings:visible');
        await menu.locator('summary').first().click();
        await saveAuthorizedCreator(menu);
        await menu.screenshot({path:path.join(root,route+'-'+stage+'-model.png')});
        await menu.getByRole('button',{name:'收起',exact:true}).click();
        await page.getByPlaceholder('说明希望怎样起草或修改').fill(route==='original'
          ? '依照已采用的上游成果和本阶段技能，完成本阶段的完整内容。保持60–75秒、同一黄昏渡口、两个主要角色、可见因果与已定故事结局。各阶段沿用稳定编号；不要复用任何个人系列技能或角色。'
          : '依照已采用的完整定稿剧本和本阶段技能，完成本阶段全部内容。保持65秒、16:9、同一公交站、阿成和老太太两名主角、原四场编号、原对白及100%电量结局。采用轻资产制作，角色和状态编号稳定；文字设计不冒充实际生成。');
        await page.getByRole('button',{name:'发送消息',exact:true}).click();
        }
        let finished;
        for(let i=0;i<900;i++) {
          const current = await query('episode_view',{episode_id:state.episodeId});
          const a = current.artifacts.find(a=>a.stage===stage);
          const run = a?.tasks.flatMap(t=>t.runs).at(-1);
          if (run && ['failed','stopped','waiting_quota'].includes(run.status)) throw new Error(JSON.stringify(run));
          if (a?.versions.length) {finished=current;break;}
          if (i%30===0) console.log('REAL_WAIT',stage,run?.status,run?.requested_model,run?.requested_effort);
          await new Promise(resolve=>setTimeout(resolve,1000));
        }
        assert(finished,'real stage did not complete');
        fs.writeFileSync(path.join(root,route+'-'+stage+'.json'),JSON.stringify(finished,null,2));
        await page.getByRole('button',{name:'只采用',exact:true}).waitFor({timeout:15000});
        console.log('REAL_DOCUMENT_COMPLETE',stage,JSON.stringify(finished.artifacts.find(a=>a.stage===stage).versions.at(-1).content));
      } else {
        await page.getByText(art.versions.at(-1).content.synopsis?.slice(0,30) || '手工修改为新版本',{exact:false}).first().waitFor();
      }
      await page.screenshot({path:path.join(root,route+'-'+stage+'-'+action+'.png'),fullPage:true});
    }
    if (action === 'create-original') {
      const idea = '制作一支60–75秒的原创温暖喜剧短片。黄昏渡口，蛋糕店学徒小雨要在末班船离岸前，把生日蛋糕交给一位不认识的老人。她误认了拿红伞的人，发现真正寿星在对岸候船，必须用现场已有的办法让老人看懂并赶上交付。动作因果清楚，两个主要角色，一个固定渡口场景；结局完成交付且有可见的小反转。先完成01完整梗概。后续仍按同一流程开发，不使用任何品牌或个人系列角色。';
      await page.getByPlaceholder('E01：谁想做什么，遇到了什么意外？').fill(idea);
      await page.getByRole('button',{name:'继续',exact:true}).click();
      await page.waitForURL(/#\/e\/[^/]+\/story/, {timeout:45000});
      const eid = page.url().match(/#\/e\/([^/]+)/)[1];
      fs.writeFileSync(path.join(root,'original-state.json'),JSON.stringify({episodeId:eid,url:page.url(),idea},null,2));
      let finished;
      for(let i=0;i<900;i++) {
        const view = await query('episode_view',{episode_id:eid});
        const art = view.artifacts.find(a=>a.stage==='story');
        const runs = art.tasks.flatMap(t=>t.runs);
        const run = runs.at(-1);
        if (run && ['failed','stopped','waiting_quota'].includes(run.status)) throw new Error(JSON.stringify(run));
        if (art.versions.length) {finished=view;break;}
        if (i%30===0) console.log('REAL_WAIT',eid,run?.status,run?.requested_model,run?.requested_effort);
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
      assert(finished,'real story did not complete');
      fs.writeFileSync(path.join(root,'original-01.json'),JSON.stringify(finished,null,2));
      await page.getByRole('button',{name:'成果文档',exact:true}).click();
      await page.screenshot({path:path.join(root,'original-01.png'),fullPage:true});
      console.log('REAL_STORY_COMPLETE',eid,JSON.stringify(finished.artifacts.find(a=>a.stage==='story').versions.at(-1).content));
    }
    await page.screenshot({path:path.join(root,'inspect.png'),fullPage:true});
    console.log((await page.locator('body').innerText()).slice(0,2200));
    console.log('CONTROLS',JSON.stringify(await page.locator('input,textarea,select').evaluateAll(nodes=>nodes.map(n=>({tag:n.tagName,type:n.type,placeholder:n.placeholder,label:n.getAttribute('aria-label'),value:n.value,options:n.tagName==='SELECT'?[...n.options].map(o=>({text:o.text,value:o.value})):undefined})))));
    assert.deepEqual(errors,[]);
  } catch(error) {
    await page.screenshot({path:path.join(root,action+'-failure.png'),fullPage:true}).catch(()=>{});
    console.error('UI_FAILURE_BODY',(await page.locator('body').innerText()).slice(-4000));
    throw error;
  } finally {
    fs.writeFileSync(path.join(root,'browser-errors.json'),JSON.stringify(errors,null,2));
    await context.tracing.stop({path:path.join(root,action+'-'+(process.argv[3]||'home')+'-trace.zip')});
    // Leave the tab and its current result visible when this driver exits.
  }
}
main().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1);});
