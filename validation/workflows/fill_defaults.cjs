const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const base='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1000}});page.setDefaultTimeout(15000);
  const q=async(name,args={})=>{const r=await page.request.get(base+'/api/q/'+name,{params:{args:JSON.stringify(args)}});assert.ok(r.ok(),await r.text());return r.json();};
  assert.match((await q('storage_status')).dataDir,/adw-workflows-/);
  const [project]=await q('list_projects'),pid=project.id,eid=project.recent[0].id;
  const output=path.resolve(__dirname,'../../output/validation/20261111/continuous');fs.mkdirSync(output,{recursive:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const button=name=>page.getByRole('button',{name,exact:true});
  // W-A1: actual empty reason submission stores the default while keeping selectedBy=user.
  await page.goto(base+`/#/e/${eid}/assets?tab=round`);
  await button('标必用').first().click();await page.getByLabel('选择理由',{exact:true}).fill('');
  await button('保存必用要求').click();await page.locator('.modal').waitFor({state:'hidden'});
  let assets=await q('episode_assets',{episode_id:eid});assert.equal(assets.find(a=>a.requirement)?.requirement.reason,'用户指定必用');
  assert.equal(assets.find(a=>a.requirement)?.requirement.selectedBy,'user');
  await page.screenshot({path:path.join(output,'fill-defaults-A1.png')});
  // W-A2: no description is required for an explicit text-only choice.
  await page.goto(base+`/#/e/${eid}/worksheet`);const need=page.locator('[data-need="bell"]');
  await need.getByRole('button',{name:'更改备图决定',exact:true}).click();await need.getByLabel('决定理由').fill('');
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/cmd/set_asset_need'));
  await need.getByRole('button',{name:'只用文字描述',exact:true}).click();assert.ok((await saved).ok());
  assert.equal((await q('asset_worksheet',{episode_id:eid})).rows[0].decision_reason,'导演选择只用文字');
  await page.screenshot({path:path.join(output,'fill-defaults-A2.png')});
  // W-A3: empty skip reason succeeds and persists after reload.
  await page.goto(base+`/#/e/${eid}/space`);await button('跳过这一步').click();await page.getByLabel('跳过原因').fill('');
  await button('确认跳过').click();await page.getByText('本步已跳过 · 导演选择跳过本步',{exact:true}).waitFor();
  assert.equal((await q('episode_view',{episode_id:eid})).skippedStages.space,'导演选择跳过本步');
  await page.screenshot({path:path.join(output,'fill-defaults-A3.png')});
  // W-A4: model-provided exact substring is preselected; user confirms without typing.
  await page.goto(base+`/#/p/${pid}`);await button('＋ 新增一集').click();await button('新增并去开工').click();
  await page.getByPlaceholder('一句话想法').fill('让主角说出最后一句台词。');
  await page.getByRole('button',{name:/用当前 CLI 检查冲突/}).click();await page.getByText('发现冲突',{exact:true}).waitFor();
  await page.getByText('手动标记冲突',{exact:true}).click();
  assert.equal(await page.getByLabel('选择冲突的必守设定').inputValue(),'dialogue');
  assert.equal(await page.getByPlaceholder('粘贴与设定冲突的本集原句').inputValue(),'让主角说出最后一句台词。');
  const decided=page.waitForResponse(r=>r.url().endsWith('/api/cmd/kickoff_check_decide'));
  await button('确认两句冲突').click();const decision=await (await decided).json();assert.equal(decision.decisions.dialogue.conflict,true);assert.equal(decision.decisions.dialogue.requestQuote,'让主角说出最后一句台词。');
  await page.getByText(/^导演已核对 · 存在冲突/).waitFor();
  await page.screenshot({path:path.join(output,'fill-defaults-A4.png')});
  // W-A5: continuous-choice context supplies the documented reason; same settings page layout.
  const reason='连续制作：两份候选都与此设定冲突，导演选择修改设定';
  await page.goto(base+`/#/p/${pid}/settings?reason_default=${encodeURIComponent(reason)}`);
  await button('提议修改').click();await page.getByPlaceholder('可不填',{exact:true}).fill('');
  const changed=page.waitForResponse(r=>r.url().endsWith('/api/cmd/propose_setting_change'));
  await button('确认写入新修订').click();assert.ok((await changed).ok());
  assert.equal((await q('project_view',{project_id:pid})).settings[0].user_reason,reason);
  await page.screenshot({path:path.join(output,'fill-defaults-A5.png')});
  // W-A6: upload actual bytes through the UI with usage left empty.
  assets=await q('episode_assets',{episode_id:eid});const bytes=await (await page.request.get(base+'/api/blob/'+assets[0].sha256)).body();
  await page.goto(base+`/#/p/${pid}/library`);await button('添加图片').click();
  await page.getByLabel('图片文件').setInputFiles({name:'默认用途.png',mimeType:'image/png',buffer:bytes});await page.getByLabel('用途',{exact:true}).fill('');
  await button('保存到资产库').click();await page.locator('.modal').waitFor({state:'hidden'});
  assert.equal((await q('library',{project_id:pid})).find(r=>r.name==='默认用途.png').usage,'导演上传');
  await page.screenshot({path:path.join(output,'fill-defaults-A6.png')});assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'fill-defaults-results.json'),JSON.stringify({passed:true,assertions:['W-A1','W-A2','W-A3','W-A4','W-A5','W-A6']},null,2));
  console.log('PASS W-A1–W-A6 空值提交、建议预填、保存默认文字与用户选择记录');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
