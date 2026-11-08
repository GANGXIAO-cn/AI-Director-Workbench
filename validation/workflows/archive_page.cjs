const assert=require("node:assert/strict");
const crypto=require("node:crypto");
const fs=require("node:fs/promises");
const path=require("node:path");
const {chromium}=require("playwright");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const out=path.resolve(__dirname,"../../修复验证_20261108");
  const inventory=JSON.parse(await fs.readFile(path.join(out,"历史图文页与实际上传逐段核对.json")));
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    const get=async(name,args={})=>{
      const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(res.status(),200);return res.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const {token}=await (await page.request.get(base+"/api/session")).json();
    const create=await page.request.post(base+"/api/cmd/create_project",{headers:{"x-adw-token":token},data:{config_id:"puggy",name:"历史图文页核对"}});
    const {projectId}=await create.json();
    await page.goto(base+"/#/p/"+projectId+"/knowledge");
    await page.getByText(/核对历史集资料（/).click();
    const checks=[];
    for(const [folder,sid] of [["2026-11-03-拍照","01"],["2026-10-30-red-carpet-mud","06"]]){
      await page.getByLabel("历史集",{exact:true}).selectOption(folder);
      await page.getByRole("button",{name:"导入副本并核对"}).click();
      const heading=page.getByText("查看页面逐段核对",{exact:true});
      await heading.click();
      const receipt=await page.getByLabel("核对版本").inputValue();
      const actual=(await get("historical_receipt",{project_id:projectId,receipt})).materialRechecked.pageContent;
      const expected=inventory.results.find(r=>r.episode===folder);
      assert.deepEqual(actual.details,expected.details);
      const row=actual.details.segments.find(r=>r.segment===sid);
      const label=heading.locator("..").locator("summary").filter({hasText:new RegExp("^"+sid+" · TXT")});
      await label.click();
      const detail=label.locator("..");
      const raw=await page.request.get(base+await detail.getByRole("link",{name:"下载本段原始 TXT"}).getAttribute("href"));
      assert.equal(raw.status(),200);
      const body=await raw.body();assert.equal(crypto.createHash("sha256").update(body).digest("hex"),row.promptSha256);
      if(row.difference){
        assert.match(await detail.innerText(),/首处差异在原文第 5 行/);
        assert.equal(await detail.locator("pre").first().innerText(),row.difference.page);
        assert.equal(await detail.locator("pre").last().innerText(),row.difference.expected);
        await detail.scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,"56_历史图文与独立TXT首处差异.png")});
      }else{
        const ref=row.inputs[0];
        const media=await page.request.get(base+await detail.getByRole("link",{name:ref.label+" · "+ref.assetId}).getAttribute("href"));
        assert.equal(crypto.createHash("sha256").update(await media.body()).digest("hex"),ref.sha256);
      }
      checks.push({folder,receipt,state:actual.state,segment:row});
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(out,"历史图文页面逐段核对浏览器验收.json"),JSON.stringify(checks,null,2)+"\n");
    console.log("PASS archived segment input, original media, raw TXT download and first divergent line");
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
