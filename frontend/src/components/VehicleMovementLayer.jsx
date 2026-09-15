import React from 'react';
import { Marker, Polyline, Popup } from 'react-leaflet';
import L from 'leaflet';
import { toLatLngTuples, toMovementPoints } from '../utils/geo';

/**
 * Numbered pin marking one stop on a vehicle's path.
 * A div icon avoids shipping image assets through the bundler.
 */
function hopIcon(sequence, isSelected) {
  return L.divIcon({
    className: 'sentinel-hop-wrapper',
    html: `<span class="sentinel-hop${isSelected ? ' sentinel-hop-selected' : ''}">${sequence}</span>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

/**
 * Draws a vehicle's cross-camera movement as an ordered path.
 *
 * The sequence shown is the order in which cameras *detected* the vehicle, not
 * a surveyed route: the straight segments between stops are a visual link, not
 * the road actually travelled.
 */
export default function VehicleMovementLayer({
  timeline,
  selectedEventId,
  onSelectEvent,
}) {
  const points = toMovementPoints(timeline);

  if (points.length === 0) {
    return null;
  }

  const positions = toLatLngTuples(points);

  return (
    <>
      {positions.length > 1 && (
        <Polyline
          positions={positions}
          pathOptions={{ color: '#1890ff', weight: 3, opacity: 0.85 }}
        />
      )}

      {points.map((entry, index) => (
        <Marker
          key={entry.event_id}
          position={[entry.latitude, entry.longitude]}
          icon={hopIcon(index + 1, entry.event_id === selectedEventId)}
          eventHandlers={{
            click: () => onSelectEvent && onSelectEvent(entry.event_id),
          }}
        >
          <Popup>
            <div className="sentinel-popup">
              <strong>{`Stop ${index + 1} of ${points.length}`}</strong>
              <div>{`Camera: ${entry.camera_name || entry.camera_code}`}</div>
              <div>{`Seen: ${new Date(entry.timestamp).toLocaleString()}`}</div>
              {entry.location && <div>{`Location: ${entry.location}`}</div>}
            </div>
          </Popup>
        </Marker>
      ))}
    </>
  );
}
