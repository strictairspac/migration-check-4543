"""按委托号固定核验凭据与交接包。

设计要点：
- 凭据在迁移完成时一次性生成并随 result 持久化，之后任何下载、备份恢复都不会改动它。
- 凭据是“源批次委托号名单 + 每个委托号可交接行的承诺（行数/总体摘要/逐行摘要）”，
  它是外部凭据持有者离线验包的唯一依据，不依赖数据库或网络。
- 交接包只包含委托号完全相等的已接受目标行；包内不含其他委托的结果。
- 凭据与包均为确定性 JSON：同一完成记录重复生成字节一致，下载中断后重试不会换凭据。
"""
import hashlib,re
import json
from src.constants.enums import TYPES

# 与 migration.digest 保持同一规范；外站离线校验脚本必须复现同一算法。
CANONICAL='sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode("utf-8"))'
CREDENTIAL_KIND='handover-credential'
PACKAGE_KIND='handover-package'


def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def key_digest(row):
 return digest([row['sample_code'],row['test_name']])


def normalize_entrustment(value):
 """把源/目标委托号统一成可比较的字符串；无法归属时返回 None。"""
 if value is None or isinstance(value,bool):return None
 if isinstance(value,str):v=value.strip()
 elif isinstance(value,(int,float)):v=str(value).strip()
 else:return None
 if not v or len(v)>160:return None
 return v


def canonical_rows(rows):
 return sorted(rows,key=lambda r:(str(r.get('sample_code') or ''),str(r.get('test_name') or '')))


def source_entrustments(source_rows,rules):
 """列出源批次能归属的全部委托号；complete 表示每一行都能归属到某个委托号。"""
 code_rule=next((r for r in rules if isinstance(r,dict) and r.get('target')=='sample_code'),None)
 codes=set();complete=True
 for row in source_rows:
  if not isinstance(row,dict) or not code_rule or code_rule.get('source') not in row:
   complete=False;continue
  code=normalize_entrustment(row[code_rule['source']])
  if code is None:complete=False;continue
  codes.add(code)
 return codes,complete


def _credential_body(accepted_rows,source_rows,rules,run_id,batch_name,completed_at,result_checksum,accepted_count,rejected_count):
 accepted=canonical_rows(accepted_rows)
 keys=[key_digest(r) for r in accepted]
 if len(set(keys))!=len(keys):raise ValueError('完成记录的自然键重复，无法固定凭据')
 groups={}
 for row in accepted:
  code=normalize_entrustment(row.get('sample_code'))
  if code is None:raise ValueError('完成记录缺少有效委托号，无法固定凭据')
  groups.setdefault(code,[]).append(row)
 source_codes,complete=source_entrustments(source_rows,rules)
 entries=[]
 for code in sorted(set(groups)|source_codes):
  rows=groups.get(code,[])
  entries.append({'entrustment':code,'count':len(rows),'checksum':digest(rows),'row_hashes':[digest(r) for r in rows]})
 return {
  'version':1,
  'kind':CREDENTIAL_KIND,
  'run_id':run_id,
  'batch_name':batch_name,
  'completed_at':completed_at,
  'algorithm':'sha256',
  'canonical':CANONICAL,
  'scope':{
   'source_row_count':len(source_rows),
   'accepted_count':accepted_count,
   'rejected_count':rejected_count,
   'entrustment_census_complete':complete,
  },
  'run_checksum':result_checksum,
  'entrustments':entries,
 }


def build_credential(accepted_rows,source_rows,rules,run_id,batch_name,completed_at,result_checksum,accepted_count=None,rejected_count=0):
 body=_credential_body(accepted_rows,source_rows,rules,run_id,batch_name,completed_at,result_checksum,len(accepted_rows) if accepted_count is None else accepted_count,rejected_count)
 body['credential_digest']=digest(body)
 return body


def verify_credential(credential):
 """校验凭据自身结构/摘要；损坏时抛 ValueError。"""
 if not isinstance(credential,dict) or credential.get('version')!=1 or credential.get('kind')!=CREDENTIAL_KIND:raise ValueError('凭据版本或类型不一致')
 if not isinstance(credential.get('run_id'),int):raise ValueError('凭据缺少运行编号')
 if credential.get('algorithm')!='sha256' or credential.get('canonical')!=CANONICAL:raise ValueError('凭据算法不受支持')
 scope=credential.get('scope')
 if not isinstance(scope,dict) or not isinstance(scope.get('entrustment_census_complete'),bool):raise ValueError('凭据缺少委托名单完整性声明')
 entries=credential.get('entrustments')
 if not isinstance(entries,list):raise ValueError('凭据委托名单无效')
 seen=set()
 for entry in entries:
  if not isinstance(entry,dict) or set(entry)!={'entrustment','count','checksum','row_hashes'}:raise ValueError('凭据委托条目结构无效')
  code=normalize_entrustment(entry['entrustment'])
  if code is None or code!=entry['entrustment'] or code in seen:raise ValueError('凭据委托号无效或重复')
  if not isinstance(entry['count'],int) or entry['count']<0:raise ValueError('凭据行数无效')
  if not isinstance(entry['checksum'],str) or not isinstance(entry['row_hashes'],list) or len(entry['row_hashes'])!=entry['count']:raise ValueError('凭据摘要无效')
  seen.add(code)
 body={k:v for k,v in credential.items() if k!='credential_digest'}
 if credential.get('credential_digest')!=digest(body):raise ValueError('凭据摘要不匹配')
 return credential


def build_package(credential,accepted_rows,entrustment):
 """按委托号从完成记录生成交接包；完成记录必须与固定凭据完全一致。"""
 verify_credential(credential)
 code=normalize_entrustment(entrustment)
 if code is None:raise ValueError('委托号为空或超过160字符')
 entries={e['entrustment']:e for e in credential['entrustments']}
 listed=code in entries
 rows=canonical_rows([r for r in accepted_rows if isinstance(r,dict) and normalize_entrustment(r.get('sample_code'))==code])
 if listed:
  entry=entries[code]
  actual=[digest(r) for r in rows]
  if len(rows)!=entry['count'] or digest(rows)!=entry['checksum'] or actual!=entry['row_hashes']:
   raise ValueError('完成记录与固定凭据不一致，拒绝生成交接包')
  expected={'listed':True,'count':entry['count'],'checksum':entry['checksum'],'row_hashes':list(entry['row_hashes'])}
 else:
  if not credential['scope']['entrustment_census_complete']:raise ValueError('凭据不是完整委托名单，无法证明该委托号本来没有结果')
  if rows:raise ValueError('凭据未列出该委托号，但完成记录中存在其行')
  expected={'listed':False,'count':0,'checksum':digest([]),'row_hashes':[]}
 return {
  'version':1,
  'kind':PACKAGE_KIND,
  'run_id':credential['run_id'],
  'entrustment':code,
  'record_basis':'migration-completion',
  'credential_digest':credential['credential_digest'],
  'expected':expected,
  'count':len(rows),
  'rows':rows,
  'row_hashes':[digest(r) for r in rows],
  'checksum':digest(rows),
 }


def credential_for(run_record):
 """取运行完成时冻结的凭据；旧完成记录按当时 result/snapshot 确定性重建（不写库）。"""
 result=run_record.get('result') or {}
 stored=result.get('credential')
 if stored is not None:
  verify_credential(stored)
  if stored['run_id']!=run_record.get('id'):raise ValueError('固定凭据不属于本运行')
  if stored.get('run_checksum')!=result.get('checksum'):raise ValueError('固定凭据与完成摘要不一致')
  return stored
 return rebuild_credential(run_record)


def rebuild_credential(run_record):
 """仅用于迁移前已完成的旧记录：按当时的 result 与输入快照确定性重建，不写入数据库。"""
 result=run_record.get('result') or {}
 snapshot=run_record.get('snapshot') or {}
 batch=snapshot.get('batch') or {}
 mapping=snapshot.get('mapping') or {}
 accepted=result.get('accepted_rows')
 if run_record.get('status')!='completed' or not isinstance(accepted,list) or not isinstance(batch.get('rows'),list) or not isinstance(mapping.get('rules'),list):raise ValueError('运行没有可用的完成记录')
 completed_at=result.get('completed_at') or run_record.get('created_at') or ''
 return build_credential(
  accepted_rows=accepted,
  source_rows=batch['rows'],
  rules=mapping['rules'],
  run_id=run_record['id'],
  batch_name=batch.get('name',''),
  completed_at=completed_at,
  result_checksum=result.get('checksum',''),
  accepted_count=result.get('accepted_count',len(accepted)),
  rejected_count=result.get('rejected_count',0),
 )


def credential_filename(run_id):
 return 'credential-'+str(run_id)+'.json'


def package_filename(run_id,entrustment):
 slug=re.sub(r'[^A-Za-z0-9._-]+','_',str(entrustment or '')).strip('._-')
 if not slug:slug='entrustment'
 return 'handover-'+str(run_id)+'-'+slug[:60]+'.json'
