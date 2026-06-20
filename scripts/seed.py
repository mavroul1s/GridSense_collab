# scripts/seed.py
# GridSense — seed all 5 databases with test data
#
# Run from repo root after stack is up:
#   docker exec -it gridsense_api python scripts/seed.py
#
# IDEMPOTENT: running twice produces no duplicates
# Cassandra: 50,000 readings across 20 sensor IDs
# MongoDB:   30 equipment records (3 schema shapes)
# PostgreSQL: 100 consumer accounts + 1 invoice per account
# Neo4j:     verified only — seeded by neo4j-init container
# Redis:     5 test alerts pushed to buffer

import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# ── Load environment ──────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── DB modules ────────────────────────────────────────────────────
import sys
sys.path.insert(0, "/app")   # inside the api container WORKDIR

import db.cassandra  as cassandra_db
import db.neo4j      as neo4j_db
import db.mongo      as mongo_db
import db.postgres   as postgres_db
import db.redis      as redis_db

from db.cassandra import execute_async
from db.neo4j     import run_query, run_write
from db.mongo     import equipment_collection
from db.postgres  import execute, fetch_one, execute_transaction
from db.redis     import push_alert_to_buffer, publish_alert
from datetime import datetime, timezone, timedelta, date

# ─────────────────────────────────────────────────────────────────
# CASSANDRA — 50,000 readings across 20 sensors
# 2,500 readings per sensor × 4 metric types = 10,000 writes per sensor
# Written to BOTH tables (sensor_readings + sensor_readings_by_time)
# ─────────────────────────────────────────────────────────────────
SENSOR_IDS   = [f"SENSOR_{i:03d}" for i in range(1, 21)]   # SENSOR_001 … SENSOR_020
METRIC_TYPES = ["voltage", "current", "power_factor", "temperature"]

# Realistic value ranges per metric type
METRIC_RANGES = {
    "voltage":      (215.0, 245.0),
    "current":      (0.5,   50.0),
    "power_factor": (0.80,  1.00),
    "temperature":  (15.0,  65.0),
}

METRIC_UNITS = {
    "voltage":      "V",
    "current":      "A",
    "power_factor": "pf",
    "temperature":  "degC",
}


async def seed_cassandra():
    print("\n[Cassandra] Seeding 50,000 sensor readings...")

    # Base timestamp: 90 days ago, stepping forward by 1 minute per reading
    base_time = datetime(2026, 3, 1, tzinfo=timezone.utc)  # fixed, not datetime.now()
    readings_per_sensor = 2500   # 2500 timestamps × 4 metrics × 20 sensors = 200,000 writes
                                  # but 2500 × 20 = 50,000 unique timestamp+sensor combos

    total = 0

    for sensor_id in SENSOR_IDS:
        for i in range(readings_per_sensor):
            reading_time = base_time + timedelta(minutes=i)
            time_bucket  = reading_time.strftime('%Y-%m-%dT%H:%M')

            for metric_type in METRIC_TYPES:
                lo, hi = METRIC_RANGES[metric_type]
                value  = round(random.uniform(lo, hi), 3)
                unit   = METRIC_UNITS[metric_type]

                # Write 1: sensor_readings (per-sensor query pattern)
                await execute_async(
                    """
                    INSERT INTO sensor_readings
                        (sensor_id, reading_time, metric_type, value, unit, quality_flag)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (sensor_id, reading_time, metric_type, value, unit, 0)
                )

                # Write 2: sensor_readings_by_time (dashboard query pattern)
                await execute_async(
                    """
                    INSERT INTO sensor_readings_by_time
                        (time_bucket, reading_time, sensor_id, metric_type, value)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (time_bucket, reading_time, sensor_id, metric_type, value)
                )

                total += 1

        print(f"  {sensor_id} — {readings_per_sensor * len(METRIC_TYPES):,} writes done")

    print(f"[Cassandra] Done — {total:,} total writes across 2 tables")


# ─────────────────────────────────────────────────────────────────
# MONGODB — 30 equipment records (3 schema shapes × 10 each)
# Shape 1: Transformer       (TX_1  … TX_10)
# Shape 2: SmartMeter        (SM_1  … SM_10)
# Shape 3: ProtectionRelay   (RELAY_1 … RELAY_10)
# IDEMPOTENT: upsert on asset_id
# ─────────────────────────────────────────────────────────────────
TRANSFORMER_MODELS  = ["TrafoBloc-250", "GEAFOL-160", "Minera-400", "Prolec-100", "TrafoBloc-630"]
METER_MODELS        = ["E360", "E650", "DLMS-Pro", "SmartOne-3P", "NetZero-II"]
RELAY_MODELS        = ["SEL-351S", "ABB-REF615", "Siemens-7SJ85", "GE-D60", "Schneider-P14N"]
MANUFACTURERS       = ["ABB", "Siemens", "Schneider Electric", "GE", "SEL", "Landis+Gyr"]


async def seed_mongo():
    print("\n[MongoDB] Seeding 30 equipment records...")
    col = equipment_collection()
    count = 0

    # ── Shape 1: Transformers ─────────────────────────────────────
    for i in range(1, 11):
        asset_id = f"TX_{i}"
        doc = {
            "asset_id":            asset_id,
            "equipment_type":      "Transformer",
            "manufacturer":        random.choice(MANUFACTURERS[:4]),
            "model":               random.choice(TRANSFORMER_MODELS),
            "serial_number":       f"SN-TX-{i:04d}",
            "installed":           f"201{random.randint(3,9)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "last_inspection":     f"202{random.randint(3,5)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "status":              "active",
            "schema_version":      1,
            "rating_kVA":          random.choice([100, 160, 250, 400, 630]),
            "primary_voltage_kV":  11.0,
            "secondary_voltage_kV": 0.4,
            "vector_group":        "Dyn11",
            "cooling_type":        random.choice(["ONAN", "ONAF"]),
            "tap_changer":         random.choice([True, False]),
            "oil_temp_sensor":     True,
            "max_oil_temp_C":      85.0,
            "extra_fields":        {
                "oil_sample_date":     f"2025-{random.randint(1,12):02d}-01",
                "oil_acidity_mgKOH_g": round(random.uniform(0.01, 0.15), 3),
            }
        }
        await col.update_one(
            {"asset_id": asset_id},
            {"$setOnInsert": doc},
            upsert=True
        )
        count += 1

    print(f"  Transformers: 10 records")

    # ── Shape 2: SmartMeters ──────────────────────────────────────
    for i in range(1, 11):
        asset_id = f"METER_{i}"
        doc = {
            "asset_id":               asset_id,
            "equipment_type":         "SmartMeter",
            "manufacturer":           random.choice(["Landis+Gyr", "Itron", "Honeywell"]),
            "model":                  random.choice(METER_MODELS),
            "serial_number":          f"SN-SM-{i:04d}",
            "installed":              f"202{random.randint(0,4)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "last_inspection":        None,
            "status":                 "active",
            "schema_version":         1,
            "premise_id":             f"PREM_{i}",
            "meter_id":               f"SM_{i}",
            "firmware_version":       f"3.{random.randint(0,5)}.{random.randint(0,9)}",
            "communication_protocol": random.choice(["DLMS", "MBUS", "ZIGBEE", "LORAWAN"]),
            "metrology_class":        random.choice(["A", "B", "C"]),
            "phase":                  random.choice(["single", "three-phase"]),
            "tamper_detection":       True,
            "remote_disconnect":      random.choice([True, False]),
            "hes_id":                 f"HES-{i:06d}",
            "extra_fields": {
                "battery_voltage_mV":    random.randint(3300, 3700),
                "signal_strength_dBm":   random.randint(-90, -40),
                "last_heartbeat":        datetime.now(timezone.utc).isoformat(),
            }
        }
        await col.update_one(
            {"asset_id": asset_id},
            {"$setOnInsert": doc},
            upsert=True
        )
        count += 1

    print(f"  SmartMeters: 10 records")

    # ── Shape 3: ProtectionRelays ─────────────────────────────────
    feeders = [f"F_{i:03d}" for i in range(1, 11)]
    for i in range(1, 11):
        asset_id = f"RELAY_{i:03d}"
        doc = {
            "asset_id":            asset_id,
            "equipment_type":      "ProtectionRelay",
            "manufacturer":        random.choice(["SEL", "ABB", "Siemens", "GE", "Schneider Electric"]),
            "model":               random.choice(RELAY_MODELS),
            "serial_number":       f"SN-RL-{i:04d}",
            "installed":           f"201{random.randint(5,9)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "last_inspection":     f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "status":              "active",
            "schema_version":      1,
            "feeder_id":           feeders[i - 1],
            "protection_function": random.choice(["OVERCURRENT", "DISTANCE", "DIFFERENTIAL"]),
            "pickup_current_A":    round(random.uniform(200, 800), 1),
            "time_dial_setting":   round(random.uniform(0.1, 0.8), 2),
            "curve_type":          random.choice(["IEC_NI", "IEC_VI", "IEC_EI"]),
            "auto_reclose_enabled": random.choice([True, False]),
            "reclose_attempts":    random.randint(1, 3),
            "reclose_delay_s":     round(random.uniform(0.3, 1.0), 1),
            "extra_fields": {
                "iec61850_address": f"IED_{i:03d}",
                "dnp3_address":     i * 10,
            }
        }
        await col.update_one(
            {"asset_id": asset_id},
            {"$setOnInsert": doc},
            upsert=True
        )
        count += 1

    print(f"  ProtectionRelays: 10 records")
    print(f"[MongoDB] Done — {count} equipment records")


# ─────────────────────────────────────────────────────────────────
# POSTGRESQL — 100 consumer accounts + 1 invoice per account
# 3 tariff classes: residential (60), commercial (30), industrial (10)
# IDEMPOTENT: INSERT ... ON CONFLICT DO NOTHING
# ─────────────────────────────────────────────────────────────────
GREEK_NAMES = [
    "Maria Papadopoulou", "Nikos Georgiou", "Anna Stavridou",
    "Kostas Alexiou", "Eleni Konstantinou", "Dimitris Nikolaou",
    "Sofia Andreou", "Yannis Petrou", "Christina Makri", "Takis Vasiliou",
    "Katerina Dimos", "Stavros Lekkas", "Ioanna Mpaka", "Petros Samaras",
    "Despina Fotou", "Alexandros Raptis", "Vasia Tsakiri", "Manolis Zervos",
    "Antonia Katsarou", "Giannis Papageorgiou"
]

ADDRESSES = [
    "14 Aristotelous St, Thessaloniki",
    "8 Egnatia Ave, Thessaloniki",
    "22 Tsimiski St, Thessaloniki",
    "5 Mitropoleos St, Thessaloniki",
    "31 Venizelou St, Thessaloniki",
]


async def seed_postgres():
    print("\n[PostgreSQL] Seeding 100 consumer accounts...")

    accounts_inserted = 0
    invoices_inserted = 0

    for i in range(1, 101):
        premise_id = f"PREM_{i}"
        name       = GREEK_NAMES[i % len(GREEK_NAMES)]
        address    = f"{random.choice(ADDRESSES)}, Unit {i}"

        # Tariff class distribution: 60 residential, 30 commercial, 10 industrial
        if i <= 60:
            tariff_info = {
                "tariff_class":    "residential",
                "rate_per_kwh":    round(random.uniform(0.15, 0.22), 4),
                "standing_charge": round(random.uniform(0.28, 0.42), 4),
            }
        elif i <= 90:
            tariff_info = {
                "tariff_class":     "commercial",
                "rate_per_kwh":     round(random.uniform(0.13, 0.19), 4),
                "standing_charge":  round(random.uniform(0.50, 0.80), 4),
                "demand_charge_kw": round(random.uniform(8.0, 15.0), 2),
            }
        else:
            tariff_info = {
                "tariff_class":         "industrial",
                "rate_per_kwh":         round(random.uniform(0.10, 0.16), 4),
                "standing_charge":      round(random.uniform(1.50, 3.00), 4),
                "demand_charge_kw":     round(random.uniform(20.0, 50.0), 2),
                "tou_peak_rate":        round(random.uniform(0.18, 0.25), 4),
                "tou_offpeak_rate":     round(random.uniform(0.08, 0.12), 4),
                "tou_peak_start":       "07:00",
                "tou_peak_end":         "23:00",
                "interruptible":        False,
                "regulatory_surcharge": round(random.uniform(0.005, 0.015), 4),
            }

        balance = round(random.uniform(-50.0, 200.0), 2)

        # INSERT account — skip if already exists (idempotent)
        await execute(
            """
            INSERT INTO consumer_accounts
                (premise_id, name, address, tariff_info, balance)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT (premise_id) DO NOTHING
            """,
            premise_id,
            name,
            address,
            json.dumps(tariff_info),
            balance,
        )
        accounts_inserted += 1

        # INSERT one invoice per account for May 2026
        consumption = round(random.uniform(50.0, 800.0), 3)
        rate        = tariff_info["rate_per_kwh"]
        standing    = tariff_info["standing_charge"]
        energy_amt  = round(consumption * rate, 2)
        standing_amt = round(standing * 31, 2)
        amount_due  = round(energy_amt + standing_amt, 2)

        line_items = json.dumps([
            {
                "description": "Energy consumption",
                "units":       consumption,
                "unit_label":  "kWh",
                "rate":        rate,
                "amount":      energy_amt,
            },
            {
                "description": "Standing charge",
                "units":       31,
                "unit_label":  "days",
                "rate":        standing,
                "amount":      standing_amt,
            }
        ])

        await execute(
    """
    INSERT INTO invoices
        (premise_id, period_start, period_end,
         consumption_kwh, amount_due, line_items, status)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'issued')
    ON CONFLICT (premise_id, period_start, period_end) DO NOTHING
    """,
    premise_id,
    date(2026, 5, 1),
    date(2026, 5, 31),
            consumption,
            amount_due,
            line_items,
        )
        invoices_inserted += 1

    print(f"[PostgreSQL] Done — {accounts_inserted} accounts, {invoices_inserted} invoices")


# ─────────────────────────────────────────────────────────────────
# NEO4J — verify seed was applied by cassandra-init container
# ─────────────────────────────────────────────────────────────────
async def verify_neo4j():
    print("\n[Neo4j] Verifying seed data...")

    counts = await run_query("""
        MATCH (g:GridSupplyPoint) WITH count(g) AS gsps
        MATCH (s:Substation)      WITH gsps, count(s) AS subs
        MATCH (t:Transformer)     WITH gsps, subs, count(t) AS txs
        MATCH (m:SmartMeter)      WITH gsps, subs, txs, count(m) AS meters
        RETURN gsps, subs, txs, meters
    """)

    if counts:
        r = counts[0]
        print(f"  GridSupplyPoints : {r['gsps']}")
        print(f"  Substations      : {r['subs']}")
        print(f"  Transformers     : {r['txs']}")
        print(f"  SmartMeters      : {r['meters']}")

        if r['gsps'] == 2 and r['subs'] == 10 and r['txs'] == 40 and r['meters'] == 200:
            print("[Neo4j] Seed verified ✓")
        else:
            print("[Neo4j] WARNING — counts don't match expected values")
            print("        Expected: 2 GSPs, 10 substations, 40 transformers, 200 meters")
    else:
        print("[Neo4j] WARNING — no data found, neo4j-init may not have run yet")


# ─────────────────────────────────────────────────────────────────
# REDIS — push 5 test alerts to the rolling buffer
# ─────────────────────────────────────────────────────────────────

async def seed_redis():
    print("\n[Redis] Pushing 5 test alerts to buffer...")
    r = redis_db.get_redis()
    await r.delete("gridsense:alerts:recent")
    test_alerts = [
        {
            "event_id":   "SEED_ALERT_001",
            "feeder_id":  "F_001",
            "relay_id":   "RELAY_001",
            "event_type": "TRIP",
            "fault_type": "OVERCURRENT",
            "current_kA": 3.2,
            "resolved":   False,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        },
        {
            "event_id":   "SEED_ALERT_002",
            "feeder_id":  "F_002",
            "relay_id":   "RELAY_002",
            "event_type": "ALARM",
            "fault_type": "EARTH_FAULT",
            "current_kA": 0.8,
            "resolved":   False,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        },
        {
            "event_id":   "SEED_ALERT_003",
            "feeder_id":  "F_003",
            "relay_id":   "RELAY_003",
            "event_type": "RECLOSE",
            "fault_type": None,
            "current_kA": None,
            "resolved":   True,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        },
        {
            "event_id":   "SEED_ALERT_004",
            "feeder_id":  "F_004",
            "relay_id":   "RELAY_004",
            "event_type": "LOCKOUT",
            "fault_type": "OVERCURRENT",
            "current_kA": 5.1,
            "resolved":   False,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        },
        {
            "event_id":   "SEED_ALERT_005",
            "feeder_id":  "F_005",
            "relay_id":   "RELAY_005",
            "event_type": "TRIP",
            "fault_type": "DISTANCE",
            "current_kA": 2.7,
            "resolved":   True,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        },
    ]

    for alert in test_alerts:
        await push_alert_to_buffer(alert)
        await publish_alert(alert)

    print(f"[Redis] Done — 5 alerts in buffer")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("GridSense Seed Script")
    print("=" * 60)

    # Connect all databases
    print("\nConnecting to databases...")
    cassandra_db.connect()
    await neo4j_db.connect()
    await mongo_db.connect()
    await postgres_db.connect()
    await redis_db.connect()
    print("All databases connected")

    try:
        await seed_cassandra()
        await seed_mongo()
        await seed_postgres()
        await verify_neo4j()
        await seed_redis()
    finally:
        # Always disconnect cleanly
        cassandra_db.disconnect()
        await neo4j_db.disconnect()
        await mongo_db.disconnect()
        await postgres_db.disconnect()
        await redis_db.disconnect()

    print("\n" + "=" * 60)
    print("Seed complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())