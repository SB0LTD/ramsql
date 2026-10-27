"""Independent workload model and portable, versioned binary replay format.

Each trace cycle restores its initial state. Engines can repeat the *same*
checked distribution for a duration without turning updates into no-ops.
"""
import hashlib
import random
import struct
from pathlib import Path

MAGIC = b'RAMTRC01'
MASK = (1 << 64) - 1
CASES = ('uniform_get', 'hotspot_get', 'range_page', 'sort_score',
         'filtered_sum', 'grouped', 'filtered_group', 'mixed_95_5',
         'mixed_50_50', 'churn')


def mix(h, value):
    return ((h * 1099511628211) ^ (value & MASK)) & MASK


def digest(rows, columns):
    h = mix(len(rows), columns)
    for row in rows:
        for value in row:
            if value is None:
                h = mix(h, 0)
            elif isinstance(value, int):
                h = mix(mix(h, 1), value)
            else:
                raw = value.encode('utf-8')
                h = mix(mix(h, 2), len(raw))
                for byte in raw:
                    h = mix(h, byte)
    return h


def row(i, groups, width, seed):
    label = None if i % 17 == 0 else (f'item_{i}_' + 'x' * width)[:width]
    return (i, (i * 48271 + seed) % 100003, i % groups, label)


def requests(name, rows, seed, count):
    """SQL and expected results; Python evaluates the model, never an engine."""
    rng = random.Random(seed)
    data = {r[0]: r for r in rows}
    initial = dict(data)
    n = len(data)
    out = []

    def read(sql, values, columns):
        out.append((1, len(values), columns, digest(values, columns), sql))

    def update(i, value):
        old = data[i]
        assert old[1] != value, 'write trace must change a value'
        data[i] = (i, value, old[2], old[3])
        out.append((2, 1, 0, 0, f'UPDATE bench SET value={value} WHERE id={i}'))

    def get(i):
        values = [(data[i][0], data[i][1], data[i][3])] if i in data else []
        read(f'SELECT id,value,payload FROM bench WHERE id={i}', values, 3)

    if name in ('uniform_get', 'hotspot_get'):
        for _ in range(count):
            keyspace = max(1, n // 100) if name == 'hotspot_get' and rng.random() < .9 else n
            get(rng.randrange(keyspace))
    elif name == 'range_page':
        ordered = sorted(rows)
        for _ in range(count):
            start = rng.randrange(n)
            read(f'SELECT id,value FROM bench WHERE id>={start} ORDER BY id LIMIT 32',
                 [(r[0], r[1]) for r in ordered[start:start+32]], 2)
    elif name == 'sort_score':
        values = [(r[0], r[1]) for r in sorted(rows, key=lambda r: (r[1], r[0]))[:32]]
        read('SELECT id,value FROM bench ORDER BY value,id LIMIT 32', values, 2)
    elif name in ('filtered_sum', 'grouped', 'filtered_group'):
        for _ in range(min(count, 16)):
            group = rng.randrange(max(r[2] for r in rows) + 1)
            if name == 'filtered_sum':
                selected = [r for r in rows if r[2] == group]
                read(f'SELECT COUNT(*),SUM(value) FROM bench WHERE category={group}',
                     [(len(selected), sum(r[1] for r in selected) if selected else None)], 2)
            else:
                selected = rows if name == 'grouped' else [r for r in rows if r[2] < group]
                sums = {}
                for r in selected:
                    size, total = sums.get(r[2], (0, 0))
                    sums[r[2]] = (size+1, total+r[1])
                where = '' if name == 'grouped' else f' WHERE category<{group}'
                read('SELECT category,COUNT(*),SUM(value) FROM bench'+where+' GROUP BY category ORDER BY category',
                     [(g, *sums[g]) for g in sorted(sums)], 3)
    elif name in ('mixed_95_5', 'mixed_50_50'):
        # Pair each mutation with a genuine restoration, with reads between.
        reads = 38 if name == 'mixed_95_5' else 2
        for _ in range(max(1, count // (reads+2))):
            i = rng.randrange(n)
            old = data[i][1]
            update(i, old+1)
            get(i)  # observe the changed value, not only affected-row count
            for _ in range(reads-1):
                get(rng.randrange(n))
            update(i, old)
    elif name == 'churn':
        for _ in range(max(1, count // 4)):
            i = rng.randrange(n)
            old = data.pop(i)
            out.append((2, 1, 0, 0, f'DELETE FROM bench WHERE id={i}'))
            get(i)
            payload = 'NULL' if old[3] is None else "'"+old[3]+"'"
            out.append((2, 1, 0, 0, f'INSERT INTO bench VALUES ({i},{old[1]},{old[2]},{payload})'))
            data[i] = old
            get(i)
    else:
        raise ValueError(name)
    assert data == initial, 'cycle did not restore its initial state'
    return out


def write_fixture(path, *, n, groups, width, seed, case, count=1024):
    if not (64 <= n <= 10000000 and 1 <= groups <= min(n, 2048) and 1 <= width <= 512 and count >= 1):
        raise ValueError('invalid fixture capacity')
    data = [row(i, groups, width, seed) for i in range(n)]
    queries = requests(case, data, seed, count)
    checks = [(1, 1, 3, digest([(n, sum(r[1] for r in data), sum(r[3] is not None for r in data))], 3),
               'SELECT COUNT(*),SUM(value),COUNT(payload) FROM bench WHERE id+0>=0')]
    random.Random(seed ^ 0x9e3779b9).shuffle(data)
    path = Path(path)
    with path.open('wb') as file:
        file.write(MAGIC + struct.pack('<IIIIIQ', n, groups, width, len(queries), len(checks), seed))
        for i, value, group, text in data:
            raw = None if text is None else text.encode()
            file.write(struct.pack('<iiii', i, value, group, -1 if raw is None else len(raw)))
            if raw is not None:
                file.write(raw)
        for kind, result_rows, columns, wanted, sql in queries + checks:
            raw = sql.encode()
            file.write(struct.pack('<IIIIQ', kind, len(raw), result_rows, columns, wanted) + raw)
    return dict(case=case, rows=n, groups=groups, payload_bytes=width, seed=seed,
                requests=len(queries), reads=sum(q[0] == 1 for q in queries),
                writes=sum(q[0] == 2 for q in queries), layout='seeded Fisher-Yates permutation',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read_fixture(path):
    """Strict decoder used for independent SQL-reference verification."""
    with Path(path).open('rb') as file:
        def exact(n):
            data=file.read(n)
            if len(data)!=n:raise ValueError('truncated fixture')
            return data
        if exact(8)!=MAGIC:raise ValueError('bad magic')
        n,groups,width,count,checks,seed=struct.unpack('<IIIIIQ',exact(28))
        if not (64<=n<=10000000 and 1<=groups<=2048 and 1<=width<=512 and 1<=count<=65536 and 1<=checks<=16):
            raise ValueError('invalid fixture header')
        rows=[]
        for _ in range(n):
            i,value,group,length=struct.unpack('<iiii',exact(16))
            if not -1<=length<=width:raise ValueError('bad text length')
            rows.append((i,value,group,None if length<0 else exact(length).decode('ascii')))
        queries=[]
        for _ in range(count+checks):
            kind,size,result_rows,columns,wanted=struct.unpack('<IIIIQ',exact(24))
            if kind not in (1,2) or not 1<=size<=65536 or result_rows>2048 or columns>4:
                raise ValueError('bad query record')
            queries.append((kind,result_rows,columns,wanted,exact(size).decode('utf-8')))
        if file.read(1):raise ValueError('trailing fixture bytes')
        return rows,queries[:count],queries[count:]
