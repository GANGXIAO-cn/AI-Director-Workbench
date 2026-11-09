const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const evidence = path.resolve(__dirname,"../../修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors = [];
    page.on("pageerror",e=>errors.push(e.message));
    async function query(name,args={}) {
      const response = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(response.status(),200); return response.json();
    }
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const [project] = await query("list_projects");
    const eid = project.recent[0].id;
    const episode = await query("episode_view",{episode_id:eid});
    const version = episode.artifacts.find(a=>a.stage==="prompts").adopted_version_id;
    const report = await query("prompt_signals",{version_id:version});
    const repeated = report.warnings.find(w=>w.kind==="within_prompt_repeat").note;
    const states = await query("prompt_review_status",{version_id:version});
    for (const input of report.inputs) {
      assert.ok(states[input.segment].ok);
      for (const pass of ["coldRead","comparison"]) {
        const packet = await query("run_manifest",{run_id:states[input.segment][pass].runId});
        assert.equal(input.sha256,packet.manifest.inputs.find(i=>i.role==="segment_txt").sha256);
      }
    }
    await page.goto(base+"/#/e/"+eid+"/prompts?v="+version);
    await page.getByRole("button",{name:"修改本阶段",exact:true}).click();
    const oldText = page.getByLabel("G01 提示词正文");
    const draft = (await oldText.inputValue())+"\n编辑中的句子";
    await oldText.fill(draft);
    await page.getByText("提示词通读线索 · R01",{exact:true}).click();
    const panel = page.locator(".prompt-signals:visible");
    await panel.getByLabel("查看段落").selectOption("G03");
    const result = panel.locator("li").filter({hasText:repeated});
    await result.getByRole("button",{name:"查看 G03 正文"}).click();
    await page.waitForFunction(phrase=>{
      const el = document.activeElement;
      return el?.getAttribute("aria-label")==="G03 提示词正文" && el.value.slice(el.selectionStart,el.selectionEnd)===phrase;
    },repeated);
    await page.getByRole("button",{name:"G01 · 8s",exact:true}).click();
    assert.equal(await oldText.inputValue(),draft);
    await result.getByRole("button",{name:"查看 G03 正文"}).click();
    const editor = page.getByLabel("G03 提示词正文");
    await editor.fill((await editor.inputValue()).replace(repeated,"他把卡留在原地"));
    await page.getByRole("button",{name:"保存本段",exact:true}).click();
    await page.getByText("提示词通读线索 · R02",{exact:true}).click();
    await panel.getByLabel("查看段落").selectOption("G03");
    await panel.getByText(/疑似内部资产代号/).waitFor();
    assert.equal(await panel.getByText(repeated,{exact:true}).count(),0);
    const revised = (await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="prompts").versions.find(v=>v.display_no===2).id;
    assert.ok(revised && revised!==version);
    assert.equal(new URLSearchParams(new URL(page.url()).hash.split("?")[1]).get("v"),revised);
    await page.goto(base+"/#/e/"+eid+"/review?v="+version);
    await page.getByText("提示词通读线索 · R01",{exact:true}).click();
    await panel.getByLabel("查看段落").selectOption("G01");
    await panel.getByText("本段共 2 处").waitFor();
    assert.deepEqual(await query("prompt_review_status",{version_id:version}),states);
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"33_提示词独审与原文位置.png")});

    await page.goto(base+"/#/e/"+eid+"/review?v="+revised);
    const first = page.getByRole("button",{name:"送审材料（第一遍）"});
    const second = page.getByRole("button",{name:"开始第二遍"});
    await page.waitForFunction(()=>Array.from(document.querySelectorAll("button")).some(b=>b.textContent==="送审材料（第一遍）"&&!b.disabled));
    assert.ok(await second.isDisabled());
    async function submit(button) {
      const response = page.waitForResponse(r=>r.url().endsWith("/api/cmd/start_prompt_review_batch"));
      await button.click();
      const r = await response;
      assert.equal(r.status(),200);
      return (await r.json()).segments;
    }
    const cold = await submit(first);
    assert.deepEqual(Object.keys(cold),["G03"]);
    const comparison = await submit(second);
    assert.deepEqual(Object.keys(comparison),["G02","G03"]);
    assert.ok(Object.values(await query("prompt_review_status",{version_id:revised})).every(x=>x.ok));
    const token = (await (await page.request.get(base+"/api/session")).json()).token;
    const create = await page.request.post(base+"/api/cmd/create_project",{headers:{"x-adw-token":token},
      data:{config_id:"puggy",name:"历史提示词复核"}});
    assert.equal(create.status(),200);
    const {projectId} = await create.json();
    const folder = "2026-10-23-leaf-tornado";
    const baseline = JSON.parse(await fs.readFile(path.join(evidence,"历史集提示词通读线索逐项对照.json")));
    const expected = baseline.results.find(r=>r.episode===folder);
    await page.goto(base+"/#/p/"+projectId+"/knowledge");
    await page.getByText(/核对历史集资料（/).click();
    await page.getByLabel("历史集",{exact:true}).selectOption(folder);
    await page.getByRole("button",{name:"导入副本并核对"}).click();
    await page.getByText("查看通读位置与原文",{exact:true}).click();
    assert.deepEqual(await panel.locator("ol li p").allTextContents(),expected.warnings.map(w=>w.note));
    await panel.getByText("核对的 TXT 指纹",{exact:true}).click();
    assert.deepEqual(await panel.locator("code").allTextContents(),expected.inputs.map(i=>i.sha256));
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"34_历史集提示词通读核对.png")});
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"提示词通读定位浏览器验收.json"),JSON.stringify({
      version,revised,inputsMatchingBothReviewPasses:report.inputs,selectedText:repeated,unsavedSegmentRecovered:true,
      priorVerdictsPreserved:true,batchAfterG03Revision:{cold,comparison},historical:{folder,segments:expected.inputs.length,messages:expected.warnings.length},errors},null,2)+"\n");
    console.log("PASS saved-version prompt signals, exact editor selection, unsaved segment, both review packets, archived texts and digests");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
