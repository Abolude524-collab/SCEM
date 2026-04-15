import requests
import random
import time

API_URL = "http://127.0.0.1:5000/api/update-bin"  # Change if deployed

# Example bin IDs and base coordinates (simulate bins around FUT Minna)
bins = [
    {"bin_id": "bin_001", "base_location": {"lat": 9.615, "lng": 6.553}},
    {"bin_id": "bin_002", "base_location": {"lat": 9.617, "lng": 6.557}},
    {"bin_id": "bin_003", "base_location": {"lat": 9.613, "lng": 6.550}},
]

def generate_fill_level(prev_level):
    # Simulate fill increasing randomly, reset at 100%
    if prev_level >= 100:
        return 0
    return min(100, prev_level + random.randint(1, 10))

def simulate_bins():
    fill_levels = {bin['bin_id']: 0 for bin in bins}

    while True:
        for bin in bins:
            bin_id = bin['bin_id']
            # Increase fill level
            fill_levels[bin_id] = generate_fill_level(fill_levels[bin_id])
            fill_level = fill_levels[bin_id]

            # Add small random offset to location for simulation
            loc = bin['base_location']
            location = {
                "lat": loc['lat'] + random.uniform(-0.0005, 0.0005),
                "lng": loc['lng'] + random.uniform(-0.0005, 0.0005),
            }

            data = {
                "bin_id": bin_id,
                "fill_level": fill_level,
                "location": location
            }

            try:
                res = requests.post(API_URL, json=data)
                if res.status_code == 200:
                    print(f"Updated {bin_id}: fill_level={fill_level}%")
                else:
                    print(f"Failed to update {bin_id}: {res.text}")
            except Exception as e:
                print(f"Error sending update for {bin_id}: {e}")

        time.sleep(60)  # Wait 1 minute before next update

if __name__ == "__main__":
    simulate_bins()
