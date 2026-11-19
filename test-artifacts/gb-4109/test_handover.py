"""纯逻辑核验测试：不依赖数据库/Redis/FastAPI。"""
import copy,json,subprocess,sys,tempfile,os
sys.path.insert(0,'/workspace/backend')
from src.services.migration import migrate
from src.services.handover import build_credential,build_package,credential_for,digest,verify_credential

RULES=[{'source':a,'target':b,'type':c,'required':True} for a,b,c in [('委托号','sample_code','text'),('检测项','test_name','text'),('结果','result_value','decimal'),('单位','unit','text'),('时间','measured_at','datetime')]]
GOOD=[{'委托号':f'JS-20261117-{i:03d}','检测项':'拉伸强度','结果':str(420+i/10),'单位':'MPa','时间':'2026-11-17T09:00:00+08:00'} for i in range(1,13)]
BAD=GOOD[:2]+[GOOD[0],{'委托号':'JS-BAD-01','检测项':'拉伸强度','结果':'四百二十','单位':'MPa','时间':'2026-11-17T09:00:00+08:00'},{'委托号':'JS-BAD-02','检测项':'拉伸强度','结果':None,'单位':'MPa','时间':'2026-11-17T09:00:00+08:00'},{'委托号':'JS-BAD-03','检测项':'拉伸强度','结果':'430','时间':'2026-11-17T09:00:00+08:00'}]
VERIFIER='/workspace/backend/scripts/verify_handover.py'
checks=[]

def ok(name,cond):
 checks.append((name,bool(cond)));print(('PASS' if cond else 'FAIL'),name)

def run(rows,run_id=7,name='批'):
 r=migrate(rows,RULES,4)
 cred=build_credential(r['accepted_rows'],rows,RULES,run_id,name,'2026-11-19T10:00:00+00:00',r['checksum'],r['accepted_count'],r['rejected_count'])
 return r,cred

def offline(cred,pkg,code):
 with tempfile.TemporaryDirectory() as d:
  cp=os.path.join(d,'c.json');pp=os.path.join(d,'p.json')
  json.dump(cred,open(cp,'w'),ensure_ascii=False);json.dump(pkg,open(pp,'w'),ensure_ascii=False)
  p=subprocess.run([sys.executable,'-I',VERIFIER,'--credential',cp,'--entrustment',code,'--package',pp],capture_output=True,text=True)
  return p.returncode,p.stdout,p.stderr

r,cred=run(GOOD)
ok('凭据自身可校验',verify_credential(copy.deepcopy(cred))['run_id']==7)
ok('名单覆盖12个委托',len(cred['entrustments'])==12 and cred['scope']['entrustment_census_complete'] is True)
ok('凭据确定性（重复生成字节一致）',json.dumps(build_credential(r['accepted_rows'],GOOD,RULES,7,'批','2026-11-19T10:00:00+00:00',r['checksum'],r['accepted_count'],r['rejected_count']),ensure_ascii=False,sort_keys=True)==json.dumps(cred,ensure_ascii=False,sort_keys=True))
pkg=build_package(cred,r['accepted_rows'],'JS-20261117-001')
ok('交接包只含该委托1行',len(pkg['rows'])==1 and pkg['rows'][0]['sample_code']=='JS-20261117-001')
rc,out,err=offline(cred,pkg,'JS-20261117-001');ok('离线验包通过',rc==0 and 'verified' in out)
ok('包确定性',json.dumps(build_package(cred,r['accepted_rows'],'JS-20261117-001'),sort_keys=True)==json.dumps(pkg,sort_keys=True))

# 少给：移除行并重算包内全部摘要，仍应对照凭据失败
def repack(p,rows):
 p=copy.deepcopy(p);p['rows']=rows;p['count']=len(rows);p['row_hashes']=[digest(x) for x in rows];p['checksum']=digest(rows);return p
rc,_,err=offline(cred,repack(pkg,[]),'JS-20261117-001');ok('少给被拒',rc==4)
# 重复：同一行两次（自然键重复）
rc,_,err=offline(cred,repack(pkg,pkg['rows']*2),'JS-20261117-001');ok('重复被拒',rc==4)
# 改值：修改结果并重算包摘要
alt=copy.deepcopy(pkg['rows'][0]);alt['result_value']='999.000000'
rc,_,err=offline(cred,repack(pkg,[alt]),'JS-20261117-001');ok('改值被拒',rc==4)
# 混入其他委托
other=build_package(cred,r['accepted_rows'],'JS-20261117-002')['rows'][0]
rc,_,err=offline(cred,repack(pkg,pkg['rows']+[other]),'JS-20261117-001');ok('混入他委托被拒',rc==4)
# 包声称别的委托
bad=copy.deepcopy(pkg);bad['entrustment']='JS-20261117-002'
rc,_,err=offline(cred,bad,'JS-20261117-001');ok('包委托号不符被拒',rc==4)
# 凭据被改
tam=copy.deepcopy(cred);tam['entrustments'][0]['count']=9
rc,_,err=offline(tam,pkg,'JS-20261117-001');ok('凭据改动被拒',rc==3)
# 不存在的委托：完整名单 -> 否定证明
empty=build_package(cred,r['accepted_rows'],'JS-20261117-099')
rc,out,err=offline(cred,empty,'JS-20261117-099');ok('本来没有结果可证明',rc==0 and 'verified-empty' in out)
# 不存在的委托但包声称有行
rc,_,err=offline(cred,repack(empty,pkg['rows']),'JS-20261117-099');ok('空委托伪造行被拒',rc==4)
# 名单不完整的凭据不能做否定证明：生成阶段拒绝，且即使伪造包，离线也拒绝
partial=copy.deepcopy(cred);partial['scope']['entrustment_census_complete']=False
body={k:v for k,v in partial.items() if k!='credential_digest'};partial['credential_digest']=digest(body)
try:build_package(partial,r['accepted_rows'],'JS-20261117-099');ok('名单不完整否定证明被拒',False)
except ValueError:ok('名单不完整否定证明被拒（生成拒绝）',True)
forged=copy.deepcopy(empty);forged['credential_digest']=partial['credential_digest']
rc,out,err=offline(partial,forged,'JS-20261117-099');ok('名单不完整否定证明被拒（离线拒绝）',rc==4)

# 异常样本：重复自然键进入拒绝区，凭据仍完整
rb,credb=run(BAD,8,'异常')
ok('异常样本拒绝重复/非法/空值/缺列',rb['rejected_count']==4 and rb['accepted_count']==2)
ok('拒绝行不影响凭据名单完整',credb['scope']['entrustment_census_complete'] is True and {e['entrustment'] for e in credb['entrustments']}>= {'JS-20261117-001','JS-20261117-002','JS-BAD-01','JS-BAD-02','JS-BAD-03'})
rc,out,err=offline(credb,build_package(credb,rb['accepted_rows'],'JS-BAD-01'),'JS-BAD-01');ok('被拒委托在名单中为零行承诺',rc==0 and '"result": "verified-empty"' in out and '"rows": 0' in out)

# 空批次
re,crede=run([],9,'空')
ok('空批次凭据可校验且无委托',verify_credential(crede) and crede['entrustments']==[] and crede['scope']['entrustment_census_complete'] is True)
rc,out,err=offline(crede,build_package(crede,[],'JS-NONE'),'JS-NONE');ok('空批次任意委托均为空证明',rc==0 and 'verified-empty' in out)

# 源批次含无法归属委托号的行：名单标记不完整，不能做否定证明
mixed=[GOOD[0],{'检测项':'拉伸强度','结果':'421','单位':'MPa','时间':'2026-11-17T09:00:00+08:00'}]
rm,credm=run(mixed,10,'混合')
ok('缺委托号源行使名单不完整',credm['scope']['entrustment_census_complete'] is False)
rc,out,err=offline(credm,build_package(credm,rm['accepted_rows'],'JS-20261117-001'),'JS-20261117-001');ok('不完整名单仍可核验已列委托',rc==0 and '"verified"' in out)
try:build_package(credm,rm['accepted_rows'],'JS-20261117-777');ok('不完整名单拒绝未列委托',False)
except ValueError:ok('不完整名单拒绝未列委托',True)

# 完成记录不可被当前目标表/备份恢复影响：credential_for 读取 result.credential
run_rec={'id':7,'status':'completed','created_at':'2026-11-19T09:00:00','snapshot':{'batch':{'name':'批','rows':GOOD},'mapping':{'rules':RULES}},'result':{**r,'completed_at':'2026-11-19T10:00:00+00:00','credential':cred}}
ok('完成记录冻结凭据与生成时一致',credential_for(run_rec)==cred)
legacy=copy.deepcopy(run_rec);del legacy['result']['credential']
ok('旧完成记录按当时记录重建凭据',credential_for(legacy)==cred)
# 重试下载不改凭据：同一运行再次读取完全一致
ok('重复读取凭据字节一致',json.dumps(credential_for(run_rec),sort_keys=True)==json.dumps(credential_for(run_rec),sort_keys=True))

failed=[n for n,c in checks if not c]
print('\n%d/%d passed'%(len(checks)-len(failed),len(checks)))
sys.exit(1 if failed else 0)
