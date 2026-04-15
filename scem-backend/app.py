from datetime import datetime, timedelta, timezone
import copy
import math
import os

from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from twilio.rest import Client


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")}})

BASE_LOCATION = {
    "lat": float(os.getenv("BASE_LAT", "9.616")),
    "lng": float(os.getenv("BASE_LNG", "6.555")),
}
DEFAULT_PORT = int(os.getenv("PORT", "5000"))

# --------------------------
# Twilio WhatsApp Credentials
# --------------------------
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
FROM_WHATSAPP = os.getenv("TWILIO_FROM_WHATSAPP", "whatsapp:+14155238886")
TO_WHATSAPP = os.getenv("TWILIO_TO_WHATSAPP", "")

client = Client(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID and AUTH_TOKEN else None

# --------------------------
# MongoDB connection with fallback
# --------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
mongo_available = False
mongo_client = None
bins_collection = None
collections_collection = None

try:
    mongo_client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=1000,
        connectTimeoutMS=1000,
        socketTimeoutMS=1000,
    )
    mongo_client.admin.command("ping")
    db = mongo_client[os.getenv("MONGO_DB_NAME", "smart_waste_db")]
    bins_collection = db[os.getenv("MONGO_BINS_COLLECTION", "bins")]
    collections_collection = db[os.getenv("MONGO_COLLECTIONS_COLLECTION", "collections")]
    mongo_available = True
except Exception as exc:
    print(f"⚠️ MongoDB unavailable, using in-memory fallback: {exc}")


memory_bins = []
memory_collections = []
iot_data = []


def now_utc():
    return datetime.now(timezone.utc)


def format_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return value


def normalize_location(location):
    if not location:
        return None
    return {"lat": float(location["lat"]), "lng": float(location["lng"])}


def bin_status(fill_level):
    if fill_level >= 100:
        return "full"
    if fill_level >= 80:
        return "attention"
    return "ok"


def serialize_bin(doc):
    if not doc:
        return None
    fill_level = float(doc.get("fill_level", 0))
    return {
        "bin_id": doc.get("bin_id"),
        "location": normalize_location(doc.get("location")),
        "fill_level": fill_level,
        "status": doc.get("status", bin_status(fill_level)),
        "owner_id": doc.get("owner_id"),
        "alert_sent": doc.get("alert_sent", False),
        "last_update": format_datetime(doc.get("last_update")),
        "last_collection_time": format_datetime(doc.get("last_collection_time")),
        "created_at": format_datetime(doc.get("created_at")),
    }


def serialize_collection(doc):
    return {
        "bin_id": doc.get("bin_id"),
        "collected_at": format_datetime(doc.get("collected_at")),
    }


def list_bin_docs():
    if mongo_available:
        return list(bins_collection.find({}))
    return [copy.deepcopy(item) for item in memory_bins]


def get_bin_doc(bin_id):
    if mongo_available:
        return bins_collection.find_one({"bin_id": bin_id})
    for item in memory_bins:
        if item.get("bin_id") == bin_id:
            return item
    return None


def upsert_bin_doc(bin_id, update_data):
    existing = get_bin_doc(bin_id) or {"bin_id": bin_id}
    merged = {**existing, **update_data, "bin_id": bin_id}

    if mongo_available:
        bins_collection.update_one({"bin_id": bin_id}, {"$set": update_data}, upsert=True)
    else:
        for index, item in enumerate(memory_bins):
            if item.get("bin_id") == bin_id:
                memory_bins[index] = merged
                break
        else:
            memory_bins.append(merged)

    return merged


def insert_collection_doc(doc):
    if mongo_available:
        collections_collection.insert_one(doc)
    else:
        memory_collections.append(copy.deepcopy(doc))


def list_collection_docs(limit=50):
    if mongo_available:
        return list(collections_collection.find({}).sort("collected_at", -1).limit(limit))
    return sorted(memory_collections, key=lambda item: item.get("collected_at", now_utc()), reverse=True)[:limit]


def clear_state(clear_history=True):
    iot_data.clear()
    if mongo_available:
        bins_collection.delete_many({})
        if clear_history:
            collections_collection.delete_many({})
    else:
        memory_bins.clear()
        if clear_history:
            memory_collections.clear()


def send_whatsapp_alert(message):
    if not client or not TO_WHATSAPP:
        print(f"⚠️ WhatsApp alert skipped: {message}")
        return

    try:
        client.messages.create(body=message, from_=FROM_WHATSAPP, to=TO_WHATSAPP)
        print(f"✅ WhatsApp alert sent: {message}")
    except Exception as exc:
        print(f"❌ Failed to send WhatsApp alert: {exc}")


def haversine_distance_km(point_a, point_b):
    radius_km = 6371.0
    lat1 = math.radians(float(point_a["lat"]))
    lon1 = math.radians(float(point_a["lng"]))
    lat2 = math.radians(float(point_b["lat"]))
    lon2 = math.radians(float(point_b["lng"]))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bin_priority_score(doc):
    fill_level = float(doc.get("fill_level", 0))
    last_update = doc.get("last_update") or doc.get("last_collection_time")
    age_hours = 0.0
    if isinstance(last_update, datetime):
        age_hours = max((now_utc() - last_update).total_seconds() / 3600.0, 0.0)
    recency_bonus = min(age_hours, 24.0) * 1.5
    status_bonus = 18.0 if fill_level >= 100 else 10.0 if fill_level >= 80 else 0.0
    return (fill_level * 1.15) + recency_bonus + status_bonus


def build_route_candidates(candidate_bins):
    remaining = [copy.deepcopy(doc) for doc in candidate_bins]
    ordered = []
    current_location = BASE_LOCATION

    while remaining:
        remaining.sort(
            key=lambda doc: (
                -bin_priority_score(doc),
                haversine_distance_km(current_location, normalize_location(doc.get("location"))),
                doc.get("bin_id", ""),
            )
        )
        next_bin = remaining.pop(0)
        ordered.append(next_bin)
        current_location = normalize_location(next_bin.get("location"))

    route_points = [BASE_LOCATION] + [normalize_location(doc.get("location")) for doc in ordered] + [BASE_LOCATION]
    total_distance_km = 0.0
    for index in range(len(route_points) - 1):
        total_distance_km += haversine_distance_km(route_points[index], route_points[index + 1])

    naive_distance_km = 0.0
    for doc in candidate_bins:
        location = normalize_location(doc.get("location"))
        naive_distance_km += haversine_distance_km(BASE_LOCATION, location) * 2

    return ordered, route_points, round(total_distance_km, 3), round(naive_distance_km, 3)


def candidate_bins_from_request(data):
    bin_ids = data.get("bin_ids") or []
    threshold = float(data.get("threshold", 80))
    all_bins = list_bin_docs()
    if bin_ids:
        bins = [doc for doc in all_bins if doc.get("bin_id") in bin_ids]
    else:
        bins = [doc for doc in all_bins if float(doc.get("fill_level", 0)) >= threshold]
    return [doc for doc in bins if normalize_location(doc.get("location"))]


def build_route_payload(candidate_bins):
    if not candidate_bins:
        return {
            "route": [],
            "stops": [],
            "summary": {
                "candidate_bins": 0,
                "estimated_distance_km": 0,
                "baseline_distance_km": 0,
                "distance_saved_km": 0,
                "distance_saved_percent": 0,
            },
        }

    ordered_bins, route_points, distance_km, baseline_distance_km = build_route_candidates(candidate_bins)
    savings_km = max(baseline_distance_km - distance_km, 0)
    savings_percent = round((savings_km / baseline_distance_km) * 100, 1) if baseline_distance_km else 0

    return {
        "route": route_points,
        "stops": [serialize_bin(doc) for doc in ordered_bins],
        "summary": {
            "candidate_bins": len(candidate_bins),
            "estimated_distance_km": distance_km,
            "baseline_distance_km": baseline_distance_km,
            "distance_saved_km": round(savings_km, 3),
            "distance_saved_percent": savings_percent,
            "priority_top_score": round(max(bin_priority_score(doc) for doc in ordered_bins), 1),
        },
    }


def build_metrics_payload():
    bins = [serialize_bin(doc) for doc in list_bin_docs()]
    fills = [float(doc.get("fill_level", 0)) for doc in bins]
    collections = [serialize_collection(doc) for doc in list_collection_docs(limit=200)]

    full_bins = [doc for doc in bins if float(doc["fill_level"]) >= 100]
    attention_bins = [doc for doc in bins if 80 <= float(doc["fill_level"]) < 100]
    service_delays = []
    now = now_utc()

    for doc in list_bin_docs():
        last_update = doc.get("last_update")
        last_collection_time = doc.get("last_collection_time")
        if isinstance(last_update, datetime) and isinstance(last_collection_time, datetime) and last_collection_time >= last_update:
            service_delays.append((last_collection_time - last_update).total_seconds() / 3600.0)

    route_payload = build_route_payload([doc for doc in list_bin_docs() if float(doc.get("fill_level", 0)) >= 80])
    drain_events = [event for event in iot_data if event.get("device") == "drain_sensor" and event.get("water_level", 0) >= 100]

    return {
        "total_bins": len(bins),
        "full_bins": len(full_bins),
        "attention_bins": len(attention_bins),
        "average_fill_level": round(sum(fills) / len(fills), 1) if fills else 0,
        "collections_total": len(collections),
        "service_delay_hours": round(sum(service_delays) / len(service_delays), 2) if service_delays else 0,
        "route": route_payload["summary"],
        "estimated_fuel_savings_liters": round(route_payload["summary"]["distance_saved_km"] * 0.32, 2),
        "estimated_cost_savings_usd": round(route_payload["summary"]["distance_saved_km"] * 0.85, 2),
        "drain_risk_events": len(drain_events),
        "telemetry_events": len(iot_data),
        "generated_at": format_datetime(now),
    }


def scenario_template(label, fills, offsets, drain_events=None):
    bins = []
    for index, fill_level in enumerate(fills, start=1):
        bins.append(
            {
                "bin_id": f"bin_{index:03d}",
                "location": {
                    "lat": BASE_LOCATION["lat"] + offsets[index - 1][0],
                    "lng": BASE_LOCATION["lng"] + offsets[index - 1][1],
                },
                "fill_level": fill_level,
                "status": bin_status(fill_level),
                "last_update": now_utc() - timedelta(hours=index * 2),
            }
        )

    telemetry = [
        {
            "device": "waste_bin",
            "level": fill_level,
            "weight": round(fill_level * 0.12, 2),
            "timestamp": format_datetime(now_utc() - timedelta(minutes=index * 15)),
        }
        for index, fill_level in enumerate(fills, start=1)
    ]

    if drain_events:
        telemetry.extend(drain_events)

    return {"label": label, "bins": bins, "telemetry": telemetry}


def scenario_presets():
    normal_offsets = [
        (-0.0012, -0.0010),
        (-0.0008, 0.0011),
        (0.0005, -0.0013),
        (0.0011, 0.0008),
        (0.0016, -0.0006),
        (0.0002, 0.0018),
    ]
    surge_offsets = [
        (-0.0014, -0.0006),
        (-0.0010, 0.0014),
        (0.0006, -0.0015),
        (0.0010, 0.0009),
        (0.0018, -0.0002),
        (0.0003, 0.0019),
    ]
    rain_offsets = [
        (-0.0011, -0.0011),
        (-0.0009, 0.0010),
        (0.0007, -0.0012),
        (0.0013, 0.0007),
        (0.0017, -0.0005),
        (0.0004, 0.0015),
    ]

    return {
        "normal": scenario_template("normal", [25, 41, 58, 72, 83, 96], normal_offsets),
        "surge": scenario_template("surge", [68, 82, 91, 100, 88, 79], surge_offsets),
        "rain": scenario_template(
            "rain",
            [22, 35, 54, 79, 86, 67],
            rain_offsets,
            drain_events=[
                {
                    "device": "drain_sensor",
                    "water_level": 112,
                    "timestamp": format_datetime(now_utc() - timedelta(minutes=5)),
                },
                {
                    "device": "drain_sensor",
                    "water_level": 128,
                    "timestamp": format_datetime(now_utc() - timedelta(minutes=2)),
                },
            ],
        ),
    }


@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.json or {}
    data["timestamp"] = format_datetime(now_utc())
    iot_data.append(data)

    print("📥 Received raw data:", data)

    if data.get("device") == "waste_bin" and float(data.get("level", 0)) >= 80:
        send_whatsapp_alert(f"🚮 Waste Bin Alert: Bin is {data['level']}% full! Please collect.")

    if data.get("device") == "drain_sensor" and float(data.get("water_level", 0)) >= 100:
        send_whatsapp_alert(f"🌊 Drainage Alert: Water level is {data['water_level']} cm. Flood risk high!")

    return jsonify({"status": "success"}), 200


@app.route("/api/data", methods=["GET"])
def get_data():
    return jsonify(iot_data), 200


@app.route("/api/bins", methods=["GET"])
def get_bins():
    print("GET /api/bins called")
    return jsonify([serialize_bin(doc) for doc in list_bin_docs()]), 200


@app.route("/api/plan-route", methods=["POST"])
def plan_route():
    data = request.json or {}
    candidate_bins = candidate_bins_from_request(data)
    payload = build_route_payload(candidate_bins)
    if not payload["route"]:
        return jsonify({"message": "No bins met the route criteria", **payload}), 200
    return jsonify(payload), 200


@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    return jsonify(build_metrics_payload()), 200


@app.route("/api/demo/reset", methods=["POST"])
def demo_reset():
    clear_state(clear_history=True)
    return jsonify({"message": "Demo state reset"}), 200


@app.route("/api/demo/seed", methods=["POST"])
def demo_seed():
    data = request.json or {}
    scenario_name = (data.get("scenario") or "normal").lower()
    presets = scenario_presets()
    if scenario_name not in presets:
        return jsonify({"error": f"Unknown scenario: {scenario_name}"}), 400

    clear_state(clear_history=True)
    preset = presets[scenario_name]
    for bin_doc in preset["bins"]:
        upsert_bin_doc(bin_doc["bin_id"], bin_doc)
    iot_data.extend(copy.deepcopy(preset["telemetry"]))

    route_payload = build_route_payload(preset["bins"])
    metrics_payload = build_metrics_payload()

    return jsonify(
        {
            "scenario": scenario_name,
            "label": preset["label"],
            "bins": [serialize_bin(doc) for doc in preset["bins"]],
            "route": route_payload,
            "metrics": metrics_payload,
            "telemetry": copy.deepcopy(preset["telemetry"]),
        }
    ), 200


@app.route("/api/update-bin", methods=["POST"])
def update_bin():
    data = request.json or {}
    bin_id = data.get("bin_id")
    fill_level = data.get("fill_level")
    location = data.get("location")
    owner_id = data.get("owner_id")

    if not bin_id or fill_level is None or not location:
        return jsonify({"error": "Missing required fields"}), 400

    existing_bin = get_bin_doc(bin_id) or {}
    alert_sent = existing_bin.get("alert_sent", False)
    fill_level = float(fill_level)
    normalized_location = normalize_location(location)

    update_data = {
        "fill_level": fill_level,
        "location": normalized_location,
        "status": bin_status(fill_level),
        "last_update": now_utc(),
    }

    if owner_id:
        update_data["owner_id"] = owner_id

    if fill_level >= 100 and not alert_sent:
        send_whatsapp_alert(f"🚮 Waste Bin Alert: Bin {bin_id} is FULL (100%)! Please collect immediately.")
        update_data["alert_sent"] = True
    elif fill_level < 80:
        update_data["alert_sent"] = False

    upsert_bin_doc(bin_id, update_data)
    return jsonify({"message": "Bin updated", "bin": serialize_bin(get_bin_doc(bin_id))}), 200


@app.route("/api/collect-bin", methods=["POST"])
def collect_bin():
    data = request.json or {}
    bin_id = data.get("bin_id")

    if not bin_id:
        return jsonify({"error": "Missing bin_id"}), 400

    collected_at = now_utc()
    existing_bin = get_bin_doc(bin_id) or {"bin_id": bin_id, "location": BASE_LOCATION}

    upsert_bin_doc(
        bin_id,
        {
            "fill_level": 0,
            "status": "collected",
            "alert_sent": False,
            "last_collection_time": collected_at,
            "last_update": collected_at,
            "location": normalize_location(existing_bin.get("location")) or BASE_LOCATION,
        },
    )

    insert_collection_doc({"bin_id": bin_id, "collected_at": collected_at})
    send_whatsapp_alert(f"🗑️ Waste Bin {bin_id} has been collected and emptied at {format_datetime(collected_at)}.")

    return jsonify({"message": f"Bin {bin_id} marked as collected and emptied."}), 200


@app.route("/api/collection-history", methods=["GET"])
def collection_history():
    history = [serialize_collection(doc) for doc in list_collection_docs(limit=50)]
    return jsonify(history), 200


@app.route("/api/scenarios", methods=["GET"])
def get_scenarios():
    presets = scenario_presets()
    return jsonify({name: {"label": preset["label"], "bin_count": len(preset["bins"])} for name, preset in presets.items()}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=DEFAULT_PORT, debug=True)
