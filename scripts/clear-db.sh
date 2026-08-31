#!/bin/bash
# Clear all transactional data (keep reference tables)
# Usage: bash scripts/clear-db.sh
#
# Tables CLEARED: cases, progress_updates, wa_messages, reminder_log
# Tables KEPT (reference): areas, regionals, sumber_tickets, jenis_cases, solver_contacts
#
# ⚠️  Make sure you run backup-db.sh FIRST!

set -e

echo "=== Clear Database ==="
echo ""
echo "This will DELETE all data from:"
echo "  - cases"
echo "  - progress_updates"
echo "  - wa_messages"
echo "  - reminder_log"
echo ""
echo "Reference tables KEPT:"
echo "  - areas, regionals"
echo "  - sumber_tickets, jenis_cases"
echo "  - solver_contacts"
echo ""

# Disable triggers temporarily for faster truncation
docker exec moban-db psql -U postgres -d moban -c "
-- Reset sequences
ALTER SEQUENCE IF EXISTS cases_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS progress_updates_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS wa_messages_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS reminder_log_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS solver_contacts_id_seq RESTART WITH 1;

-- Clear transactional data
TRUNCATE TABLE reminder_log CASCADE;
TRUNCATE TABLE progress_updates CASCADE;
TRUNCATE TABLE wa_messages CASCADE;
TRUNCATE TABLE cases CASCADE;
"

# Verify
echo ""
echo "=== Remaining data ==="
echo ""
docker exec moban-db psql -U postgres -d moban -c "
SELECT 'cases' as table_name, COUNT(*) as rows FROM cases
UNION ALL SELECT 'progress_updates', COUNT(*) FROM progress_updates
UNION ALL SELECT 'wa_messages', COUNT(*) FROM wa_messages
UNION ALL SELECT 'reminder_log', COUNT(*) FROM reminder_log
UNION ALL SELECT 'areas', COUNT(*) FROM areas
UNION ALL SELECT 'regionals', COUNT(*) FROM regionals
UNION ALL SELECT 'sumber_tickets', COUNT(*) FROM sumber_tickets
UNION ALL SELECT 'jenis_cases', COUNT(*) FROM jenis_cases
UNION ALL SELECT 'solver_contacts', COUNT(*) FROM solver_contacts;
"

echo ""
echo "=== Clear Complete ==="
echo "Sequences reset to 1. Database ready for fresh input."
