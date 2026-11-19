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
    await page.getByLabel("你想拍什么？（选填）").fill("做两个长篇选题，每条4分钟，关于夜班交接");
    await page.getByLabel("选题数量").selectOption("5");
    await button("生成选题").click();
    const card=page.getByRole("article").filter({hasText:"交班的编号"});
    await card.getByText("交班的编号",{exact:true}).waitFor();
    await card.getByText("长篇 · 4 分钟",{exact:true}).waitFor();
    assert.equal((await topics()).length,before.length);
    await page.reload();
    await page.getByLabel("历史研究").selectOption({index:1});
    await card.getByLabel("选题编号").fill("原01");
    await card.getByRole("button",{name:"保存到选题池"}).click();
    await page.getByText("该编号已被选题使用，请换一个编号").waitFor();
    await card.getByLabel("选题编号").fill("研02");
    await card.getByRole("button",{name:"保存到选题池"}).click();
    await card.getByText("已保存到选题池 · 研02").waitFor();
    await page.reload();
    await page.getByLabel("历史研究").selectOption({index:1});
    await card.getByText("已保存到选题池 · 研02").waitFor();
    const pool=await topics(),added=pool.find(c=>c.code==="研02");
    assert.equal(pool.length,before.length+1);assert.equal(added.status,"候选");
    assert.equal(added.premise,"站务员按交班单的编号寻找第二联。");assert.equal(added.episode_id,null);
    assert.equal((await query("project_view",{project_id:pid})).episodes.length,episodeCount);
    const study=(await query("topic_researches",{project_id:pid})).runs[0];
    assert.equal(study.candidates.length,5);assert.equal(study.candidates[0].savedCode,null);
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const repeat=await page.request.post(base+"/api/cmd/accept_research_topic",{headers:{"x-adw-token":token},
      data:{project_id:pid,turn_id:study.id,index:1,code:"研02"}});
    assert.equal((await repeat.json()).topicId,added.id);
    await page.getByText("对比两个选题").click();
    await page.getByLabel("第一张选题卡").selectOption(before.find(t=>t.code==="原01").id);
    await page.getByLabel("第二张选题卡").selectOption(added.id);
    await page.getByLabel("搜索选题").fill("研02");
    const saved=page.locator(".grid2 > .card").filter({hasText:"研02"});
    await saved.getByText("长篇 · 4 分钟",{exact:true}).waitFor();
    await saved.getByRole("button",{name:"用这个题开一集"}).click();
    await page.locator(".picked-topic").filter({hasText:"长篇 · 4 分钟"}).waitFor();
    assert.deepEqual(errors,[]);
    console.log("PASS 研究卡、历史、入池、对比与选用保留原定篇幅和时长");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
