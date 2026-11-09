// Run using verify_browser.py against its isolated fake providers.
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
const hash = bytes=>createHash("sha256").update(bytes).digest("hex");

async function main() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(6500);
    const query = async (key,args={})=>{
      const res = await page.request.get(base+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
      assert.ok(res.ok(),await res.text());
      return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {token} = await (await page.request.get(base+"/api/session")).json();
    const cmd = async (name,data)=>{
      const res = await page.request.post(base+"/api/cmd/"+name,{data,headers:{"x-adw-token":token}});
      assert.ok(res.ok(),await res.text());
      return res.json();
    };
    const [project] = await query("list_projects");
    const pid = project.id;
    const episodes = async ()=>(await query("project_overview",{project_id:pid})).episodes;
    const bound = await cmd("add_topic",{project_id:pid,code:"USED",premise:"已开工候选"});
    const incoming = await cmd("add_topic",{project_id:pid,code:"OPEN",premise:"站台末班车"});
    await cmd("start_episode",{episode_id:null,project_id:pid,display_code:"E02",topic_id:bound.topicId,source_type:"topic",
      executor_plan:{create:{provider:"claude"},review:{provider:"codex"}}});
    const before = await episodes();
    const errors = [];
    page.on("pageerror",err=>errors.push(err.message));
    await page.goto(base);
    const btn=name=>page.getByRole("button",{name,exact:typeof name==="string"});
    // Use three independently drawn images, so a mix-up cannot pass a digest check.
    const png = async color=>Buffer.from(await page.evaluate(color=>{
      const c=document.createElement("canvas");c.width=c.height=12;
      const ctx=c.getContext("2d");ctx.fillStyle=color;ctx.fillRect(0,0,12,12);
      return c.toDataURL().split(",")[1];
    },color),"base64");
    const [one,two,three]=await Promise.all(["red","green","blue"].map(png));
    for (const [kind,name,buf] of [["paste","站台.png",one],["drop","末班车.png",two]]) {
      await page.locator(".bigbox textarea").evaluate((target,{kind,name,bytes})=>{
        const dt=new DataTransfer();
        dt.items.add(new File([new Uint8Array(bytes)],name,{type:"image/png"}));
        target.dispatchEvent(kind==="paste"?new ClipboardEvent(kind,{bubbles:true,cancelable:true,clipboardData:dt}):
          new DragEvent(kind,{bubbles:true,cancelable:true,dataTransfer:dt}));
      },{kind,name,bytes:[...buf]});
      await page.getByRole("img",{name}).evaluate(el=>el.decode());
    }
    await btn("风格").click(); await btn("墨绿").click(); await btn("应用到本集").click();
    await btn("选题").click();
    await btn(/OPEN · 站台末班车/).waitFor();
    assert.equal(await btn(/USED · 已开工候选/).count(),0);
    await btn(/OPEN · 站台末班车/).click();
    await btn(/规格预设 ·/).click(); await page.getByLabel("画幅").selectOption("16:9");
    await page.getByLabel("起草",{exact:true}).selectOption("codex"); await btn("完成").click();
    await btn("继续").click();
    const modal = page.locator(".modal");
    const chosen = modal.locator("select").filter({has:page.locator("option",{hasText:"选择选题卡"})});
    await chosen.locator("option",{hasText:"站台末班车"}).waitFor({state:"attached"});
    assert.equal(await chosen.inputValue(),incoming.topicId);
    assert.equal(await chosen.locator("option",{hasText:"已开工候选"}).count(),0);
    await modal.getByRole("button",{name:"取消",exact:true}).click();
    assert.deepEqual((await episodes()).map(e=>e.id),before.map(e=>e.id));
    assert.equal(await page.locator(".bigbox .director-files img").count(),2);
    await btn("继续").click();
    // Simulate a lost response after the server has committed. The same click can be retried.
    await page.route("**/api/with-files/start_episode",async route=>{
      const res = await route.fetch();assert.ok(res.ok(),await res.text());
      await route.abort("failed");
    },{times:1});
    await btn("开工，进入故事稿").click(); await modal.locator(".error").waitFor();
    await btn("开工，进入故事稿").click(); await page.waitForURL(/\/e\/.+\/story/);
    const eid=page.url().split("/").at(-2);
    assert.equal((await episodes()).length,before.length+1);
    const view=await query("episode_view",{episode_id:eid});
    assert.equal(view.kickoff.topic_id,incoming.topicId);
    assert.equal(view.kickoff.spec.aspectRatio,"16:9");assert.match(view.kickoff.spec.style,/墨绿/);
    assert.equal(view.kickoff.executor_plan.create.provider,"codex");
    await page.reload();await page.getByText(/本集开工参考图 · 2 张/).click();
    for (const [name,buf] of [["站台.png",one],["末班车.png",two]]) {
      const image=page.getByRole("img",{name,exact:true});
      assert.equal(await image.getAttribute("src"),"/api/blob/"+hash(buf));await image.evaluate(el=>el.decode());
    }
    const composer=page.locator(".composer");
    assert.equal(await composer.locator("textarea").getAttribute("maxlength"),"500");
    await composer.getByText("⌘ / Ctrl + 回车发送").waitFor();
    await page.getByLabel("添加阶段附件").setInputFiles([
      {name:"本次用图.png",mimeType:"image/png",buffer:three},
      {name:"说明.md",mimeType:"text/markdown",buffer:Buffer.from("末班车先关后门")}
    ]);
    await composer.locator("textarea").fill("按两张参考和附件起草");
    const sent = page.waitForResponse(res=>res.url().endsWith("/with-files/create_task"));
    await composer.locator("textarea").press("Control+Enter");
    const res=await sent;assert.ok(res.ok(),await res.text());
    const {runId}=await res.json();
    const {manifest,matches}=await query("run_manifest",{run_id:runId});
    assert.ok(matches);
    assert.deepEqual(manifest.inputs.filter(i=>i.role==="image").map(i=>i.sha256),[one,two,three].map(hash));
    assert.ok(manifest.inputs.some(i=>i.role==="citation" && i.name.includes("说明.md")));
    await btn("本集对话历史").click(); await modal.getByText("附件 · 本次用图.png").waitFor();
    await modal.getByText("附件 · 说明.md").waitFor();
    assert.deepEqual(errors,[]);
    console.log("PASS 首页图片粘贴与拖入、候选资格、取消及丢失回执后重试、阶段原图与稿件送达");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
