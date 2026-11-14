const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const {checkImageChoice}=require('./continuous.cjs');
(async()=>{
 const base='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:2400,height:1100}});page.setDefaultTimeout(15000);
  const q=async(name,args={})=>{const r=await page.request.get(base+'/api/q/'+name,{params:{args:JSON.stringify(args)}});assert.ok(r.ok(),await r.text());return r.json();};
  assert.match((await q('storage_status')).dataDir,/adw-workflows-/);
  const [project]=await q('list_projects');const episodes=project.recent;
  const output=path.resolve(__dirname,'../../output/validation/20261111/continuous');fs.mkdirSync(output,{recursive:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const code of ['E01','E02','E03','E04','E05']){
   const ep=episodes.find(e=>e.display_code===code);assert.ok(ep,JSON.stringify(episodes));
   const run=(await q('continuous_view',{episode_id:ep.id})).run;
   await page.goto(base+`/#/e/${ep.id}/assets`);
   const card=page.locator('.image-choice');await card.waitFor();
   await card.locator('img').first().waitFor();
   assert.equal(await card.locator('article').count(),code==='E02'?1:2);
   if(code==='E02')await card.getByRole('button',{name:'再出一组看看',exact:true}).waitFor();
   if(['E03','E05'].includes(code)){
    assert.equal(await card.getByRole('button',{name:'用这张，保留这个问题',exact:true}).count(),2);
    assert.match(await card.innerText(),/usage.*支撑/s);assert.doesNotMatch(await card.innerText(),/我上传一张/);
   }
   if(code!=='E02')await checkImageChoice(page,card,run,output,code);
   else await page.screenshot({path:path.join(output,`images-${code}-card.png`)});
   if(['E04','E05'].includes(code))continue;
   await card.getByRole('button',{name:'并排看大图',exact:true}).click();
   assert.ok(page.url().endsWith(`/assets?compare=${run.id}`));
   const compare=page.locator('.image-compare');await compare.waitFor();
   for(const width of [1320,1000,860,700]){
    await page.evaluate(w=>{const center=document.querySelector('.center');center.style.width=w+'px';center.style.minWidth=w+'px';center.style.maxWidth=w+'px';const root=document.querySelector('.image-compare');root.style.width=w+'px';root.style.maxWidth='none';},width);
    await page.waitForFunction(()=>[...document.querySelectorAll('.image-compare img')].every(i=>i.complete&&i.naturalWidth));
    const metrics=await compare.evaluate(root=>{
     const articles=[...root.querySelectorAll('.image-compare-grid article')].filter(e=>getComputedStyle(e).display!=='none');
     return {width:root.getBoundingClientRect().width,overflow:root.scrollWidth-root.clientWidth,columns:articles.map(a=>{const p=a.querySelector('.image-compare-picture'),i=p.querySelector('img');return {width:p.getBoundingClientRect().width,top:p.getBoundingClientRect().top,fit:getComputedStyle(i).objectFit,natural:i.naturalWidth/i.naturalHeight,display:Math.min(i.clientWidth,i.clientHeight*i.naturalWidth/i.naturalHeight)/Math.min(i.clientHeight,i.clientWidth*i.naturalHeight/i.naturalWidth)};})};
    });
    assert.equal(Math.round(metrics.width),width);assert.ok(metrics.overflow<=1,JSON.stringify(metrics));
    assert.equal(metrics.columns.length,width<900?1:(code==='E02'?1:2));
    for(const col of metrics.columns){assert.equal(col.fit,'contain');assert.ok(Math.abs(col.natural/col.display-1)<=.01,JSON.stringify(col));}
    if(metrics.columns.length===2){assert.ok(Math.abs(metrics.columns[0].width-metrics.columns[1].width)<=2);assert.ok(Math.abs(metrics.columns[0].top-metrics.columns[1].top)<=2);}
    await compare.evaluate(r=>{const center=r.closest('.center');center.scrollTop=center.scrollHeight;});
    await page.waitForTimeout(100);
    const overlap=await compare.evaluate(r=>r.querySelector('.image-compare-assessment').getBoundingClientRect().bottom-r.querySelector('footer').getBoundingClientRect().top);
    assert.ok(overlap<=1,'操作栏遮挡评估文字 '+overlap);
    await page.screenshot({path:path.join(output,`images-${code}-${width}.png`)});
   }
  }
  assert.deepEqual(errors,[]);console.log('H image cards and comparison assertions passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
