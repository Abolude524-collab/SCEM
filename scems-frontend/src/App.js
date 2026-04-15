import React, { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MapContainer, Marker, Polyline, Popup, TileLayer } from "react-leaflet";
import { ToastContainer, toast } from "react-toastify";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "react-toastify/dist/ReactToastify.css";
import "./App.css";

const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://localhost:5000";

const truckIcon = new L.Icon({
  iconUrl: "https://cdn-icons-png.flaticon.com/512/1995/1995472.png",
  iconSize: [40, 40],
});

const pieColors = ["#4f8cff", "#43c59e", "#ffb84d"];

const scenarioButtons = [
  { key: "normal", label: "Normal day" },
  { key: "surge", label: "Festival surge" },
  { key: "rain", label: "Rain-risk day" },
];

const basePosition = [9.616, 6.555];

function App() {
  const [bins, setBins] = useState([]);
  const [route, setRoute] = useState([]);
  const [routeSummary, setRouteSummary] = useState(null);
  const [truckPos, setTruckPos] = useState(basePosition);
  const [collectionHistory, setCollectionHistory] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const [activeScenario, setActiveScenario] = useState("normal");
  const [isLoadingScenario, setIsLoadingScenario] = useState(false);

  const progressRef = useRef(0);
  const routeIndexRef = useRef(0);
  const animationFrameRef = useRef(null);

  const statusCounts = bins.reduce(
    (accumulator, bin) => {
      if (bin.fill_level >= 100) accumulator.full += 1;
      else if (bin.fill_level >= 80) accumulator.attention += 1;
      else accumulator.ok += 1;
      return accumulator;
    },
    { ok: 0, attention: 0, full: 0 }
  );

  const fillChartData = bins.map((bin) => ({
    name: bin.bin_id,
    fill: bin.fill_level,
  }));

  const statusChartData = [
    { name: "OK", value: statusCounts.ok },
    { name: "Attention", value: statusCounts.attention },
    { name: "Full", value: statusCounts.full },
  ];

  const seedScenario = useCallback(async (scenario, silent = false) => {
    setIsLoadingScenario(true);
    try {
      const response = await axios.post(`${backendUrl}/api/demo/seed`, { scenario });
      const payload = response.data;

      setActiveScenario(payload.scenario || scenario);
      setBins(payload.bins || []);
      setCollectionHistory([]);
      setMetrics(payload.metrics || null);
      setTelemetry((payload.telemetry || []).slice(-12).reverse());

      if (payload.route) {
        setRoute(payload.route.route || []);
        setRouteSummary(payload.route.summary || null);
        progressRef.current = 0;
        routeIndexRef.current = 0;
        if ((payload.route.route || []).length > 0) {
          setTruckPos(payload.route.route[0]);
        }
      }

      if (!silent) {
        toast.success(`Loaded ${payload.label || scenario} scenario.`);
      }
    } catch (error) {
      console.error("Error loading scenario:", error);
      toast.error("Unable to load scenario.");
    } finally {
      setIsLoadingScenario(false);
    }
  }, []);

  const fetchDashboard = useCallback(async () => {
    try {
      const [binsRes, historyRes, metricsRes, telemetryRes] = await Promise.all([
        axios.get(`${backendUrl}/api/bins`),
        axios.get(`${backendUrl}/api/collection-history`),
        axios.get(`${backendUrl}/api/metrics`),
        axios.get(`${backendUrl}/api/data`),
      ]);

      const binData = binsRes.data || [];
      if (binData.length === 0) {
        await seedScenario("normal", true);
        return;
      }

      setBins(binData);
      setCollectionHistory(historyRes.data || []);
      setMetrics(metricsRes.data || null);
      setTelemetry((telemetryRes.data || []).slice(-12).reverse());
    } catch (error) {
      console.error("Error fetching dashboard:", error);
      toast.error("Failed to load dashboard data.");
    }
  }, [seedScenario]);

  const refreshMetricsAndRoute = useCallback(async () => {
    try {
      const [metricsRes, telemetryRes] = await Promise.all([
        axios.get(`${backendUrl}/api/metrics`),
        axios.get(`${backendUrl}/api/data`),
      ]);

      setMetrics(metricsRes.data || null);
      setTelemetry((telemetryRes.data || []).slice(-12).reverse());
    } catch (error) {
      console.error("Error refreshing metrics:", error);
    }
  }, []);

  const planRoute = useCallback(async (binsToPlan = bins) => {
    const priorityBins = binsToPlan.filter((bin) => bin.fill_level >= 80);
    if (priorityBins.length === 0) {
      setRoute([]);
      setRouteSummary(null);
      return;
    }

    try {
      const response = await axios.post(`${backendUrl}/api/plan-route`, {
        bin_ids: priorityBins.map((bin) => bin.bin_id),
      });

      setRoute(response.data.route || []);
      setRouteSummary(response.data.summary || null);
      progressRef.current = 0;
      routeIndexRef.current = 0;

      if ((response.data.route || []).length > 0) {
        setTruckPos(response.data.route[0]);
      }
    } catch (error) {
      console.error("Error planning route:", error);
      toast.error("Route planning failed.");
    }
  }, [bins]);

  const collectBin = async (binId) => {
    try {
      await axios.post(`${backendUrl}/api/collect-bin`, { bin_id: binId });
      toast.success(`Bin ${binId} collected and emptied.`);
      await fetchDashboard();
    } catch (error) {
      console.error(error);
      toast.error("Failed to collect bin.");
    }
  };

  const animateTruck = useCallback(() => {
    if (route.length < 2) {
      return;
    }

    const speed = 0.006;

    const animate = () => {
      const from = route[routeIndexRef.current];
      const to = route[routeIndexRef.current + 1];

      if (!from || !to) {
        cancelAnimationFrame(animationFrameRef.current);
        return;
      }

      progressRef.current += speed;
      if (progressRef.current >= 1) {
        routeIndexRef.current += 1;
        if (routeIndexRef.current >= route.length - 1) {
          setTruckPos(to);
          cancelAnimationFrame(animationFrameRef.current);
          return;
        }
        progressRef.current = 0;
      }

      const nextPosition = [
        from.lat + (to.lat - from.lat) * progressRef.current,
        from.lng + (to.lng - from.lng) * progressRef.current,
      ];

      setTruckPos(nextPosition);
      animationFrameRef.current = requestAnimationFrame(animate);
    };

    animationFrameRef.current = requestAnimationFrame(animate);
  }, [route]);

  const handleScenarioLoad = async (scenario) => {
    setActiveScenario(scenario);
    await seedScenario(scenario);
  };

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  useEffect(() => {
    if (bins.length > 0) {
      planRoute(bins);
      refreshMetricsAndRoute();
    } else {
      setRoute([]);
      setRouteSummary(null);
    }
  }, [bins, planRoute, refreshMetricsAndRoute]);

  useEffect(() => {
    animateTruck();
    return () => cancelAnimationFrame(animationFrameRef.current);
  }, [animateTruck]);

  return (
    <div className="app-shell">
      <div className="hero-panel">
        <div>
          <p className="eyebrow">Hardware-agnostic digital twin</p>
          <h1>Smart City Environmental Command Center</h1>
          <p className="hero-copy">
            Seed a scenario, visualize the fleet, and show investors route savings,
            collection coverage, and flood-risk alerts without waiting for hardware.
          </p>
        </div>

        <div className="hero-actions">
          {scenarioButtons.map((button) => (
            <button
              key={button.key}
              className={`scenario-button ${activeScenario === button.key ? "active" : ""}`}
              onClick={() => handleScenarioLoad(button.key)}
              disabled={isLoadingScenario}
            >
              {button.label}
            </button>
          ))}
        </div>
      </div>

      <section className="kpi-grid">
        <article className="kpi-card">
          <span className="kpi-label">Total bins</span>
          <strong>{metrics?.total_bins ?? bins.length}</strong>
          <small>Live monitored assets</small>
        </article>
        <article className="kpi-card">
          <span className="kpi-label">Attention bins</span>
          <strong>{metrics?.attention_bins ?? statusCounts.attention}</strong>
          <small>Needs collection soon</small>
        </article>
        <article className="kpi-card">
          <span className="kpi-label">Distance saved</span>
          <strong>{metrics?.route?.distance_saved_km ?? routeSummary?.distance_saved_km ?? 0} km</strong>
          <small>vs fixed collection route</small>
        </article>
        <article className="kpi-card">
          <span className="kpi-label">Estimated cost saved</span>
          <strong>${metrics?.estimated_cost_savings_usd ?? 0}</strong>
          <small>Projected demo ROI</small>
        </article>
      </section>

      <section className="content-grid">
        <div className="panel panel-map">
          <div className="panel-header">
            <div>
              <p className="panel-kicker">Operational map</p>
              <h2>Collection route and bin hotspots</h2>
            </div>
            <button className="secondary-button" onClick={fetchDashboard}>
              Refresh
            </button>
          </div>

          <div className="map-wrap">
            <MapContainer center={basePosition} zoom={15} className="demo-map">
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

              {bins.map((bin) => (
                <Marker
                  key={bin.bin_id}
                  position={[bin.location.lat, bin.location.lng]}
                >
                  <Popup>
                    <div className="popup-card">
                      <h3>{bin.bin_id}</h3>
                      <p>Fill level: {bin.fill_level}%</p>
                      <p>Status: {bin.status || (bin.fill_level >= 100 ? "full" : bin.fill_level >= 80 ? "attention" : "ok")}</p>
                      <p>Last collection: {bin.last_collection_time || "N/A"}</p>
                      {bin.fill_level >= 80 && (
                        <button className="primary-button" onClick={() => collectBin(bin.bin_id)}>
                          Collect bin
                        </button>
                      )}
                    </div>
                  </Popup>
                </Marker>
              ))}

              {route.length > 0 && <Marker position={truckPos} icon={truckIcon} title="Autonomous truck" />}

              {route.length > 1 && <Polyline positions={route.map((point) => [point.lat, point.lng])} color="#43c59e" />}
            </MapContainer>
          </div>

          <div className="route-summary">
            <div>
              <span className="kpi-label">Route status</span>
              <strong>{routeSummary ? `${routeSummary.candidate_bins} stops planned` : "No active route"}</strong>
            </div>
            <div>
              <span className="kpi-label">Estimated distance</span>
              <strong>{routeSummary?.estimated_distance_km ?? 0} km</strong>
            </div>
            <div>
              <span className="kpi-label">Savings</span>
              <strong>{routeSummary?.distance_saved_percent ?? 0}%</strong>
            </div>
          </div>
        </div>

        <aside className="side-column">
          <section className="panel">
            <div className="panel-header compact">
              <div>
                <p className="panel-kicker">Status distribution</p>
                <h2>Bin readiness</h2>
              </div>
            </div>
            <div className="chart-box pie-box">
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie data={statusChartData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={90} paddingAngle={3}>
                    {statusChartData.map((entry, index) => (
                      <Cell key={`cell-${entry.name}`} fill={pieColors[index % pieColors.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header compact">
              <div>
                <p className="panel-kicker">Fill levels</p>
                <h2>Bin pressure by location</h2>
              </div>
            </div>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={fillChartData}>
                  <XAxis dataKey="name" tick={{ fill: "#cfe3ff", fontSize: 12 }} />
                  <YAxis tick={{ fill: "#cfe3ff", fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="fill" radius={[8, 8, 0, 0]} fill="#4f8cff" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
        </aside>
      </section>

      <section className="lower-grid">
        <section className="panel">
          <div className="panel-header compact">
            <div>
              <p className="panel-kicker">Telemetry stream</p>
              <h2>Live IoT events</h2>
            </div>
          </div>
          <div className="telemetry-list">
            {telemetry.length === 0 && <p className="muted">No telemetry yet.</p>}
            {telemetry.map((event, index) => (
              <div className="telemetry-item" key={`${event.device}-${index}`}>
                <div>
                  <strong>{event.device}</strong>
                  <p>{event.timestamp}</p>
                </div>
                <span>
                  {event.device === "drain_sensor"
                    ? `${event.water_level} cm`
                    : `${event.level}% / ${event.weight ?? 0} kg`}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header compact">
            <div>
              <p className="panel-kicker">Collection history</p>
              <h2>Completed pickups</h2>
            </div>
          </div>
          <div className="history-list">
            {collectionHistory.length === 0 && <p className="muted">No collections yet.</p>}
            {collectionHistory.map((item, index) => (
              <div className="history-item" key={`${item.bin_id}-${index}`}>
                <strong>{item.bin_id}</strong>
                <span>{item.collected_at}</span>
              </div>
            ))}
          </div>
        </section>
      </section>

      <ToastContainer position="top-right" autoClose={4000} />
    </div>
  );
}

export default App;
