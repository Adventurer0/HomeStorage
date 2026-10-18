from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analytics import activity, initialize_analytics, record_search
from app.db import connect, initialize, transaction


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='homestorage-activity-')))
        self.path = self.root / 'inventory.sqlite3'
        initialize(self.path)
        initialize_analytics(lambda: connect(self.path))
        self.db = self.enterContext(closing(connect(self.path)))
        self.db.execute("INSERT INTO items(id,name,location_id,stored_date,created_at,updated_at) VALUES (1,'数据线',1,'2026-10-17','2026-10-17T00:00:00Z','2026-10-17T00:00:00Z')")
        self.db.commit()

    def log(self, action, at, note=''):
        return self.db.execute('INSERT INTO logs(action,note,created_at) VALUES (?,?,?)', (action, note, at)).lastrowid

    def test_empty_days_and_local_timezone_boundaries_exclude_future_and_old_data(self):
        with transaction(self.db):
            self.log('move', '2026-10-16T16:00:00Z')
            self.log('restock', '2026-10-17T15:59:59Z')
            self.log('take_out', '2026-10-17T16:00:00Z')
            self.log('move', '2026-08-01T00:00:00Z')
            self.log('update', '2026-10-17T00:00:00Z')
        result = activity(self.db, days=7, today=date(2026, 10, 17))
        self.assertEqual(len(result['daily']), 7)
        self.assertEqual(result['daily'][-1], {'date': '2026-10-17', 'movements': 2, 'searches': 0})
        self.assertEqual(result['totals'], {'movements': 2, 'searches': 0})

    def test_linked_and_legacy_searches_are_not_double_counted_and_hits_are_deduplicated(self):
        with transaction(self.db):
            linked = self.log('search', '2026-10-17T00:00:00Z', '数据线')
            self.log('search', '2026-10-17T01:00:00Z', '旧搜索')
            record_search(self.db, '数据线', [1, 1, 999], log_id=linked, created_at='2026-10-17T00:00:00Z')
            record_search(self.db, '数据线', [1], log_id=linked, created_at='2026-10-17T00:00:00Z')
            record_search(self.db, '在哪里', [1], source='ai', created_at='2026-10-17T02:00:00Z')
        result = activity(self.db, days=7, today=date(2026, 10, 17))
        self.assertEqual(result['totals']['searches'], 3)
        self.assertEqual(result['top_items'], [{'id': 1, 'name': '数据线', 'search_count': 2}])
        self.assertEqual(result['search_sources'], {'legacy': 1, 'web': 1, 'ai': 1})

    def test_search_event_rolls_back_with_the_audit_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.db):
                linked = self.log('search', '2026-10-17T00:00:00Z', '测试')
                record_search(self.db, '测试', [1], log_id=linked)
                raise RuntimeError('cancel')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM search_events').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM logs').fetchone()[0], 0)

    def test_search_hits_preserve_event_names_not_recomputed_from_later_query_results(self):
        with transaction(self.db):
            record_search(self.db, '数据线', [1], created_at='2026-10-17T00:00:00Z')
            self.db.execute("UPDATE items SET name='改名后的线材' WHERE id=1")
        result = activity(self.db, days=7, today=date(2026, 10, 17))
        self.assertEqual(result['top_items'][0]['name'], '改名后的线材')
        self.assertEqual(result['top_items'][0]['search_count'], 1)
