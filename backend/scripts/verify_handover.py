#!/usr/bin/env python3
"""外站离线交接包核验器（仅标准库，不连接站点/数据库）。

用法：
  python3 verify_handover.py --credential credential-12.json --entrustment JS-20261117-001 \
      --package handover-12-JS-20261117-001.json

退出码：0 通过；2 参数/文件错误；3 凭据自身损坏；4 包被拒绝（少给/重复/改值/混入/错委托）。

判定“本次委托本来就没有结果”需要凭据声明 scope.entrustment_census_complete=true，
且该委托号不在凭据名单中，同时交接包声明 expected.listed=false 且为 0 行。
"""
import argparse,hashlib,json,sys

CANONICAL='sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode("utf-8"))'
CREDENTIAL_KIND='handover-credential'
PACKAGE_KIND='handover-package'
TYPES={'sample_code':'text','test_name':'text','result_value':'decimal','unit':'text','measured_at':'datetime'}


def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def key_digest(row):
 return digest([row['sample_code'],row['test_name']])


def fail(code,message):
 print('REJECTED: '+message,file=sys.stderr);sys.exit(code)


def load(path,label):
 try:
  with open(path,encoding='utf-8') as f:data=json.load(f)
 except (OSError,ValueError) as e:fail(2,f'{label}无法读取或不是JSON：{e}')
 if not isinstance(data,dict):fail(2,label+'必须是JSON对象')
 return data


def check_credential(credential):
 if credential.get('version')!=1 or credential.get('kind')!=CREDENTIAL_KIND:fail(3,'凭据版本或类型不一致')
 if not isinstance(credential.get('run_id'),int):fail(3,'凭据缺少运行编号')
 if credential.get('algorithm')!='sha256' or credential.get('canonical')!=CANONICAL:fail(3,'凭据算法/规范不受支持')
 scope=credential.get('scope')
 if not isinstance(scope,dict) or not isinstance(scope.get('entrustment_census_complete'),bool):fail(3,'凭据缺少委托名单完整性声明')
 entries=credential.get('entrustments')
 if not isinstance(entries,list):fail(3,'凭据委托名单无效')
 seen={}
 for entry in entries:
  if not isinstance(entry,dict) or set(entry)!={'entrustment','count','checksum','row_hashes'}:fail(3,'凭据委托条目结构无效')
  code=entry['entrustment']
  if not isinstance(code,str) or not code.strip() or code!=code.strip() or len(code)>160:fail(3,'凭据委托号无效')
  if code in seen:fail(3,'凭据委托号重复：'+code)
  if not isinstance(entry['count'],int) or entry['count']<0:fail(3,'凭据行数无效')
  if not isinstance(entry['checksum'],str) or not isinstance(entry['row_hashes'],list) or len(entry['row_hashes'])!=entry['count']:fail(3,'凭据摘要无效：'+code)
  seen[code]=entry
 body={k:v for k,v in credential.items() if k!='credential_digest'}
 if credential.get('credential_digest')!=digest(body):fail(3,'凭据摘要不匹配（凭据被改动或残缺）')
 return seen


def check_package(package,credential,entries,entrustment):
 if package.get('version')!=1 or package.get('kind')!=PACKAGE_KIND:fail(4,'交接包版本或类型不一致')
 if package.get('record_basis')!='migration-completion':fail(4,'交接包不是迁移完成时的记录')
 if package.get('run_id')!=credential['run_id']:fail(4,'交接包运行编号与凭据不一致')
 if package.get('credential_digest')!=credential['credential_digest']:fail(4,'交接包不是由这份凭据生成的')
 if package.get('entrustment')!=entrustment:fail(4,'交接包委托号与请求不一致（混入其他委托）')
 rows=package.get('rows')
 if not isinstance(rows,list) or len(rows)>2000:fail(4,'交接包行数无效')
 # 每行必须是完整目标schema，且必须属于该委托。
 for row in rows:
  if not isinstance(row,dict) or set(row)!=set(TYPES):fail(4,'交接包目标schema不一致')
  code=row.get('sample_code')
  if not isinstance(code,str) or code.strip()!=entrustment:fail(4,'交接包混入其他委托的检验结果：'+repr(code))
 keys=[key_digest(r) for r in rows]
 if len(set(keys))!=len(keys):fail(4,'交接包自然键重复（重复给行）')
 actual_hashes=[digest(r) for r in rows]
 if package.get('count')!=len(rows):fail(4,'交接包声明行数与实际不符（少给或多给）')
 if package.get('row_hashes')!=actual_hashes:fail(4,'交接包逐行摘要不匹配（行被改动、缺失或替换）')
 if package.get('checksum')!=digest(rows):fail(4,'交接包总体摘要不匹配（少给/重复/改值）')
 expected=package.get('expected')
 if not isinstance(expected,dict) or set(expected)!={'listed','count','checksum','row_hashes'}:fail(4,'交接包缺少凭据承诺')
 entry=entries.get(entrustment)
 if expected.get('listed')!=(entry is not None):fail(4,'交接包承诺的委托名单归属与凭据不一致')
 if entry is not None:
  if expected['count']!=entry['count'] or expected['checksum']!=entry['checksum'] or expected['row_hashes']!=list(entry['row_hashes']):fail(4,'交接包承诺与凭据不一致')
  if len(rows)!=entry['count']:fail(4,'交接包行数与凭据承诺不符（少给或多给）')
  if digest(rows)!=entry['checksum'] or actual_hashes!=list(entry['row_hashes']):fail(4,'交接包内容与凭据承诺不符')
  if len(rows)==0:return {'result':'verified-empty','entrustment':entrustment,'run_id':credential['run_id'],'rows':0,'checksum':digest([]),'statement':'核验通过：迁移完成时的凭据列明该委托，且其可交接结果为0行。'}
  return {'result':'verified','entrustment':entrustment,'run_id':credential['run_id'],'rows':len(rows),'checksum':entry['checksum'],'statement':'核验通过：交接包与迁移完成时的凭据完全一致。'}
 # 委托不在凭据名单中：只有完整委托名单才能证明“本来没有结果”。
 if not credential['scope']['entrustment_census_complete']:fail(4,'凭据委托名单不完整，不能证明该委托本来没有结果')
 if expected.get('listed') is not False:fail(4,'凭据未列出该委托，交接包却声明已列出')
 if expected.get('count')!=0 or expected.get('row_hashes')!=[]:fail(4,'凭据未列出该委托，交接包却承诺了行')
 if rows:fail(4,'凭据未列出该委托，交接包却带有行（混入）')
 return {'result':'verified-empty','entrustment':entrustment,'run_id':credential['run_id'],'rows':0,'checksum':digest([]),'statement':'核验通过：迁移完成时的凭据证明该委托本来就没有检验结果。'}


def main():
 ap=argparse.ArgumentParser(description='离线核验见山交接包')
 ap.add_argument('--credential',required=True,help='先前留存的核验凭据JSON')
 ap.add_argument('--entrustment',required=True,help='本次抽查的委托号')
 ap.add_argument('--package',required=True,help='页面按委托下载的交接包JSON')
 args=ap.parse_args()
 entrustment=args.entrustment.strip()
 if not entrustment or len(entrustment)>160:fail(2,'委托号为空或超过160字符')
 credential=load(args.credential,'凭据')
 entries=check_credential(credential)
 package=load(args.package,'交接包')
 verdict=check_package(package,credential,entries,entrustment)
 print(json.dumps(verdict,ensure_ascii=False,indent=2))
 return 0


if __name__=='__main__':
 sys.exit(main())
