import fs from 'fs';
const src=fs.readFileSync('/workspace/frontend/src/pages/results.js','utf8');
const stubs=`
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json=v=>esc(JSON.stringify(v,null,2));
const status=s=>'<span class="status">'+s+'</span>';
const table=(head,rows)=>'<table>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</table>';
`;
const body=src.replace(/^import .*$/gm,'').replace(/export function resultsPage/,'function resultsPage');
const env=new Function(stubs+'return {esc,json,status,table};')();
const {esc,json,status,table}=env;
const fn=new Function('store','esc','json','status','table',body+';return resultsPage(3);');
const cred={version:1,kind:'handover-credential',run_id:3,credential_digest:'abc123',scope:{source_row_count:12,accepted_count:12,rejected_count:0,entrustment_census_complete:true},entrustments:[{entrustment:'JS-20261117-001',count:1,checksum:'deadbeefdeadbeef',row_hashes:['x']},{entrustment:'JS-BAD-01',count:0,checksum:'e3b0c442',row_hashes:[]}]};
const makeStore=c=>[{id:3,name:'shift',status:'completed',created_at:'now',result:{source_count:12,accepted_count:12,rejected_count:0,checksum:'run',accepted_rows:[],rejected_rows:[],chunks:[],credential:c},recovery:[]}];
const html=fn({runs:makeStore(cred),user:{role:'admin'}},esc,json,status,table);
const checks=[['凭据区块',html.includes('核验凭据 · 迁移完成时固定')],['凭据下载按钮',html.includes('data-credential="3"')],['委托号按钮',html.includes('data-handover="JS-20261117-001"')],['零行委托也在名单',html.includes('data-handover="JS-BAD-01"')],['名单完整提示',html.includes('名单完整')],['抽查输入可选',!html.includes('id="handover-code" placeholder="JS-20261117-014" disabled')],['凭据摘要展示',html.includes('abc123')]];
let fail=0;for(const [n,c] of checks){console.log((c?'PASS':'FAIL'),n);if(!c)fail++;}
const cred2={...cred,scope:{...cred.scope,entrustment_census_complete:false}};
const html2=fn({runs:makeStore(cred2),user:{role:'admin'}},esc,json,status,table);
const c2=[['名单不完整提示',html2.includes('名单不完整')],['抽查输入禁用',html2.includes('id="handover-code" placeholder="JS-20261117-014" disabled')]];
for(const [n,c] of c2){console.log((c?'PASS':'FAIL'),n);if(!c)fail++;}
process.exit(fail?1:0);
