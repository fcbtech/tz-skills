-- All companies a user belongs to
-- Profile: tz-prod-read-replica (read-only)
-- Vars: ${EMAIL}
-- Used when: ticket gives an email; need to disambiguate which tenant it's about

SELECT
    u.id           AS user_id,
    u.email,
    u.is_active   AS user_active,
    p.company_id,
    c.name         AS company_name,
    c.is_active    AS company_active,
    p.is_owner,
    p.active       AS profile_active,
    p.creation_date AS profile_created
FROM auth_user u
JOIN profile_mgt_userprofile p ON p.user_id = u.id
LEFT JOIN profile_mgt_company c ON c.id = p.company_id
WHERE u.email = '${EMAIL}'
ORDER BY p.creation_date;
