import json
from urllib.parse import quote
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from src.middlewares.auth import current,writer,admin
from src.schemas.business import RunInput,RestoreInput
from src.services import runs
from src.services.handover import credential_filename,package_filename
from src.controllers.business import start_run
from src.services.audit import log
router=APIRouter()
@router.get('/runs')
def listing(u=Depends(current)):return runs.list_items()
@router.post('/runs')
def create(data:RunInput,u=Depends(writer)):return start_run(data,u)
@router.get('/runs/{item_id}')
def detail(item_id:int,u=Depends(current)):return runs.detail(item_id)
@router.post('/runs/{item_id}/cancel')
def cancel(item_id:int,u=Depends(writer)):
 r=runs.cancel(item_id);log(u.email,'run.cancel',str(item_id));return r
@router.get('/runs/{item_id}/targets')
def targets(item_id:int,u=Depends(current)):return runs.targets(item_id)
@router.get('/runs/{item_id}/backup')
def backup(item_id:int,u=Depends(current)):
 return Response(json.dumps(runs.backup(item_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="backup-{item_id}.json"'})
@router.get('/runs/{item_id}/export')
def export(item_id:int,u=Depends(current)):
 return Response(json.dumps(runs.detail(item_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reconciliation-{item_id}.json"'})
@router.post('/runs/{item_id}/restore')
def restore(item_id:int,data:RestoreInput,u=Depends(admin)):
 r=runs.restore(item_id,data);log(u.email,'run.restore',str(item_id));return r
@router.get('/runs/{item_id}/credential')
def credential(item_id:int,u=Depends(current)):
 c=runs.credential(item_id)
 return Response(json.dumps(c,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{credential_filename(item_id)}"'})
@router.get('/runs/{item_id}/handover')
def handover(item_id:int,entrustment:str='',u=Depends(current)):
 p=runs.entrustment_package(item_id,entrustment)
 filename=package_filename(item_id,p['entrustment'])
 return Response(json.dumps(p,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename,safe="")}'})
