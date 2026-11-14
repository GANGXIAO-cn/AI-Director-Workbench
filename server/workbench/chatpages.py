"""Stable cursor pages for the episode's visible task conversation."""
from .db import connect
from .errors import Rejected


INTERNAL_MESSAGE="json_extract(CASE WHEN json_valid(im.payload) THEN im.payload ELSE '{}' END,'$.internalJudgment') IS NOT NULL"
VISIBLE_TASK="(NOT EXISTS(SELECT 1 FROM run r WHERE r.task_id=t.id) OR EXISTS(SELECT 1 FROM run r WHERE r.task_id=t.id AND r.purpose!='image_assess')) AND NOT EXISTS(SELECT 1 FROM thread ih JOIN message im ON im.thread_id=ih.id WHERE ih.task_id=t.id AND "+INTERNAL_MESSAGE+")"


class ChatPages:
    def episode_messages(self,episode_id,before=None,limit=30,stage=None,user_only=False):
        with connect(self.db_path) as db:
            db.execute('BEGIN')
            self._episode_in(db,episode_id)
            return self._episode_messages(db,episode_id,before,limit,stage,user_only)

    def _episode_messages(self,db,eid,before=None,limit=30,stage=None,user_only=False):
        if type(limit) is not int or not 1<=limit<=100:raise Rejected('每次加载 1–100 条消息')
        where=["t.episode_id=?", VISIBLE_TASK]
        args=[eid]
        if stage:
            where.append("CASE WHEN t.goal='两遍独审' THEN 'review' ELSE t.stage END=?");args.append(stage)
        if user_only:where.extend(["m.role='user'","m.kind='text'"])
        if before:
            anchor=db.execute('SELECT m.created_at,t.created_at,t.id,m.seq,m.id FROM message m JOIN thread h ON h.id=m.thread_id JOIN task t ON t.id=h.task_id WHERE m.id=? AND t.episode_id=?',(before,eid)).fetchone()
            if not anchor:raise Rejected('历史消息游标不属于本集')
            where.append('(m.created_at,t.created_at,t.id,m.seq,m.id)<(?,?,?,?,?)');args.extend(tuple(anchor))
        rows=db.execute("""SELECT m.*,t.id AS taskId,d.code AS decisionCode FROM message m
            JOIN thread h ON h.id=m.thread_id JOIN task t ON t.id=h.task_id
            LEFT JOIN director_decision d ON d.episode_id=t.episode_id AND d.origin_ref='message:'||m.id
            WHERE """+' AND '.join(where)+" ORDER BY m.created_at DESC,t.created_at DESC,t.id DESC,m.seq DESC,m.id DESC LIMIT ?",[*args,limit+1]).fetchall()
        more=len(rows)>limit;messages=[dict(r) for r in reversed(rows[:limit])]
        return dict(messages=messages,hasMore=more,before=messages[0]['id'] if messages else None)
