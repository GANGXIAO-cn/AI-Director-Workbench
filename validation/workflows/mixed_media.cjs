const assert=require("node:assert/strict");
const {randomUUID,createHash}=require("node:crypto");
const {execFileSync}=require("node:child_process");
const fs=require("node:fs/promises"), path=require("node:path");
const {chromium}=require("playwright");
const digest=data=>createHash("sha256").update(data).digest("hex");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const evidence=path.resolve(__dirname,"../../修复验证_20261108");
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on("pageerror",e=>errors.push(e.message));
    const query=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    async function command(name,body){
      const r=await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data:{command_id:randomUUID(),...body}});
      assert.equal(r.status(),200,await r.text()); return r.json();
    }
    const dir=(await query("storage_status")).dataDir;
    assert.match(dir,/adw-workflows-/);
    const [project]=await query("list_projects"), eid=project.recent[0].id;
    const artifact=async()=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="prompts");
    const original=await artifact(), vid=original.adopted_version_id;
    await page.goto(base+"/#/e/"+eid+"/prompts?v="+vid);
    const field=page.getByLabel("G01 提示词正文"), before=await field.inputValue();
    const originalCount=(await query("episode_assets",{episode_id:eid})).length;
    const pick=page.getByLabel("本集可选音视频"), upload=page.getByLabel("上传本集音视频参考");
    async function selectFile(file,expected){
      const pending=page.waitForResponse(r=>r.url().endsWith("/api/upload/reference-media"));
      await upload.setInputFiles(file); const r=await pending;
      assert.equal(r.status(),expected,await r.text()); return r.json();
    }
    const rejected=await selectFile({name:"伪装.mp4",mimeType:"video/mp4",buffer:Buffer.from("not a movie")},422);
    assert.equal((await query("episode_assets",{episode_id:eid})).length,originalCount);
    const file1=path.join(dir,"转身.mp4"), file2=path.join(dir,"雨声.wav");
    execFileSync("ffmpeg",["-loglevel","error","-f","lavfi","-i","color=blue:s=64x64:d=2","-c:v","libx264","-pix_fmt","yuv420p",file1]);
    execFileSync("ffmpeg",["-loglevel","error","-f","lavfi","-i","sine=frequency=240:duration=2",file2]);
    const dirty=before.replace("【开场】","【开场】\n站台檐下滴水。");
    await field.fill(dirty);
    await page.getByPlaceholder("环境声（逗号分隔）").fill("雨点、脚步");
    async function mutate(name,btn){
      const pending=page.waitForResponse(r=>r.url().endsWith("/api/cmd/"+name));
      await btn.click(); const r=await pending;
      assert.equal(r.status(),200,await r.text());
      const result=await r.json();
      if(result.versionId) await page.waitForFunction(id=>location.hash.includes(id) && document.querySelector('textarea[aria-label="G01 提示词正文"]')?.dataset.version===id,result.versionId);
      return result;
    }
    async function add(v,purpose){
      await pick.selectOption(v.versionId);
      await page.getByLabel("音视频参考用途").fill(purpose);
      return mutate("edit_prompt_reference",page.getByRole("button",{name:"用于本段"}));
    }
    const video=await selectFile(file1,200), audio=await selectFile(file2,200);
    assert.equal(video.duration,2); assert.equal(audio.duration,2);
    await add(video,"转身节奏"); await add(audio,"雨声远近");
    await page.waitForFunction(()=>document.querySelector('audio[aria-label="音频1预览"]')?.duration===2 &&
      document.querySelector('video[aria-label="视频1预览"]')?.duration===2);
    const files=[];
    for(const [type,v,file,mime] of [["video",video,file1,"video/mp4"],["audio",audio,file2,"audio/wav"]]){
      const url=base+"/api/reference-media/"+eid+"/"+v.versionId;
      const r=await page.request.get(url), bytes=await r.body();
      assert.equal(r.headers()["content-type"],mime); assert.equal(digest(bytes),digest(await fs.readFile(file)));
      const preview=page.getByLabel((type==="video"?"视频1":"音频1")+"预览");
      await preview.evaluate(async media=>{media.muted=true; await media.play();});
      await page.waitForFunction(type=>document.querySelector(type+'[aria-label$="1预览"]')?.currentTime>0.15,type);
      const played=await preview.evaluate(media=>{media.pause();return media.currentTime;});
      files.push({type,versionId:v.versionId,sha256:digest(bytes),previewSeconds:v.duration,playedSeconds:played});
    }
    await page.getByLabel("音频1预览").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"46_提示词分段音视频实际参考.png")});
    await field.fill((await field.inputValue()).replace("站台檐下滴水。","站台檐下滴水，依音频1渐近。"));
    await mutate("reorder_refs",page.getByLabel("音频1前移"));
    const reordered=await mutate("reorder_refs",page.getByRole("button",{name:"确认重排"}));
    const state=await artifact(), entry=state.versions.find(v=>v.id===reordered.versionId).content.segments.G01;
    assert.equal(state.versions.find(v=>v.id===vid).content.segments.G01.txt,before);
    assert.deepEqual(entry.refs.map(r=>r.kind),["image","audio","video"]);
    assert.deepEqual(entry.sound.ambience,["雨点","脚步"]);
    assert.ok(entry.txt.includes("音频1：雨声远近") && entry.txt.includes("视频1：转身节奏") && entry.txt.includes("依音频1渐近"));
    const refused=page.waitForResponse(r=>r.url().endsWith("/api/cmd/edit_prompt_reference"));
    await page.getByLabel("移出音频1").click(); assert.equal((await refused).status(),422);
    await command("adopt",{artifact_id:state.id,version_id:reordered.versionId,expected_adopted_version_id:vid,and_continue:true});
    const reviews={};
    for(const pass of ["cold_read","comparison"]){
      const {runId}=await command("start_prompt_review",{version_id:reordered.versionId,segment:"G01",pass_name:pass});
      const {matches,manifest}=await query("run_manifest",{run_id:runId}); assert.ok(matches);
      const inputs=manifest.inputs;
      assert.equal(inputs.find(i=>i.role==="segment_txt").sha256,digest(entry.txt));
      const order=await (await page.request.get(base+"/api/blob/"+inputs.find(i=>i.role==="reference_order").sha256)).text();
      assert.deepEqual(order.split("\n").slice(0,3).map(l=>l.split("：")[0]),["图片1","音频1","视频1"]);
      assert.ok(order.includes("未审看视频或试听音频"));
      assert.equal(inputs.filter(i=>i.role==="image").length,1);
      assert.ok(files.every(f=>!inputs.some(i=>i.sha256===f.sha256)));
      reviews[pass]={runId,order,txtSha256:digest(entry.txt),images:1};
    }
    const {deliveryId}=await command("deliver",{episode_id:eid,scope:"trial",segments:["G01"]});
    const {previewHash}=await command("package_preview",{delivery_id:deliveryId,segments:["G01"]});
    const zip=await command("package",{delivery_id:deliveryId,segments:["G01"],expected_preview:previewHash});
    const studio=path.resolve(__dirname,"../../../自媒体 2/h3-canvas-studio");
    const imported=path.join(evidence,"浏览器上传音视频的H3实际导入.json");
    execFileSync(process.execPath,["--import",path.join(studio,"node_modules/tsx/dist/loader.mjs"),path.join(__dirname,"h3_import.mjs"),studio,zip.path,imported]);
    const report=JSON.parse(await fs.readFile(imported));
    assert.deepEqual(report.segments[0].referenceOrder.map(r=>r.label),["图片1","音频1","视频1"]);
    assert.match(report.segments[0].compiledPrompt,/依<Audio 1>渐近/);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"浏览器音视频选择与送审材料.json"),JSON.stringify({files,original:vid,edited:reordered.versionId,
      rejectedInvalidFile:rejected.error,refusedRemovalOfUsedTag:true,oldVersionUnchanged:true,reviews,zipHash:report.sha256,errors},null,2)+"\n");
    console.log("PASS mixed media upload, local playback, editing, immutable review inputs and native H3 import");
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
