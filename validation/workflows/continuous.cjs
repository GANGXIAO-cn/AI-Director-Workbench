const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('playwright');
// 7.3: actual portrait/landscape fixture images; only assistant thumbnails.
async function checkImageChoice(page,card,run,output,code){
 for(const width of [320,380]){
  await page.locator('.assistant').evaluate((el,w)=>{el.style.width=w+'px';el.style.minWidth=w+'px';el.style.maxWidth=w+'px';},width);
  await page.waitForFunction(()=>[...document.querySelectorAll('.image-choice img')].every(i=>i.complete&&i.naturalWidth));
  await card.evaluate(el=>{const panel=el.closest('.msgs');panel.scrollTop+=el.getBoundingClientRect().top-panel.getBoundingClientRect().top;});
  const metrics=await card.evaluate(el=>{
   const panel=el.closest('.msgs'),rect=panel.getBoundingClientRect();
   return {panelBottom:rect.bottom,top:el.getBoundingClientRect().top,panelTop:rect.top,lastButton:[...el.querySelectorAll('button')].at(-1).getBoundingClientRect().bottom,
    images:[...el.querySelectorAll('img')].map(i=>{const r=i.getBoundingClientRect(),a=i.closest('article').getBoundingClientRect();return {height:r.height,width:r.width,natural:i.naturalWidth/i.naturalHeight,fit:getComputedStyle(i).objectFit,center:(r.left+r.right-a.left-a.right)/2};})};
  });
  const label=`${run.choice.kind} ${run.choice.aspectRatio} ${width}`;
  for(const img of metrics.images){assert.ok(img.height<=(run.choice.kind==='image_failed'?128.5:200.5),label+' height '+JSON.stringify(metrics));assert.equal(img.fit,'contain');assert.ok(Math.abs(img.center)<=1,label+' centered');assert.ok(Math.abs(img.natural-Number(run.choice.aspectRatio.split(':')[0])/Number(run.choice.aspectRatio.split(':')[1]))<.01,label+' actual image aspect');}
  assert.ok(Math.abs(metrics.top-metrics.panelTop)<=1,label+' card at panel top');
  assert.ok(metrics.lastButton<=metrics.panelBottom+1,label+' last button outside panel '+JSON.stringify(metrics));
  await page.screenshot({path:path.join(output,`images-${code}-card-${width}.png`)});
 }
 await page.screenshot({path:path.join(output,`images-${code}-card.png`)});
}
module.exports={checkImageChoice};
if(require.main!==module)return;
// Section 11: one entry point for every new interface. Keep the existing
// assertions in their owning files so standalone regression commands still work.
if(process.env.ADW_CONTINUOUS_PAGE==='direction'){require('./quickstart.cjs');return;}
if(process.env.ADW_CONTINUOUS_PAGE==='images'){require('./continuous_images.cjs');return;}
(async()=>{
 const base='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
 // __dirname is validation/workflows: artifacts live at the repository root.
 const output=path.resolve(__dirname,'../../output/validation/20261111/continuous');fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH});
 try{
 const page=await browser.newPage({viewport:{width:1920,height:1080}});page.setDefaultTimeout(15000);
 const q=async(name,args={})=>{const r=await page.request.get(base+'/api/q/'+name,{params:{args:JSON.stringify(args)}});assert.equal(r.status(),200);return r.json();};
 assert.match((await q('storage_status')).dataDir,/\/adw-workflows-[^/]+\/data$/);
 // J-T2 / K-T2 / X-T2: additional UI branch uses one reusable isolated fixture.
 if(process.env.ADW_CONTINUOUS_PAGE==='preview'){
   const project=(await q('list_projects'))[0];const view=await q('project_view',{project_id:project.id});const eid=view.episodes[0].id;
   const run=(await q('continuous_view',{episode_id:eid})).run;
   await page.goto(base+`/#/e/${eid}/delivery`);
   const card=page.locator('.preview-choice');await card.waitFor();
   assert.match(await card.innerText(),/图文交付 R01 已完成，看一眼/);
   const open=card.getByRole('link',{name:'打开图文交付页',exact:true});assert.equal(await open.getAttribute('target'),'_blank');
   const response=await page.request.get(new URL(await open.getAttribute('href'),base).href);assert.equal(response.status(),200);assert.match(await response.text(),/data-delivery-template/);
   for(const width of [380,320]){
     await page.locator('.assistant').evaluate((el,w)=>{el.style.width=w+'px';el.style.minWidth=w+'px';el.style.maxWidth=w+'px';},width);
     const boxes=await card.locator('.choice-buttons > *').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {top:r.top,left:r.left,right:r.right};}));
     if(width===380)assert.ok(Math.abs(boxes[0].top-boxes[1].top)<=2);else assert.ok(boxes[1].top>boxes[0].top);
     assert.ok(await card.evaluate(e=>e.scrollWidth<=e.clientWidth));
     assert.match(await card.innerText(),/不满意？直接在下方说哪里不喜欢/);
     assert.equal(await page.getByRole('dialog').count(),0);
     await page.screenshot({path:path.join(output,`preview-${width}.png`)});
   }
   await card.getByRole('button',{name:'满意，完成交付',exact:true}).click();
   await page.locator('.continuous-status').getByText('图文交付已完成',{exact:true}).waitFor();
   const done=(await q('continuous_view',{episode_id:eid})).run;assert.equal(done.state,'done');assert.ok(!done.cursor.packageId);
   await page.locator('.composer textarea').fill('为什么这样安排？');
   await page.locator('.composer').getByRole('button',{name:'发送消息',exact:true}).click();
   await page.locator('.continuous-status').getByText('这是本集图文交付预览，可先查看再决定出包。',{exact:true}).waitFor();
   const discussed=(await q('continuous_view',{episode_id:eid})).run;assert.equal(discussed.mode,'fix');assert.equal(discussed.cursor.fixPhase,'discussion');
   // UI progress contracts: isolated query responses exercise both phases without
   // generating redundant images or changing the fixture's completed delivery.
   const data=await q('continuous_view',{episode_id:eid});
   for(const [stage,phase,current,total,label] of [['assets','images',2,5,'07 出图 · 2/5 张'],['prompts','review',3,7,'09 独审 3/7 段']]){
     await page.route('**/api/q/continuous_view?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...data,run:{...data.run,state:'running',choice:null,cursor:{...data.run.cursor,stage,phase,progress:{current,total}}}})}));
     await page.reload();await page.locator('.continuous-status').getByText(label,{exact:false}).waitFor();
     await page.unroute('**/api/q/continuous_view?*');
   }
   console.log('PASS J preview 380/320, real HTML, explicit package acceptance, K discussion');return;
 }
 const projects=await q('list_projects');const firstProject=projects.find(p=>p.name==='连续制作验收');const secondProject=projects.find(p=>p.name==='连续制作候选验收');
 const firstView=await q('project_view',{project_id:firstProject.id});const secondView=await q('project_view',{project_id:secondProject.id});
 const first=firstView.episodes.find(e=>e.display_code==='E01').id;const second=secondView.episodes.find(e=>e.display_code==='E02').id;
 const goals=await q('standard_goals');
 // B-T0: click each existing button and inspect its actual emitted command.
 const labels={story:'按开工资料起草',characters:'根据故事稿起草角色小传',world:'根据已采用稿起草世界规则',treatment:'根据已采用稿起草分场',script:'按采用大纲起草完整剧本',stress:'根据已采用剧本生成审查报告'};
 for(const stage of Object.keys(goals)){
   const eid=firstView.episodes.find(e=>e.display_code==='G_'+stage).id;let captured;
   await page.route('**/api/cmd/create_task',async route=>{captured=route.request().postDataJSON();await route.fulfill({status:200,contentType:'application/json',body:'{}'});});
   await page.goto(base+`/#/e/${eid}/${stage}`);await page.getByRole('button',{name:labels[stage],exact:true}).click();
   await page.waitForFunction(()=>true);assert.equal(captured?.goal,goals[stage],stage+' emitted goal');
   await page.unroute('**/api/cmd/create_task');
 }
 await page.goto(base+`/#/e/${first}/story`);
 await page.getByRole('button',{name:'继续做到 06 故事审查',exact:true}).click();
 await page.locator('.continuous-status:visible').getByText('01–06 已完成',{exact:true}).waitFor();
 const done=await q('continuous_view',{episode_id:first});assert.equal(done.run.state,'done');assert.equal(done.decisions.filter(d=>d.kind==='adopt').length,6);
 await page.goto(base+`/#/e/${second}/story`);
 await page.getByRole('button',{name:'继续做到 06 故事审查',exact:true}).click();
 await page.locator('.continuous-choice').waitFor();await page.getByRole('button',{name:'并排比较',exact:true}).click();
 await page.locator('.candidate-compare .compare-cell').first().waitFor();
 assert.match(page.url(),/\?compare=/);
 const results=[];
 async function inspect(label){
   await page.locator('.center:visible').evaluate(el=>el.scrollTop=el.scrollHeight);
   await page.waitForTimeout(100);
   const metrics=await page.evaluate(()=>{
     const root=[...document.querySelectorAll('.candidate-compare')].find(e=>e.getBoundingClientRect().width>0);const visible=el=>el.getBoundingClientRect().width>0 && el.getBoundingClientRect().height>0;
     const heads=[...root.querySelectorAll('.compare-candidate-head')].filter(visible).map(e=>e.getBoundingClientRect().width);
     const pairs=[...root.querySelectorAll('.compare-row .compare-grid')].map(row=>[...row.children].filter(visible).map(e=>e.getBoundingClientRect().top));
     const rows=[...root.querySelectorAll('.compare-row')];const footer=root.querySelector('.compare-footer').getBoundingClientRect();
     const body=[...root.querySelectorAll('.compare-cell,.compare-cell p,.compare-cell td,.compare-field,.compare-row-title')].filter(visible);
     return {windowOverflow:document.documentElement.scrollWidth>innerWidth,overflow:root.scrollWidth>root.clientWidth,width:root.clientWidth,heads,pairs,
       tabs:visible(root.querySelector('.compare-tabs')),visibleSides:[...new Set([...root.querySelectorAll('.compare-cell')].filter(visible).map(e=>e.dataset.side))],
       lastBottom:rows.at(-1).getBoundingClientRect().bottom,footerTop:footer.top,footerBottom:footer.bottom,windowHeight:innerHeight,
       minimumFont:Math.min(...body.map(e=>parseFloat(getComputedStyle(e).fontSize))),storyFont:parseFloat(getComputedStyle(root.querySelector('.story-reading')).fontSize)};
   });
   // C3.1: document and comparison never scroll horizontally.
   assert.equal(metrics.windowOverflow,false,label+' window overflow');assert.equal(metrics.overflow,false,label+' comparison overflow');
   if(metrics.width>=900){
     // C3.2: equal heads and top-aligned cells in every paired row.
     assert.equal(metrics.heads.length,2);assert.ok(Math.abs(metrics.heads[0]-metrics.heads[1])<=2,label+' equal widths');
     for(const tops of metrics.pairs){assert.equal(tops.length,2);assert.ok(Math.abs(tops[0]-tops[1])<=2,label+' row top alignment');}
   }else{
     // C3.3: one complete candidate at a time, with a functional A|B switch.
     assert.equal(metrics.tabs,true);assert.equal(metrics.visibleSides.length,1);
     await page.getByRole('navigation',{name:'A｜B'}).getByRole('button',{name:'B',exact:true}).click();
     assert.equal(await page.locator('.compare-cell[data-side="0"]:visible').count(),0);
     assert.ok(await page.locator('.compare-cell[data-side="1"]:visible').count());
     await page.getByRole('navigation',{name:'A｜B'}).getByRole('button',{name:'A',exact:true}).click();
   }
   // C3.4: last row clears the visible action bar after scrolling to the bottom.
   assert.ok(metrics.footerTop>=0 && metrics.footerBottom<=metrics.windowHeight+2,label+' footer visible');assert.ok(metrics.lastBottom<=metrics.footerTop+2,label+' last row clear');
   // C3.5: readable body and synopsis sizes.
   assert.ok(metrics.minimumFont>=14,label+' body font');assert.ok(metrics.storyFont>=17,label+' synopsis font');
   // C3.7: screenshot at every requested viewport/container width.
   await page.locator('.center:visible').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(output,`compare-${label}.png`)});results.push({label,...metrics});
 }
 for(const width of [1920,1280,1024,760]){await page.setViewportSize({width,height:1080});await inspect('window-'+width);}
 for(const target of [1320,1000,860,700]){
   await page.setViewportSize({width:target+1000,height:1080});
   for(let i=0;i<3;i++){const actual=await page.locator('.candidate-compare:visible').evaluate(e=>e.clientWidth);if(Math.abs(actual-target)<=2)break;await page.setViewportSize({width:page.viewportSize().width+target-actual,height:1080});}
   const actual=await page.locator('.candidate-compare:visible').evaluate(e=>e.clientWidth);assert.ok(Math.abs(actual-target)<=2,`container ${target}, actual ${actual}`);await inspect(String(target));
 }
 // C3.6: use the existing column setting for both assistant widths.
 for(const width of [320,380]){
   await page.evaluate(w=>localStorage.setItem('ws-cols',JSON.stringify([200,w])),width);await page.setViewportSize({width:1920,height:1080});await page.reload();await page.locator('.continuous-choice').waitFor();
   const metrics=await page.locator('.continuous-choice').evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth,aside:e.closest('.assistant').getBoundingClientRect().width}));assert.ok(Math.abs(metrics.aside-width)<=2);assert.ok(metrics.scroll<=metrics.client);
   await page.waitForFunction(()=>{const card=[...document.querySelectorAll('.continuous-choice')].find(e=>e.getBoundingClientRect().width>0);if(!card)return false;const a=card.getBoundingClientRect(),b=card.closest('.msgs').getBoundingClientRect();return a.top>=b.top-2 && a.bottom<=b.bottom+2;});await page.screenshot({path:path.join(output,`choice-${width}.png`)});
 }
 await page.locator('.compare-footer:visible').getByRole('button',{name:'用 A 这一版',exact:true}).click();
 await page.locator('.continuous-status:visible').getByText('01–06 已完成',{exact:true}).waitFor();assert.equal((await q('continuous_view',{episode_id:second})).run.state,'done');
 const thirdProject=projects.find(p=>p.name==='连续制作单候选验收');
 const thirdView=await q('project_view',{project_id:thirdProject.id});const third=thirdView.episodes.find(e=>e.display_code==='E03').id;
 await page.goto(base+`/#/e/${third}/story`);await page.getByRole('button',{name:'继续做到 06 故事审查',exact:true}).click();
 await page.locator('.continuous-choice:visible').waitFor();await page.getByRole('button',{name:'并排比较',exact:true}).click();await page.locator('.candidate-compare:visible').waitFor();
 const single=await q('continuous_view',{episode_id:third});assert.equal(single.run.choice.candidates.length,1);assert.equal(single.run.choice.current.adoptable,false);
 for(const width of [1920,1280,1024,760]){await page.setViewportSize({width,height:1080});await inspect('single-window-'+width);}
 for(const target of [1320,1000,860,700]){
   await page.setViewportSize({width:target+1000,height:1080});
   for(let i=0;i<3;i++){const actual=await page.locator('.candidate-compare:visible').evaluate(e=>e.clientWidth);if(Math.abs(actual-target)<=2)break;await page.setViewportSize({width:page.viewportSize().width+target-actual,height:1080});}
   await inspect('single-'+target);
 }
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,assertions:['C3.1','C3.2','C3.3','C3.4','C3.5','C3.6','C3.7'],widths:results},null,2));
 console.log('PASS continuous: six emitted standard goals; six-stage completion; qualified candidate comparison; A selection to done; C3.1–C3.7 at all widths');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
