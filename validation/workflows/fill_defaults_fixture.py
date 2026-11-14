from test_kickoff_checks import report

def prepare(env):
    pid,eid=env.new_episode(name='默认文字验收')
    env.adopt_story(eid);env.script(eid)
    env.save_adopt(eid,'asset_design',dict(strategy='lightweight',assets=[dict(id='bell',title='门铃',kind='prop',description='铜制门铃',imagePrompt='铜制门铃',dependsOn=[])]),'worksheet')
    env.image(pid,eid,'门铃参考','prop')
    env.wb.propose_setting_change(pid,'dialogue','全片无对白','required','默片','rule')
    env.codex.script=[report];env.claude.script=[report]
