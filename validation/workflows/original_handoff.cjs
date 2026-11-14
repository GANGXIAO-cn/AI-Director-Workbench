const {chromium}=require('playwright'), assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
 const page=await browser.newPage(), base='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
 const query=async(k,args={})=>(await page.request.get(base+'/api/q/'+k,{params:{args:JSON.stringify(args)}})).json();
 const btn=name=>page.getByRole('button',{name,exact:true});
 const [owner]=await query('list_projects'), {token}=await (await page.request.get(base+'/api/session')).json();
 await page.request.post(base+'/api/cmd/create_project',{headers:{'x-adw-token':token},data:{config_id:'_template',name:owner.name}});
 await page.goto(base);await page.locator('.home-controls button').first().click();
 await page.locator('.pop button').filter({hasText:owner.id.slice(0,4)}).click();
 await btn('风格').click();const palette=page.locator('.director-open').first();await palette.click();const paletteName=await palette.locator('strong').innerText();
 await page.getByLabel('本集风格说明').fill('冷色夜景');await btn('应用到本集').click();
 // 实测 114：本脚本验证手工开工路径。
 await page.getByRole('button',{name:/^规格预设 ·/}).click();await page.getByLabel('运行模式').selectOption('manual');await btn('完成').click();
 await btn('选题').click();await btn('从共享知识库找素材').click();
 await page.locator('.script-list article button').filter({hasText:'白话全本子不语'}).first().click();
 for(let i=0;i<3;i++)await btn('下一段').click();
 await page.getByText(/^7201–9600/).waitFor();await btn('引用此段').click();
 await btn('一句话开拍').click();
 await page.getByText(/已选素材，请先选择/).waitFor();
 assert.equal((await query('list_projects')).reduce((n,p)=>n+p.episodes,0),0);
 await btn('按这段原文拍').click();await btn('一句话开拍').click();
 await page.waitForURL(/#\/e\/.+\/story$/);
 await page.getByText('已生成 R01，待采用',{exact:true}).first().waitFor();
 assert.match(await page.locator('.story-result:visible').innerText(),/R01 · 草稿 · 待采用/);
 const ep=await query('episode_view',{episode_id:page.url().split('/').at(-2)});
 assert.equal(ep.kickoff.spec.style,'冷色夜景');assert.equal(ep.kickoff.source_type,'import');
 const imp=await query('read_import',{import_id:ep.kickoff.source_import_id});
 assert.equal(imp.project_id,owner.id);assert.match(imp.parsed_text,/白话全本子不语.*7201–9600/);
 const vid=ep.artifacts.find(a=>a.stage==='story').versions[0].id;
 const frozen=(await query('version_sources',{version_id:vid})).inputs.find(i=>i.role==='kickoff');
 assert.ok(frozen.text.includes(imp.parsed_text));assert.ok(frozen.text.includes('冷色夜景'));
 for(const stage of ['assets','prompts'])assert.equal((await query('episode_palettes',{episode_id:ep.episode.id,stage})).cards[0].name,paletteName);
 const projects=await query('list_projects');assert.equal(projects.length,2);assert.equal(projects.find(p=>p.id===owner.id).episodes,1);
 console.log('PASS: 同名项目、原文范围、风格、第一稿；项目数不变。');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
