const assert = require("node:assert/strict");
const {chromium} = require("playwright");

async function main() {
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage();
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    const query=async(name,args={})=>{
      const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(res.ok(),await res.text());return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await query("list_projects"),pid=project.id;
    const topics=()=>query("topics",{project_id:pid});
    const before=await topics(), episodeCount=(await query("project_view",{project_id:pid})).episodes.length;
    await page.goto(base+"/#/p/"+pid+"/topics");
    const button=name=>page.getByRole("button",{name,exact:true});
    await page.getByLabel("研究方向").fill("找一个能拍出夜班交接动作的新故事");
    await button("研究备选题").click();
    const card=page.getByRole("article").filter({hasText:"交班的编号"});
    await card.getByText("交班的编号",{exact:true}).waitFor();
    assert.match(await card.innerText(),/交班单有两联/);
    assert.equal((await topics()).length,before.length);
    await page.reload();
    await card.getByLabel("入池编号").fill("原01");
    await card.getByRole("button",{name:"选中并入池"}).click();
    await page.getByText("该编号已被选题使用，请换一个编号").waitFor();
    await card.getByLabel("入池编号").fill("研02");
    await card.getByRole("button",{name:"选中并入池"}).click();
    await card.getByText("已入池 · 研02").waitFor();
    await page.reload();
    await card.getByText("已入池 · 研02").waitFor();
    const pool=await topics(),added=pool.find(c=>c.code==="研02");
    assert.equal(pool.length,before.length+1);assert.equal(added.status,"候选");
    assert.equal(added.premise,"站务员按交班单的编号寻找第二联。");assert.equal(added.episode_id,null);
    assert.equal((await query("project_view",{project_id:pid})).episodes.length,episodeCount);
    const study=(await query("topic_researches",{project_id:pid})).runs[0];
    assert.equal(study.candidates.length,2);assert.equal(study.candidates[0].savedCode,null);
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const repeat=await page.request.post(base+"/api/cmd/accept_research_topic",{headers:{"x-adw-token":token},
      data:{project_id:pid,turn_id:study.id,index:1,code:"研02"}});
    assert.equal((await repeat.json()).topicId,added.id);
    await page.getByText("比较两张选题卡").click();
    await page.getByLabel("第一张选题卡").selectOption("source:原01");
    await page.getByLabel("第二张选题卡").selectOption("topic:"+added.id);
    await page.getByText(/既有对照与结尾 · 夹具旧题/).waitFor();
    await page.getByLabel("搜索选题").fill("原01");
    await page.getByText("原01 · 夜班旧卡",{exact:true}).waitFor();
    assert.equal(await page.locator(".card.line").getByText("研02",{exact:true}).count(),0);
    assert.deepEqual(errors,[]);
    console.log("PASS 本项目原卡、台账及知识资料送入研究；两卡并排、依据可读、手选一张入池、重开及重复确认");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
