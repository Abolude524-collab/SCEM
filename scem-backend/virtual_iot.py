import requests, random, time

# Backend API URL
API_URL = "http://127.0.0.1:5000/api/data"  # Change to your server IP if deployed

def send_fake_data():
    while True:
        # Waste bin simulation
        waste_level = random.randint(0, 100)  # % full
        waste_weight = round(random.uniform(0, 15), 2)  # kg

        waste_data = {
            "device": "waste_bin",
            "level": waste_level,
            "weight": waste_weight
        }

        # Drain sensor simulation
        drain_level = random.randint(0, 150)  # cm water level

        drain_data = {
            "device": "drain_sensor",
            "water_level": drain_level
        }

        try:
            # Send waste bin data
            r1 = requests.post(API_URL, json=waste_data)
            # Send drain data
            r2 = requests.post(API_URL, json=drain_data)

            print(f"📤 Sent: Waste Bin → {waste_level}% ({waste_weight}kg) | Drain → {drain_level}cm")

        except Exception as e:
            print(f"❌ Error sending data: {e}")

        time.sleep(60)  # Send every 5 seconds

if __name__ =="__main__":
    send_fake_data()    