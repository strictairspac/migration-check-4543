"""集成测试：SQLite 临时库 + TestClient，真实走迁移完成、凭据、按委托交接包、备份恢复与审计。
运行：PYTHONPATH=/tmp/site:/workspace/backend python3 -I test-artifacts/gb-4109/test_api.py
"""
import os,sys,tempfile,json,subprocess
TMP=tempfile.mkdtemp(prefix='handover-')
os.environ['DATABASE_URL']='sqlite:///'+TMP+'/test.db'
os.environ['REDIS_URL']='redis://127.0.0.1:6399/0'
os.environ['JWT_SECRET']='test-secret'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,'/workspace/backend')

from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Base,engine,Session
from src.models.identity import User
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow
from src.utils.security import hash_password
from src.worker import execute
from src.services.migration import digest
from src.services import runs as runs_service

RULES=[{'source':a,'target':b,'type':c,'required':True} for a,b,c in [('委托号','sample_code','text'),('检测项','test_name','text'),('结果','result_value','decimal'),('单位','unit','text'),('时间','measured_at','datetime')]]
GOOD=[{'委托号':f'JS-20261117-{i:03d}','检测项':'拉伸强度','结果':str(420+i/10),'单位':'MPa','时间':'2026-11-17T09:00:00+08:00'} for i in range(1,13)]
BAD=GOOD[:2]+[GOOD[0],{'委托号':'JS-BAD-01','检测项':'拉伸强度','结果':'四百二十','单位':'MPa','时间':'2026-11-17T09:00:00+08:00'}]
checks=[]
def ok(n,c):checks.append((n,bool(c)));print(('PASS' if c else 'FAIL'),n)

Base.metadata.create_all(engine)
with Session() as s:
 for role in ('admin','operator','viewer'):
  s.add(User(email=role+'@t.local',role=role,password=hash_password('pw')))
 s.add(Batch(name='日班',source_file='a.json',description='',rows=GOOD))
 s.add(Batch(name='异常',source_file='b.json',description='',rows=BAD))
 s.commit()
 s.flush()
 s.add(Mapping(name='日班映射',batch_id=s.query(Batch).filter_by(name='日班').one().id,rules=RULES,chunk_size=4,note=''))
 s.add(Mapping(name='异常映射',batch_id=s.query(Batch).filter_by(name='异常').one().id,rules=RULES,chunk_size=4,note=''))
 s.commit()
 bid=s.query(Batch).filter_by(name='日班').one().id;mid=s.query(Mapping).filter_by(name='日班映射').one().id
 abid=s.query(Batch).filter_by(name='异常').one().id;amid=s.query(Mapping).filter_by(name='异常映射').one().id

client=TestClient(app)
def login(email):
 r=client.post('/auth/login',json={'email':email,'password':'pw'});assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}
admin=login('admin@t.local');viewer=login('viewer@t.local');operator=login('operator@t.local')

def start(payload,mapping_id,batch_name):
 with Session() as s:
  m=s.get(Mapping,mapping_id);b=s.get(Batch,m.batch_id)
  r=Run(name=payload,mapping_id=m.id,batch_id=b.id,status='queued',snapshot={'batch':{'id':b.id,'name':b.name,'rows':b.rows},'mapping':{'id':m.id,'chunk_size':m.chunk_size,'rules':m.rules}})
  s.add(r);s.commit();s.refresh(r);rid=r.id;snap=r.snapshot
 execute(rid,snap)
 return rid

rid=start('run-good',mid,'日班')
r=client.get('/runs/%d'%rid,headers=viewer);ok('完成详情带固定凭据',r.status_code==200 and r.json()['result'].get('credential',{}).get('run_id')==rid)
cred=r.json()['result']['credential']
ok('凭据名单12个且完整',len(cred['entrustments'])==12 and cred['scope']['entrustment_census_complete'])

# 凭据可下载（viewer 也有权限）
d=client.get('/runs/%d/credential'%rid,headers=viewer)
ok('viewer 下载核验凭据',d.status_code==200 and d.json()['credential_digest']==cred['credential_digest'])
ok('凭据下载文件名',d.headers['content-disposition'].startswith('attachment; filename="credential-%d.json"'%rid))
# 重复下载字节一致（中断重试不换凭据）
d2=client.get('/runs/%d/credential'%rid,headers=viewer)
ok('重复下载凭据字节一致',d2.content==d.content)

# 按委托下载交接包
p=client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-001'},headers=viewer)
ok('按委托下载交接包',p.status_code==200 and p.json()['count']==1 and p.json()['rows'][0]['sample_code']=='JS-20261117-001')
ok('交接包不含其他委托',all(x['sample_code']=='JS-20261117-001' for x in p.json()['rows']))
empty=client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-999'},headers=viewer)
ok('未列出委托得到零行否定证明',empty.status_code==200 and empty.json()['count']==0 and empty.json()['expected']['listed'] is False)
bad=client.get('/runs/%d/handover'%rid,params={'entrustment':'  '},headers=viewer)
ok('空委托号拒绝',bad.status_code==422)
missing=client.get('/runs/%d/handover'%rid,headers=viewer)
ok('缺少委托号拒绝',missing.status_code==422)

# 离线验包：留存凭据 + 委托号
with open(TMP+'/cred.json','wb') as f:f.write(d.content)
with open(TMP+'/pkg.json','wb') as f:f.write(p.content)
ver=subprocess.run([sys.executable,'-I','/workspace/backend/scripts/verify_handover.py','--credential',TMP+'/cred.json','--entrustment','JS-20261117-001','--package',TMP+'/pkg.json'],capture_output=True,text=True)
ok('外站离线验包通过',ver.returncode==0 and '"verified"' in ver.stdout)
with open(TMP+'/pkg-empty.json','wb') as f:f.write(empty.content)
ver=subprocess.run([sys.executable,'-I','/workspace/backend/scripts/verify_handover.py','--credential',TMP+'/cred.json','--entrustment','JS-20261117-999','--package',TMP+'/pkg-empty.json'],capture_output=True,text=True)
ok('外站离线负证明通过',ver.returncode==0 and 'verified-empty' in ver.stdout)

# 备份恢复：替换目标行不改完成记录与凭据
backup=client.get('/runs/%d/backup'%rid,headers=viewer).json()
res=client.post('/runs/%d/restore'%rid,json={'backup':backup},headers=admin)
ok('管理员恢复成功',res.status_code==200 and res.json()['verified'] and res.json()['restored_count']==12)
after=client.get('/runs/%d'%rid,headers=viewer).json()
ok('恢复后固定凭据不变',after['result']['credential']==cred)
ok('恢复记录带凭据摘要',after['recovery'][-1]['credential_digest']==cred['credential_digest'])
p2=client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-001'},headers=viewer)
ok('恢复后交接包与先前一致',p2.content==p.content)

# 篡改备份被拒且目标行不变
tampered=json.loads(json.dumps(backup));tampered['rows'][0]['result_value']='1.000000'
badres=client.post('/runs/%d/restore'%rid,json={'backup':tampered},headers=admin)
ok('篡改备份被拒',badres.status_code==422)
t=client.get('/runs/%d/targets'%rid,headers=viewer).json()
ok('被拒恢复不改变目标行',t['count']==12 and t['checksum']==digest(after['result']['accepted_rows']))

# 异常批次：被拒委托零行承诺
rid2=start('run-bad',amid,'异常')
r2=client.get('/runs/%d'%rid2,headers=viewer).json()
c2=r2['result']['credential'];ok('异常批次凭据完整',c2['scope']['entrustment_census_complete'] and {'JS-BAD-01'}<= {e['entrustment'] for e in c2['entrustments']})
pbad=client.get('/runs/%d/handover'%rid2,params={'entrustment':'JS-BAD-01'},headers=viewer)
ok('被拒委托零行包',pbad.status_code==200 and pbad.json()['count']==0 and pbad.json()['expected']['listed'] is True)

# 未完成运行不能下载凭据/交接包
with Session() as s:
 q=Run(name='queued-one',mapping_id=mid,batch_id=bid,status='queued',snapshot={});s.add(q);s.commit();s.refresh(q);qid=q.id
ok('未完成运行拒绝凭据',client.get('/runs/%d/credential'%qid,headers=viewer).status_code==409)
ok('未完成运行拒绝交接包',client.get('/runs/%d/handover'%qid,params={'entrustment':'JS-20261117-001'},headers=viewer).status_code==409)
ok('未完成运行拒绝备份',client.get('/runs/%d/backup'%qid,headers=viewer).status_code==409)
ok('viewer 不能恢复',client.post('/runs/%d/restore'%rid,json={'backup':backup},headers=viewer).status_code==403)
ok('未登录不能下载交接包',client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-001'}).status_code==401)

# 旧版完成记录（result 里没有 credential）：读取时按当时记录重建，可正常下载
from src.services.migration import migrate as _migrate,make_backup as _backup
with Session() as s:
 res=_migrate(GOOD,RULES,4)
 leg=Run(name='legacy-done',mapping_id=mid,batch_id=bid,status='completed',snapshot={'batch':{'id':bid,'name':'日班','rows':GOOD},'mapping':{'id':mid,'chunk_size':4,'rules':RULES}})
 leg.result={k:v for k,v in res.items()};leg.result['backup']=_backup(res,999,'日班');s.add(leg);s.commit();s.refresh(leg);lid=leg.id
legd=client.get('/runs/%d'%lid,headers=viewer)
ok('旧完成记录详情重建凭据',legd.status_code==200 and legd.json()['result']['credential']['run_id']==lid)
ok('旧完成记录可下载凭据',client.get('/runs/%d/credential'%lid,headers=viewer).status_code==200)
ok('旧完成记录可按委托下载交接包',client.get('/runs/%d/handover'%lid,params={'entrustment':'JS-20261117-001'},headers=viewer).json()['count']==1)
with Session() as s:ok('读取旧记录未改写数据库',s.get(Run,lid).result.get('credential') is None)

# 备份恢复与交接包下载交错（并发读）：恢复前后交错下载，包字节不变
seq=[]
for i in range(3):
 seq.append(client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-002'},headers=viewer).content)
 client.post('/runs/%d/restore'%rid,json={'backup':backup},headers=admin)
ok('恢复与下载交错时包不变',len(set(seq))==1 and seq[0]==client.get('/runs/%d/handover'%rid,params={'entrustment':'JS-20261117-002'},headers=viewer).content)
ok('交错恢复后凭据仍为完成时凭据',client.get('/runs/%d/credential'%rid,headers=viewer).json()==cred)

failed=[n for n,c in checks if not c]
print('\n%d/%d passed'%(len(checks)-len(failed),len(checks)))
print('tmp:',TMP)
sys.exit(1 if failed else 0)
