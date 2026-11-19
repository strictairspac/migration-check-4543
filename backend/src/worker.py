import time,logging,redis
from datetime import datetime,timezone
from pathlib import Path
from src.config.settings import REDIS_URL
from src.config.database import Session
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow
from src.services.migration import migrate,make_backup,key_digest,Cancelled
from src.services.handover import build_credential
from src.services.audit import log
q=redis.Redis.from_url(REDIS_URL)
def progress(run_id,info):
 Path('/tmp/worker-heartbeat').touch()
 with Session() as s:
  r=s.get(Run,run_id)
  if r.cancel_requested or r.status=='cancelled':raise Cancelled()
  r.progress=info;s.commit()
def execute(run_id,snapshot):
 """执行一次已出队的迁移；完成时在同一事务里固定凭据并提交目标行。"""
 result=migrate(snapshot['batch']['rows'],snapshot['mapping']['rules'],snapshot['mapping']['chunk_size'],lambda info:progress(run_id,info))
 completed_at=datetime.now(timezone.utc).isoformat()
 result['completed_at']=completed_at
 result['backup']=make_backup(result,run_id,snapshot['batch']['name'])
 # 迁移完成时固定凭据：写入 result 后再提交目标行，下载永远读这份冻结记录。
 result['credential']=build_credential(result['accepted_rows'],snapshot['batch']['rows'],snapshot['mapping']['rules'],run_id,snapshot['batch']['name'],completed_at,result['checksum'],result['accepted_count'],result['rejected_count'])
 with Session() as s:
  r=s.query(Run).filter_by(id=run_id).with_for_update().first()
  if r.cancel_requested:raise Cancelled()
  for i,row in enumerate(result['accepted_rows']):s.add(TargetRow(run_id=run_id,ordinal=i,key_digest=key_digest(row),payload=row))
  r.result=result;r.status='completed';s.commit()
 return result
def main():
 while True:
  Path('/tmp/worker-heartbeat').touch()
  try:
   item=q.blpop('migration-runs',timeout=2)
   if not item:continue
   run_id=int(item[1])
   with Session() as s:
    r=s.query(Run).filter_by(id=run_id).with_for_update().first()
    if not r or r.status!='queued':continue
    r.status='running';snapshot=r.snapshot;s.commit()
   try:
    execute(run_id,snapshot)
    log('worker','run.completed',str(run_id))
   except Cancelled:
    with Session() as s:r=s.get(Run,run_id);r.status='cancelled';r.error='用户取消，未提交目标行';s.commit()
    log('worker','run.cancelled',str(run_id))
   except Exception as exc:
    logging.exception('migration failed')
    with Session() as s:r=s.get(Run,run_id);r.status='failed';r.error=str(exc)[:1000];s.commit()
  except Exception:logging.exception('worker loop');time.sleep(2)
if __name__=='__main__':main()
