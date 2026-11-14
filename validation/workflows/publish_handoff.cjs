const assert=require("node:assert/strict");
const fs=require("node:fs/promises"),path=require("node:path");
const {createHash}=require("node:crypto");
const {chromium}=require("playwright");
const hash=bytes=>createHash("sha256").update(bytes).digest("hex");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const output=path.resolve(__dirname,"../../修复验证_20261108");
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    async function query(name,args={}){
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200);return r.json();
    }
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const [project]=await query("list_projects"),eid=project.recent[0].id;
    const art=async()=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="publish");
    const orig=(await art()).adopted_version_id;
    const previous=(await query("project_overview",{project_id:project.id})).episodes.find(e=>e.id===eid).deliveries[0].id;
    const handUrl=id=>base+"/api/delivery-file/"+id+"/03_精剪交接.md";
    const oldBytes=await (await page.request.get(handUrl(previous))).body();
    const button=name=>page.getByRole("button",{name,exact:true});
    async function click(name,command){
      const reply=page.waitForResponse(r=>r.url().endsWith("/api/cmd/"+command));
      await button(name).click();const r=await reply;assert.equal(r.status(),200,await r.text());return r.json();
    }
    await page.goto(base+"/#/e/"+eid+"/publish?v="+orig);
    await button("修改本阶段").click();
    await button("结束修改").waitFor();
    await page.getByLabel("配乐使用状态").selectOption("enabled");
    await button("补齐各段设计时间").click();
    const times=page.getByLabel("全片设计时间"),actions=page.getByLabel("动作与音乐处理");
    assert.equal(await times.count(),3); // One unscoped legacy point stays intact.
    assert.equal(await times.nth(1).inputValue(),"00:00.000–00:08.000");
    assert.equal(await times.nth(2).inputValue(),"00:08.000–00:15.000");
    await actions.nth(1).fill("手握住把手时压低");
    await actions.nth(2).fill("抽屉弹回时硬停");
    await button("补齐各段设计时间").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"47_发布页分段剪辑音乐对位.png")});
    await times.nth(2).fill("00:01.000");
    const invalid=await click("保存为新版本","save_manual");
    const diagnosis=page.getByText(/G02 的对位须落在全片设计时窗/).first();
    await diagnosis.waitFor();
    assert.ok(await button("只采用").isDisabled());
    await times.nth(2).fill("00:08.000–00:15.000");
    const saved=await click("保存为新版本","save_manual");
    await click("只采用","adopt");
    const read=await query("compose_publish",{episode_id:eid,version_id:saved.versionId});
    assert.equal(read.musicState,"enabled");assert.equal(read.cues.length,3);
    assert.equal(read.cues[2].action,"抽屉弹回时硬停");assert.ok(read.basisFrozen);
    assert.equal((await query("compose_publish",{episode_id:eid,version_id:orig})).musicState,"backup");
    await page.goto(base+"/#/e/"+eid+"/delivery");
    const delivered=await click("生成图文交付","deliver");
    const index=base+"/api/delivery-file/"+delivered.deliveryId+"/";
    await page.goto(index+"00_图文对照.html");
    const handLink=page.getByRole("link",{name:"精剪交接",exact:true});
    assert.equal(new URL(await handLink.getAttribute("href"),page.url()).href,new URL(handUrl(delivered.deliveryId)).href);
    const hand=await (await page.request.get(handUrl(delivered.deliveryId))).text();
    for(const phrase of ["本步需求 R1","保留抽屉弹回时的停顿","看清角色耳缘","只用正面","收银台位置沿用本集设定",
                          "收好钥匙","抽屉弹回时硬停","00:08.000–00:15.000",saved.versionId]) assert.ok(hand.includes(phrase),phrase);
    const manifest=await (await page.request.get(index+"episode.json")).json();
    const sources=manifest.workbench.finecut.sourceFiles;
    for(const [stage,file] of Object.entries(sources)){
      assert.equal(file.version,manifest.workbench.adopted[stage]);
      assert.ok(hand.includes("]("+file.path+")"));
      const body=await (await page.request.get(index+file.path)).body();
      assert.equal(hash(body),file.sha256);
      const truth=(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage===stage);
      assert.deepEqual(JSON.parse(body),truth.versions.find(v=>v.id===file.version).content);
    }
    await page.locator("[data-publish-pack]").getByText("抽屉弹回时硬停",{exact:true}).waitFor();
    assert.deepEqual(oldBytes,await (await page.request.get(handUrl(previous))).body());
    assert.ok((await query("verify_delivery",{delivery_id:delivered.deliveryId})).ok);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,"发布对位与整集精剪交接浏览器验收.json"),JSON.stringify({
      oldPublish:orig,rejectedOutOfWindow:invalid.versionId,newPublish:saved.versionId,basis:read.cueBasis,oldDelivery:previous,newDelivery:delivered.deliveryId,
      oldHandoffSha256:hash(oldBytes),newHandoffSha256:hash(hand),oldBytesRetained:true,cues:read.cues,
      sources,md:hand,offlineVerified:true,errors},null,2)+"\n");
    console.log("PASS publish editor, authored cue events, old versions, offline full delivery and finecut anchors");
  }finally{await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
