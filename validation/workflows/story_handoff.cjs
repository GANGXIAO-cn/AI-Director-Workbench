const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {createHash, randomUUID} = require("node:crypto");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const root = path.resolve(__dirname,"../..");
  const evidence = path.join(root,"修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(12000);
    const errors = [];
    page.on("pageerror",e=>errors.push(e.message));
    const decoded = async response=>{
      const obj = await response.json();
      assert.equal(response.status(),200,JSON.stringify(obj));
      return obj;
    };
    const query = async(name,args={})=>decoded(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}}));
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const token = (await decoded(await page.request.get(base+"/api/session"))).token;
    const headers = {"x-adw-token":token};
    const command = async(name,args)=>decoded(await page.request.post(base+"/api/cmd/"+name,{headers,data:{...args,command_id:randomUUID()}}));
    const [project] = await query("list_projects");
    const eid = project.recent[0].id;
    const story = async()=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="story");
    const original = (await story()).adopted_version_id;
    const originalEvent = "检票员发现两半票根的缺口相合";
    const file = {name:"ticket.mp4",mimeType:"video/mp4",buffer:fs.readFileSync(path.join(root,"web/public/e2e-film.mp4"))};
    const source = await decoded(await page.request.post(base+"/api/upload/reference",{headers,multipart:{meta:JSON.stringify({projectId:project.id}),file}}));
    const {packId} = await command("create_pack",{source_id:source.sourceId,episode_id:eid});
    const pack = await query("pack_view",{pack_id:packId});
    for (const shot of pack.shots) {
      await command("edit_cuts",{pack_id:packId,code:shot.code,op:"confirm",fields:{camera:"近景",motion:"固定",narrative:"发现票根"}});
    }
    await command("add_beat",{pack_id:packId,code:"B03",shot_codes:[pack.shots[0].code],event:originalEvent,info_gain:"共同来处"});
    const otherEvent = "乘客收起检过的票根";
    await command("add_beat",{pack_id:packId,code:"B07",shot_codes:[pack.shots.at(-1).code],event:otherEvent,info_gain:"确认完成交接"});
    await command("save_pack",{pack_id:packId});
    const screen = page.locator(".desktop-page:not([hidden])");
    const button = name=>screen.getByRole("button",{name,exact:true});
    const storyUrl = base+"/#/e/"+eid+"/story";
    await page.goto(base+"/#/p/"+project.id+"/ref/"+packId);
    await button("改编").click();
    await screen.getByLabel("改编目标集").selectOption(eid);
    await screen.getByPlaceholder(/在 B03/).fill("保留发现线索的顺序，改为夜班交接。");
    await button("写改编故事").click();
    await page.waitForURL(url=>url.hash.includes("/story?v="));
    const activeId = ()=>new URLSearchParams(new URL(page.url()).hash.split("?")[1]).get("v");
    const adapted = (await story()).versions.find(v=>v.id===activeId());
    assert.ok(adapted && adapted.id!==original && !adapted.base_id);
    assert.equal((await story()).adopted_version_id,original);
    const header = screen.locator(".center .stagehead").first();
    await header.getByText(/改编自参考包 ticket.mp4 v1/).waitFor();
    await button("开发辅助").click();
    const card = screen.locator("section").filter({has:page.getByRole("heading",{name:"改编对照"})});
    await screen.getByText(originalEvent,{exact:false}).first().waitFor();
    const aids = {"故事脊":"值班员要交班，却发现抽屉少了交接单。","时间线":"关店前发现缺口，交班时补签。","规则与道具表":"两联交接单的编号相同。","因果核对表":"发现编号相合，所以追回误交的单据。"};
    const reason = "沿用先看缺口再知来处的顺序，让交接单推动本集结局。";
    await button("✎ 编辑").click();
    await screen.getByLabel("故事脊",{exact:true}).fill("取消的临时内容");
    await button("取消").click();
    await button("✎ 编辑").click();
    assert.equal(await screen.getByLabel("故事脊",{exact:true}).inputValue(),adapted.content.spine || "");
    for (const [label,value] of Object.entries(aids)) await screen.getByLabel(label,{exact:true}).fill(value);
    await screen.getByLabel("取舍理由",{exact:true}).fill(reason);
    await button("添加改编对照").click();
    const row = card.locator(".field").last();
    assert.match(await row.getByLabel("来源镜号或节拍").locator("option:checked").innerText(),/B07/);
    assert.equal(await row.getByLabel("原事件",{exact:true}).inputValue(),otherEvent);
    await row.getByLabel("本集新事件",{exact:true}).fill("值班员留存签好的第二联。");
    await row.getByLabel("取舍理由",{exact:true}).fill("回收手里的单据，确认交接完成。");
    await button("保存为手稿").click();
    await page.waitForURL(url=>url.hash.includes("/story?v=")&&!url.hash.endsWith(adapted.id));
    const edited = (await story()).versions.find(v=>v.id===activeId());
    assert.equal(edited.base_id,adapted.id);
    assert.equal(edited.content.adaptation[0].reason,reason);
    await header.getByText(/改编自参考包 ticket.mp4 v1/).waitFor();
    await card.getByText(reason,{exact:false}).waitFor();
    assert.equal(edited.content.adaptation[1].unit,"B07");
    await page.screenshot({path:path.join(evidence,"37_故事开发辅助与改编对照.png")});
    await button("发起独立审查").click();
    await button("只采用").waitFor();
    const art = await story();
    const reviewed = art.versions.find(v=>v.id===edited.id);
    assert.equal(reviewed.reviews.length,1);
    await command("adopt",{artifact_id:art.id,version_id:edited.id,expected_adopted_version_id:original});
    const script = await command("create_task",{episode_id:eid,stage:"script",goal:"按已采用的本集故事写分场剧本"});
    const frozen = async(id)=>(await query("run_manifest",{run_id:id})).manifest;
    const checked = async(manifest,role)=>{
      const entry = manifest.inputs.find(e=>e.role===role);
      assert.ok(entry,role);
      const response = await page.request.get(base+"/api/blob/"+entry.sha256);
      const bytes = await response.body();
      assert.equal(createHash("sha256").update(bytes).digest("hex"),entry.sha256);
      return {entry,text:bytes.toString("utf8")};
    };
    const create = await frozen(adapted.source_run_id);
    const review = await frozen(reviewed.reviews[0].run_id);
    const scriptInput = await frozen(script.runId);
    const reviewedStory = await checked(review,"reviewed_version");
    const handedOff = await checked(scriptInput,"adopted_story");
    assert.ok(handedOff.text.endsWith(reviewedStory.text));
    assert.ok(handedOff.text.includes(edited.id) && handedOff.text.includes(reviewed.reviews[0].id));
    for (const value of [...Object.values(aids),reason,originalEvent,otherEvent,edited.content.adaptation[1].reason,adapted.content.adaptation[0].adapted])
      assert.ok(handedOff.text.includes(value),value);
    for (const role of ["adaptation_source","reference_pack"])
      assert.equal((await checked(create,role)).entry.sha256,(await checked(review,role)).entry.sha256);
    assert.equal(review.sessionMode,"ephemeral");
    const oldInputs = await query("version_sources",{version_id:original});
    assert.equal(oldInputs.inputs.some(i=>i.role==="reference_pack"),false);
    await page.goto(storyUrl+"?v="+original);
    await page.reload();
    await header.getByText(/草稿/).waitFor();
    assert.doesNotMatch(await header.innerText(),/改编自参考包/);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(evidence,"改编故事独审与剧本交接.json"),JSON.stringify({
      scope:"本机 Chrome、真实页面与服务、模拟执行器",packId,original,adapted:adapted.id,manuscript:edited.id,
      createRun:adapted.source_run_id,reviewRun:reviewed.reviews[0].run_id,scriptRun:script.runId,
      reviewedStory:reviewedStory.entry.sha256,adoptedStory:handedOff.entry.sha256,sourceInputs:review.inputs.filter(
        i=>["adaptation_source","reference_pack"].includes(i.role)),aids,adaptation:edited.content.adaptation,
      originalAdoptionPreservedDuringDraft:true,originalVersionAttributedToPack:false,errors
    },null,2)+"\n");
    console.log("PASS adaptation opens new version, source attribution, four aids, independent review and script handoff");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
