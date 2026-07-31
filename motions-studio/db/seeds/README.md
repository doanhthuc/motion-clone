# Workflow Seeds

Các file trong thư mục này chỉ chạy thủ công. Không đặt workflow seed trong `db/init`, vì Postgres sẽ tự chạy `db/init/*.sql` khi tạo DB volume mới và làm workflow mẫu xuất hiện sau deploy.

## Import / cập nhật workflow mẫu

Chạy từ thư mục `motion-backend` khi stack Docker đang chạy:

```bash
node scripts/apply-workflow-seed.mjs
```

Mặc định script import `db/seeds/workflow_templates.sql` vào service Docker Compose `postgres`.

Tuỳ chọn:

```bash
# Chạy một file seed cụ thể
node scripts/apply-workflow-seed.mjs db/seeds/face_motion_workflow.sql

# Dùng DATABASE_URL thay vì docker compose postgres
DATABASE_URL=postgres://motion:motionpass@127.0.0.1:5532/motion node scripts/apply-workflow-seed.mjs

# Nếu service postgres trong compose có tên khác
WORKFLOW_SEED_DB_SERVICE=postgres POSTGRES_USER=motion POSTGRES_DB=motion node scripts/apply-workflow-seed.mjs
```

## Export lại seed từ VPS production

```bash
node scripts/export-workflow-seed.mjs
```

Script export sẽ ghi vào `db/seeds/workflow_templates.sql`, không ghi vào `db/init`.
