# PostgreSQL 备份、恢复与迁移回滚演练

项目提供 `scripts/postgres_disaster_rehearsal.ps1`，在不修改生产数据库的前提下执行完整演练：

1. 对 Compose 中的 PostgreSQL 执行 custom-format `pg_dump`。
2. 把备份复制到本地 `backups/` 并计算 SHA-256。
3. 创建名称带 `_restore_check_<PID>` 的一次性数据库。
4. 使用 `pg_restore --exit-on-error` 恢复并检查 pgvector、论文数和 Chunk 数。
5. 只在一次性数据库上执行 `alembic downgrade -1`、`upgrade head` 和 `current`。
6. 默认删除一次性数据库和容器内临时备份；本地备份保留。

运行：

```powershell
Set-Location E:\code\ScholarAgent
powershell -ExecutionPolicy Bypass -File .\scripts\postgres_disaster_rehearsal.ps1
```

如需人工检查恢复库，可增加 `-KeepRestoreDatabase`。演练脚本会验证数据库名只含字母、数字和下划线，并显式禁止恢复目标等于源数据库。它不会删除源数据库，也不会操作 `scholar_postgres` 数据卷。

当前真实演练仍受 Docker Desktop 阻塞：`docker version --format '{{json .Server}}'` 返回 `null`，Linux engine named pipe 不存在，且 `%LOCALAPPDATA%\Docker\run\dockerInference` 是当前权限无法安全清理的 ReparsePoint。恢复 Docker 后，应把脚本输出的备份路径、SHA-256、恢复计数和 Alembic current revision 记录到发布验收单，再勾选 `RAG_TODO.md` 中的真实演练项。
