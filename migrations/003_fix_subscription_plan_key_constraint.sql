-- CloudGPT schema migration 003: allow the 'max' plan at the database level.
--
-- Migration 001 shipped a CHECK constraint on subscriptions.plan_key limited to
-- ('lite', 'pro'), while the application layer (db.upsert_subscription,
-- services/billing.py) validates and offers the 'max' plan. Any Stripe webhook
-- creating a max-plan subscription failed with a constraint violation.
--
-- quota_reservations.plan_key and usage_events.plan_key already include 'max'.

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_plan_key_check;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_plan_key_check
    CHECK (plan_key IN ('lite', 'pro', 'max'));

-- In case the anonymous inline CHECK from an older bootstrap is present,
-- drop any constraint on plan_key that still excludes 'max' before re-adding.
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE rel.relname = 'subscriptions'
          AND con.contype = 'c'
          AND con.conname <> 'subscriptions_plan_key_check'
          AND pg_get_constraintdef(con.oid) LIKE '%plan_key%'
    LOOP
        EXECUTE format('ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS %I', constraint_name);
    END LOOP;
END $$;

ALTER TABLE quota_reservations DROP CONSTRAINT IF EXISTS quota_reservations_plan_key_check;
ALTER TABLE quota_reservations ADD CONSTRAINT quota_reservations_plan_key_check
    CHECK (plan_key IN ('lite', 'pro', 'max'));

-- The plans catalog table was also seeded with CHECK (plan_key IN ('lite', 'pro')).
ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_plan_key_check;
ALTER TABLE plans ADD CONSTRAINT plans_plan_key_check
    CHECK (plan_key IN ('lite', 'pro', 'max'));
