# Schema v1
SQLAlchemy metadata creates users/audits/batches/mappings/runs/target_rows. Unique natural names, foreign keys, and target unique(run_id,key_digest) enforce linkage. Successful workers atomically commit accepted target rows; cancelled/failed runs do not persist partial targets. Restore replaces a run's target set under a run row lock and checks the logical backup checksum before commit.

Handover credentials (v1) need no new tables or columns: the frozen credential is stored inside `runs.result.credential` in the same transaction that commits target rows, and the per-entrustment handover package is derived on read from that frozen result. Restore never rewrites `runs.result`, so a credential fixed at migration completion survives any backup restore.
