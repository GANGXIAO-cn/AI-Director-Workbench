const assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const base='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
 const output=path.resolve(__dirname,'../../output/validation/20261111/continuous');fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:960}});page.setDefaultTimeout(15000);
  const faults=[];page.on('pageerror',e=>faults.push(e.message));
  const q=async(name,args={})=>(await page.request.get(base+'/api/q/'+name,{params:{args:JSON.stringify(args)}})).json();
  assert.match((await q('storage_status')).dataDir,/adw-workflows-/);
  await page.goto(base);const results=[];
  // E-T1/E-T3 and section 3.2: preserve settings and both complete visible buttons.
  for(const width of [1280,760]){
   await page.setViewportSize({width,height:960});
   // 实测 114：首页只保留“一句话开拍”。
   assert.equal(await page.getByRole('button',{name:'详细设置后开工',exact:true}).count(),0);
   for(const name of ['一句话开拍']){
    const button=page.getByRole('button',{name,exact:true});await button.waitFor();
    const metrics=await button.evaluate(el=>({box:el.getBoundingClientRect().toJSON(),scroll:el.scrollWidth,client:el.clientWidth,whiteSpace:getComputedStyle(el).whiteSpace}));
    assert.ok(metrics.box.x>=0 && metrics.box.right<=width);assert.ok(metrics.scroll<=metrics.client+1);assert.equal(metrics.whiteSpace,'nowrap');
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:path.join(output,`quickstart-home-${width}.png`)});
  }
  await page.setViewportSize({width:1280,height:960});
  await page.getByRole('button',{name:'风格',exact:true}).click();await page.getByLabel('本集风格说明').fill('墨绿夜景');await page.getByRole('button',{name:'应用到本集',exact:true}).click();
  await page.locator('.bigbox textarea').fill('夜班学徒送错了蛋糕。');
  const sent=page.waitForResponse(r=>r.url().endsWith('/api/cmd/quick_start'));
  await page.getByRole('button',{name:'一句话开拍',exact:true}).click();const response=await sent;assert.ok(response.ok(),await response.text());
  const ids=await response.json();assert.ok(ids.episodeId && ids.continuousId);
  await page.waitForURL(/#\/e\/.+\/story$/);
  const card=page.locator('.direction-choice');await card.waitFor();
  assert.equal(await card.locator('.direction-option').count(),3);assert.equal(await card.getByText('推荐',{exact:true}).count(),1);
  assert.equal((await q('episode_view',{episode_id:ids.episodeId})).kickoff.spec.style,'墨绿夜景');
  const styles=await card.evaluate(el=>({heading:parseFloat(getComputedStyle(el.querySelector('h3')).fontSize),weight:getComputedStyle(el.querySelector('h3')).fontWeight,title:parseFloat(getComputedStyle(el.querySelector('.direction-option > b')).fontSize),hint:parseFloat(getComputedStyle(el.querySelector('.direction-hint')).fontSize)}));
  assert.deepEqual(styles,{heading:15,weight:'700',title:14,hint:12});
  await page.getByRole('button',{name:'并排看三个方向',exact:true}).click();
  await page.waitForURL(url=>url.hash.includes('?directions='+ids.continuousId));
  // F-T1/F-T3 and section 4.4: full text, exact typography, equal card width/top when three columns fit.
  for(const width of [2400,1320,1000,860]){
   await page.setViewportSize({width,height:960});await page.locator('.direction-card').first().waitFor();
   const metrics=await page.locator('.direction-compare').evaluate(el=>({width:el.clientWidth,cards:[...el.querySelectorAll('.direction-card')].map(c=>({width:c.getBoundingClientRect().width,top:c.getBoundingClientRect().top,font:parseFloat(getComputedStyle(c.querySelector('.story-reading p')).fontSize),text:c.querySelector('.story-reading').textContent})),overflow:document.documentElement.scrollWidth>innerWidth}));
   assert.equal(metrics.overflow,false);assert.equal(metrics.cards.length,3);assert.ok(metrics.cards.every(c=>c.font>=17 && c.text.length>=150));
   if(metrics.width>=1000){assert.ok(Math.max(...metrics.cards.map(c=>c.width))-Math.min(...metrics.cards.map(c=>c.width))<=2);assert.ok(Math.max(...metrics.cards.map(c=>c.top))-Math.min(...metrics.cards.map(c=>c.top))<=2);}
   else assert.ok(metrics.cards[0].top<metrics.cards[1].top && metrics.cards[1].top<metrics.cards[2].top);
   results.push({width,...metrics});await page.screenshot({path:path.join(output,`directions-${width}.png`)});
  }
  for(const target of [1320,1000,860]){
   await page.setViewportSize({width:target+1000,height:960});
   for(let i=0;i<4;i++){
    const actual=await page.locator('.direction-compare').evaluate(e=>e.clientWidth);
    if(Math.abs(actual-target)<=2)break;
    await page.setViewportSize({width:page.viewportSize().width+target-actual,height:960});
   }
   const measured=await page.locator('.direction-compare').evaluate(el=>({width:el.clientWidth,cards:[...el.querySelectorAll('.direction-card')].map(c=>({width:c.getBoundingClientRect().width,top:c.getBoundingClientRect().top})),overflow:document.documentElement.scrollWidth>innerWidth}));
   assert.ok(Math.abs(measured.width-target)<=2);assert.equal(measured.overflow,false);
   if(target>=1000){assert.ok(Math.max(...measured.cards.map(c=>c.width))-Math.min(...measured.cards.map(c=>c.width))<=2);assert.ok(Math.max(...measured.cards.map(c=>c.top))-Math.min(...measured.cards.map(c=>c.top))<=2);}
   else assert.ok(measured.cards[0].top<measured.cards[1].top && measured.cards[1].top<measured.cards[2].top);
   results.push({containerTarget:target,...measured});await page.screenshot({path:path.join(output,`directions-container-${target}.png`)});
  }
  assert.ok(results.some(r=>r.width>=1000 && r.cards[0].top===r.cards[1].top),'必须实际覆盖三栏模式');
  await page.locator('.direction-card').nth(1).getByRole('button',{name:'用这个方向',exact:true}).click();
  await page.waitForFunction(async eid=>{const r=await fetch('/api/q/episode_view?args='+encodeURIComponent(JSON.stringify({episode_id:eid})));return (await r.json()).artifacts.some(a=>a.stage==='stress'&&a.adopted_version_id);},ids.episodeId);
  const final=await q('continuous_view',{episode_id:ids.episodeId});assert.ok(final.decisions.some(d=>d.kind==='choose_direction'&&JSON.parse(d.detail).key==='B'));
  assert.deepEqual(faults,[]);fs.writeFileSync(path.join(output,'quickstart-results.json'),JSON.stringify({passed:true,assertions:['E-T1','E-T3','F-T1','F-T3','3.2','4.4'],results},null,2));
  console.log('PASS 一句话开拍、首页显式风格、三方向与全宽度比较、选 B 后实际完成 01–06');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
