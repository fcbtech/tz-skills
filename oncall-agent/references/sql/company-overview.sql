-- Company overview probe — the first-60-seconds baseline for any ticket
-- Profile: tz-prod-read-replica (read-only)
-- Vars: ${COMPANY_ID}
-- Purpose: one query block that answers "who is this customer? are they active?
--          who owns the account? how many users? when was the last activity?"
-- Verify column names against tz-core/ models before relying on the output.

-- 1. Company row
SELECT
    c.id           AS company_id,
    c.name,
    c.gst,
    c.is_active,
    c.creation_date,
    c.last_modification_date
FROM profile_mgt_company c
WHERE c.id = ${COMPANY_ID};

-- 2. Owner(s)
SELECT
    u.id           AS owner_user_id,
    u.email,
    u.first_name,
    u.last_name,
    u.last_login,
    p.is_owner
FROM profile_mgt_userprofile p
JOIN auth_user u ON u.id = p.user_id
WHERE p.company_id = ${COMPANY_ID}
  AND p.is_owner = 1
  AND p.active = 1;

-- 3. Active user count + most-recent login
SELECT
    COUNT(*)                  AS active_users,
    MAX(u.last_login)         AS latest_login,
    MIN(u.last_login)         AS earliest_login
FROM profile_mgt_userprofile p
JOIN auth_user u ON u.id = p.user_id
WHERE p.company_id = ${COMPANY_ID}
  AND p.active = 1;
