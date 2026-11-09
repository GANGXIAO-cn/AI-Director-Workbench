const assert = require("node:assert/strict");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1024,height:768}});
    const q=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    assert.match((await q("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await q("list_projects");
    assert.equal(project.episodes,0);
    await page.goto(base+"/#/p/"+project.id+"/knowledge");
    await page.getByLabel("导入第一份资料").setInputFiles({name:"站台.txt",mimeType:"text/plain",buffer:Buffer.from("站台广播叫错了末班车的名字。")});
    await page.getByText("可读",{exact:true}).waitFor();
    assert.equal((await q("project_knowledge",{project_id:project.id}))[0].title,"站台.txt");
    await page.goto(base+"/#/p/"+project.id+"/preset");
    await page.getByText(/本项目还没有已开工的集/).waitFor();
    await page.getByRole("link",{name:"去项目选择或新增一集"}).click();
    const button=name=>page.getByRole("button",{name,exact:true});
    await button("＋ 新增一集").click();
    await button("新增并去开工").click();
    await page.getByPlaceholder("一句话想法").fill("列车员在空站台听到返程广播。");
    await button("开工，进入故事稿").click();
    await page.waitForURL(/#\/e\/[^/]+\/story$/);
    const eid=page.url().split("/").at(-2);
    await page.goto(base+"/#/e/"+eid+"/assets?tab=canvas");
    await page.getByRole("heading",{name:"这张画布还没有节点"}).waitFor();
    await button("添加第一个文本节点").click();
    const node=page.locator("[data-node]");
    await node.waitFor();
    assert.equal(await node.locator("input").first().inputValue(),"角色档案");
    await page.waitForFunction(()=>!document.querySelector(".canvas-board .first-use"));
    await page.reload();
    await page.locator("[data-node]").waitFor();
    assert.equal((await q("canvas_view",{episode_id:eid})).doc.nodes.length,1);
    console.log("PASS 空项目知识库首份资料、规格入口进入开工、空画布添加首节点并刷新保留");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
