import json,redis
from datetime import datetime,timezone
from fastapi import HTTPException
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow
from src.utils.serialization import record
from src.services.migration import digest,key_digest,validate_backup
from src.services.handover import credential_for,rebuild_credential,build_package,normalize_entrustment
queue=redis.Redis.from_url(REDIS_URL)
def with_credential(item):
 if item['status']=='completed' and isinstance(item.get('result'),dict) and 'credential' not in item['result']:
  try:
   # 复制后再补凭据，避免改动 ORM 跟踪的 JSON 值。
   item['result']=dict(item['result']);item['result']['credential']=rebuild_credential(item)
  except ValueError:pass
 return item
def list_items():
 with Session() as s:return [with_credential(record(r)) for r in s.query(Run).order_by(Run.id.desc()).limit(100).all()]
def detail(item_id):
 with Session() as s:
  r=s.get(Run,item_id)
  if not r:raise HTTPException(404,'未找到对账运行')
  item=record(r)
 # 完成记录对外始终带固定凭据；旧记录按当时 result/snapshot 重建，不改写数据库。
 return with_credential(item)
def create(data):
 with Session() as s:
  m=s.get(Mapping,data.mapping_id)
  if not m:raise HTTPException(404,'未找到字段映射')
  b=s.get(Batch,m.batch_id)
  r=Run(name=data.name,batch_id=b.id,mapping_id=m.id,status='queued',snapshot={'batch':record(b),'mapping':record(m)})
  s.add(r);s.commit();s.refresh(r);result=record(r)
 try:queue.rpush('migration-runs',result['id'])
 except redis.RedisError:
  with Session() as s:r=s.get(Run,result['id']);r.status='failed';r.error='队列不可用，请创建新任务';s.commit()
  raise HTTPException(503,'队列不可用，请稍后重试')
 return result
def cancel(item_id):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status not in ('queued','running'):raise HTTPException(409,'运行已终止，无法取消')
  r.cancel_requested=True
  if r.status=='queued':r.status='cancelled'
  s.commit();return record(r)
def targets(item_id):
 detail(item_id)
 with Session() as s:rows=[r.payload for r in s.query(TargetRow).filter_by(run_id=item_id).order_by(TargetRow.ordinal)]
 return {'rows':rows,'count':len(rows),'checksum':digest(rows)}
def backup(item_id):
 r=detail(item_id)
 if r['status']!='completed':raise HTTPException(409,'仅完成运行可以备份')
 return r['result']['backup']
def credential(item_id):
 r=detail(item_id)
 if r['status']!='completed':raise HTTPException(409,'仅完成运行可以固定核验凭据')
 try:return credential_for(r)
 except ValueError as e:raise HTTPException(409,str(e))
def entrustment_package(item_id,entrustment):
 r=detail(item_id)
 if r['status']!='completed':raise HTTPException(409,'仅完成运行可以按委托下载交接包')
 code=normalize_entrustment(entrustment)
 if code is None:raise HTTPException(422,'请提供有效的委托号')
 try:
  # 只读完成时的 accepted_rows 与固定凭据，与当前 target_rows 状态无关。
  return build_package(credential_for(r),r['result']['accepted_rows'],code)
 except ValueError as e:raise HTTPException(409,str(e))
def restore(item_id,data):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status!='completed':raise HTTPException(409,'仅完成运行可以恢复')
  try:rows=validate_backup(data.backup,r.result['checksum'],item_id)
  except ValueError as e:raise HTTPException(422,str(e))
  # 恢复只替换目标行；完成时的 result（含固定凭据与交接包依据）保持冻结不变。
  frozen=json.dumps(r.result,sort_keys=True,ensure_ascii=False,default=str)
  before=s.query(TargetRow).filter_by(run_id=item_id).count()
  s.query(TargetRow).filter_by(run_id=item_id).delete()
  for i,row in enumerate(rows):s.add(TargetRow(run_id=item_id,ordinal=i,key_digest=key_digest(row),payload=row))
  s.flush()
  actual=[x.payload for x in s.query(TargetRow).filter_by(run_id=item_id).order_by(TargetRow.ordinal)]
  proof={'restored_at':datetime.now(timezone.utc).isoformat(),'previous_count':before,'restored_count':len(actual),'checksum':digest(actual),'verified':digest(actual)==r.result['checksum'],'credential_digest':(r.result.get('credential') or {}).get('credential_digest')}
  if not proof['verified']:raise HTTPException(409,'恢复后校验失败，已回滚')
  if json.dumps(r.result,sort_keys=True,ensure_ascii=False,default=str)!=frozen:raise HTTPException(409,'完成记录被改动，已回滚')
  r.recovery=(r.recovery or [])+[proof];s.commit();return proof
