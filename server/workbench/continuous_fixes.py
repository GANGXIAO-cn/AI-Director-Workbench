"""User-directed scoped changes after delivery; previous outputs stay intact."""
import json
from .db import connect,transaction,dumps,new_id
from .errors import row,Rejected,Conflict
from .stressrepairs import adopted
from . import workflow,stages


class ContinuousFixes:
    def fix_after_delivery_precheck(self,episode_id,text,command_id=None):
        if not isinstance(text,str) or not text.strip():raise Rejected('请说明想改哪里')
        with connect(self.db_path) as db:
            if not db.execute('SELECT 1 FROM delivery WHERE episode_id=?',(episode_id,)).fetchone():raise Rejected('本集尚未交付，请在对应阶段说明修改要求')
            active=db.execute("SELECT state,choice FROM continuous_run WHERE episode_id=? AND state NOT IN ('done','cancelled')",(episode_id,)).fetchone()
            if active and (active['state']!='waiting_choice' or json.loads(active['choice'] or '{}').get('kind')!='preview'):raise Conflict('本集正在制作或修改，请在当前进度继续')

    def fix_after_delivery(self,episode_id,text,command_id=None):
        with connect(self.db_path) as db:prior=self._existing_command(db,command_id,'fix_after_delivery',[episode_id,text]) if command_id else None
        if prior:return self._advance_continuous(prior['continuousId'])
        self.fix_after_delivery_precheck(episode_id,text,command_id)
        with transaction(self.db_path) as db:
            def create():
                parent=db.execute('SELECT * FROM continuous_run WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(episode_id,)).fetchone()
                if parent and parent['state'] not in ('done','cancelled'):
                    db.execute("UPDATE continuous_run SET state='done',reason='已接续到本次反馈处理' WHERE id=?",(parent['id'],))
                cid=new_id();cursor=dict(base=adopted(db,episode_id),ownedRuns=[],queue=[],rounds={},attempts={},stressReports=0,userStamp=self._continuous_user_stamp(db,episode_id,[]),imageOptions=json.loads(parent['cursor']).get('imageOptions',{}) if parent else {},fixText=text,fixIndex=0,fixPhase='classify',priorChoice=json.loads(parent['choice']) if parent and parent['choice'] else None,priorState=parent['state'] if parent else None)
                db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,created_at,updated_at,parent_id,mode,scope) VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,episode_id,'delivery','running',dumps(cursor),self.clock(),self.clock(),parent['id'] if parent else None,'fix','[]'))
                return dict(continuousId=cid)
            result=self._command(db,command_id,'fix_after_delivery',[episode_id,text],create)
        return self._advance_continuous(result['continuousId'])

    def _fix_context(self,eid):
        arts={a['stage']:dict(versionId=a['adopted_version_id'],content=next((v['content'] for v in a['versions'] if v['id']==a['adopted_version_id']),None)) for a in self.episode_view(eid)['artifacts'] if a['adopted_version_id']}
        needs=self.asset_worksheet(eid)['rows']
        return dict(adopted=arts,needs=[dict(needKey=n['need_key'],title=n['title'],versionId=n['adopted_version_id']) for n in needs])

    def _fix_classify(self,cid):
        value=self._continuous_record(cid);eid=value['episode_id'];text=value['cursor']['fixText'];context=self._fix_context(eid)
        plan=context['adopted']['plan']['content'];segments={s['id'] for s in plan['segments']}
        answer=self._continuous_json(cid,'prompts','判断用户是在讨论还是要求修改。reply JSON {"kind":"discussion|change","earliestStage":"最早受影响阶段","instruction":"保留用户原话的指令或问题答复","segments":[],"reason":"依据"}。不把问题自动当修改。\n'+dumps(dict(userText=text,**context)),lambda a:isinstance(a,dict) and a.get('kind') in ('discussion','change') and isinstance(a.get('instruction'),str) and bool(a['instruction'].strip()) and isinstance(a.get('segments'),list) and set(a['segments'])<=segments and (a['kind']=='discussion' or a.get('earliestStage') in workflow.ORDER),base=context['adopted']['prompts']['versionId'])
        if answer['kind']=='discussion':
            prior=value['cursor'].get('priorChoice')
            self._continuous_cursor(cid,fixPhase='discussion',reply=answer['instruction'])
            if prior and prior.get('kind')=='preview' and value['cursor'].get('priorState')=='waiting_choice':
                self._continuous_update(cid,state='waiting_choice',choice=dict(prior,reply=answer['instruction']),reason=answer['instruction'])
            else:self._continuous_update(cid,state='done',reason=answer['instruction'])
            return False
        scenes={s['id'] for s in context['adopted'].get('script',{}).get('content',{}).get('scenes',[])}
        needs={n['needKey'] for n in context['needs']}
        def valid(items):
            if not isinstance(items,list) or not items:return False
            for item in items:
                if not isinstance(item,dict) or item.get('type') not in ('prompt','image','continuity','story','other'):return False
                for key,known in (('segments',segments),('needKeys',needs),('scenes',scenes)):
                    if not isinstance(item.get(key),list) or any(not isinstance(v,str) for v in item[key]) or not set(item[key])<=known:return False
                if not all(isinstance(item.get(key),str) and item[key].strip() for key in ('instruction','reason')):return False
                if item['type'] in ('prompt','continuity') and not item['segments']:return False
                if item['type']=='image' and not item['needKeys']:return False
                if item['type']=='story' and not item['scenes']:return False
                if item['type']=='other':
                    if not isinstance(item.get('stages'),list) or not item['stages'] or not set(item['stages'])<=set(workflow.ORDER):return False
                    known=segments|needs|scenes|{field for stage in item['stages'] for field in stages.WRITABLE[stage]}
                    if not isinstance(item.get('targets'),list) or not item['targets'] or any(not isinstance(t,str) or t not in known for t in item['targets']):return False
            return True
        instruction='把用户修改要求拆成具体任务。reply 仅 JSON 数组，每项 type=prompt/image/continuity/story/other，segments 段号数组、needKeys 素材条目数组、scenes 场次数组、instruction 保留用户原话、reason。所有引用须存在。上下场空间/站位/道具/光线问题优先 continuity，不因连续性问题改剧本。story 仅剧本本身确有问题。other 另含 stages 有效阶段数组、targets 现有段号/场次/条目/字段名数组。类型不限制用户需求。\n'+dumps(dict(userText=text,rangeJudgment=answer,**context))
        tasks=self._continuous_json(cid,'prompts',instruction,valid,base=context['adopted']['prompts']['versionId'])
        order={'story':0,'image':1,'continuity':2,'prompt':3,'other':4};tasks.sort(key=lambda t:order[t['type']])
        for task in tasks:
            if task['type'] in ('story','other'):
                task['segments']=sorted(set(task['segments'])|{s['segment'] for s in plan['shots'] if s.get('origin',{}).get('scene') in task['scenes']})
            if task['type']=='image':
                old_images={n['versionId'] for n in context['needs'] if n['needKey'] in task['needKeys']}
                task['segments']=sorted(set(task['segments'])|{sid for sid,entry in context['adopted']['prompts']['content']['segments'].items() if any(r['assetVersionId'] in old_images for r in entry.get('refs',[]))})
            task['instruction']=text+'\n'+task['instruction']
            mapped=sorted({s['origin']['scene'] for s in plan['shots'] if s['segment'] in task['segments'] and s.get('origin',{}).get('scene') in scenes}|set(task['scenes']))
            stage={'story':'script','image':'asset_design','continuity':'prompts','prompt':'prompts'}.get(task['type']) or min(task['stages'],key=workflow.ORDER.index)
            with transaction(self.db_path) as db:
                decision=self._new_decision(db,eid,text,stage,'dialogue',cid,'scenes' if mapped else 'episode',mapped)
            task['decisionId']=decision['id'];task['mappedScenes']=mapped
            self._continuous_log(cid,stage,'apply_user_change',decision['id'],'准备修改：'+task['reason'],dict(userText=text,task=task,phase='planned'))
        with transaction(self.db_path) as db:db.execute('UPDATE continuous_run SET scope=? WHERE id=?',(dumps(tasks),cid))
        self._continuous_cursor(cid,fixPhase='apply')
        return True

    def _fix_stage(self,cid,stage,task):
        eid=self._continuous_record(cid)['episode_id'];art=self._continuous_art(eid,stage);base=art['adopted_version_id'];old=self._continuous_version(base)
        instruction=task['instruction']+'\n只改以下范围，未列出的场次、段、素材条目保持原样：'+dumps(task)
        self._continuous_create(cid,stage,instruction,base=base,prompt_images=stage=='prompts')
        candidate=self._continuous_art(eid,stage)['versions'][-1];content=dict(candidate['content'])
        scene_ids=set(task['scenes'])|set(task.get('mappedScenes',[]));segment_ids=set(task['segments']);need_ids=set(task['needKeys'])
        targets=set(task.get('targets',[]));scene_ids|=targets;segment_ids|=targets;need_ids|=targets
        fields=targets & set(stages.WRITABLE[stage])
        if fields:
            # A named field is a precise edit scope as well (e.g. music).
            # Preserve the rest of the document even if the model rewrites it.
            metadata={'decisionNotes','productionNoteResponses','supplements'}
            content={key:content.get(key,value) if key in fields|metadata else value for key,value in old.items()}
        # Exact preservation uses IDs. The model chooses the substantive edit;
        # deterministic merging prevents unrelated rewriting.
        for field,key,allowed in ([('scenes','id',scene_ids)] if stage in ('script','treatment') else [('scenes','sceneId',scene_ids)] if stage=='acting' else [('assets','id',need_ids)] if stage=='asset_design' else [('shots','id',{s['id'] for s in old.get('shots',[]) if s['segment'] in segment_ids or s.get('origin',{}).get('scene') in scene_ids}),('segments','id',segment_ids)] if stage=='plan' else []):
            updates={item[key]:item for item in content.get(field,[])}
            content[field]=[updates.get(item[key],item) if field in fields or item[key] in allowed else item for item in old.get(field,[])]
        if stage=='asset_design' and task.get('standardSceneVersionId'):
            scene_key=next(n['need_key'] for n in self.asset_worksheet(eid)['rows'] if n['adopted_version_id']==task['standardSceneVersionId'])
            for item in content['assets']:
                if item['id'] in need_ids:item['dependsOn']=list(dict.fromkeys([*item.get('dependsOn',[]),scene_key]))
        if stage=='prompts':
            content['segments']={sid:content.get('segments',{}).get(sid,entry) if sid in segment_ids else entry for sid,entry in old['segments'].items()}
            required=task.get('standardSceneVersionId')
            if required and any(not any(ref['assetVersionId']==required for ref in content['segments'][sid]['refs']) for sid in task['segments']):
                self._continuous_stop(cid,'blocked','连续性修订未让两段都引用标准场景图')
        if content!=candidate['content']:
            with transaction(self.db_path) as db:new=self._new_version(db,art['id'],candidate['id'],'manual',content,baseline_from=candidate['id'])
            vid=self._continuous_internal_version(cid,new)
        else:vid=candidate['id']
        if stage=='prompts':
            vid=self._continuous_shot_observations(cid,vid,segment_ids)
            vid=self._continuous_prompt_checks(cid,vid)
        self._continuous_adopt(cid,stage,vid)
        return vid

    def _fix_images(self,cid,task):
        eid=self._continuous_record(cid)['episode_id'];cursor=self._continuous_record(cid)['cursor']
        receipt=cursor.get('fixImageReceipt')
        if receipt is None:
            self._fix_stage(cid,'asset_design',task)
            receipt={key:self._need(eid,key)['adopted_version_id'] for key in task['needKeys']}
            self._continuous_cursor(cid,fixImageReceipt=receipt)
        for key in task['needKeys']:
            token=str(cursor['fixIndex'])+':'+key
            state=self._image_state(cid,key)
            if state.get('fixPrepared')!=token:
                need=self._need(eid,key)
                self.set_asset_need(eid,key,need['revision'],'generate',f'{cid}:fix-image:{token}',prompt=need['image_prompt']+'\n'+task['instruction'])
                self._image_state(cid,key,attempts=[],retiredCandidates=list(need['candidates']),libraryChecked=True,assessedRevision=None,fixPrepared=token,epoch=token)
            state=self._image_state(cid,key)
            if not state['attempts']:self._image_generate(cid,self._need(eid,key))
        if not self._continuous_images(cid):return False
        mapping={old:self._need(eid,key)['adopted_version_id'] for key,old in receipt.items() if old}
        prompts=self._continuous_art(eid,'prompts');content=self._continuous_version(prompts['adopted_version_id'])
        affected=set(task['segments'])|{sid for sid,entry in content['segments'].items() if any(r['assetVersionId'] in mapping for r in entry.get('refs',[]))}
        plan_art=self._continuous_art(eid,'plan');plan=self._continuous_version(plan_art['adopted_version_id']);changed=False
        for shot in plan['shots']:
            if shot['segment'] not in affected:continue
            for field in ('startAssetId','endAssetId'):
                if shot.get(field) in mapping:shot[field]=mapping[shot[field]];changed=True
        if changed:
            self._fix_stage(cid,'plan',dict(task,segments=sorted(affected),instruction=task['instruction']+'\n只同步下列原图版本替换及决定落点，不改变其他镜头：'+dumps(mapping)))
        matrix=self.cast_matrix(eid)
        for item in matrix['rows']:
            if item['segment'] not in affected:continue
            for role,cell in item['cells'].items():
                if cell.get('assetVersionId') in mapping:self.set_cast(eid,item['segment'],role,cell['status'],mapping[cell['assetVersionId']],cells=cell.get('cells',[]),note=cell.get('note',''),command_id=f'{cid}:rebind:{item["segment"]}:{role}')
        task=dict(task,segments=sorted(affected),instruction=task['instruction']+'\n旧图替换为新图，更新对应引用与逐镜观察：'+dumps(mapping))
        self._fix_stage(cid,'prompts',task)
        self._continuous_cursor(cid,fixImageReceipt=None)
        return True

    def _fix_continuity(self,cid,task):
        eid=self._continuous_record(cid)['episode_id'];cursor=self._continuous_record(cid)['cursor']
        selected=cursor.get('continuitySelection')
        if selected is None:
            context=self._fix_context(eid)
            adopted_scenes={n['adopted_version_id'] for n in self.asset_worksheet(eid)['rows'] if n['kind']=='scene' and n['adopted_version_id']}
            images=[a for a in self.episode_assets(eid) if a['id'] in adopted_scenes]
            ids={a['id'] for a in images};assessments=[]
            ordered=sorted(ids)
            for offset in range(0,len(ordered),4):
                assessments.extend(self.assess_images(eid,ordered[offset:offset+4],f'{cid}:continuity-scene:{cursor["fixIndex"]}:{offset}',context=dict(use='核对是否为无角色、无剧情状态的标准场景图',task=task),continuous_id=cid))
            instruction='读取所附两段原图和当前提示词，具体指出空间、站位、道具、光线哪里对不上。为两段选同一张已采用、无角色无剧情状态的标准场景图；没有合适的则要求新增。reply JSON {"explanation":"具体不一致及准备改法，一两句话","sceneVersionId":"现有标准场景图版本或 null","newSceneInstruction":"需要新图时的清晰场景要求，否则空串","stateNeedKeys":[]}。stateNeedKeys 仅列必须以该场景为基准统一的现有状态图条目。\n'+dumps(dict(task=task,context=context,images=images,actualAssessments=assessments))
            keys={n['needKey'] for n in context['needs']}
            selected=self._continuous_json(cid,'prompts',instruction,lambda a:isinstance(a,dict) and isinstance(a.get('explanation'),str) and bool(a['explanation'].strip()) and (a.get('sceneVersionId') in ids or a.get('sceneVersionId') is None and isinstance(a.get('newSceneInstruction'),str) and bool(a['newSceneInstruction'].strip())) and isinstance(a.get('stateNeedKeys'),list) and set(a['stateNeedKeys'])<=keys,base=context['adopted']['prompts']['versionId'],prompt_images=True)
            # A choice must be an actually adopted scene image, not a rejected candidate.
            if selected['sceneVersionId']:
                allowed={n['adopted_version_id'] for n in self.asset_worksheet(eid)['rows'] if n['kind']=='scene'}
                if selected['sceneVersionId'] not in allowed:self._continuous_stop(cid,'blocked','连续性修订选中的标准场景图尚未采用')
            self._continuous_cursor(cid,continuitySelection=selected)
            self._continuous_log(cid,'prompts','apply_user_change',cid,selected['explanation'],dict(task=task,phase='planned'))
        if not selected['sceneVersionId']:
            art=self._continuous_art(eid,'asset_design');old=self._continuous_version(art['adopted_version_id'])
            if not selected.get('newNeedKey'):
                self._continuous_create(cid,'asset_design','保留全部已有条目，新增一个不含角色和剧情状态的标准场景图条目，用于以下两段共用空间；不要设为 keyVisual。'+task['instruction']+'\n'+selected['newSceneInstruction'],base=art['adopted_version_id'])
                new=self._continuous_art(eid,'asset_design')['versions'][-1]
                added=[item for item in new['content']['assets'] if item['id'] not in {a['id'] for a in old['assets']}]
                if len(added)!=1 or added[0]['kind']!='scene':self._continuous_stop(cid,'blocked','连续性修订须新增一个标准场景图条目')
                if any(next((a for a in new['content']['assets'] if a['id']==item['id']),None)!=item for item in old['assets']):self._continuous_stop(cid,'blocked','标准场景新增改动了原素材条目')
                self._continuous_adopt(cid,'asset_design',new['id'])
                selected['newNeedKey']=added[0]['id'];self._continuous_cursor(cid,continuitySelection=selected)
            if not self._continuous_images(cid):return False
            selected['sceneVersionId']=self._need(eid,selected['newNeedKey'])['adopted_version_id']
            self._continuous_cursor(cid,continuitySelection=selected)
        scene=selected['sceneVersionId']
        # Freeze the newly chosen scene into the existing per-shot reference
        # scaffold before asking for rewritten prompts. Otherwise the normal
        # model-result contract correctly rejects the new reference.
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        shots=[s['id'] for s in plan['shots'] if s['segment'] in task['segments']]
        with connect(self.db_path) as db:
            existing=db.execute("SELECT c.id,c.shot_ids,d.details FROM image_choice c LEFT JOIN required_image_details d ON d.choice_id=c.id WHERE c.episode_id=? AND c.asset_version_id=? AND c.mark='required'",(eid,scene)).fetchone()
        details=json.loads(existing['details']) if existing and existing['details'] else dict(reason=selected['explanation'],fitness='not_tested',limitations='')
        details['targetSegments']=sorted(set(details.get('targetSegments',[]))|set(task['segments']))
        request={key:details[key] for key in ('reason','fitness','limitations','targetSegments')}
        self.mark_image(eid,scene,'required',shots,command_id=f'{cid}:continuity-bind:{cursor["fixIndex"]}',requirement=request)
        with transaction(self.db_path) as db:
            choice=db.execute("SELECT id,shot_ids FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark='required'",(eid,scene)).fetchone()
            db.execute('UPDATE image_choice SET shot_ids=? WHERE id=?',(dumps(list(dict.fromkeys([*json.loads(choice['shot_ids']),*shots]))),choice['id']))
            details['selectedBy']='agent' if not existing else details.get('selectedBy','user')
            db.execute('UPDATE required_image_details SET details=? WHERE choice_id=?',(dumps(details),choice['id']))
        if selected['stateNeedKeys'] and not selected.get('statesDone'):
            state_task=dict(task,type='image',needKeys=selected['stateNeedKeys'],standardSceneVersionId=scene,instruction=task['instruction']+'\n两段状态图必须以这张统一场景图为基准：'+scene)
            if not self._fix_images(cid,state_task):return False
            selected['statesDone']=True;self._continuous_cursor(cid,continuitySelection=selected)
        task=dict(task,standardSceneVersionId=scene,instruction=task['instruction']+'\n'+selected['explanation']+'\n两段都必须引用标准场景图 '+scene+'；前段结尾与后段开场按同一空间、站位、道具状态和光线写明，保留用户原话。')
        vid=self._fix_stage(cid,'prompts',task)
        if any(not any(ref['assetVersionId']==scene for ref in self._continuous_version(vid)['segments'][sid]['refs']) for sid in task['segments']):
            self._continuous_stop(cid,'blocked','连续性修订未让两段都引用标准场景图')
        self._continuous_cursor(cid,continuitySelection=None)
        return True

    def _run_continuous_fix(self,cid):
        self._continuous_execute(cid)
        value=self._continuous_record(cid);cursor=value['cursor'];eid=value['episode_id']
        if cursor.get('fixPhase')=='classify' and not self._fix_classify(cid):return self._continuous_record(cid)
        value=self._continuous_record(cid);tasks=value['scope']
        while value['cursor']['fixIndex']<len(tasks):
            index=value['cursor']['fixIndex'];task=tasks[index]
            self._continuous_cursor(cid,fixStatus='准备修改：'+task['reason'])
            if task['type']=='image':
                if not self._fix_images(cid,task):return self._continuous_record(cid)
            elif task['type']=='continuity':
                if not self._fix_continuity(cid,task):return self._continuous_record(cid)
            elif task['type']=='prompt':self._fix_stage(cid,'prompts',task)
            else:
                requested=['script'] if task['type']=='story' else task['stages']
                plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
                affected=set(task['segments'])|{s['segment'] for s in plan['shots'] if s.get('origin',{}).get('scene') in task['scenes']}
                task=dict(task,segments=sorted(affected))
                for stage in sorted(requested,key=workflow.ORDER.index):self._fix_stage(cid,stage,task)
                if any(workflow.ORDER.index(stage)<workflow.ORDER.index('plan') for stage in requested):
                    # Model identifies required image changes rather than assuming
                    # a rewritten scene can use mismatching previous images.
                    context=self._fix_context(eid)
                    keys={n['needKey'] for n in context['needs']}
                    repair=self._continuous_json(cid,'asset_design','上游指定范围已修改。核对哪些采用图片必须随之改变。reply JSON {"needKeys":[],"reason":"依据"}。只列实际不匹配的现有条目。\n'+dumps(dict(task=task,context=context)),lambda a:isinstance(a,dict) and isinstance(a.get('needKeys'),list) and set(a['needKeys'])<=keys and isinstance(a.get('reason'),str))
                    if repair['needKeys']:
                        if not self._fix_images(cid,dict(task,needKeys=repair['needKeys'])):return self._continuous_record(cid)
                    self._fix_stage(cid,'plan',task);self._continuous_cast(cid)
                if 'prompts' not in requested and any(workflow.ORDER.index(stage)<workflow.ORDER.index('prompts') for stage in requested):self._fix_stage(cid,'prompts',task)
            self._continuous_log(cid,'prompts','apply_user_change',cid,'已完成：'+task['reason'],dict(task=task,phase='completed'))
            self._continuous_cursor(cid,fixIndex=index+1)
            value=self._continuous_record(cid)
        self._continuous_cursor(cid,fixPhase='delivery')
        self._continuous_delivery(cid)
        return self._continuous_record(cid)
