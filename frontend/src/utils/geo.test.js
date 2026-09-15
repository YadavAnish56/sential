import { describe, test, expect } from 'vitest';
import {
  DEFAULT_CENTER,
  DEFAULT_ZOOM,
  isValidLatLng,
  toCameraPoints,
  toLatLngTuples,
  toMovementPoints,
} from './geo';

describe('coordinate validation', () => {
  test('accepts in-range WGS84 pairs', () => {
    expect(isValidLatLng(21.1702, 72.8311)).toBe(true);
    expect(isValidLatLng(0, 0)).toBe(true);
    expect(isValidLatLng(-90, -180)).toBe(true);
    expect(isValidLatLng(90, 180)).toBe(true);
  });

  test('rejects out-of-range values', () => {
    expect(isValidLatLng(91, 0)).toBe(false);
    expect(isValidLatLng(-91, 0)).toBe(false);
    expect(isValidLatLng(0, 181)).toBe(false);
    expect(isValidLatLng(0, -181)).toBe(false);
  });

  test('rejects null, undefined and non-numeric input', () => {
    expect(isValidLatLng(null, null)).toBe(false);
    expect(isValidLatLng(undefined, undefined)).toBe(false);
    expect(isValidLatLng('21.17', '72.83')).toBe(false);
    expect(isValidLatLng(NaN, 0)).toBe(false);
    expect(isValidLatLng(Infinity, 0)).toBe(false);
  });

  test('rejects half-populated coordinates', () => {
    expect(isValidLatLng(21.1702, null)).toBe(false);
    expect(isValidLatLng(null, 72.8311)).toBe(false);
  });
});

describe('point extraction', () => {
  test('keeps only plottable cameras', () => {
    const cameras = [
      { id: 1, latitude: 21.17, longitude: 72.83 },
      { id: 2, latitude: null, longitude: null },
      { id: 3, latitude: 999, longitude: 72.83 },
      { id: 4 },
    ];
    expect(toCameraPoints(cameras).map((c) => c.id)).toEqual([1]);
  });

  test('keeps timeline order untouched while dropping unplottable entries', () => {
    const timeline = [
      { event_id: 1, latitude: 21.19, longitude: 72.79 },
      { event_id: 2, latitude: null, longitude: null },
      { event_id: 3, latitude: 21.20, longitude: 72.87 },
    ];
    expect(toMovementPoints(timeline).map((e) => e.event_id)).toEqual([1, 3]);
  });

  test('handles null and undefined collections', () => {
    expect(toCameraPoints(null)).toEqual([]);
    expect(toMovementPoints(undefined)).toEqual([]);
    expect(toLatLngTuples(null)).toEqual([]);
  });

  test('converts records to Leaflet tuples', () => {
    expect(toLatLngTuples([{ latitude: 1.5, longitude: 2.5 }])).toEqual([[1.5, 2.5]]);
  });
});

describe('default viewport', () => {
  test('falls back to Gujarat when nothing is geolocated', () => {
    expect(DEFAULT_CENTER).toEqual([22.2587, 71.1924]);
    expect(isValidLatLng(DEFAULT_CENTER[0], DEFAULT_CENTER[1])).toBe(true);
    expect(DEFAULT_ZOOM).toBe(7);
  });
});
