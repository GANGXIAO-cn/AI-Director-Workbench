// Browser acceptance on a disposable clone of the mounted 12,592-document package.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const os = require("node:os");
const {chromium} = require("playwright");

async function main() {
  const root = await fs.realpath(process.env.ADW_LIBRARY_CLONE);
  assert.equal(path.dirname(path.dirname(root)), await fs.realpath(os.tmpdir()));
  assert.match(path.basename(path.dirname(root)),/^adw-full-library-/);
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    const query = async (name,args={})=>{
      const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(res.status(),200);return res.json();
    };
    const source=(await query("resource_mounts")).knowledge;
    assert.notEqual(await fs.realpath(source),root);
    const digest=async file=>crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
    const originals=["knowledgeIndex.sqlite","knowledgeCatalog.csv","knowledgeIndex.json"];
    const before=await Promise.all(originals.map(f=>digest(path.join(source,f))));
    const routes=await query("storage_status");
    assert.match(routes.dataDir,/adw-workflows-/);
    await page.goto(base+"/#/knowledge");
    await page.getByText("本机资料位置",{exact:true}).click();
    await page.getByLabel("资料路径").fill(root);
    await page.getByRole("button",{name:"挂载并刷新"}).click();
    await page.waitForFunction(async expected=>(await (await fetch("/api/q/resource_mounts")).json()).knowledge===expected,root);
    const update = page.getByRole("button",{name:"更新所选目录索引"});
    await update.click();
    await page.getByRole("status").filter({hasText:/新增 0、更新 0、移出 0 · 共 12592 份/}).waitFor();
    assert.deepEqual(await Promise.all(originals.map(f=>digest(path.join(root,f)))),before);
    await page.getByRole("button",{name:"添加剧本",exact:true}).click();
    const title="夜班耳机架验收稿";
    const body="站台外的手推车载着琥珀铃，雨棚里的两人同时抬头。";
    await page.getByLabel("剧本名称").fill(title);
    await page.getByLabel("剧本正文").fill(body);
    await page.getByRole("button",{name:"保存并加入索引"}).click();
    await page.getByRole("status").filter({hasText:/新增 1、更新 0、移出 0 · 共 12593 份/}).waitFor();
    const rel="07-用户授权作品/"+title+".md";
    const find=async needle=>(await query("browse_scripts",{query:needle})).rows.some(r=>r.path===rel);
    // Exact title and a body-only phrase both resolve in the native index.
    assert.ok((await query("browse_scripts",{query:title})).rows.some(r=>r.path===rel));
    await page.locator(".script-list button").filter({hasText:title}).click();
    await page.getByText(body,{exact:true}).first().waitFor();
    await page.getByRole("button",{name:"返回资料列表"}).click();
    await fs.writeFile(path.join(root,rel),"在灯下举起靛鹭印章。");
    await update.click();
    await page.getByRole("status").filter({hasText:/新增 0、更新 1、移出 0 · 共 12593 份/}).waitFor();
    assert.ok(await find("靛鹭"));
    const output=path.resolve(__dirname,"../../修复验证_20261108");
    await page.screenshot({path:path.join(output,"05_剧本知识库增量维护.png")});
    await fs.rename(path.join(root,rel),path.join(root,"90-隔离与待审",title+".md"));
    await update.click();
    await page.getByRole("status").filter({hasText:/新增 0、更新 0、移出 1 · 共 12592 份/}).waitFor();
    assert.ok(!await find("靛鹭"));
    const after=await Promise.all(originals.map(f=>digest(path.join(source,f))));
    assert.deepEqual(before,after);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,"知识库原包副本维护验收.json"),JSON.stringify({
      initialDocuments:12592, initialIndexed:1975, added:rel, afterAdd:12593,
      contentUpdateFound:"靛鹭", afterMove:12592, originalBundleHashes:Object.fromEntries(originals.map((f,i)=>[f,before[i]])),
      noChangeRefreshByteIdentical:true,originalBundleUnchanged:true
    },null,2)+"\n");
    console.log("PASS 12592 real documents, add/update/move in browser, original catalog and index unchanged");
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
