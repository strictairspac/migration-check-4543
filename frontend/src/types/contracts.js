/** @typedef {number} RunId */
/**
 * @typedef {Object} EntrustmentPromise
 * @property {string} entrustment 委托号（与 sample_code 完全相等才进入交接包）
 * @property {number} count 可交接目标行数
 * @property {string} checksum 排序后该委托全部行的 SHA-256
 * @property {string[]} row_hashes 逐行摘要
 */
/**
 * @typedef {Object} Credential 迁移完成时固定的核验凭据
 * @property {1} version
 * @property {'handover-credential'} kind
 * @property {RunId} run_id
 * @property {string} completed_at
 * @property {{source_row_count:number,accepted_count:number,rejected_count:number,entrustment_census_complete:boolean}} scope
 * @property {string} run_checksum
 * @property {EntrustmentPromise[]} entrustments
 * @property {string} credential_digest
 */
/**
 * @typedef {Object} HandoverPackage 按委托下载的交接包
 * @property {1} version
 * @property {'handover-package'} kind
 * @property {RunId} run_id
 * @property {string} entrustment
 * @property {'migration-completion'} record_basis
 * @property {string} credential_digest
 * @property {{listed:boolean,count:number,checksum:string,row_hashes:string[]}} expected
 * @property {number} count
 * @property {object[]} rows 仅含该委托的已接受目标行
 */
/** @typedef {{id:number,name:string,source_file:string,rows:object[]}} Batch */
/** @typedef {{id:number,name:string,batch_id:number,rules:object[],chunk_size:number}} Mapping */
/** @typedef {{id:number,name:string,status:'queued'|'running'|'completed'|'failed'|'cancelled',result:object|null}} Run */
export {};
