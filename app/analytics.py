"""Household activity counts derived from audited actions and explicit searches."""

from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query


MOVEMENTS = frozenset({'create', 'move', 'take_out', 'return', 'lend', 'consume', 'restock', 'discard', 'lost', 'archive', 'restore'})


def initialize_analytics(connect):
    with closing(connect()) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS search_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_id INTEGER UNIQUE REFERENCES logs(id),
                query TEXT NOT NULL,
                source TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_event_items (
                event_id INTEGER NOT NULL REFERENCES search_events(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES items(id),
                PRIMARY KEY(event_id,item_id)
            );
            CREATE INDEX IF NOT EXISTS search_events_time ON search_events(created_at);
            CREATE INDEX IF NOT EXISTS search_event_items_item ON search_event_items(item_id);
            CREATE INDEX IF NOT EXISTS logs_created_at ON logs(created_at);
        ''')


def record_search(db, query, item_ids=(), *, source='web', log_id=None, actor=None, created_at=None):
    """Called within the caller's transaction; repeated IDs count once per query."""
    query = str(query).strip()[:200]
    if not query:
        return None
    if source not in {'web', 'ai', 'external'}:
        raise ValueError('Invalid search source')
    if log_id is not None and db.execute('SELECT id FROM search_events WHERE log_id=?', (log_id,)).fetchone():
        return None
    actor = actor or {}
    created_at = created_at or datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    cursor = db.execute(
        'INSERT INTO search_events(log_id,query,source,actor_user_id,actor_name,created_at) VALUES (?,?,?,?,?,?)',
        (log_id, query, source, actor.get('id'), actor.get('display_name') or actor.get('username') or '', created_at),
    )
    identities = {identity for identity in item_ids if type(identity) is int and identity > 0}
    db.executemany(
        'INSERT OR IGNORE INTO search_event_items(event_id,item_id) SELECT ?,id FROM items WHERE id=?',
        [(cursor.lastrowid, identity) for identity in sorted(identities)],
    )
    return cursor.lastrowid


def _local_date(value, tz):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(tz).date()
    except (ValueError, TypeError, AttributeError):
        return None


def activity(db, *, days=90, timezone_name='Asia/Shanghai', today=None):
    tz = ZoneInfo(timezone_name)
    end = today or datetime.now(tz).date()
    if not isinstance(end, date) or not 7 <= days <= 366:
        raise ValueError('Invalid activity range')
    start = end - timedelta(days=days - 1)
    # Include a one-day UTC margin before local aggregation (including DST).
    lower = (start - timedelta(days=1)).isoformat()
    upper = (end + timedelta(days=2)).isoformat()
    daily = {(start + timedelta(days=index)).isoformat(): {'movements': 0, 'searches': 0} for index in range(days)}
    queries = Counter()
    event_ids = []
    sources = Counter()
    db.execute('SAVEPOINT analytics_read')
    try:
        rows = db.execute('SELECT action,note,created_at,id FROM logs WHERE created_at>=? AND created_at<?', (lower, upper)).fetchall()
        linked_logs = {row['log_id'] for row in db.execute('SELECT log_id FROM search_events WHERE log_id IS NOT NULL AND created_at>=? AND created_at<?', (lower, upper))}
        for row in rows:
            day = _local_date(row['created_at'], tz)
            key = day.isoformat() if day is not None else ''
            if key not in daily:
                continue
            if row['action'] in MOVEMENTS:
                daily[key]['movements'] += 1
            elif row['action'] == 'search' and row['id'] not in linked_logs:
                # Historical searches can be counted, but their result items
                # cannot be reconstructed reliably after later inventory edits.
                daily[key]['searches'] += 1
                queries[row['note']] += 1
                sources['legacy'] += 1
        for row in db.execute('SELECT id,query,source,created_at FROM search_events WHERE created_at>=? AND created_at<?', (lower, upper)):
            day = _local_date(row['created_at'], tz)
            key = day.isoformat() if day is not None else ''
            if key in daily:
                daily[key]['searches'] += 1
                queries[row['query']] += 1
                sources[row['source']] += 1
                event_ids.append(row['id'])
        item_counts = Counter()
        # Chunk to stay below SQLite's bound variable limit with a busy household.
        for offset in range(0, len(event_ids), 500):
            chunk = event_ids[offset:offset + 500]
            placeholders = ','.join('?' for _ in chunk)
            for row in db.execute(f'SELECT item_id,COUNT(*) count FROM search_event_items WHERE event_id IN ({placeholders}) GROUP BY item_id', chunk):
                item_counts[row['item_id']] += row['count']
        top_items = []
        for identity, count in sorted(item_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]:
            row = db.execute('SELECT name FROM items WHERE id=?', (identity,)).fetchone()
            if row:
                top_items.append({'id': identity, 'name': row['name'], 'search_count': count})
    finally:
        db.execute('RELEASE SAVEPOINT analytics_read')
    return {
        'days': days, 'timezone': timezone_name, 'from': start.isoformat(), 'to': end.isoformat(),
        'daily': [{'date': key, **value} for key, value in daily.items()],
        'totals': {metric: sum(day[metric] for day in daily.values()) for metric in ('movements', 'searches')},
        'top_items': top_items,
        'top_queries': [{'query': query, 'count': count} for query, count in queries.most_common(10)],
        'search_sources': dict(sources),
        'definition': '调动按登记、存取、移动、借还、消耗及归档等操作次数统计，非物品数量；搜索按明确提交的查询次数统计。',
        'history_note': '旧搜索记录仅保留查询次数；物品被搜索次数从升级后记录，表示该物品曾出现在搜索结果中。',
    }


def build_analytics_router(services):
    router = APIRouter(prefix='/api/analytics', tags=['activity'])

    @router.get('/activity')
    def get_activity(days: int = Query(default=90, ge=7, le=366)):
        with closing(services['connect']()) as db:
            return activity(db, days=days, timezone_name=services['timezone'], today=services['today']())

    return router
