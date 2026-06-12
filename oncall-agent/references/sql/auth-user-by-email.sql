-- Lookup auth_user by email
-- Profile: tz-prod-read-replica (read-only)
-- Vars: ${EMAIL}
-- Used when: ticket gives only an email; need to find the user_id
-- Note: email is not necessarily unique to one company — a user can belong to multiple companies.
--       Use user-profile-across-companies.sql to find all tenants for the user.

SELECT
    id AS user_id,
    email,
    first_name,
    last_name,
    is_active,
    last_login,
    date_joined
FROM auth_user
WHERE email = '${EMAIL}';
