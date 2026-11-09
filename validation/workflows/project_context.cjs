const assert=require("node:assert/strict");
const {chromium}=require("playwright");
const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;

async function main(){
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:960}});page.setDefaultTimeout(6500);
    const query=async(key,args={})=>{
      const res=await page.request.get(base+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
      assert.ok(res.ok(),await res.text());return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const projects=await query("list_projects");
    const first=projects.find(p=>p.name==="夜班柴犬"),second=projects.find(p=>p.name==="码头项目");
    const nav=page.getByRole("navigation",{name:"主导航"});
    const link=label=>nav.getByRole("link",{name:label,exact:true});
    const errors=[];page.on("pageerror",err=>errors.push(err.message));
    await page.goto(base);
    await link("资产库").click();
    await page.getByRole("heading",{name:"选择要查看资产库的项目"}).waitFor();
    await page.locator("main").getByRole("link",{name:second.name}).click();
    await page.getByRole("heading",{name:"复用资产库"}).waitFor();
    await page.locator("main:visible").getByText(/v1 · .+登船/).waitFor();
    assert.equal(await page.locator("main:visible").getByText(/v1 · .+守夜/).count(),0);
    const filter=page.locator("main:visible select").filter({has:page.locator("option",{hasText:"全部状态"})});
    await filter.selectOption({label:"可复用"});await page.locator("main:visible").getByText("库中还没有条目。").waitFor();
    await filter.selectOption({label:"待审核"});await page.locator("main:visible").getByText("同名道具",{exact:true}).waitFor();
    await link("选题池").click();await page.locator("main:visible").getByText(/登船时发现雨伞/).first().waitFor();
    for(const project of [first,second]){
      await link(project.name).click();
      const body=page.locator("main:visible");
      await body.getByRole("heading",{name:project.name,exact:true}).waitFor();
      assert.equal(await body.getByText(project.name,{exact:true}).count(),1);
      const wrench=body.getByRole("link",{name:"技能与分工"});
      assert.equal(await wrench.locator("svg.lucide-wrench").count(),1);
    }
    await link(first.name).click();await link("资产库").click();
    await page.locator("main:visible").getByText(/v1 · .+守夜/).waitFor();
    await link("选题池").click();await page.locator("main:visible").getByText(/守夜时发现雨伞/).first().waitFor();
    await link("剧本知识库").click();await page.waitForURL("**/#/knowledge");
    await link("设置").click();
    await page.locator("main:visible").getByRole("link",{name:/关于导演工作台/}).click();
    const info=await (await page.request.get(base+"/build-info.json")).json();
    assert.match(info.commit,/^[a-f0-9]{12}$/);assert.ok(Number.isFinite(Date.parse(info.builtAt)));
    const about=page.locator("main:visible");
    await about.getByText(info.version,{exact:true}).waitFor();
    assert.match(await about.innerText(),new RegExp(info.commit));
    assert.equal(await about.locator("time").getAttribute("datetime"),info.builtAt);
    console.log("PASS 生产构建的时间和提交号、多项目首次选择与同名资产、选题归属、单一项目标题");
    const eid=first.recent[0].id;
    await page.goto(base+"/#/e/"+eid+"/assets?tab=canvas");
    const board=page.locator(".canvas-board:visible");
    await board.locator("[data-node]").first().waitFor();
    const seeded=(await query("canvas_view",{episode_id:eid})).doc;
    const batch=(await query("episode_batches",{episode_id:eid}))[0];
    assert.ok(seeded.nodes.some(n=>n.data.batchId===batch.id));
    for(const a of await query("episode_assets",{episode_id:eid}))
      await board.locator('[data-node="'+a.id+'"] img[src="/api/blob/'+a.sha256+'"]').evaluate(el=>el.decode());
    const icons=[["文本","type"],["参考图","image"],["锁定图","lock-keyhole"],["生成","wand-sparkles"],["下游用途","link-2"]];
    for(const [label,icon] of icons){
      const tool=board.getByRole("button",{name:"＋ "+label,exact:true});
      await tool.locator("svg.lucide-"+icon).waitFor();
      await tool.click();
      await board.locator("[data-node].sel .spread svg.lucide-"+icon).waitFor();
    }
    await page.setViewportSize({width:1000,height:840});
    await board.getByRole("button",{name:"整理布局"}).click();
    const jump=board.getByLabel("跳到画布分区");
    const offsets=[];
    for(const type of ["text","usage"]){
      await jump.selectOption(type);
      offsets.push(await board.locator(".canvas-viewport").evaluate(el=>el.scrollLeft));
      const selected=board.locator("[data-node].sel");
      const cell=await selected.boundingBox(), frame=await board.locator(".canvas-viewport").boundingBox();
      assert.ok(cell.x>=frame.x && cell.x<frame.x+frame.width);
    }
    assert.ok(offsets[1]>offsets[0]+400,JSON.stringify(offsets));
    const selected=board.getByLabel("定位节点");
    const generator=seeded.nodes.find(n=>n.data.batchId===batch.id);
    const sender=seeded.nodes.find(n=>n.type==="lock");
    const source=board.locator('[data-node="'+sender.id+'"]');
    const target=board.locator('[data-node="'+generator.id+'"]');
    await board.getByLabel("缩放").selectOption("0.5");
    assert.equal(await target.evaluate(el=>getComputedStyle(el.parentElement).zoom),"0.5");
    await source.locator(".canvas-port").dragTo(target);
    await selected.selectOption(generator.id);
    const mention=board.getByRole("button",{name:sender.title,exact:true});
    await mention.click();
    assert.ok((await board.getByPlaceholder(/对这个生成节点写要求/).inputValue()).includes("@"+sender.title));
    const wires=board.locator(".canvas-viewport svg > path[stroke-dasharray], .canvas-viewport svg > path[stroke-width]");
    const pathCount=await wires.count();
    await board.getByRole("button",{name:"剪断连线"}).click();
    await wires.last().dispatchEvent("click");
    assert.equal(await wires.count(),pathCount-1);
    await board.getByRole("button",{name:"撤销",exact:true}).click();
    assert.equal(await wires.count(),pathCount);
    await board.getByLabel("缩放").selectOption("1");
    await jump.selectOption("usage");
    const vp=board.locator(".canvas-viewport");
    await vp.scrollIntoViewIfNeeded();
    const pt=await vp.evaluate(el=>{
      const box=el.getBoundingClientRect();
      for(let y=Math.max(box.top+5,10);y<Math.min(box.bottom-5,innerHeight);y+=20){
        for(let x=box.left+90;x<box.right-160;x+=50){
          const target=document.elementFromPoint(x,y);
          if(target && el.contains(target) && !target.closest(".card,button,input,select,textarea,path")) return {x,y};
        }
      }
      throw new Error("找不到可平移的画布空白");
    });
    const start=await vp.evaluate(el=>el.scrollLeft);
    await page.mouse.move(pt.x,pt.y);await page.mouse.down();await page.mouse.move(pt.x+100,pt.y,{steps:5});await page.mouse.up();
    assert.ok(await vp.evaluate(el=>el.scrollLeft)<start);
    await board.getByRole("button",{name:"全屏",exact:true}).click();
    await page.waitForFunction(()=>document.fullscreenElement?.classList.contains("canvas-board"));
    await board.getByRole("button",{name:"全屏",exact:true}).click();
    const newWindow=page.waitForEvent("popup");
    await board.getByRole("link",{name:"独立窗口"}).click();
    const other=await newWindow;
    await other.locator('[data-node="'+generator.id+'"]').waitFor();
    assert.equal(other.url(),page.url());await other.close();
    assert.deepEqual(errors,[]);
    console.log("PASS 画布初始图片和批次、五类节点工具与标题图标、跨分区定位");
  }finally{await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
