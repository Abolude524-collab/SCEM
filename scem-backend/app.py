from flask import Flask, request, jsonify
from datetime import datetime, timezone
import os
from twilio.rest import Client
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "http://localhost:3000"}})  # Adjust origin as needed

# --------------------------
# Twilio WhatsApp Credentials
# --------------------------
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
FROM_WHATSAPP = os.getenv("TWILIO_FROM_WHATSAPP", "whatsapp:+14155238886")
TO_WHATSAPP = os.getenv("TWILIO_TO_WHATSAPP", "")

client = Client(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID and AUTH_TOKEN else None

# --------------------------
# MongoDB Atlas connection
# --------------------------
MONGO_URI = "mongodb://localhost:27017"

mongo_client = MongoClient(MONGO_URI)
db = mongo_client['smart_waste_db']
bins_collection = db['bins']
collections_collection = db['collections']  # or whatever your collection name is


# --------------------------
# Function to send WhatsApp alert
# --------------------------
def send_whatsapp_alert(message):
    if not client or not TO_WHATSAPP:
        print("⚠️ WhatsApp alert skipped: Twilio config missing")
        return

    try:
        client.messages.create(
            body=message,
            from_=FROM_WHATSAPP,
            to=TO_WHATSAPP
        )
        print(f"✅ WhatsApp alert sent: {message}")
    except Exception as e:
        print(f"❌ Failed to send WhatsApp alert: {e}")

# --------------------------
# In-memory storage for raw IoT data (optional)
# --------------------------
iot_data = []

# --------------------------
# API endpoint to receive raw IoT data (logs only)
# --------------------------
@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.json
    data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    iot_data.append(data)

    print("📥 Received raw data:", data)

    # Send alerts at >=80% fill level (but no truck dispatch here)
    if data.get("device") == "waste_bin" and data.get("level", 0) >= 80:
        send_whatsapp_alert(f"🚮 Waste Bin Alert: Bin is {data['level']}% full! Please collect.")

    if data.get("device") == "drain_sensor" and data.get("water_level", 0) >= 100:
        send_whatsapp_alert(f"🌊 Drainage Alert: Water level is {data['water_level']} cm. Flood risk high!")

    return jsonify({"status": "success"}), 200

# --------------------------
# API endpoint to get stored IoT data
# --------------------------
@app.route("/api/data", methods=["GET"])
def get_data():
    return jsonify(iot_data)

# ------------------------------
# Endpoint to get all bins info
# ------------------------------
@app.route('/api/bins', methods=['GET'])
def get_bins():
    print("GET /api/bins called")
    bins = []
    cursor = bins_collection.find({})
    for doc in cursor:
        bins.append({
            "bin_id": doc['bin_id'],
            "location": doc['location'],
            "fill_level": doc.get('fill_level', 0),
            "last_collection_time": doc.get('last_collection_time')
        })
    return jsonify(bins), 200

# -----------------------------------------------
# Endpoint to plan route based on bins at 100% full
# -----------------------------------------------
@app.route('/api/plan-route', methods=['POST'])
def plan_route():
    data = request.json
    bin_ids = data.get('bin_ids', [])

    # Fetch bins from DB where fill_level == 100 and bin_id in list
    full_bins = list(bins_collection.find({
        "bin_id": {"$in": bin_ids},
        "fill_level": 100
    }))

    if not full_bins:
        return jsonify({"message": "No bins at 100% full for route planning", "route": []}), 200

    # Base location: SMS Lodge, Gidan Kwano (example coords)
    base = {"lat": 9.616, "lng": 6.555}

    # Naive route: base -> each full bin -> base
    # TODO: Replace with route optimization algorithm
    route = [base] + [bin['location'] for bin in full_bins] + [base]

    return jsonify({"route": route}), 200

# -----------------------------------------------
# Update bin fill_level & info (called by simulation)
# -----------------------------------------------
@app.route("/api/update-bin", methods=["POST"])
def update_bin():
    data = request.json
    bin_id = data.get("bin_id")
    fill_level = data.get("fill_level")
    location = data.get("location")
    owner_id = data.get("owner_id", None)

    if not bin_id or fill_level is None or not location:
        return jsonify({"error": "Missing required fields"}), 400

    # Fetch existing bin record from DB
    existing_bin = bins_collection.find_one({"bin_id": bin_id})

    alert_sent = existing_bin.get("alert_sent", False) if existing_bin else False

    update_data = {
        "fill_level": fill_level,
        "location": location,
        "last_update": datetime.now(timezone.utc),
    }

    if owner_id:
        update_data["owner_id"] = owner_id

    # Logic for sending alert only once at 100% full until emptied
    if fill_level >= 100 and not alert_sent:
        send_whatsapp_alert(f"🚮 Waste Bin Alert: Bin {bin_id} is FULL (100%)! Please collect immediately.")
        update_data["alert_sent"] = True  # Mark alert as sent
    elif fill_level < 80:
        # Bin emptied or below alert threshold, reset alert flag so next fill triggers alert
        update_data["alert_sent"] = False

    # Upsert bin data in DB
    bins_collection.update_one(
        {"bin_id": bin_id},
        {"$set": update_data},
        upsert=True
    )

    return jsonify({"message": "Bin updated"}), 200


@app.route("/api/collect-bin", methods=["POST"])
def collect_bin():
    data = request.json
    bin_id = data.get("bin_id")

    if not bin_id:
        return jsonify({"error": "Missing bin_id"}), 400

    now = datetime.now(timezone.utc)

    # Update bin: reset fill_level, alert_sent, record collection time
    bins_collection.update_one(
        {"bin_id": bin_id},
        {
            "$set": {
                "fill_level": 0,
                "alert_sent": False,
                "last_collection_time": now
            }
        }
    )

    # Insert into collection history
    collections_collection.insert_one({
        "bin_id": bin_id,
        "collected_at": now
    })

    # Send WhatsApp notification
    send_whatsapp_alert(f"🗑️ Waste Bin {bin_id} has been collected and emptied at {now.strftime('%Y-%m-%d %H:%M:%S UTC')}.")

    return jsonify({"message": f"Bin {bin_id} marked as collected and emptied."}), 200

# New endpoint to get collection history
@app.route("/api/collection-history", methods=["GET"])
def collection_history():
    history = []
    cursor = collections_collection.find({}).sort("collected_at", -1).limit(50)
    for doc in cursor:
        history.append({
            "bin_id": doc["bin_id"],
            "collected_at": doc["collected_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
        })
    return jsonify(history), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
