import hashlib,json,pathlib,sys
sys.path.insert(0,'tools')
from test_server import Server,PG,errors,rows
binary=pathlib.Path('.sig-cache/dbmeter-directory-fast/bin/ramsql.exe').resolve()
schema=[
 'CREATE TABLE regions(id INT PRIMARY KEY,name TEXT NOT NULL)',
 'CREATE TABLE stores(id INT PRIMARY KEY,region_id INT NOT NULL,name TEXT NOT NULL)',
 'CREATE TABLE categories(id INT PRIMARY KEY,name TEXT NOT NULL)',
 'CREATE TABLE products(id INT PRIMARY KEY,category_id INT NOT NULL,unit_price_cents INT NOT NULL)',
 'CREATE TABLE customers(id INT PRIMARY KEY,segment TEXT NOT NULL)',
 'CREATE TABLE sales(id BIGINT PRIMARY KEY,product_id INT NOT NULL,store_id INT NOT NULL,customer_id INT NOT NULL,sale_date DATE NOT NULL,quantity INT NOT NULL,discount_pct INT NOT NULL,amount_cents BIGINT NOT NULL)']
report=dict(schema=1,method='Empty-table SQL capability probe only; no five-million-row dataset, expected CSV check or throughput claim. Schema definitions adapted to memory mode, no schema namespace or generator SQL. Original query text retained except bound store_id=1.',binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),schema_statements=schema,queries=[],isolated_features=[])
with Server(binary,max_clients=2) as server:
 pg=PG(server.raw());pg.startup()
 try:
  for source in schema:pg.ok(source)
  for path in sorted(pathlib.Path('../dbMeter/examples/sales-agg/sql').glob('q*.sql')):
   raw=path.read_bytes();source=raw.decode().replace(':{store_id:int}','1')
   messages=pg.query(source);failures=errors(messages)
   report['queries'].append(dict(file=path.name,original_sha256=hashlib.sha256(raw).hexdigest(),errors=failures,empty_rows=rows(messages)))
   print(path.name,failures or 'accepted on empty tables',flush=True)
  for name,source in [
   ('date_format',"SELECT to_char(DATE '2025-01-01','YYYY-MM')"),
   ('numeric_round','SELECT ROUND(12.345,2)'),
   ('window_rank','SELECT RANK() OVER (ORDER BY id) FROM sales'),
   ('window_sum','SELECT SUM(id) OVER () FROM sales'),
   ('rollup','SELECT store_id,COUNT(*) FROM sales GROUP BY ROLLUP(store_id)')]:
   messages=pg.query(source)
   report['isolated_features'].append(dict(name=name,sql=source,errors=errors(messages)))
 finally:pg.close()
out=pathlib.Path('benchmarks/dbmeter-reviewer-20261027/evidence/full-suite-empty-feature-probe.json')
out.write_text(json.dumps(report,indent=2)+'\n')
