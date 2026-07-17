import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, Text, View } from 'react-native';
import { CodexButton } from './CodexButton';

// Leaflet + OpenStreetMap tiles, web only. Loaded from CDN on first use —
// same pattern as Stripe.js in PaymentMethodsScreen.
let leafletPromise: Promise<any> | null = null;
function loadLeaflet(): Promise<any> {
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const existing = (window as any).L;
    if (existing) return resolve(existing);
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = () => {
      const L = (window as any).L;
      if (L) resolve(L);
      else reject(new Error('Leaflet failed to initialize'));
    };
    script.onerror = () => reject(new Error('Failed to load the map library'));
    document.head.appendChild(script);
  });
  return leafletPromise;
}

// Radius choices in feet (what people think in) stored as meters (what the
// verifier uses).
const RADIUS_CHOICES = [
  { label: '50 ft', meters: 15 },
  { label: '100 ft', meters: 30 },
  { label: '200 ft', meters: 61 },
  { label: '500 ft', meters: 152 },
];

interface Props {
  onConfirm: (latitude: number, longitude: number, radiusM: number) => void;
  onCancel: () => void;
}

export function MapPicker({ onConfirm, onCancel }: Props) {
  const [point, setPoint] = useState<{ lat: number; lng: number } | null>(null);
  const [radiusM, setRadiusM] = useState(61);
  const [error, setError] = useState<string | null>(null);
  const mapRef = useRef<any>(null);
  const markerRef = useRef<any>(null);
  const circleRef = useRef<any>(null);
  const leafletRef = useRef<any>(null);
  const radiusRef = useRef(radiusM);
  radiusRef.current = radiusM;

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    let cancelled = false;

    loadLeaflet()
      .then((L) => {
        if (cancelled) return;
        leafletRef.current = L;
        const map = L.map('sacrifice-map-picker', { zoomControl: true });
        mapRef.current = map;
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19,
          attribution: '© OpenStreetMap contributors',
        }).addTo(map);
        map.setView([39.5, -98.35], 4); // continental US until located

        if (navigator.geolocation) {
          navigator.geolocation.getCurrentPosition(
            (pos) => {
              if (!cancelled) map.setView([pos.coords.latitude, pos.coords.longitude], 15);
            },
            () => {}, // stay on the default view
            { timeout: 8000 },
          );
        }

        map.on('click', (e: any) => {
          const { lat, lng } = e.latlng;
          setPoint({ lat, lng });
          if (markerRef.current) {
            markerRef.current.setLatLng(e.latlng);
            circleRef.current.setLatLng(e.latlng);
            circleRef.current.setRadius(radiusRef.current);
          } else {
            markerRef.current = L.marker(e.latlng).addTo(map);
            circleRef.current = L.circle(e.latlng, {
              radius: radiusRef.current,
              color: '#8A2A1C',
              fillOpacity: 0.15,
            }).addTo(map);
          }
        });
      })
      .catch((e) => setError(e?.message || 'Could not load the map'));

    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        markerRef.current = null;
        circleRef.current = null;
      }
    };
  }, []);

  const pickRadius = useCallback((meters: number) => {
    setRadiusM(meters);
    if (circleRef.current) circleRef.current.setRadius(meters);
  }, []);

  if (Platform.OS !== 'web') {
    return (
      <View className="p-4">
        <Text className="font-sans text-sm text-codex-muted">
          The map picker is available in the web app.
        </Text>
      </View>
    );
  }

  return (
    <View testID="map-picker" className="border-t border-codex-border bg-codex-bg px-4 py-3">
      <Text className="mb-2 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
        Tap the map to set the target spot
      </Text>
      {/* nativeID becomes the DOM id Leaflet mounts into. */}
      <View
        nativeID="sacrifice-map-picker"
        className="rounded-sm border border-codex-border"
        style={{ height: 300, width: '100%' }}
      />
      {error && <Text className="mt-2 font-sans text-sm text-codex-accent">{error}</Text>}

      <View className="mt-2 flex-row items-center gap-2">
        <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Radius</Text>
        {RADIUS_CHOICES.map((choice) => (
          <Pressable
            key={choice.meters}
            testID={`radius-${choice.meters}`}
            className={`rounded-full px-3 py-1.5 ${
              radiusM === choice.meters
                ? 'bg-codex-accent'
                : 'border border-codex-border bg-codex-surface'
            }`}
            onPress={() => pickRadius(choice.meters)}
          >
            <Text
              className={`font-sans-medium text-xs ${
                radiusM === choice.meters ? 'text-codex-surface' : 'text-codex-muted'
              }`}
            >
              {choice.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <View className="mt-3 flex-row gap-2">
        <CodexButton
          testID="map-picker-confirm"
          disabled={!point}
          onPress={() => point && onConfirm(point.lat, point.lng, radiusM)}
        >
          {point
            ? `Use this spot (${point.lat.toFixed(5)}, ${point.lng.toFixed(5)})`
            : 'Tap the map first'}
        </CodexButton>
        <CodexButton testID="map-picker-cancel" variant="secondary" onPress={onCancel}>
          Cancel
        </CodexButton>
      </View>
    </View>
  );
}
