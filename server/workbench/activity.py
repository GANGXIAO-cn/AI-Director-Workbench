"""Read local execution receipts without exposing prompts, provider payloads or credentials."""
from .errors import Rejected
from .executors.cli import classify

STATUS = {
    "queued":"排队中", "running":"运行中", "succeeded":"已完成", "answered":"已回答",
    "failed":"失败", "waiting_quota":"等待额度", "stopped":"已停止", "cancelled":"已取消",
    "pending":"待发送", "submitted":"已发送，等待回执", "unknown":"发送结果待核对",
    "confirmed_not_executed":"已核实未执行", "awaiting_authorization":"待授权",
}
REASONS = {
    "model_not_found":"当前账号或通道无法使用所选模型，请切换到可用模型",
    "quota":"通道额度不足或达到频率限制", "authentication":"通道认证失败，请核对登录状态或凭证",
    "unavailable":"本机找不到执行器", "schema":"返回内容不符合所需格式", "timeout":"等待通道回执超时",
    "network":"与通道的连接中断", "executor_error":"执行器运行出错", "disconnect":"与通道的连接中断",
    "interrupted":"服务中断，未能记录完整回执", "insufficient_quota":"通道额度不足",
}
LOCAL_REASONS = ("尺寸不符", "冻结的输入图片不可用", "输入清单指纹不符", "回包两次不符合契约",
                 "回包无法入库", "回包不属于本次", "执行器没有返回有效的对话正文", "服务重启时中断")
IMAGE_KINDS = {"edit":"图像改图", "generate":"图像生成"}
STAGE_NAMES = {"story":"故事", "script":"剧本", "plan":"制作方案", "prompts":"提示词", "publish":"发布包"}
PURPOSE = {"create":"起草","revise":"改稿","review":"独审","cold_read":"独立冷读","comparison":"对照审查","parallel":"并排起草","proposal":"设定提议"}

RECEIPTS = """
WITH turn_times AS (
  SELECT json_extract(result,'$.attemptId') attempt, max(created_at) stamp
  FROM command WHERE kind IN ('send_free_chat','retry_free_chat','start_topic_research') GROUP BY attempt
), receipts AS (
  SELECT 'stage' kind, r.purpose action, t.stage stage, r.id id, t.project_id project, t.episode_id episode,
         NULL batch, r.status state, coalesce(r.ended_at,r.started_at,r.created_at) stamp,
         r.reason reason, r.diagnostic diagnostic
  FROM run r JOIN task t ON t.id=r.task_id
  UNION ALL
  SELECT 'image', b.operation, NULL, i.id, e.project_id, b.episode_id, b.id,
         CASE WHEN i.state='pending' AND b.state IN ('awaiting_authorization','cancelled') THEN b.state ELSE i.state END,
         coalesce(i.ended_at,i.submitted_at,b.created_at),NULL,i.error
  FROM image_request i JOIN image_batch b ON b.id=i.batch_id JOIN episode e ON e.id=b.episode_id
  UNION ALL
  SELECT 'chat', CASE WHEN tr.thread_id IS NOT NULL THEN 'research' ELSE 'chat' END, s.stage, t.id,
         f.project_id,s.episode_id,NULL,t.status,c.stamp,NULL,t.reason
  FROM free_turn t JOIN free_thread f ON f.id=t.thread_id LEFT JOIN episode_stage_thread s ON s.thread_id=f.id
  LEFT JOIN topic_research tr ON tr.thread_id=f.id LEFT JOIN turn_times c ON c.attempt=t.attempt
  UNION ALL
  SELECT 'reference','cut',NULL,q.id,p.project_id,p.episode_id,NULL,q.state,q.created_at,NULL,q.diagnostic
  FROM ref_question q JOIN ref_pack p ON p.id=q.pack_id
)
SELECT * FROM receipts WHERE (? IS NULL OR project=?) ORDER BY stamp DESC, id DESC LIMIT ? OFFSET ?
"""


def receipt_reason(record):
    state = record["state"]
    if state == "confirmed_not_executed":
        return "已核实通道未执行该请求"
    if state in ("answered","succeeded"):
        return "已收到有效回包"
    if state == "waiting_quota":
        return REASONS["quota"]
    # Free-form diagnostics can echo headers, command lines, user text and paths. Only
    # disclose a recognised cause, never the underlying diagnostic, even for old receipts.
    for raw in (record["reason"],record["diagnostic"]):
        if not raw:
            continue
        if raw in REASONS:
            return REASONS[raw]
        for prefix in LOCAL_REASONS:
            if raw.startswith(prefix):
                return prefix
        category = classify(raw)
        if category in REASONS:
            return REASONS[category]
    return {"unknown":"尚无可确认的执行结果","failed":"执行失败，回执未注明可识别的原因",
            "stopped":"执行已停止","cancelled":"批次已取消","pending":"请求尚未发送",
            "submitted":"正在等待通道回执","awaiting_authorization":"等待批准生成",
            "queued":"已进入执行队列","running":"执行器正在处理"}.get(state,"请查看对应任务的回执")


def page(db,project_id=None,offset=0,limit=40):
    if type(offset) is not int or not 0 <= offset <= 2**53-1 or type(limit) is not int or not 1 <= limit <= 100:
        raise Rejected("日志分页参数无效")
    records = list(db.execute(RECEIPTS,(project_id,project_id,limit+1,offset)))
    items = []
    for r in records[:limit]:
        kind,stage = r["kind"],STAGE_NAMES.get(r["stage"],"")
        if kind == "stage":
            label = stage + PURPOSE.get(r["action"],"任务")
        elif kind == "image":
            label = IMAGE_KINDS.get(r["action"],"图像请求")
        elif kind == "reference":
            label = "参考片切点提问"
        else:
            label = "选题研究" if r["action"]=="research" else stage+"对话" if stage else "自由对话"
        items.append(dict(id=r["id"],project=r["project"],episode=r["episode"],batch=r["batch"],
                          time=r["stamp"],label=label,status=STATUS.get(r["state"],"状态待核对"),
                          reason=receipt_reason(r),hasPrivateDetail=bool(r["diagnostic"]),
                          needsReconcile=kind=="image" and r["state"] in ("unknown","submitted")))
    return dict(items=items,hasMore=len(records)>limit)
