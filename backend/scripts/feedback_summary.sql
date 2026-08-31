-- Feedback summary, with seeded rows separated from real ones.
--
-- Run:  make feedback-summary
--   or: psql -U stellarerp -d stellarerp -f scripts/feedback_summary.sql
--
-- Why the split is the whole point
-- -------------------------------
-- The Stellar Builder checklist asks for a *user feedback summary*, which means
-- feedback from users. `scripts/seed_demo.py` writes twelve rows so the inbox screen
-- has something in it for a screenshot, and those rows are not evidence of anything.
--
-- A `SELECT count(*) FROM feedback` would return both kinds added together, and a
-- screenshot of that number is a claim about users that the number does not support.
-- So every query below reports `real` and `seeded` as separate rows and never as a
-- total. If the real column reads zero, that is the honest answer, and the fix is to
-- collect feedback rather than to change the query.
--
-- Seeded rows are identified the same way `submission_evidence.py` identifies them -
-- the marker domain on `contact_email` - so the two cannot drift apart and report
-- different figures about the same table.
--
-- `contact_email IS NULL` counts as real: the widget works signed out and the address
-- is optional, so a genuine submission from somebody who did not leave one has a null
-- there. The seeder always writes an address, so nothing seeded lands in that bucket.
-- Note that `NULL NOT LIKE '...'` is NULL rather than true, which is why the null case
-- is spelled out instead of relying on the negation.

\pset null '-'
\timing off

\echo
\echo ===============================================================
\echo  Feedback: real vs seeded
\echo ===============================================================

SELECT
    count(*) FILTER (
        WHERE contact_email IS NULL
           OR contact_email NOT LIKE '%@demo-seed.example.com'
    )                                                                  AS real_submissions,
    count(*) FILTER (WHERE contact_email LIKE '%@demo-seed.example.com') AS seeded_demo_rows,
    count(*)                                                            AS rows_in_table
FROM feedback;

\echo
\echo ===============================================================
\echo  By kind  (only the "real" rows are evidence)
\echo ===============================================================

SELECT
    CASE
        WHEN contact_email LIKE '%@demo-seed.example.com' THEN 'seeded (not evidence)'
        ELSE 'real'
    END                             AS source,
    kind,
    count(*)                        AS submissions,
    count(rating)                   AS rated,
    round(avg(rating), 1)           AS mean_rating
FROM feedback
GROUP BY 1, 2
ORDER BY 1, 2;

\echo
\echo ===============================================================
\echo  Triage state
\echo ===============================================================

SELECT
    CASE
        WHEN contact_email LIKE '%@demo-seed.example.com' THEN 'seeded (not evidence)'
        ELSE 'real'
    END          AS source,
    status,
    count(*)     AS submissions
FROM feedback
GROUP BY 1, 2
ORDER BY 1, 2;

\echo
\echo ===============================================================
\echo  Real submissions, most recent first
\echo ===============================================================
-- The message is truncated rather than shown whole: this output is meant to be
-- screenshotted, and somebody who sent a bug report did not agree to have all of it
-- published. The screen it came from is the useful column anyway - it is what makes
-- the report actionable, and it is why the widget sends it.

SELECT
    created_at::timestamp(0)        AS received,
    kind,
    rating,
    coalesce(screen, '-')           AS screen,
    left(message, 60)               AS message_preview
FROM feedback
WHERE contact_email IS NULL
   OR contact_email NOT LIKE '%@demo-seed.example.com'
ORDER BY created_at DESC
LIMIT 20;
