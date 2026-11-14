const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {createHash} = require("node:crypto");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const evidence = path.resolve(__dirname,"../../修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    page.setDefaultTimeout(10000);
    const query = async(name,args={})=>{
      const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(res.status(),200);return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const [project] = await query("list_projects");
    const pid = project.id, eid=project.recent[0].id;
    await page.goto(base+"/#/p/"+pid);
    const ui = page.locator(".desktop-page:not([hidden])");
    await ui.getByRole("link",{name:"自由对话",exact:true}).click();
    await ui.getByText("更多工具",{exact:true}).click();
    await ui.getByText("创作技能（0）",{exact:true}).click();
    const file = Buffer.from("# 候车动作\r\n让动作先于对白。","utf8");
    await ui.getByLabel("技能名称").fill("候车动作");
    await ui.getByLabel("自由对话技能文件").setInputFiles({name:"SKILL.md",mimeType:"text/markdown",buffer:file});
    await ui.getByRole("button",{name:"上传并使用 SKILL.md"}).click();
    await ui.getByText("已选用上传的 R1 版").waitFor();
    const opts=await query("free_chat_options",{project_id:pid});
    const asset=opts.assets[0];
    assert.ok(asset && opts.canvases.some(c=>c.id===eid));
    await ui.getByText("附项目原图（0/6）").click();
    await ui.getByLabel("附项目原图",{exact:true}).selectOption(asset.id);
    await ui.getByLabel("引用画布").selectOption(eid);
    await ui.getByLabel("对话内容").fill("把车站告别的台词改成一个动作。");
    await ui.getByRole("button",{name:"发送",exact:true}).click();
    await page.waitForURL(u=>u.hash.split("/").length===5 && u.hash.includes("/free/"));
    const url=page.url();
    const thread_id=new URL(url).hash.split("/").at(-1);
    await ui.locator(".model-reply").filter({hasText:/Codex/}).getByText("已回复",{exact:true}).waitFor();
    const first=(await query("free_chat",{project_id:pid,thread_id})).turns[0];
    assert.deepEqual(first.sources.map(s=>s.role),["project_method","canvas","image"]);
    assert.equal(first.sources[0].sha256,createHash("sha256").update(file).digest("hex"));
    assert.equal(first.sources[2].sha256,asset.sha256);
    await ui.getByText("本次引用（3）").click();
    await ui.getByText("将回复放到画布",{exact:true}).click();
    await ui.getByLabel("目标集").selectOption(eid);
    await ui.getByRole("button",{name:"添加文本节点"}).click();
    await ui.getByText("回复已写入目标画布的文本节点").waitFor();
    await page.screenshot({path:path.join(evidence,"39_自由对话与项目原图画布.png")});
    await ui.getByRole("link",{name:"打开目标画布"}).click();
    const nodeText=ui.locator(".canvas-board textarea").filter({visible:true});
    await nodeText.first().waitFor();
    assert.ok((await nodeText.evaluateAll(nodes=>nodes.map(n=>n.value))).includes(first.reply));
    await page.goto(url);
    await ui.getByLabel("对话内容").fill("把这个动作再缩短。");
    await ui.getByRole("button",{name:"发送",exact:true}).click();
    await ui.locator(".model-reply").filter({hasText:/Codex/}).getByText("已回复",{exact:true}).nth(1).waitFor();
    await page.reload();
    await ui.locator(".model-reply").filter({hasText:/Codex/}).getByText("已回复",{exact:true}).nth(1).waitFor();
    const final = await query("free_chat",{project_id:pid,thread_id});
    assert.equal(final.threads.length,1);
    assert.deepEqual(final.turns[1].methods,first.methods);
    assert.deepEqual(final.turns[1].sources.map(s=>s.role),["project_method"]);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"自由对话与画布接续验收.json"),JSON.stringify({project:pid,thread_id,
      turns:final.turns.map(t=>({id:t.id,input:t.input_hash,sources:t.sources,status:t.status})),
      canvasNode:"free-chat-"+first.id,canvasBody:first.reply,reopened:true,errors},null,2)+"\n");
    console.log("PASS project free chat, uploaded method, frozen originals, canvas read and write, follow-up after navigation and reload");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
