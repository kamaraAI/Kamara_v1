# Subscription Usage Events

## What this table is for

`subscription_usage_events` is an append-only usage ledger.

It should store one row every time a protected subscription feature is consumed, so the backend can:

- Count usage per user and per feature
- Enforce plan limits
- Reset usage by period when needed
- Audit who used what, and when

It is not a billing table.
It does not replace `subscriptions`.
It is the event log that `subscriptions` can be measured against.

## What the backend currently expects

The current backend code reads and writes these fields:

- `user_id`
- `feature_key`
- `quantity`
- `created_at`

The code uses these paths:

- `backend/app/subscriptions/service.py`
  - `record_usage_event()` inserts a usage row
  - `get_usage_count()` counts rows for a user and feature
  - `evaluate_feature_access()` checks plan limits against those counts
- `backend/app/subscriptions/api.py`
  - `POST /api/v1/subscription/consume` records usage after access is approved
- `backend/pages/courses.py`
  - records usage for course generation flow

Current feature keys used by the backend:

- `message_send`
- `course_generation` is normalized to `message_send`
- `note_size`
- `pdf_upload`
- `external_source`

## What needs to exist in the database

### Required table

```sql
create table if not exists public.subscription_usage_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  feature_key text not null,
  quantity integer not null default 1,
  created_at timestamptz not null default now()
);
```

### Recommended foreign key

If your Supabase project uses `auth.users` as the source of truth for `user_id`, add:

```sql
alter table public.subscription_usage_events
  add constraint subscription_usage_events_user_id_fkey
  foreign key (user_id)
  references auth.users(id)
  on delete cascade;
```

If you prefer to link to `profiles(id)` instead, use that table instead of `auth.users`.

### Recommended checks

```sql
alter table public.subscription_usage_events
  add constraint subscription_usage_events_quantity_check
  check (quantity > 0);
```

### Recommended indexes

```sql
create index if not exists idx_subscription_usage_events_user_feature_created_at
  on public.subscription_usage_events (user_id, feature_key, created_at desc);

create index if not exists idx_subscription_usage_events_feature_created_at
  on public.subscription_usage_events (feature_key, created_at desc);
```

## Optional columns that help later

These are not required by the current code, but they are useful if you want stronger auditing or idempotency:

```sql
alter table public.subscription_usage_events
  add column if not exists session_id uuid;

alter table public.subscription_usage_events
  add column if not exists request_id text;

alter table public.subscription_usage_events
  add column if not exists metadata jsonb not null default '{}'::jsonb;
```

Optional supporting constraints:

```sql
create unique index if not exists idx_subscription_usage_events_request_id
  on public.subscription_usage_events (request_id)
  where request_id is not null;
```

## Subscription table columns the backend depends on

The code also expects `public.subscriptions` to have these columns:

- `user_id`
- `plan_id`
- `status`
- `trial_started_at`
- `trial_ends_at`
- `current_period_start`
- `current_period_end`
- `created_at`
- `updated_at` is recommended for lifecycle changes

The backend no longer expects `subscriptions.plan`.
Plan names should come from `subscriptions.plan_id -> plans.name`.

## SQL for subscription support columns

If you need to add the missing lifecycle column:

```sql
alter table public.subscriptions
  add column if not exists updated_at timestamptz;

update public.subscriptions
set updated_at = coalesce(updated_at, created_at, now());

alter table public.subscriptions
  alter column updated_at set default now();
```

## Suggested seed rows for `plans`

The backend expects at least these plan names to exist:

- `starter`
- `pro`

If they are missing, seed them with something like:

```sql
insert into public.plans (name)
values ('starter')
on conflict (name) do nothing;

insert into public.plans (name)
values ('pro')
on conflict (name) do nothing;
```

## What was implemented in code

Already implemented in the backend:

- Usage is recorded through `record_usage_event()`
- Access is checked through `evaluate_feature_access()`
- Feature limits are tied to `PLAN_CATALOG`
- Missing usage rows currently fall back to `0` and log warnings
- Missing `subscriptions.plan` reads were removed and replaced with `plan_id -> plans.name`

## What is still missing in the database

If `subscription_usage_events` does not exist yet, the backend will keep warning and usage counts will fall back to zero.

That means the protection layer can become too permissive until the table exists.

So the minimum database fix is:

1. Create `public.subscription_usage_events`
2. Add the indexes
3. Add the foreign key if appropriate
4. Make sure `public.subscriptions` has `plan_id` and lifecycle columns
5. Make sure `public.plans` has `starter` and `pro`

