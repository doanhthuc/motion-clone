-- #region ALD 05/07/2026 - "Social Management": Content Plan (chạy workflow theo giờ hẹn + tự đăng) + Social
-- Post (log/queue đăng bài, dùng chung cho recommend thủ công VÀ plan tự động). Idempotent.

-- Kế hoạch đăng bài: 1 user có thể có nhiều plan (vd "Plan hàng ngày"), mỗi plan gồm nhiều slot (mỗi slot = 1
-- bài/ngày, chạy 1 workflow ở 1 giờ cố định rồi tự đăng).
CREATE TABLE IF NOT EXISTS content_plans (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        text NOT NULL,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS content_plans_user_idx ON content_plans (user_id);

-- Mỗi slot = 1 "post" trong ngày: workflow nào, input gì (session.field → giá trị), giờ chạy, thứ nào trong
-- tuần, đăng lên đâu (facebook/tiktok accountIds) + caption. weekdays dùng convention Postgres EXTRACT(DOW):
-- 0=Chủ nhật .. 6=Thứ 7. time_of_day/weekdays so khớp theo giờ Asia/Ho_Chi_Minh (xem social-scheduler.js).
CREATE TABLE IF NOT EXISTS content_plan_slots (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id       uuid NOT NULL REFERENCES content_plans(id) ON DELETE CASCADE,
  label         text,
  workflow_id   uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  input         jsonb NOT NULL DEFAULT '{}'::jsonb,   -- { [session.field]: text | {name,mimeType,data} }
  time_of_day   time NOT NULL,
  weekdays      smallint[] NOT NULL DEFAULT '{0,1,2,3,4,5,6}',
  caption       text,
  facebook      jsonb NOT NULL DEFAULT '{"enabled":false,"accountIds":[]}'::jsonb,
  tiktok        jsonb NOT NULL DEFAULT '{"enabled":false,"accountIds":[]}'::jsonb,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS content_plan_slots_plan_idx ON content_plan_slots (plan_id);
CREATE INDEX IF NOT EXISTS content_plan_slots_active_idx ON content_plan_slots (is_active) WHERE is_active = true;

-- Log 1 lần chạy/ngày của 1 slot — UNIQUE(slot_id, scheduled_date) chống bắn trùng nếu scheduler tick nhiều lần
-- trong ngày (kể cả worker restart giữa chừng).
CREATE TABLE IF NOT EXISTS content_plan_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slot_id         uuid NOT NULL REFERENCES content_plan_slots(id) ON DELETE CASCADE,
  scheduled_date  date NOT NULL,
  workflow_run_id uuid REFERENCES workflow_runs(id) ON DELETE SET NULL,
  status          text NOT NULL DEFAULT 'queued',   -- queued | success | error
  error_msg       text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  UNIQUE (slot_id, scheduled_date)
);
CREATE INDEX IF NOT EXISTS content_plan_runs_wfrun_idx ON content_plan_runs (workflow_run_id);

-- social_posts: NGUỒN SỰ THẬT DUY NHẤT cho "đã đăng chưa" (dùng để Recommend bỏ qua output đã tạo bài) VÀ
-- hàng đợi đăng (scheduler tickSocialPosts publish khi scheduled_for<=now). 1 dòng = 1 (platform,account) —
-- đăng 1 output lên 2 Page Facebook + 1 TikTok = 3 dòng, mỗi dòng tự retry/theo dõi độc lập.
CREATE TABLE IF NOT EXISTS social_posts (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workflow_run_id     uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  content_plan_slot_id uuid REFERENCES content_plan_slots(id) ON DELETE SET NULL, -- NULL = tạo thủ công từ Recommend
  platform            text NOT NULL,               -- 'facebook' | 'tiktok'
  account_id          uuid NOT NULL REFERENCES social_accounts(id) ON DELETE CASCADE,
  caption             text,
  status              text NOT NULL DEFAULT 'scheduled', -- scheduled | posting | posted | error | cancelled
  scheduled_for       timestamptz NOT NULL DEFAULT now(),
  external_post_id    text,
  error_msg           text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  posted_at           timestamptz
);
CREATE INDEX IF NOT EXISTS social_posts_user_idx ON social_posts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS social_posts_wfrun_idx ON social_posts (workflow_run_id);
CREATE INDEX IF NOT EXISTS social_posts_due_idx ON social_posts (status, scheduled_for) WHERE status = 'scheduled';
-- #endregion
