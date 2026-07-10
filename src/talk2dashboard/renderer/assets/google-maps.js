(function () {
  let configPromise;
  let loadPromise;

  async function getConfig() {
    configPromise ||= fetch("/api/maps/client-config").then((response) => response.json());
    return configPromise;
  }

  async function loadGoogle() {
    if (window.google?.maps?.importLibrary) return window.google;
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      const config = await getConfig();
      if (!config.api_key) throw new Error("GOOGLE_MAPS_NOT_CONFIGURED");
      await new Promise((resolve, reject) => {
        window.__talk2dGoogleMapsReady = resolve;
        const script = document.createElement("script");
        const params = new URLSearchParams({
          key: config.api_key,
          loading: "async",
          callback: "__talk2dGoogleMapsReady",
          v: "weekly",
          language: config.language || "nl",
          region: config.region || "NL",
          auth_referrer_policy: "origin"
        });
        if (config.map_id) params.set("map_ids", config.map_id);
        script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
        script.async = true;
        script.onerror = () => reject(new Error("GOOGLE_MAPS_LOAD_FAILED"));
        document.head.append(script);
      });
      return window.google;
    })();
    return loadPromise;
  }

  function pointsFor(host) {
    try { return JSON.parse(host.dataset.points || "[]"); } catch { return []; }
  }

  async function mount2d(host, points, config) {
    const [{ Map }, { AdvancedMarkerElement }] = await Promise.all([
      google.maps.importLibrary("maps"),
      google.maps.importLibrary("marker")
    ]);
    const center = points[0] || { lat: 52.12, lng: 5.29 };
    const map = new Map(host, {
      center,
      zoom: points.length ? 9 : 7,
      mapId: config.map_id || "DEMO_MAP_ID",
      gestureHandling: "cooperative",
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: true
    });
    points.slice(0, 500).forEach((point) => new AdvancedMarkerElement({
      map,
      position: { lat: Number(point.lat), lng: Number(point.lng) },
      title: String(point.title || "Bronrecord")
    }));
  }

  async function mount3d(host, points, config) {
    const { Map3DElement, Marker3DInteractiveElement } = await google.maps.importLibrary("maps3d");
    const center = points[0] || { lat: 52.12, lng: 5.29 };
    const map = new Map3DElement({
      center: { lat: Number(center.lat), lng: Number(center.lng), altitude: 0 },
      range: points.length > 1 ? 80000 : 20000,
      tilt: 55,
      heading: 0,
      mode: "HYBRID",
      mapId: config.map_id || undefined,
      gestureHandling: "COOPERATIVE"
    });
    map.style.width = "100%";
    map.style.height = "100%";
    points.slice(0, 250).forEach((point) => map.append(new Marker3DInteractiveElement({
      position: { lat: Number(point.lat), lng: Number(point.lng), altitude: 0 },
      title: String(point.title || "Bronrecord"),
      label: String(point.title || "").slice(0, 40)
    })));
    host.replaceChildren(map);
  }

  async function mount(host) {
    if (host.dataset.mounted === "true") return;
    host.dataset.mounted = "true";
    const fallback = host.parentElement?.querySelector(".talk2d-map-fallback");
    try {
      const config = await getConfig();
      await loadGoogle();
      const points = pointsFor(host);
      if (host.dataset.mapMode === "3d") await mount3d(host, points, config);
      else await mount2d(host, points, config);
      host.dataset.status = "ready";
      if (fallback) fallback.hidden = true;
    } catch (error) {
      host.dataset.status = "fallback";
      host.replaceChildren(Object.assign(document.createElement("div"), {
        className: "talk2d-map-loading",
        textContent: "Google Maps niet geconfigureerd; open-data kaartfallback actief."
      }));
      if (fallback) fallback.hidden = false;
    }
  }

  function scan() {
    document.querySelectorAll(".talk2d-google-map").forEach((host) => void mount(host));
  }
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", scan, { once: true });
  window.addEventListener("talk2d:refresh", () => window.setTimeout(scan, 30));
})();
