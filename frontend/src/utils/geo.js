/**
 * WGS84 coordinate helpers shared by the Sentinel map components.
 *
 * Camera coordinates are optional in the database, so anything reaching Leaflet
 * must be filtered first: a missing coordinate is never coerced to 0, because
 * (0, 0) is a real place in the Gulf of Guinea and would plot a false sighting.
 */

/** Geographic centre of Gujarat — used until real coordinates are known. */
export const DEFAULT_CENTER = [22.2587, 71.1924];
export const DEFAULT_ZOOM = 7;

/** True only for a finite, in-range WGS84 latitude/longitude pair. */
export function isValidLatLng(latitude, longitude) {
  return (
    typeof latitude === 'number' &&
    Number.isFinite(latitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    typeof longitude === 'number' &&
    Number.isFinite(longitude) &&
    longitude >= -180 &&
    longitude <= 180
  );
}

/** Cameras that can actually be placed on the map. */
export function toCameraPoints(cameras) {
  return (cameras || []).filter((camera) =>
    isValidLatLng(camera?.latitude, camera?.longitude)
  );
}

/**
 * Plottable points of a vehicle's cross-camera timeline.
 *
 * Order is preserved exactly as the API returned it — the backend sorts by
 * event timestamp, and the map must not re-sort or re-interpret that sequence.
 */
export function toMovementPoints(timeline) {
  return (timeline || []).filter((entry) =>
    isValidLatLng(entry?.latitude, entry?.longitude)
  );
}

/** Leaflet-shaped [lat, lng] tuples for a list of geolocated records. */
export function toLatLngTuples(points) {
  return (points || []).map((point) => [point.latitude, point.longitude]);
}
