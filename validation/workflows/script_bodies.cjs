// Search the actual read-only companion catalog. All creation runs use the isolated fake executor.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
const out = path.resolve(__dirname,"../../修复验证_20261108");
const digest = bytes=>crypto.createHash("sha256").update(bytes).digest("hex");

async function main() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = [];
    page.on("pageerror",e=>errors.push(e.message));
    const get = async(name,args={})=>{
      const response = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(response.status(),200); return response.json();
    };
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const cmd=async(name,data)=>{
      const response=await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data});
      assert.equal(response.status(),200,await response.text());return response.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const root=(await get("resource_mounts")).knowledge;
    const native=sql=>JSON.parse(execFileSync("sqlite3",["-readonly","-json",path.join(root,"knowledgeIndex.sqlite"),sql],{encoding:"utf8"}));
    const counts=native("select kind,indexed,count(*) as count from documents group by kind,indexed");
    const sourceFile=async(id,phrase)=>{
      const [row]=native("select title,path,indexed from documents where id="+id);
      const bytes=await fs.readFile(path.join(root,row.path));
      const text=bytes.toString("utf8").replace(/^\uFEFF/,"");
      assert.equal(row.indexed,0);assert.ok(text.includes(phrase)&&!row.path.includes(phrase));
      return {...row,phrase,text,sha256:digest(bytes),at:Array.from(text.split(phrase)[0]).length};
    };
    const novel=await sourceFile(5775,"就那么想去赚大钱");
    const method=await sourceFile(1040,"很有可能你对特瑞");
    const choose=async(source,expectedTotal)=>{
      const panel=page.getByRole("region",{name:"全库正文查找"});
      await page.getByLabel("搜索资料").fill(source.phrase);
      await page.getByLabel("搜索资料").press("Enter");
      if (expectedTotal) await panel.getByRole("status").filter({hasText:"已查 "+expectedTotal+" / "+expectedTotal}).waitFor({timeout:240000});
      const entry=panel.getByRole("button").filter({hasText:source.path});
      await entry.first().click({timeout:90000});
      await page.locator(".modal mark").filter({hasText:source.phrase}).first().waitFor();
    };
    await page.goto(base+"/#/knowledge");
    await page.getByLabel("检索方式").selectOption("body");
    await choose(novel,12592);
    await page.getByRole("button",{name:"返回资料列表"}).click();
    const screen=page.getByRole("region",{name:"全库正文查找"});
    const novelStatus=await screen.getByRole("status").innerText();
    const unresolved=await screen.locator("summary").allTextContents();
    assert.deepEqual(unresolved,[]);
    await page.screenshot({path:path.join(out,"52_全库未建索引的小说正文.png")});
    const project=(await get("list_projects")).find(p=>p.name==="夜班柴犬");
    const eid=(await get("project_overview",{project_id:project.id})).episodes.find(e=>e.display_code==="E01").id;
    await page.goto(base+"/#/e/"+eid+"/story");
    await page.getByRole("button",{name:"本步需求与参考"}).click();
    await page.getByLabel("参考与知识库").selectOption("method");
    await page.getByRole("button",{name:"选择参考片段"}).click();
    await page.getByLabel("检索方式").selectOption("body");
    await choose(method,4769);
    await page.getByLabel("借鉴方式").fill("借鉴段内表演展开方式");
    await page.getByRole("button",{name:"引用此段"}).click();
    await page.getByRole("button",{name:"保存本步配置"}).click();
    await page.getByRole("heading",{name:"本步需求",exact:true}).waitFor({state:"hidden"});
    const config=await get("step_config",{episode_id:eid,stage:"story"});
    const actual=config.references[0];
    assert.equal(actual.sha256,method.sha256);
    assert.deepEqual(actual.match,{offset:method.at,text:method.phrase});
    assert.equal(actual.text,Array.from(method.text).slice(actual.offset,actual.offset+2400).join(""));
    const task=await cmd("create_task",{episode_id:eid,stage:"story",goal:"根据选定资料编写短片故事"});
    await cmd("save_step_config",{episode_id:eid,stage:"story",mode:"off",expected_revision:config.revision});
    const manifest=await get("run_manifest",{run_id:task.runId});assert.ok(manifest.matches);
    const file=manifest.manifest.inputs.find(i=>i.name===method.title && i.role==="citation");
    const input=await(await page.request.get(base+"/api/blob/"+file.sha256)).body();
    assert.equal(digest(input),file.sha256);assert.equal(JSON.parse(input).text,actual.text);
    const view=await get("episode_view",{episode_id:eid});
    const version=view.artifacts.flatMap(a=>a.versions).find(v=>v.source_run_id===task.runId);
    assert.ok(version);
    await page.goto(base+"/#/e/"+eid+"/story?v="+version.id);
    await page.getByRole("button",{name:"版本来源",exact:true}).click();
    await page.getByText(method.title+" · 引用资料",{exact:true}).click();
    await page.getByText(method.title+" · 第 "+(actual.offset+1)+" 字起 · 已保存原文",{exact:true}).click();
    assert.equal(await page.locator(".modal .saved-script-excerpt mark").innerText(),method.phrase);
    assert.equal(await page.locator(".modal .saved-script-excerpt pre").innerText(),actual.text);
    await page.screenshot({path:path.join(out,"53_旧故事稿知识库冻结引用.png")});
    await page.getByRole("button",{name:"关闭",exact:true}).last().click();
    await page.goto(base+"/#/e/"+eid+"/story?v=missing-story-version");
    await page.getByRole("button",{name:"返回当前稿"}).waitFor();
    assert.equal(await page.getByRole("button",{name:"版本来源",exact:true}).count(),0);
    await page.getByRole("button",{name:"返回当前稿"}).click();
    await page.getByRole("button",{name:"版本来源",exact:true}).waitFor();
    await page.goto(base+"/#/knowledge");
    await page.getByLabel("检索方式").selectOption("body");
    await page.getByLabel("资料类型").selectOption("method");
    const term=page.getByLabel("搜索资料");
    await term.fill("镜头"); await term.press("Enter");
    const pane=page.getByRole("region",{name:"全库正文查找"});
    const ready=async()=>{
      try { await pane.getByRole("status").filter({hasText:/已查.*本页命中 40 份/}).waitFor({timeout:15000}); }
      catch (error) {console.error("Current search",page.url(),(await page.locator("body").innerText()).slice(0,1400),errors);throw error;}
    };
    const paths=()=>pane.locator("article button small:first-of-type").allTextContents();
    await ready();
    const firstPage=await paths();
    assert.equal(firstPage.length,40);
    await pane.getByRole("button",{name:"下一页"}).click();
    await ready();
    const secondPage=await paths();
    assert.equal(new Set([...firstPage,...secondPage]).size,80);
    await pane.getByRole("button",{name:"上一页"}).click();await ready();
    assert.deepEqual(await paths(),firstPage);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(out,"全库正文查找与旧稿引用核对.json"),JSON.stringify({counts,novelStatus,unresolved,
      novel:{title:novel.title,path:novel.path,indexed:novel.indexed,sha256:novel.sha256},method:{title:method.title,path:method.path,indexed:method.indexed,sha256:method.sha256,at:method.at},
      version:version.id,run:task.runId,manifest:manifest.fingerprint,actualCitation:file.sha256,excerptOffset:actual.offset,excerptCodePoints:Array.from(actual.text).length,missingVersionOfferedCurrentDraft:true,distinctHitsAcrossFirstTwoPages:80},null,2)+"\n");
    console.log("PASS",novelStatus,method.path,file.sha256);
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
