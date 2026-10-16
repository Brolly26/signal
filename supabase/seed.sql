-- Demo data for Signal. FICTIONAL, written for this assessment.
--
-- Apply after the migrations:
--     docker compose exec -T db psql -U signal -d signal < supabase/seed.sql
-- or, against any Postgres:
--     psql "$DATABASE_URL" -f supabase/seed.sql
--
-- WHAT THIS FILE DOES NOT CONTAIN: AI analyses.
--
-- Seeding an analysis would mean shipping a hand-written model answer and
-- showing it in the UI as though a model had produced it. The analyses in a
-- demo are produced by running the application with a real API key, so what
-- you see on screen is what the model actually returned. The requests below
-- exist so it has something real to retrieve.
--
-- Re-running this file is safe: it removes its own rows first and inserts
-- nothing else.

BEGIN;

-- Only the rows this file created; anything you submitted yourself is kept.
DELETE FROM feature_requests WHERE requester_label LIKE 'demo:%';

INSERT INTO feature_requests (title, description, requester_label, created_at) VALUES

-- ── Pair 1: genuine duplicates. The same need, almost no shared vocabulary. ──
-- This is the pair the product exists for: a customer proposes the solution
-- they imagined ("an Excel export") while describing a problem another customer
-- already asked to have solved properly ("send it on a schedule").
(
  'Scheduled report delivery',
  E'Every Friday our account manager needs the week\'s sales summary. Right now someone on my team remembers to build it and send it, and when they are on holiday it simply does not go out.\n\nI would like the report to be emailed on a schedule so nobody has to remember.',
  'demo:priya',
  now() - interval '9 days'
),
(
  'Export sales data to Excel',
  E'Please add an Excel export button to the sales dashboard.\n\nEvery Friday I select the week, copy the numbers into a spreadsheet by hand, and email it to my manager. It takes about twenty minutes and I have made mistakes doing it.',
  'demo:marcus',
  now() - interval '2 days'
),

-- ── Pair 2: related, but NOT duplicates. ─────────────────────────────────────
-- Both are driven by the same security policy, and either could ship without
-- the other. Calling these duplicates is the expensive mistake.
(
  'Single sign-on with Okta',
  E'Our security team will not approve a company-wide rollout until staff can sign in through Okta with SAML. Right now everyone has a separate password for this tool, which is exactly what our policy forbids.',
  'demo:dani',
  now() - interval '7 days'
),
(
  'Enforce two-factor authentication',
  E'We need to be able to require a second factor for every account in our workspace, and to see who has not set one up yet.\n\nWe had a phishing incident last quarter and our insurer now asks about this directly.',
  'demo:dani',
  now() - interval '5 days'
),

-- ── Independent: nothing on the board shares this need. ──────────────────────
(
  'Bulk import contacts from a CSV file',
  E'We are migrating from another tool and have about eleven thousand contacts to bring across. There is no way to do that today short of typing them in.\n\nA CSV upload with a preview of what will be created would be enough.',
  'demo:aisha',
  now() - interval '4 days'
),

-- ── Ambiguous: not enough detail to compare or act on. ───────────────────────
(
  'Make it faster please',
  E'The app feels slow sometimes and it is frustrating when that happens. Could you look into the performance?',
  'demo:anonymous',
  now() - interval '1 day'
),

-- ── Two more, so search and ordering have something to work with. ───────────
(
  'Saved report templates',
  E'I rebuild the same three reports every month: pick the columns, set the date range, group by region. I would like to save that setup and reuse it instead of recreating it each time.',
  'demo:priya',
  now() - interval '6 days'
),
(
  'Keyboard shortcuts for common actions',
  E'I am in this tool all day and most of what I do is the same four actions. Keyboard shortcuts for them would save me a lot of clicking.',
  'demo:marcus',
  now() - interval '3 days'
);

-- Support, so the board has something to order by. Voter keys are demo values;
-- they are browser-scoped identifiers, not accounts.
INSERT INTO request_votes (request_id, voter_key)
SELECT r.id, v.voter_key
  FROM feature_requests AS r
  JOIN (VALUES
          ('Scheduled report delivery',           'demo-voter-0001'),
          ('Scheduled report delivery',           'demo-voter-0002'),
          ('Scheduled report delivery',           'demo-voter-0003'),
          ('Scheduled report delivery',           'demo-voter-0004'),
          ('Single sign-on with Okta',            'demo-voter-0001'),
          ('Single sign-on with Okta',            'demo-voter-0005'),
          ('Single sign-on with Okta',            'demo-voter-0006'),
          ('Enforce two-factor authentication',   'demo-voter-0005'),
          ('Export sales data to Excel',          'demo-voter-0002'),
          ('Export sales data to Excel',          'demo-voter-0007'),
          ('Bulk import contacts from a CSV file','demo-voter-0008'),
          ('Saved report templates',              'demo-voter-0001'),
          ('Saved report templates',              'demo-voter-0003'),
          ('Keyboard shortcuts for common actions','demo-voter-0007')
       ) AS v(title, voter_key)
    ON v.title = r.title
 WHERE r.requester_label LIKE 'demo:%';

COMMIT;

-- Expected AI behaviour, for checking a demo against something written down
-- beforehand rather than deciding afterwards that whatever happened was right:
--
--   Export sales data to Excel        -> DUPLICATE of Scheduled report delivery
--                                        (the need is automatic weekly delivery;
--                                         the export is the workaround)
--   Enforce two-factor authentication -> RELATED to Single sign-on with Okta
--                                        (shared policy driver, separate features)
--   Saved report templates            -> RELATED to the reporting requests
--                                        (shared reporting workflow, different need)
--   Bulk import contacts              -> DISTINCT
--   Make it faster please             -> NEEDS_CLARIFICATION
--   Keyboard shortcuts                -> DISTINCT
--
-- These are predictions, not guarantees. A model may disagree, and when it does
-- that disagreement is the interesting result - it is what the evaluation suite
-- in backend/evals/ exists to measure.
