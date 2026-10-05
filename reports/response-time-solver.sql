-- =============================================================================
-- REPORT: Response Time Solver — kirim case ke grup -> balasan pertama solver
-- Total all-time, semua case (open / in_progress / done)
-- Jalankan manual di VPS:
--   docker exec -it moban-db psql -U postgres -d moban -f file.sql
--   atau: psql $DATABASE_URL -f file.sql
-- =============================================================================
-- Definisi:
--   t0 = cases.created_at
--        (waktu POST /api/cases mengirim pesan root ke grup — terbukti identik
--         dengan timestamp pesan root di wa_messages, lihat case 89:
--         03:55:22.835931)
--   t1 = MIN(wa_messages.created_at) WHERE from_me = false AND case_id = c.id
--        (balasan PERTAMA dari solver yang ter-link ke case, apapun statusnya —
--         keyword done/in_progress diabaikan)
--   response_time_menit = (t1 - t0) / 60
--
-- Otomatis ter-exclude (from_me = true): pesan bot, reminder, "terimakasih",
-- update status manual dari web.
--
-- Catatan data historis (crawl): pesan lama yang masuk lewat /api/crawl
-- tersimpan dengan created_at = waktu crawl (bukan waktu asli pesan WA),
-- jadi latencynya bisa lebih besar dari kenyataan. Data yang masuk realtime
-- via webhook akurat.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1) RINGKASAN ALL-TIME (avg / min / max / median, dalam menit)
-- ---------------------------------------------------------------------------
WITH first_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id,
           author,
           author_name,
           created_at AS first_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at
),
latency AS (
    SELECT c.id,
           c.case_code,
           c.status,
           fr.author,
           fr.author_name,
           fr.first_reply_at,
           EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0 AS latency_min
    FROM cases c
    JOIN first_reply fr ON fr.case_id = c.id
    WHERE c.deleted_at IS NULL
      AND fr.first_reply_at >= c.created_at   -- buang latensi negatif (data kacau)
)
SELECT
    COUNT(*)                                            AS total_cases_dibahas,
    ROUND(AVG(latency_min), 2)                          AS avg_menit,
    ROUND(MIN(latency_min), 2)                          AS min_menit,
    ROUND(MAX(latency_min), 2)                          AS max_menit,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_min))::numeric, 2) AS median_menit
FROM latency;


-- ---------------------------------------------------------------------------
-- 2) BREAKDOWN PER SOLVER — siapa yang paling cepat merespons
-- ---------------------------------------------------------------------------
WITH first_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, author, author_name, created_at AS first_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at
)
SELECT
    COALESCE(fr.author_name, fr.author)                 AS solver,
    COUNT(*)                                            AS jumlah_case,
    ROUND(AVG(EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0), 2) AS avg_menit,
    ROUND(MIN(EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0), 2) AS min_menit,
    ROUND(MAX(EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0), 2) AS max_menit
FROM cases c
JOIN first_reply fr ON fr.case_id = c.id
WHERE c.deleted_at IS NULL
  AND fr.first_reply_at >= c.created_at
GROUP BY COALESCE(fr.author_name, fr.author)
ORDER BY avg_menit ASC;


-- ---------------------------------------------------------------------------
-- 3) DISTRIBUSI — berapa banyak yang dibalas <5 mnt, 5-15, 15-60, >60
-- ---------------------------------------------------------------------------
WITH first_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, created_at AS first_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at
),
latency AS (
    SELECT EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0 AS m
    FROM cases c
    JOIN first_reply fr ON fr.case_id = c.id
    WHERE c.deleted_at IS NULL
      AND fr.first_reply_at >= c.created_at
)
SELECT
    CASE
        WHEN m < 5    THEN 'a. < 5 menit'
        WHEN m < 15   THEN 'b. 5 - 15 menit'
        WHEN m < 60   THEN 'c. 15 - 60 menit'
        ELSE               'd. > 60 menit'
    END                                                 AS bucket,
    COUNT(*)                                            AS jumlah_case,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS persen
FROM latency
GROUP BY 1
ORDER BY 1;


-- ---------------------------------------------------------------------------
-- 4) CASE YANG BELUM DIBALAS SOLVER (open/in_progress tanpa reply) —
--    tidak masuk rata-rata, tapi bagus dipantau
-- ---------------------------------------------------------------------------
SELECT
    c.id,
    c.case_code,
    c.status,
    g.name AS group_name,
    c.created_at,
    ROUND(EXTRACT(EPOCH FROM (now() - c.created_at)) / 60.0, 1) AS menit_sejak_kirim
FROM cases c
LEFT JOIN wa_messages w
       ON w.case_id = c.id AND w.from_me = false
LEFT JOIN wa_groups g ON g.id = c.group_id   -- LEFT: case legacy tanpa grup ikut tampil
WHERE c.deleted_at IS NULL
  AND w.wa_message_id IS NULL
ORDER BY c.created_at ASC;


-- ---------------------------------------------------------------------------
-- 5) DETAIL PER CASE (audit) — satu baris per case yang sudah dibalas
-- ---------------------------------------------------------------------------
WITH first_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, author, author_name, created_at AS first_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at
)
SELECT
    c.id,
    c.case_code,
    c.status,
    g.name            AS group_name,
    c.created_at      AS kirim_case_at,
    fr.author_name    AS solver_pertama,
    fr.first_reply_at AS balasan_pertama_at,
    ROUND(EXTRACT(EPOCH FROM (fr.first_reply_at - c.created_at)) / 60.0, 2) AS response_menit
FROM cases c
JOIN first_reply fr ON fr.case_id = c.id
LEFT JOIN wa_groups g ON g.id = c.group_id   -- LEFT: case legacy tanpa grup ikut tampil
WHERE c.deleted_at IS NULL
  AND fr.first_reply_at >= c.created_at
ORDER BY c.created_at ASC;
