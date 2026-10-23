import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Crosshair, Minus, Plus, X } from 'lucide-react';
import type { Bulletin, Merchant, RiskItem } from '../contracts';
import { money } from '../ui';
import './map.css';

export interface StormMapProps { merchants: Merchant[]; bulletin?: Bulletin; items?: RiskItem[]; selectedId?: string; onSelect: (id: string) => void; }
const stateFor = (risk?: RiskItem) => !risk || risk.wind === null ? 'unknown' : risk.eligible ? 'eligible' : 'clear';
const labelFor = (risk?: RiskItem) => !risk ? 'Not analysed' : risk.wind === null ? 'Wind unavailable' : risk.eligible ? 'Trigger reached' : 'Below trigger';
const cartoKey = import.meta.env.VITE_CARTO_API_KEY;
const basemapUrl = `https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png${cartoKey ? `?key=${encodeURIComponent(cartoKey)}` : ''}`;

export default function StormMap({ merchants, bulletin, items, selectedId, onSelect }: StormMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tilesRef = useRef<L.TileLayer | null>(null);
  const retryBasemap = useRef<() => void>(() => {});
  const merchantLayer = useRef<L.LayerGroup | null>(null);
  const stormLayer = useRef<L.LayerGroup | null>(null);
  const markerIndex = useRef(new Map<string, L.Marker>());
  const fitRef = useRef<() => void>(() => {});
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [zoom, setZoom] = useState(6);
  const [tileState, setTileState] = useState<'loading' | 'ready' | 'error'>('loading');
  const riskById = useMemo(() => new Map(items?.map(item => [item.merchantId, item])), [items]);
  const selected = merchants.find(merchant => merchant.id === selectedId);
  const selectedRisk = selected ? riskById.get(selected.id) : undefined;
  // Refetching an unchanged snapshot must preserve the user's camera.
  const locationsKey = JSON.stringify(merchants.map(({ id, lat, lng }) => [id, lat, lng]));
  const bulletinKey = JSON.stringify(bulletin);

  useEffect(() => {
    if (!container.current) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const map = L.map(container.current, { zoomControl: false, scrollWheelZoom: false, minZoom: 3, maxZoom: 18, zoomSnap: 0.25, zoomAnimation: !reducedMotion, fadeAnimation: !reducedMotion, markerZoomAnimation: !reducedMotion }).setView([12, 123], 6);
    mapRef.current = map;
    let disposed = false;
    const abort = new AbortController();
    let geography: L.GeoJSON | null = null;
    const tiles = cartoKey ? L.tileLayer(basemapUrl, { subdomains: 'abcd', maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> &copy; <a href="https://carto.com/attribution/" target="_blank" rel="noreferrer">CARTO</a>' }) : null;
    tilesRef.current = tiles;
    let failed = false;
    let loaded = false;
    if (tiles) {
      tiles.on('loading', () => { failed = false; loaded = false; });
      tiles.on('tileload', () => { loaded = true; if (!failed) setTileState('ready'); });
      tiles.on('tileerror', () => { failed = true; setTileState('error'); });
      tiles.on('load', () => setTileState(failed || !loaded ? 'error' : 'ready'));
      tiles.addTo(map);
      retryBasemap.current = () => { setTileState('loading'); tiles.redraw(); };
    } else {
      map.createPane('geography');
      map.getPane('geography')!.style.zIndex = '210';
      map.getPane('geography')!.style.pointerEvents = 'none';
      map.attributionControl.addAttribution('<a href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Natural Earth</a>');
      async function loadGeography() {
        setTileState('loading');
        try {
          const response = await fetch(`${import.meta.env.BASE_URL}maps/natural-earth.geojson`, { signal: abort.signal });
          if (!response.ok) throw new Error('Unable to load geography');
          const data = await response.json();
          if (disposed) return;
          geography?.remove();
          geography = L.geoJSON(data, { pane: 'geography', interactive: false, style: { className: 'natural-earth-land', color: '#3b485a', weight: 0.65, fillColor: '#263242', fillOpacity: 1 } }).addTo(map);
          loaded = true;
          setTileState('ready');
        } catch { if (!disposed) setTileState('error'); }
      }
      retryBasemap.current = () => void loadGeography();
      void loadGeography();
    }
    merchantLayer.current = L.layerGroup().addTo(map);
    stormLayer.current = L.layerGroup().addTo(map);
    L.control.scale({ imperial: false, position: 'bottomleft', maxWidth: 100 }).addTo(map);
    map.on('zoomend', () => setZoom(map.getZoom()));
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    observer.observe(container.current);
    const timeout = window.setTimeout(() => { if (!loaded) setTileState('error'); }, 12000);
    return () => {
      disposed = true; abort.abort(); window.clearTimeout(timeout); observer.disconnect(); tiles?.off(); map.remove();
      mapRef.current = null; merchantLayer.current = null; stormLayer.current = null; markerIndex.current.clear();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const points: L.LatLngExpression[] = merchants.map(m => [m.lat, m.lng]);
    if (bulletin) points.push([bulletin.center.lat, bulletin.center.lng], ...bulletin.track.map(p => [p.lat, p.lng] as L.LatLngTuple));
    const fit = () => {
      if (points.length) map.fitBounds(L.latLngBounds(points), { paddingTopLeft: [65, 90], paddingBottomRight: [85, 55], maxZoom: 10, animate: false });
      else map.setView([12, 123], 6, { animate: false });
      setZoom(map.getZoom());
    };
    fitRef.current = fit;
    fit();
  }, [locationsKey, bulletinKey]);

  useEffect(() => {
    const layer = merchantLayer.current;
    if (!layer) return;
    layer.clearLayers(); markerIndex.current.clear();
    for (const merchant of merchants) {
      const risk = riskById.get(merchant.id);
      const marker = L.marker([merchant.lat, merchant.lng], {
        icon: L.divIcon({ className: `exposure-marker ${stateFor(risk)}`, html: '<span class="exposure-marker__dot"></span>', iconSize: [32, 32], iconAnchor: [16, 16] }),
        title: merchant.name, keyboard: true, riseOnHover: true,
      }).addTo(layer);
      // Imported names are text, never interpolated HTML.
      const tooltip = document.createElement('span'); tooltip.textContent = merchant.name;
      marker.bindTooltip(tooltip, { permanent: merchants.length <= 8, direction: 'right', offset: [10, 0], className: `exposure-tooltip ${stateFor(risk)}` });
      marker.on('click', () => onSelectRef.current(merchant.id));
      const element = marker.getElement();
      element?.setAttribute('role', 'button');
      element?.setAttribute('aria-label', `${merchant.name}, ${merchant.region}, ${labelFor(risk)}`);
      element?.setAttribute('aria-pressed', String(merchant.id === selectedId));
      element?.classList.toggle('is-selected', merchant.id === selectedId);
      element?.addEventListener('keydown', event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); onSelectRef.current(merchant.id); } });
      markerIndex.current.set(merchant.id, marker);
    }
  }, [merchants, riskById, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const marker = markerIndex.current.get(selectedId); const map = mapRef.current;
    if (marker && map && !map.getBounds().pad(-0.1).contains(marker.getLatLng())) map.panTo(marker.getLatLng(), { animate: false });
  }, [selectedId]);

  useEffect(() => {
    const layer = stormLayer.current;
    if (!layer) return;
    layer.clearLayers();
    if (!bulletin) return;
    const points: L.LatLngExpression[] = bulletin.track.map(p => [p.lat, p.lng]);
    if (points.length > 1) {
      L.polyline(points, { color: '#dfad76', weight: 2, opacity: 0.85, dashArray: '5 8', interactive: false }).addTo(layer);
    }
    for (const point of points) L.circleMarker(point, { radius: 2.5, color: '#dfad76', weight: 1, fillColor: '#1b2230', fillOpacity: 1, interactive: false }).addTo(layer);
    const center = L.marker([bulletin.center.lat, bulletin.center.lng], { icon: L.divIcon({ className: 'storm-center', html: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 9C6 7 6 2 11 2M15 12c2-6 7-6 7-1M12 15c6 2 6 7 1 7M9 12c-2 6-7 6-7 1" stroke-linecap="round"/></svg>', iconSize: [28, 28], iconAnchor: [14, 14] }), keyboard: false, zIndexOffset: -500 }).addTo(layer);
    const label = document.createElement('span'); label.textContent = `${bulletin.label} · ${bulletin.wind} m/s`;
    center.bindTooltip(label, { direction: 'right', offset: [16, 0], className: 'exposure-tooltip' });
  }, [bulletinKey]);

  return <section className="storm-map" aria-label="Merchant exposure map">
    <div className="storm-map__surface">
      <div ref={container} className="storm-map__canvas" aria-label="Interactive map. Use arrow keys to pan and plus or minus to zoom." />
      <div className="storm-map__caption"><span className="map-caption-dot" />{merchants.length} merchant locations{bulletin && <small><span className="map-storm-line" />Storm · {bulletin.wind} m/s <span className="map-simulated">Simulated</span></small>}</div>
      <div className="storm-map__toolbar">
        <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => mapRef.current?.zoomIn()} disabled={zoom >= 18}><Plus size={17} /></button>
        <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => mapRef.current?.zoomOut()} disabled={zoom <= 3}><Minus size={17} /></button>
        <span className="map-toolbar-divider" />
        <button type="button" aria-label="Fit portfolio" title="Fit portfolio and storm" onClick={() => fitRef.current()}><Crosshair size={17} /></button>
      </div>
      {tileState === 'loading' && <div className="map-notice" role="status">Loading basemap…</div>}
      {tileState === 'error' && <div className="map-notice" role="status"><span><strong>Basemap unavailable</strong><br />Locations remain visible.</span><button onClick={() => retryBasemap.current()}>Retry map</button></div>}
    </div>
    <div className="storm-map__legend" aria-label="Map legend">
      <span><i className="map-dot eligible" />Trigger reached</span><span><i className="map-dot clear" />Below trigger</span><span><i className="map-dot unknown" />{items ? 'No wind data' : 'Not analysed'}</span><small aria-live="polite">Zoom {Number(zoom.toFixed(2))}</small>
    </div>
    {selected && <aside className="map-detail" aria-label="Selected merchant">
      <div className="map-detail__heading"><div><strong>{selected.name}</strong><span>{selected.region}</span></div><button aria-label="Close merchant details" onClick={() => onSelect('')}><X size={16} /></button></div>
      <dl><div><dt>Local 10-min wind (demo estimate)</dt><dd>{selectedRisk?.wind != null ? `${selectedRisk.wind.toFixed(1)} m/s` : 'Not available'}</dd></div><div><dt>Scenario payout</dt><dd>{selectedRisk ? money(selectedRisk.payout) : 'Not analysed'}</dd></div><div><dt>Trigger status</dt><dd>{labelFor(selectedRisk)}</dd></div></dl>
    </aside>}
  </section>;
}
