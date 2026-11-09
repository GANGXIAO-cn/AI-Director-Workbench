// Verify persisted shot edits and history navigation against a local fixture.
const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
async function main() {
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(6500);
    const errors=[];
    page.on("pageerror",err=>errors.push(err.message));
    page.on("dialog",async dialog=>{errors.push(dialog.message());await dialog.dismiss();});
    const query=async (key,args={})=>{
      const res=await page.request.get(base+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
      assert.ok(res.ok(),await res.text());return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {token}=await (await page.request.get(base+"/api/session")).json();
    const cmd=async (name,data)=>{
      const res=await page.request.post(base+"/api/cmd/"+name,{data,headers:{"x-adw-token":token}});
      assert.ok(res.ok(),await res.text());return res.json();
    };
    const [project]=await query("list_projects");
    const eid=project.recent[0].id;
    const btn=name=>page.getByRole("button",{name,exact:typeof name==="string"});
    await page.goto(base);
    await btn("从参考片写剧本").click();
    await page.locator(".drop input").setInputFiles(path.resolve(__dirname,"../../web/public/e2e-film.mp4"));
    await btn("继续").click();await page.getByRole("heading",{name:"逐镜拉片"}).waitFor();
    const packId=page.url().split("/").at(-1);
    const pack=()=>query("pack_view",{pack_id:packId});
    const original=await pack(), shot=original.shots[0];
    const card=page.locator(".card").filter({has:page.locator("b").filter({hasText:shot.code+"　"})}).first();
    await card.click();
    const fields={"机位与景别":"靠窗低机位","摄影机运动":"往门口横移","叙事作用":"逐步显露脚印"};
    for(const [name,value] of Object.entries(fields)) await page.getByLabel(name,{exact:true}).fill(value);
    if(original.source.has_audio) await page.getByLabel("声音",{exact:true}).fill("远处关门声");
    await btn("保留并确认").click();await page.getByText(shot.code+" 已确认",{exact:true}).waitFor();
    await page.reload();await card.click();
    for(const [name,value] of Object.entries(fields)) assert.equal(await page.getByLabel(name,{exact:true}).inputValue(),value);
    if(original.source.has_audio) assert.equal(await page.getByLabel("声音",{exact:true}).inputValue(),"远处关门声");
    else assert.ok(await page.getByLabel("声音",{exact:true}).isDisabled());
    await btn("节拍").click();await page.getByPlaceholder("事件",{exact:true}).fill("门帘掀开");
    await page.getByPlaceholder("观众新知道").fill("窗沿上有脚印");
    await btn(/用选中镜头.*建节拍/).click();await page.getByText("观众新知道：窗沿上有脚印").waitFor();
    const menu=page.locator("select").filter({has:page.locator("option",{hasText:"收藏到哪一集"})});
    await menu.selectOption(eid);await page.getByPlaceholder("收藏这个拍法的理由").fill("借横移显露脚印");
    await page.getByPlaceholder("复用条件（选填）").fill("门帘必须遮住窗沿");
    await btn(/收藏 .+ 的拍法/).click();await page.getByText(/★.*借横移显露脚印.*门帘必须遮住窗沿/).waitFor();
    await page.reload();await btn("节拍").click();await page.getByText(/★.*借横移显露脚印.*门帘必须遮住窗沿/).waitFor();
    await btn("逐镜证据").click();await card.click();
    const cut=shot.start_ms+Math.floor((shot.end_ms-shot.start_ms)/3);
    await page.getByLabel("切点（毫秒）").fill(String(cut));await btn("按切点拆镜").click();
    await page.getByText("请复核节拍 B01",{exact:true}).waitFor();
    const after=await pack();assert.ok(after.shots.some(s=>s.end_ms===cut));assert.ok(after.shots.some(s=>s.start_ms===cut));
    console.log("PASS 逐镜字段回读、收藏理由和条件、自由切点和受影响节拍");
    const assets=base+"/#/e/"+eid+"/assets";
    await page.goto(assets);await btn("画布").click(); const canvas=page.url();
    const checkCanvas=async()=>{
      await page.locator(".canvas-board").waitFor();
      assert.equal(await page.locator(".catalog:visible, .assistant:visible").count(),0);
    };
    await checkCanvas();await page.goBack();
    await page.getByRole("heading",{name:"素材与选图"}).waitFor();await page.locator(".catalog").waitFor();await page.locator(".assistant").waitFor();
    await page.goForward();await checkCanvas();
    const direct=await browser.newPage(); await direct.goto(canvas);await direct.locator(".canvas-board").waitFor();
    assert.equal(await direct.locator(".catalog:visible,.assistant:visible").count(),0);await direct.close();
    const prompt="【测试：超时】核对原始批次 "+eid;
    const batch=await cmd("create_batch",{episode_id:eid,provider:"fake-image",operation:"generate",size:"64x64",budget_cents:100,
      items:[{prompt,target:{name:"窗边",category:"scene"}}]});
    await cmd("authorize_batch",{batch_id:batch.batchId,summary_hash:batch.summaryHash});
    await cmd("execute_batch",{batch_id:batch.batchId});
    await page.goto(base+"/#/p/"+project.id);
    const todo=page.getByRole("link",{name:/查原批次/});
    assert.equal(await todo.getAttribute("href"),"#/e/"+eid+"/assets?batch="+batch.batchId);
    await todo.click();
    await page.locator(".modal").getByText(prompt,{exact:false}).first().waitFor();
    await page.locator(".modal").getByText("待核对",{exact:true}).first().waitFor();
    assert.deepEqual(errors,[]);
    console.log("PASS 画布直接打开/前进/后退、从项目待办进入正确集与原批次");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
