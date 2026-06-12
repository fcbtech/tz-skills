-- Lookup auth_user + profile by id (impersonation prep)
-- Profile: mstag-dmz (scrambled credentials — safe for local login)
-- Vars: ${USER_ID}
-- Used when: ticket has cf_user_id and dev needs to log in as that user

SELECT
    u.id,
    u.username,
    u.email,
    u.first_name,
    u.last_name,
    u.is_active,
    u.last_login,
    u.date_joined
FROM auth_user u
WHERE u.id = ${USER_ID};

SELECT
    p.user_id,
    p.company_id,
    p.first_name,
    p.last_name,
    p.contact_no,
    p.is_owner,
    p.active,
    p.creation_date
FROM profile_mgt_userprofile p
WHERE p.user_id = ${USER_ID};
