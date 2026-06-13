// ================================================================
// GridSense — Neo4j Seed
// 2 GSPs → 10 Substations → 40 Transformers → 200 SmartMeters
// IDEMPOTENT: all writes use MERGE, never CREATE (Trap 8 fix)
// Running this file twice produces zero duplicates
// ================================================================


// ── CONSTRAINTS ─────────────────────────────────────────────────
// Neo4j 5 syntax. Creates unique index on each ID field.
// MERGE depends on these to detect existing nodes efficiently.

CREATE CONSTRAINT gsp_id_unique IF NOT EXISTS
FOR (n:GridSupplyPoint) REQUIRE n.gsp_id IS UNIQUE;

CREATE CONSTRAINT substation_id_unique IF NOT EXISTS
FOR (n:Substation) REQUIRE n.substation_id IS UNIQUE;

CREATE CONSTRAINT transformer_asset_id_unique IF NOT EXISTS
FOR (n:Transformer) REQUIRE n.asset_id IS UNIQUE;

CREATE CONSTRAINT smartmeter_meter_id_unique IF NOT EXISTS
FOR (n:SmartMeter) REQUIRE n.meter_id IS UNIQUE;


// ── GRID SUPPLY POINTS (2) ───────────────────────────────────────

MERGE (g:GridSupplyPoint {gsp_id: 'GSP_NORTH'})
ON CREATE SET g.name       = 'Northern Grid Supply Point',
              g.voltage_kV = 132,
              g.region     = 'North';

MERGE (g:GridSupplyPoint {gsp_id: 'GSP_SOUTH'})
ON CREATE SET g.name       = 'Southern Grid Supply Point',
              g.voltage_kV = 132,
              g.region     = 'South';


// ── SUBSTATIONS (10) ─────────────────────────────────────────────
// SS_001–SS_005 will be fed by GSP_NORTH
// SS_006–SS_010 will be fed by GSP_SOUTH

UNWIND [
  {id:'SS_001', name:'Northgate Primary',  voltage_kV:11, lat:51.52, lon:-1.21, year:2005},
  {id:'SS_002', name:'Riverside Primary',  voltage_kV:11, lat:51.53, lon:-1.19, year:2008},
  {id:'SS_003', name:'Eastfield Primary',  voltage_kV:11, lat:51.51, lon:-1.17, year:2003},
  {id:'SS_004', name:'Westpark Primary',   voltage_kV:33, lat:51.50, lon:-1.23, year:2010},
  {id:'SS_005', name:'Central Primary',    voltage_kV:11, lat:51.52, lon:-1.20, year:2001},
  {id:'SS_006', name:'Southgate Primary',  voltage_kV:11, lat:51.48, lon:-1.21, year:2007},
  {id:'SS_007', name:'Harbour Primary',    voltage_kV:11, lat:51.47, lon:-1.18, year:2009},
  {id:'SS_008', name:'Industrial Primary', voltage_kV:33, lat:51.49, lon:-1.25, year:2012},
  {id:'SS_009', name:'Meadow Primary',     voltage_kV:11, lat:51.46, lon:-1.22, year:2006},
  {id:'SS_010', name:'Hillside Primary',   voltage_kV:11, lat:51.45, lon:-1.20, year:2004}
] AS props
MERGE (s:Substation {substation_id: props.id})
ON CREATE SET s.name              = props.name,
              s.voltage_kV        = props.voltage_kV,
              s.lat               = props.lat,
              s.lon               = props.lon,
              s.commissioned_year = props.year;


// ── TRANSFORMERS (40, 4 per substation) ──────────────────────────
// TX_1–TX_4   → SS_001
// TX_5–TX_8   → SS_002
// TX_9–TX_12  → SS_003
// TX_13–TX_16 → SS_004
// TX_17–TX_20 → SS_005
// TX_21–TX_24 → SS_006
// TX_25–TX_28 → SS_007
// TX_29–TX_32 → SS_008
// TX_33–TX_36 → SS_009
// TX_37–TX_40 → SS_010

UNWIND [
  {id:'TX_1',  rating:250, mfr:'ABB',               model:'TrafoBloc-250', inst:'2018-03-15', insp:'2024-01-10'},
  {id:'TX_2',  rating:160, mfr:'Siemens',            model:'GEAFOL-160',    inst:'2016-07-22', insp:'2024-02-14'},
  {id:'TX_3',  rating:400, mfr:'Schneider Electric', model:'Minera-400',    inst:'2019-11-05', insp:'2023-11-20'},
  {id:'TX_4',  rating:100, mfr:'GE',                 model:'Prolec-100',    inst:'2015-04-30', insp:'2024-03-05'},
  {id:'TX_5',  rating:250, mfr:'Siemens',            model:'GEAFOL-250',    inst:'2017-09-12', insp:'2024-01-25'},
  {id:'TX_6',  rating:630, mfr:'ABB',                model:'TrafoBloc-630', inst:'2020-02-18', insp:'2024-04-01'},
  {id:'TX_7',  rating:160, mfr:'GE',                 model:'Prolec-160',    inst:'2014-06-09', insp:'2023-12-15'},
  {id:'TX_8',  rating:400, mfr:'Schneider Electric', model:'Minera-400',    inst:'2021-08-27', insp:'2024-05-10'},
  {id:'TX_9',  rating:250, mfr:'ABB',                model:'TrafoBloc-250', inst:'2016-01-14', insp:'2024-02-28'},
  {id:'TX_10', rating:160, mfr:'Siemens',            model:'GEAFOL-160',    inst:'2018-10-03', insp:'2023-10-30'},
  {id:'TX_11', rating:630, mfr:'GE',                 model:'Prolec-630',    inst:'2022-05-20', insp:'2024-06-15'},
  {id:'TX_12', rating:100, mfr:'Schneider Electric', model:'Minera-100',    inst:'2013-03-25', insp:'2024-01-05'},
  {id:'TX_13', rating:250, mfr:'Siemens',            model:'GEAFOL-250',    inst:'2019-07-08', insp:'2024-03-20'},
  {id:'TX_14', rating:400, mfr:'ABB',                model:'TrafoBloc-400', inst:'2017-12-15', insp:'2024-04-18'},
  {id:'TX_15', rating:160, mfr:'GE',                 model:'Prolec-160',    inst:'2015-09-22', insp:'2023-09-10'},
  {id:'TX_16', rating:630, mfr:'Schneider Electric', model:'Minera-630',    inst:'2021-04-11', insp:'2024-07-01'},
  {id:'TX_17', rating:250, mfr:'ABB',                model:'TrafoBloc-250', inst:'2016-08-19', insp:'2024-02-05'},
  {id:'TX_18', rating:160, mfr:'Siemens',            model:'GEAFOL-160',    inst:'2020-11-30', insp:'2024-05-22'},
  {id:'TX_19', rating:400, mfr:'GE',                 model:'Prolec-400',    inst:'2018-06-07', insp:'2024-01-18'},
  {id:'TX_20', rating:100, mfr:'Schneider Electric', model:'Minera-100',    inst:'2014-02-14', insp:'2023-11-05'},
  {id:'TX_21', rating:250, mfr:'Siemens',            model:'GEAFOL-250',    inst:'2017-05-25', insp:'2024-03-12'},
  {id:'TX_22', rating:630, mfr:'ABB',                model:'TrafoBloc-630', inst:'2022-09-14', insp:'2024-06-28'},
  {id:'TX_23', rating:160, mfr:'GE',                 model:'Prolec-160',    inst:'2015-12-01', insp:'2024-04-09'},
  {id:'TX_24', rating:400, mfr:'Schneider Electric', model:'Minera-400',    inst:'2019-03-18', insp:'2024-07-14'},
  {id:'TX_25', rating:250, mfr:'ABB',                model:'TrafoBloc-250', inst:'2016-10-05', insp:'2024-02-19'},
  {id:'TX_26', rating:160, mfr:'Siemens',            model:'GEAFOL-160',    inst:'2021-01-22', insp:'2024-08-03'},
  {id:'TX_27', rating:630, mfr:'GE',                 model:'Prolec-630',    inst:'2018-07-29', insp:'2023-12-28'},
  {id:'TX_28', rating:100, mfr:'Schneider Electric', model:'Minera-100',    inst:'2013-09-16', insp:'2024-01-31'},
  {id:'TX_29', rating:250, mfr:'Siemens',            model:'GEAFOL-250',    inst:'2020-04-08', insp:'2024-04-25'},
  {id:'TX_30', rating:400, mfr:'ABB',                model:'TrafoBloc-400', inst:'2017-11-17', insp:'2024-05-07'},
  {id:'TX_31', rating:160, mfr:'GE',                 model:'Prolec-160',    inst:'2019-08-24', insp:'2024-06-20'},
  {id:'TX_32', rating:630, mfr:'Schneider Electric', model:'Minera-630',    inst:'2022-02-03', insp:'2024-09-01'},
  {id:'TX_33', rating:250, mfr:'ABB',                model:'TrafoBloc-250', inst:'2015-05-12', insp:'2024-03-27'},
  {id:'TX_34', rating:160, mfr:'Siemens',            model:'GEAFOL-160',    inst:'2018-12-19', insp:'2024-07-08'},
  {id:'TX_35', rating:400, mfr:'GE',                 model:'Prolec-400',    inst:'2021-09-06', insp:'2024-08-15'},
  {id:'TX_36', rating:100, mfr:'Schneider Electric', model:'Minera-100',    inst:'2014-04-23', insp:'2023-10-12'},
  {id:'TX_37', rating:250, mfr:'Siemens',            model:'GEAFOL-250',    inst:'2016-11-10', insp:'2024-04-02'},
  {id:'TX_38', rating:630, mfr:'ABB',                model:'TrafoBloc-630', inst:'2023-03-27', insp:'2024-09-18'},
  {id:'TX_39', rating:160, mfr:'GE',                 model:'Prolec-160',    inst:'2019-06-15', insp:'2024-05-29'},
  {id:'TX_40', rating:400, mfr:'Schneider Electric', model:'Minera-400',    inst:'2020-08-02', insp:'2024-10-05'}
] AS props
MERGE (t:Transformer {asset_id: props.id})
ON CREATE SET t.rating_kVA      = props.rating,
              t.manufacturer    = props.mfr,
              t.model           = props.model,
              t.installed       = props.inst,
              t.last_inspection = props.insp;


// ── SMART METERS (200, 5 per transformer) ────────────────────────
// range(1,200) avoids writing 200 explicit MERGE blocks — no APOC required.
// meter_id : SM_1 through SM_200
// premise_id: PREM_1 through PREM_200
// tariff_class: SM_1–120 residential | 121–180 commercial | 181–200 industrial
// phase: n % 3 = 0 → three-phase, else single

UNWIND range(1, 200) AS n
MERGE (m:SmartMeter {meter_id: 'SM_' + toString(n)})
ON CREATE SET
  m.premise_id   = 'PREM_' + toString(n),
  m.tariff_class = CASE
                     WHEN n <= 120 THEN 'residential'
                     WHEN n <= 180 THEN 'commercial'
                     ELSE 'industrial'
                   END,
  m.phase        = CASE WHEN n % 3 = 0 THEN 'three-phase' ELSE 'single' END;


// ── GSP → SUBSTATION :FEEDS ──────────────────────────────────────
// feeder_id is the identifying property in MERGE — prevents duplicate edges
// ON CREATE SET for other properties (voltage_kV, length_km)

MATCH (g:GridSupplyPoint {gsp_id: 'GSP_NORTH'})
UNWIND [
  {ss:'SS_001', fdr:'F_001', v:11, km:2.4},
  {ss:'SS_002', fdr:'F_002', v:11, km:3.1},
  {ss:'SS_003', fdr:'F_003', v:11, km:1.8},
  {ss:'SS_004', fdr:'F_004', v:33, km:5.2},
  {ss:'SS_005', fdr:'F_005', v:11, km:2.9}
] AS props
MATCH (s:Substation {substation_id: props.ss})
MERGE (g)-[r:FEEDS {feeder_id: props.fdr}]->(s)
ON CREATE SET r.voltage_kV = props.v, r.length_km = props.km;

MATCH (g:GridSupplyPoint {gsp_id: 'GSP_SOUTH'})
UNWIND [
  {ss:'SS_006', fdr:'F_006', v:11, km:3.5},
  {ss:'SS_007', fdr:'F_007', v:11, km:4.1},
  {ss:'SS_008', fdr:'F_008', v:33, km:6.0},
  {ss:'SS_009', fdr:'F_009', v:11, km:2.7},
  {ss:'SS_010', fdr:'F_010', v:11, km:3.3}
] AS props
MATCH (s:Substation {substation_id: props.ss})
MERGE (g)-[r:FEEDS {feeder_id: props.fdr}]->(s)
ON CREATE SET r.voltage_kV = props.v, r.length_km = props.km;


// ── SUBSTATION → TRANSFORMER :SUPPLIES ───────────────────────────
// tx_start gives the first TX number for that substation
// range(tx_start, tx_start+3) gives 4 transformers per substation

UNWIND [
  {ss:'SS_001', tx_start:1},  {ss:'SS_002', tx_start:5},
  {ss:'SS_003', tx_start:9},  {ss:'SS_004', tx_start:13},
  {ss:'SS_005', tx_start:17}, {ss:'SS_006', tx_start:21},
  {ss:'SS_007', tx_start:25}, {ss:'SS_008', tx_start:29},
  {ss:'SS_009', tx_start:33}, {ss:'SS_010', tx_start:37}
] AS mapping
MATCH (s:Substation {substation_id: mapping.ss})
UNWIND range(mapping.tx_start, mapping.tx_start + 3) AS tx_n
MATCH (t:Transformer {asset_id: 'TX_' + toString(tx_n)})
MERGE (s)-[r:SUPPLIES {cable_id: 'CBL_' + toString(tx_n)}]->(t)
ON CREATE SET r.distance_m = 150 + (tx_n * 10);


// ── TRANSFORMER → SMART METER :CONNECTS_TO ───────────────────────
// TX_n owns meters SM_(5n-4) through SM_(5n)
// TX_1  → SM_1..SM_5    (range: 1  to 5)
// TX_2  → SM_6..SM_10   (range: 6  to 10)
// TX_40 → SM_196..SM_200 (range: 196 to 200)

UNWIND range(1, 40) AS tx_n
MATCH (t:Transformer {asset_id: 'TX_' + toString(tx_n)})
UNWIND range((tx_n - 1) * 5 + 1, tx_n * 5) AS m_n
MATCH (m:SmartMeter {meter_id: 'SM_' + toString(m_n)})
MERGE (t)-[:CONNECTS_TO]->(m);


// ── ALTERNATIVE_FEED (normally-open tie switches) ─────────────────
// Models backup feed paths between adjacent substations.
// tie_switch_id is the identifying property — prevents duplicate edges.

MATCH (a:Substation {substation_id: 'SS_001'})
MATCH (b:Substation {substation_id: 'SS_002'})
MERGE (a)-[r:ALTERNATIVE_FEED {tie_switch_id: 'TS_001'}]->(b)
ON CREATE SET r.normally_open = true;

MATCH (a:Substation {substation_id: 'SS_003'})
MATCH (b:Substation {substation_id: 'SS_004'})
MERGE (a)-[r:ALTERNATIVE_FEED {tie_switch_id: 'TS_002'}]->(b)
ON CREATE SET r.normally_open = true;

MATCH (a:Substation {substation_id: 'SS_006'})
MATCH (b:Substation {substation_id: 'SS_007'})
MERGE (a)-[r:ALTERNATIVE_FEED {tie_switch_id: 'TS_003'}]->(b)
ON CREATE SET r.normally_open = true;

MATCH (a:Substation {substation_id: 'SS_005'})
MATCH (b:Substation {substation_id: 'SS_009'})
MERGE (a)-[r:ALTERNATIVE_FEED {tie_switch_id: 'TS_004'}]->(b)
ON CREATE SET r.normally_open = true;


// ── NORMALIZE node_id PROPERTY ───────────────────────────────────
// grid.py (Trap 4/6 fixes) matches nodes generically via {node_id: $id}.
// Each label uses a domain-specific identifier (gsp_id, substation_id,
// asset_id, meter_id) — this copies that value into a common node_id
// property so the API can MATCH any node type uniformly.
// Idempotent: SET to the same value twice is harmless — no duplicates.

MATCH (n:GridSupplyPoint) SET n.node_id = n.gsp_id;
MATCH (n:Substation)      SET n.node_id = n.substation_id;
MATCH (n:Transformer)     SET n.node_id = n.asset_id;
MATCH (n:SmartMeter)      SET n.node_id = n.meter_id;