// Start serve.py first. This audit writes only to its fresh temporary fixture.
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright");
const base = "http://127.0.0.1:" + (process.env.ADW_TEST_PORT || "18768");
const scope = process.argv[2] || "all";
async function main() {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || undefined, headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:960}});
  page.setDefaultTimeout(7000);
  const faults = [];
  page.on("pageerror", e => faults.push(e.message));
  page.on("response", r => { if(r.url().includes("/api/") && r.status() >= 500) faults.push(r.status()+" "+r.url()); });
  const query = async (name,args={}) => {
    const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
    assert.equal(r.status(),200); return r.json();
  };
  const view = eid => query("episode_view",{episode_id:eid});
  const go = hash => page.goto(base+"/#/"+hash);
  const btn = name => page.getByRole("button",{name,exact:typeof name === "string"});
  const title = name => page.getByRole("heading",{name,exact:typeof name === "string"}).first().waitFor();
  const check = async (name,fn) => {
    try {await fn(); assert.deepEqual(faults.splice(0),[]); console.log("PASS",name);}
    catch(e){console.error("FAIL",name,e.message,await page.locator("body").innerText().then(t=>t.slice(-750)));process.exitCode=1;}
  };
  try {
    const storage = await query("storage_status");
    assert.match(storage.dataDir,/\/adw-workflows-[^/]+\/data$/,"Requires isolated serve.py");
    const pid=(await query("list_projects"))[0].id;
    const overview=()=>query("project_overview",{project_id:pid});
    const seed=(await overview()).episodes.find(e=>e.display_code==="E01");
    const tag="A"+Date.now();
    let eid;
    if(["all","home"].includes(scope)) {
      await check("project topic and series methods and script timing budget open from the seeded project",async()=>{
        for (const [route, label, section] of [
          ["topics", "选题方法与原文", "topic"],
          ["groups", "连续故事制作方法与模板", "group"]
        ]) {
          await go("p/"+pid+"/"+route);
          await page.getByText(label,{exact:true}).click();
          const item=page.getByText(section,{exact:true});
          await item.click();
          const body=item.locator("..").locator("pre");
          await body.waitFor();
          assert.ok((await body.innerText()).length>40);
        }
        await go("e/"+seed.id+"/script");
        await btn("分段与时长预算").click();
        await page.getByText(/共 1 段、8 秒/).waitFor();
        await page.getByText("原句待核对 · S01").waitFor();
      });
      await check("idea and episode use text; import and reference keep separate file pickers",async()=>{
        await go(""); await title("和导演助手聊聊这一集");
        for(const tab of [/从一个想法开始/,/给项目开一集/,/图文交付/]) {
          await btn(tab).click(); assert.equal(await page.locator(".bigbox .drop").count(),0);
        }
        await btn(/导入故事稿/).click();
        await page.locator(".bigbox input[type=file]").setInputFiles({name:"来源.txt",mimeType:"text/plain",buffer:Buffer.from("故事来源")});
        await btn(/从参考片写剧本/).click(); assert.equal(await page.locator(".drop").innerText().then(t=>t.includes("来源.txt")),false);
      });
      await check("select style, create a topic, configure ratio and executors, start and reload",async()=>{
        await go(""); await btn(/从一个想法开始/).click(); await btn("风格").click();
        await page.getByLabel("本集风格说明").fill("墨绿"); await btn("应用到本集").click();
        await btn("选题").click(); await page.getByPlaceholder("选题编号").fill(tag);
        await page.getByPlaceholder("故事前提",{exact:true}).fill("一只柴犬第一次值夜班。"); await btn("新建并选用").click();
        await btn(new RegExp("选题 · "+tag)).waitFor();
        await btn(/规格预设 ·/).click(); await page.getByLabel("画幅").selectOption("16:9");
        await page.getByLabel("起草",{exact:true}).selectOption("codex");
        await btn(/调整完整开工设置/).click(); await title(/开工 ·/);
        await btn("开工并开始创作").click(); await page.waitForURL(/\/e\/.+\/story/);
        eid=page.url().split("/").at(-2); await page.reload(); await title("01 故事方向");
        const ep=await view(eid); const card=(await query("topics",{project_id:pid})).find(t=>t.code===tag);
        assert.equal(ep.kickoff.topic_id,card.id); assert.equal(card.episode_id,eid);
        assert.equal(ep.kickoff.spec.aspectRatio,"16:9");assert.match(ep.kickoff.spec.style,/墨绿/);
        await page.getByText("横屏短片",{exact:true}).waitFor();
        assert.equal(ep.kickoff.executor_plan.create.provider,"codex");
      });
      if(eid) await check("story, review, adoption, script, shot plan, upload, prompts, two reviews, publish and delivery",async()=>{
        await page.addScriptTag({url:base+"/e2e.js"});
        await page.evaluate(async id=>window.E2E.pipeline(id),eid);
        const ep=(await overview()).episodes.find(e=>e.id===eid);
        const d=ep.deliveries.find(d=>d.scope==="full");assert.ok(d);
        assert.equal((await query("verify_delivery",{delivery_id:d.id})).ok,true);
      });
      if(eid) await check("read both frozen review manifests from the segment card",async()=>{
        await go("e/"+eid+"/review");
        const first=page.locator("details").filter({has:page.locator("summary",{hasText:"第一遍送审文件"})}).last();
        await first.locator("summary").click();
        await first.getByRole("button",{name:"图片1.png",exact:true}).waitFor();
        assert.equal(await first.getByRole("button",{name:"story.md",exact:true}).count(),0);
        const second=page.locator("details").filter({has:page.locator("summary",{hasText:"第二遍送审文件"})}).last();
        await second.locator("summary").click();
        await second.getByRole("button",{name:"story.md",exact:true}).waitFor();
        assert.ok(await second.getByRole("button",{name:"pass1.json",exact:true}).count());
        await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/09_两遍独审送审文件.png")});
      });
      if(eid) await check("inspect the adopted prompt rhythm and edit a plan table in a new draft",async()=>{
        await go("e/"+eid+"/prompts");await page.getByText("镜头充分性与节奏核对",{exact:true}).click();
        await page.getByRole("columnheader",{name:"实际镜头"}).waitFor();
        await go("e/"+eid+"/plan");await btn("修改本阶段").click();
        await btn("结束修改").waitFor();
        await page.getByText("镜头充分性与节奏核对",{exact:true}).click();
        const table=page.getByRole("textbox",{name:"镜头充分性联合表"});
        const original=await table.inputValue();assert.match(original,/TXT定位短句/);
        await table.fill("# 保存后的方案表\n"+original);
        const loaded=page.waitForResponse(r=>r.url().includes("/api/q/rhythm_review")&&r.status()===200);
        await btn("将联合表保存为方案新版").click();
        const saved=await (await loaded).json();
        assert.match(saved.markdown,/保存后的方案表/);
        assert.match(await table.inputValue(),/保存后的方案表/);
      });
      await check("one-line idea starts a new episode with no attachment",async()=>{
        await go("");await page.reload();await btn(/从一个想法开始/).click();const idea=tag+" 纸船到岸";
        await btn(/规格预设 ·/).click();await page.getByLabel("画幅").selectOption("1:1");await page.getByLabel("运行模式").selectOption("manual");await btn("完成").click();
        await page.locator(".bigbox textarea").fill(idea);await btn("一句话开拍").click();

        await page.waitForURL(/\/e\/.+\/story/);assert.equal((await view(page.url().split("/").at(-2))).kickoff.idea_text,idea);
        await page.getByText("方形画幅",{exact:true}).waitFor();
      });
      await check("import a text manuscript then start from the parsed source",async()=>{
        await go("");await btn(/导入故事稿/).click();
        await page.locator(".drop input").setInputFiles({name:"故事.md",mimeType:"text/markdown",buffer:Buffer.from("夜深了，柴犬在便利店门口接班。")});
        await page.getByPlaceholder("补充本集要求（选填）").fill("保留开场门铃声");
        await btn("继续").click();await btn(/去开工/).click();
        const goal=page.getByLabel("本集补充要求");
        assert.equal(await goal.inputValue(),"保留开场门铃声");
        await goal.fill("保留开场门铃声与关门动作");
        await btn("开工并开始创作").click();await page.waitForURL(/\/e\/.+\/story/);
        const kicked=(await view(page.url().split("/").at(-2))).kickoff;
        assert.equal(kicked.source_type,"import"); assert.equal(kicked.goal,"保留开场门铃声与关门动作");
      });
      await check("drop a Word script, start from PDF text and refuse an image-only PDF",async()=>{
        await go("");
        const filePath=name=>path.join(path.dirname(storage.dataDir),name);
        const bytes=require("node:fs").readFileSync(filePath("站台分镜.docx"));
        await page.locator(".bigbox").evaluate((el,data)=>{
          const transfer=new DataTransfer();
          transfer.items.add(new File([new Uint8Array(data)],"站台分镜.docx"));
          el.dispatchEvent(new DragEvent("drop",{bubbles:true,dataTransfer:transfer}));
        },Array.from(bytes));
        await btn("继续").click();
        await page.getByText(/Word 正文段落与表格/).first().waitFor();
        await page.getByText("查看提取正文（前 800 字符）").click();
        assert.match(await page.locator("pre:visible").innerText(),/镜头一\n他收起伞/);
        await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/13_Word正文与表格导入.png")});
        await page.locator(".drop input").setInputFiles(filePath("scan.pdf"));await btn("继续").click();
        await page.getByText(/请先做文字识别/).waitFor();
        assert.equal(await btn(/去开工/).isDisabled(),true);
        await page.locator(".drop input").setInputFiles(filePath("umbrella.pdf"));await btn("继续").click();
        await page.getByText(/umbrella.pdf.*PDF 文本层/).waitFor();
        await btn(/去开工/).click();await btn("开工并开始创作").click();await page.waitForURL(/\/e\/.+\/story/);
        assert.equal((await view(page.url().split("/").at(-2))).sourceImport.parsed_text,"Platform\n\nUmbrella");
      });
      await check("reference file to shot analysis, beat and adaptation panels",async()=>{
        await go("");await btn("从参考片写剧本").click();await page.locator(".drop input").setInputFiles(path.resolve(__dirname,"../../web/public/e2e-film.mp4"));
        await btn("继续").click();await title("逐镜拉片");await btn("节拍").click();await title("节拍与可借鉴的设计");
      });
    }
    if(["all","ops"].includes(scope)) {
      await check("resize both columns, restore their defaults independently, and open the full canvas",async()=>{
        await go("e/"+seed.id+"/assets");await page.getByText("竖屏短片",{exact:true}).waitFor();
        const left=page.getByRole("separator",{name:"调整目录宽度"}), right=page.getByRole("separator",{name:"调整助手宽度"});
        const x=await left.boundingBox();await page.mouse.move(x.x+4,x.y+60);await page.mouse.down();
        await page.mouse.move(x.x+44,x.y+60,{steps:5});await page.mouse.up();
        await right.press("ArrowLeft");const widths=()=>page.locator(".ws").evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(" "));
        assert.equal((await widths())[0],"240px");assert.equal((await widths())[2],"390px");
        await page.reload();await left.waitFor();assert.equal((await widths())[0],"240px");
        await left.dblclick();assert.equal((await widths())[0],"200px");assert.equal((await widths())[2],"390px");
        await right.press("Home");assert.equal((await widths())[2],"380px");
        await btn("画布").click();await page.waitForURL(/tab=canvas/);
        await page.locator(".canvas-board").waitFor();await left.waitFor({state:"detached"});
      });
      await check("preview dialogue timing, correct overflow, save a version and keep the adopted plan",async()=>{
        await go("e/"+seed.id+"/plan"); await btn("修改本阶段").click();
        await btn("结束修改").waitFor();
        await btn("表演与台词读秒").click();
        const before=(await view(seed.id)).artifacts.find(a=>a.stage==="plan").adopted_version_id;
        await page.getByLabel("表情、姿势与动作").fill("指尖压住票根，回头看门。");
        await btn("添加一句台词").click(); await page.getByLabel("说话者").fill("站务员");
        await page.getByLabel("台词原文").fill("Wait right here");
        await page.getByLabel("读秒方式").selectOption("words");await page.getByLabel("每分钟词数").fill("120");
        await page.getByLabel("镜内起声点（秒）").fill("7"); await page.getByLabel("句后反应停顿（秒）").fill("0.5");
        await btn("核算本次调整").click(); await page.getByText(/可用时窗少 1 秒/).waitFor();
        await page.getByLabel("镜内起声点（秒）").fill("1"); await btn("核算本次调整").click();
        await page.getByRole("cell",{name:"5 秒",exact:true}).waitFor();
        const saved = page.waitForResponse(r=>r.url().includes("/api/cmd/save_manual") && r.status()===200);
        await btn("保存表演方案新版").click(); await saved;
        await page.waitForFunction(()=>[...document.querySelectorAll("button")].some(b=>b.textContent==="保存表演方案新版" && b.disabled));
        await page.getByRole("cell",{name:"5 秒",exact:true}).waitFor();
        const art=(await view(seed.id)).artifacts.find(a=>a.stage==="plan");
        assert.equal(art.adopted_version_id,before); const newest=art.versions.at(-1);
        assert.equal(newest.content.shots[0].dialogueCues[0].text,"Wait right here");
        await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/10_表演设计与台词读秒.png")});
      });
      await check("full delivery offline verification and H3 export",async()=>{
        await go("e/"+seed.id+"/delivery");await title("交付核验");await page.getByRole("link",{name:"打开图文页"}).first().waitFor();
        const link=page.getByRole("link",{name:"将这次交付打包给 H3"}).first(); const href=await link.getAttribute("href");
        await link.click(); await page.waitForURL(/package\?delivery=/);
        const selected=page.getByRole("combobox",{name:"选择交付版本"}); await selected.waitFor(); assert.equal(await selected.inputValue(),href.split("delivery=")[1]);
        await btn("下载 H3 包").click();await page.getByText("已导出",{exact:true}).waitFor();
      });
      await check("scope a required image, inspect its review impact and restore the original delivery state",async()=>{
        await go("e/"+seed.id+"/assets");
        await btn("标必用").first().click();
        await page.getByLabel("选择理由",{exact:true}).fill("保留这张原图的站姿");
        await page.getByLabel("G01",{exact:true}).check();
        await page.getByLabel("用途适配",{exact:true}).selectOption("limited");
        assert.equal(await btn("保存必用要求").isDisabled(),true);
        await page.getByLabel("用途限制或缺陷范围",{exact:true}).fill("只用于站姿，背景以场面图为准");
        await btn("保存必用要求").click();
        await btn("编辑必用要求").first().waitFor();
        const choices = await query("episode_assets",{episode_id:seed.id});
        const record = choices.find(a=>a.requirement?.reason==="保留这张原图的站姿").requirement;
        assert.deepEqual(record.targetSegments,["G01"]);
        await page.reload();
        await btn("编辑必用要求").first().click();
        assert.equal(await page.getByLabel("用途限制或缺陷范围",{exact:true}).inputValue(),record.limitations);
        await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/14_必用原图与目标段.png")});
        await btn("取消必用").click();
        await btn("标必用").first().waitFor();
        assert.equal((await query("episode_assets",{episode_id:seed.id})).some(a=>a.requirement?.reason===record.reason),false);
      });
      await check("register an episode identity original and save a segment observation from the cast page",async()=>{
        const characterEpisode=(await overview()).episodes.find(e=>e.display_code==="角色验证");
        await go("e/"+characterEpisode.id+"/cast");await title("角色出镜与身份覆盖");
        const pictures=await query("episode_assets",{episode_id:characterEpisode.id});
        const art=pictures.find(a=>a.category==="identity"), grid=pictures.find(a=>a.grid);
        const field=page.getByPlaceholder("新增角色 ID");
        await field.fill("guard");
        await page.getByLabel("独立身份原图",{exact:true}).selectOption(art.id);
        await page.getByLabel("外观短描述").fill("左襟银扣");
        const saved=page.waitForResponse(r=>r.url().includes("/api/cmd/set_role_baseline") && r.ok());
        await btn("保存本集身份基准").click();await saved;
        const picker=page.getByRole("combobox",{name:"G01 guard 身份图"});
        const cell=picker.locator("xpath=ancestor::td");
        await picker.selectOption(art.id);
        await cell.getByLabel("对照观察").fill("抬手时能看到左襟银扣");
        const bound=page.waitForResponse(r=>r.url().includes("/api/cmd/set_cast") && r.ok());
        await cell.getByRole("button",{name:"身份清楚",exact:true}).click();await bound;
        await page.reload();await page.getByLabel("角色",{exact:true}).selectOption("guard");
        assert.equal(await page.getByLabel("外观短描述").inputValue(),"左襟银扣");
        assert.equal(await cell.getByLabel("对照观察").inputValue(),"抬手时能看到左襟银扣");
        await picker.selectOption(grid.id);
        assert.equal(await cell.getByLabel("对照观察").inputValue(),"");
        await cell.getByLabel("格位").fill("1、3、9");
        await cell.getByLabel("对照观察").fill("首格左襟银扣清楚，第三格和末格仍可辨。");
        await cell.getByLabel("已核对图中角色和本段开场状态").check();
        const checked=page.waitForResponse(r=>r.url().includes("/api/cmd/set_cast") && r.ok());
        await cell.getByRole("button",{name:"身份清楚",exact:true}).click();await checked;
        await cell.getByText("已核对本段开场状态",{exact:true}).waitFor();
        const matrix=await query("cast_matrix",{episode_id:characterEpisode.id});
        assert.equal(matrix.roster.guard.assetVersionId,art.id);
        assert.deepEqual(matrix.rows[0].cells.guard.cells,[1,3,9]);
        assert.deepEqual(matrix.rows[0].cells.guard.depictsMoment,{segment:"G01",at:"start"});
        await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/11_本集角色设定与分段绑定.png")});
      });
      await check("project closure writes setting and reviewable reuse request",async()=>{
        await go("e/"+seed.id+"/delivery");await page.getByRole("link",{name:"整理本集设定"}).first().click();
        await title("收尾：设定提议");await btn("写进设定").first().click();await page.getByPlaceholder("本次写入理由（必填）").first().fill("连续集采用这一已验证的行为");
        await page.locator("select").first().selectOption({index:1});await btn("确认").click();await title("本集收尾已确认");
        assert.ok((await query("project_view",{project_id:pid})).settings.length);
        assert.ok((await query("library",{project_id:pid})).length);
      });
      await check("knowledge upload and search",async()=>{
        await go("p/"+pid+"/knowledge");await title("知识库");await page.locator('input[type=file]').setInputFiles({name:tag+".txt",mimeType:"text/plain",buffer:Buffer.from("柴犬值夜班时靠墙边灯光确认方向。")});
        await page.getByText(new RegExp(tag)).first().waitFor();
      });
      await check("create story group and continue an episode",async()=>{
        await go("p/"+pid+"/groups");await page.getByPlaceholder(/故事组名称/).fill(tag);await btn("E01").click();await btn("建立故事组").click();
        await page.getByPlaceholder("新集号").fill(tag);await btn("承接开新一集").click();await title(/开工 ·/);
        const selected=page.locator(".modal .pill.on").filter({hasText:"承接 E01"});
        assert.equal(await selected.count(),1);
      });
      await check("all episode, project and system workspaces render without runtime errors",async()=>{
        const stages=["story","script","plan","space","cast","assets","prompts","review","publish","delivery","package"];
        const project=["settings","preset","baseline","library","knowledge","topics","groups","expert"];
        for(const route of [...stages.map(s=>"e/"+seed.id+"/"+s),...project.map(s=>"p/"+pid+"/"+s),...["executors","skills","tasks","storage"].map(s=>"sys/"+s)]){
          await go(route);await page.getByRole("heading",{level:1}).first().waitFor();await page.waitForLoadState("networkidle");
          assert.doesNotMatch(await page.locator("body").innerText(),/未知页面|未知查询/);
        }
      });
      await check("backup and restore isolated copy",async()=>{
        await go("sys/storage");await btn("立即备份").click();await page.getByText(/备份 .* 个文件/).first().waitFor();
        await page.getByPlaceholder(/恢复目录/).fill(storage.dataDir+"-restored-"+tag);
        await btn("验证并恢复").click();await page.getByText(/已恢复到/).waitFor();
      });
    }
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
