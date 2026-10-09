-- =============================================================================
-- REPORT: Resolution Time Solver — kirim case ke grup -> balasan solver TERAKHIR
-- Hanya case berstatus 'done' (termasuk yang di-update manual dari web)
-- Jalankan manual di VPS:
--   docker exec -it moban-db psql -U postgres -d moban -f file.sql
--   atau: psql $DATABASE_URL -f file.sql
-- =============================================================================
-- Definisi:
--   t0 = cases.created_at
--        (waktu POST /api/cases mengirim pesan root ke grup)
--   t1 = MAX(wa_messages.created_at) WHERE from_me = false AND case_id = c.id
--        (balasan solver TERAKHIR yang ter-link ke case, isi pesan diabaikan)
--   resolution_jam = (t1 - t0) / 3600
--
-- Kenapa TERAKHIR, bukan pertama (beda dengan response-time-solver.sql):
--   Alur nyata di lapangan — solver membalas "bisa dicek lagi rekan" (tanpa
--   keyword done, status tetap in_progress), lalu admin menutup case manual di
--   web. Case itu IKUT dihitung, tapi titik akhirnya adalah waktu balasan solver
--   terakhir tersebut — BUKAN timestamp klik admin (update manual tidak pernah
--   menulis ke wa_messages).
--
-- Syarat: cases.status = 'done' (dari keyword solver MAUPUN update manual web).
-- Case done TANPA balasan solver sama sekali tidak punya titik akhir -> tidak
-- ikut dihitung; muncul di query 4.
--
-- Otomatis ter-exclude (from_me = true): pesan bot, reminder, "terimakasih",
-- update status manual dari web.
--
-- Catatan data historis (crawl): pesan lama yang masuk lewat /api/crawl
-- tersimpan dengan created_at = waktu crawl (bukan waktu asli pesan WA),
-- jadi latencynya bisa lebih besar dari kenyataan. Data realtime via webhook akurat.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1) RINGKASAN ALL-TIME (avg / min / max / median, dalam JAM)
-- ---------------------------------------------------------------------------
WITH last_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id,
           author,
           author_name,
           created_at AS last_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at DESC
),
latency AS (
    SELECT c.id,
           c.case_code,
           c.status,
           lr.author,
           lr.author_name,
           lr.last_reply_at,
           EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0 AS resolution_jam
    FROM cases c
    JOIN last_reply lr ON lr.case_id = c.id
    WHERE c.deleted_at IS NULL
      AND c.status = 'done'
      AND lr.last_reply_at >= c.created_at   -- buang latensi negatif (data kacau)
)
SELECT
    COUNT(*)                                            AS total_cases,
    ROUND(AVG(resolution_jam), 2)                       AS avg_jam,
    ROUND(MIN(resolution_jam), 2)                       AS min_jam,
    ROUND(MAX(resolution_jam), 2)                       AS max_jam,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resolution_jam))::numeric, 2) AS median_jam
FROM latency;


-- ---------------------------------------------------------------------------
-- 2) BREAKDOWN PER SOLVER — dikreditkan ke penulis balasan TERAKHIR
-- ---------------------------------------------------------------------------
WITH last_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, author, author_name, created_at AS last_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at DESC
)
SELECT
    COALESCE(lr.author_name, lr.author)                 AS solver,
    COUNT(*)                                            AS jumlah_case,
    ROUND(AVG(EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0), 2) AS avg_jam,
    ROUND(MIN(EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0), 2) AS min_jam,
    ROUND(MAX(EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0), 2) AS max_jam
FROM cases c
JOIN last_reply lr ON lr.case_id = c.id
WHERE c.deleted_at IS NULL
  AND c.status = 'done'
  AND lr.last_reply_at >= c.created_at
GROUP BY COALESCE(lr.author_name, lr.author)
ORDER BY avg_jam ASC;


-- ---------------------------------------------------------------------------
-- 3) DISTRIBUSI (jam) — <1j, 1-6j, 6-24j, >24j
-- ---------------------------------------------------------------------------
WITH last_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, created_at AS last_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at DESC
),
latency AS (
    SELECT EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0 AS h
    FROM cases c
    JOIN last_reply lr ON lr.case_id = c.id
    WHERE c.deleted_at IS NULL
      AND c.status = 'done'
      AND lr.last_reply_at >= c.created_at
)
SELECT
    CASE
        WHEN h < 1    THEN 'a. < 1 jam'
        WHEN h < 6    THEN 'b. 1 - 6 jam'
        WHEN h < 24   THEN 'c. 6 - 24 jam'
        ELSE               'd. > 24 jam'
    END                                                 AS bucket,
    COUNT(*)                                            AS jumlah_case,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS persen
FROM latency
GROUP BY 1
ORDER BY 1;


-- ---------------------------------------------------------------------------
-- 4) WATCHLIST: belum done ATAU done tanpa balasan solver sama sekali
--    (tidak masuk rata-rata, tapi bagus dipantau — status 'done' di sini
--     berarti case ditutup tanpa jejak balasan solver, perlu dicek admin)
-- ---------------------------------------------------------------------------
SELECT
    c.id,
    c.case_code,
    c.status,
    g.name AS group_name,
    c.created_at,
    ROUND(EXTRACT(EPOCH FROM (now() - c.created_at)) / 60.0, 1) AS menit_sejak_kirim
FROM cases c
LEFT JOIN wa_groups g ON g.id = c.group_id   -- LEFT: case legacy tanpa grup ikut tampil
WHERE c.deleted_at IS NULL
  AND ( c.status != 'done'
        OR NOT EXISTS (SELECT 1 FROM wa_messages w
                       WHERE w.case_id = c.id AND w.from_me = false) )
ORDER BY c.created_at ASC;


-- ---------------------------------------------------------------------------
-- 5) DETAIL PER CASE (audit) — satu baris per case done yang dihitung
-- ---------------------------------------------------------------------------
WITH last_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, author, author_name, created_at AS last_reply_at
    FROM wa_messages
    WHERE from_me = false
      AND case_id IS NOT NULL
    ORDER BY case_id, created_at DESC
)
SELECT
    c.id,
    c.case_code,
    c.status,
    g.name                AS group_name,
    c.created_at          AS kirim_case_at,
    COALESCE(lr.author_name, lr.author) AS solver_terakhir,
    lr.last_reply_at      AS balasan_terakhir_at,
    ROUND(EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0, 2) AS resolution_jam
FROM cases c
JOIN last_reply lr ON lr.case_id = c.id
LEFT JOIN wa_groups g ON g.id = c.group_id   -- LEFT: case legacy tanpa grup ikut tampil
WHERE c.deleted_at IS NULL
  AND c.status = 'done'
  AND lr.last_reply_at >= c.created_at
ORDER BY c.created_at ASC;