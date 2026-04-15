import React, { useEffect, useState, useRef } from "react";
import axios from "axios";
import { MapContainer, TileLayer, Marker, Popup, Polyline } from "react-leaflet";
import { ToastContainer, toast } from "react-toastify";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "react-toastify/dist/ReactToastify.css";
import "./App.css";

const truckIcon = new L.Icon({
  iconUrl: "https://cdn-icons-png.flaticon.com/512/1995/1995472.png",
  iconSize: [40, 40],
});

const backendUrl = "http://localhost:5000";

function App() {
  const [bins, setBins] = useState([]);
  const [route, setRoute] = useState([]);
  const [truckPos, setTruckPos] = useState([9.616, 6.555]);
  const [selectedBin, setSelectedBin] = useState(null);
  const [collectionHistory, setCollectionHistory] = useState([]);

  const progressRef = useRef(0);
  const routeIndexRef = useRef(0);
  const animationFrameRef = useRef(null);

  const fetchBins = async () => {
    try {
      const res = await axios.get(`${backendUrl}/api/bins`);
      setBins(res.data);
      res.data.forEach((bin) => {
        if (bin.fill_level >= 80) {
          toast.warn(`🚮 Bin ${bin.bin_id} is ${bin.fill_level}% full!`);
        }
      });
    } catch (e) {
      console.error("Error fetching bins:", e);
    }
  };

  const fetchCollectionHistory = async () => {
    try {
      const res = await axios.get(`${backendUrl}/api/collection-history`);
      setCollectionHistory(res.data);
    } catch (e) {
      console.error("Error fetching collection history:", e);
    }
  };

  const planRoute = async () => {
    const fullBins = bins.filter((bin) => bin.fill_level === 100);
    if (fullBins.length === 0) {
      setRoute([]);
      return;
    }
    try {
      const res = await axios.post(`${backendUrl}/api/plan-route`, {
        bin_ids: fullBins.map((b) => b.bin_id),
      });
      setRoute(res.data.route);
      progressRef.current = 0;
      routeIndexRef.current = 0;
      if (res.data.route.length > 0) setTruckPos(res.data.route[0]);
    } catch (e) {
      console.error("Error planning route:", e);
    }
  };

  const animateTruck = () => {
    if (route.length < 2) return;

    const speed = 0.005;

    const animate = () => {
      const from = route[routeIndexRef.current];
      const to = route[routeIndexRef.current + 1];
      if (!from || !to) {
        cancelAnimationFrame(animationFrameRef.current);
        return;
      }
      progressRef.current += speed;
      if (progressRef.current >= 1) {
        routeIndexRef.current++;
        if (routeIndexRef.current >= route.length - 1) {
          progressRef.current = 1;
          setTruckPos(to);
          cancelAnimationFrame(animationFrameRef.current);
          return;
        }
        progressRef.current = 0;
      }
      const pos = [
        from.lat + (to.lat - from.lat) * progressRef.current,
        from.lng + (to.lng - from.lng) * progressRef.current,
      ];
      setTruckPos(pos);
      animationFrameRef.current = requestAnimationFrame(animate);
    };

    animationFrameRef.current = requestAnimationFrame(animate);
  };

  // Mark bin as collected
  const collectBin = async (bin_id) => {
    try {
      await axios.post(`${backendUrl}/api/collect-bin`, { bin_id });
      toast.success(`🗑️ Bin ${bin_id} has been collected and emptied.`);
      fetchBins(); // Refresh bins
      fetchCollectionHistory(); // Refresh history
      setSelectedBin(null);
    } catch (e) {
      toast.error("Failed to collect bin.");
      console.error(e);
    }
  };

  useEffect(() => {
    fetchBins();
    fetchCollectionHistory();
    const interval = setInterval(fetchBins, 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (bins.length) planRoute();
  }, [bins]);

  useEffect(() => {
    animateTruck();
    return () => cancelAnimationFrame(animationFrameRef.current);
  }, [route]);

  return (
    <div>
      <h1>🌆 Smart City Environmental Management System</h1>

      <div className="dashboard">
        {bins.map((bin) => (
          <div
            key={bin.bin_id}
            className="card"
            onClick={() => setSelectedBin(bin)}
            style={{ cursor: "pointer" }}
          >
            <h2>Bin {bin.bin_id}</h2>
            <p>Fill Level: {bin.fill_level}%</p>
            <p>Last Collection: {bin.last_collection_time || "N/A"}</p>
            <p className={bin.fill_level >= 80 ? "status-alert" : "status-ok"}>
              {bin.fill_level >= 100
                ? "Full"
                : bin.fill_level >= 80
                ? "Near Full"
                : "OK"}
            </p>
          </div>
        ))}
      </div>

      <div style={{ height: "500px", width: "100%" }}>
        <MapContainer
          center={[9.616, 6.555]}
          zoom={15}
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

          {bins.map((bin) => (
            <Marker
              key={bin.bin_id}
              position={[bin.location.lat, bin.location.lng]}
              eventHandlers={{ click: () => setSelectedBin(bin) }}
            />
          ))}

          {selectedBin && (
            <Popup
              position={[selectedBin.location.lat, selectedBin.location.lng]}
              onClose={() => setSelectedBin(null)}
            >
              <div>
                <h3>Bin ID: {selectedBin.bin_id}</h3>
                <p>
                  <b>Fill Level:</b> {selectedBin.fill_level}%
                </p>
                <p>
                  <b>Last Collection:</b>{" "}
                  {selectedBin.last_collection_time || "N/A"}
                </p>
                <p>
                  <b>Status:</b>{" "}
                  {selectedBin.fill_level >= 100
                    ? "Full"
                    : selectedBin.fill_level >= 80
                    ? "Near Full"
                    : "OK"}
                </p>
                {/* Collect Bin Button */}
                {selectedBin.fill_level >= 80 && (
                  <button
                    onClick={() => collectBin(selectedBin.bin_id)}
                    style={{ marginTop: "10px", padding: "5px 10px" }}
                  >
                    Collect Bin
                  </button>
                )}
              </div>
            </Popup>
          )}

          {route.length > 0 && (
            <Marker position={truckPos} icon={truckIcon} title="Autonomous Truck" />
          )}

          {route.length > 1 && (
            <Polyline
              positions={route.map((p) => [p.lat, p.lng])}
              color="blue"
            />
          )}
        </MapContainer>
      </div>

      {/* Collection History Section */}
      <div className="collection-history" style={{ marginTop: "20px" }}>
        <h2>🗑️ Collection History</h2>
        <ul>
          {collectionHistory.length === 0 && <li>No collections yet.</li>}
          {collectionHistory.map((item, index) => (
            <li key={index}>
              Bin <b>{item.bin_id}</b> collected at {item.collected_at}
            </li>
          ))}
        </ul>
      </div>

      <ToastContainer position="top-right" autoClose={5000} />
    </div>
  );
}

export default App;
