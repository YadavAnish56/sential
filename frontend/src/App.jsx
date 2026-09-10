import React, { useState, useCallback } from 'react';
import CameraList from './components/CameraList';
import CameraMap from './components/CameraMap';
import EventFeed from './components/EventFeed';
import VehicleTimeline from './components/VehicleTimeline';
import VehicleSearch from './components/VehicleSearch';
import AlertsInbox from './components/AlertsInbox';
import './App.css';

function App() {
  const [selectedPlate, setSelectedPlate] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState(null);

  const handlePlateSelect = (plateNumber) => {
    setSelectedPlate(plateNumber);
    setTimeline([]);
    setSelectedEventId(null);
  };

  const handleCloseTimeline = () => {
    setSelectedPlate(null);
    setTimeline([]);
    setSelectedEventId(null);
  };

  // The timeline is fetched once by VehicleTimeline and shared with the map,
  // so selecting a plate never issues the same request twice.
  const handleTimelineLoaded = useCallback((entries) => {
    setTimeline(entries);
  }, []);

  const handleSelectEvent = useCallback((eventId) => {
    setSelectedEventId((current) => (current === eventId ? null : eventId));
  }, []);

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Sentinel</h1>
        <p>Unified CCTV Viewing & Selective Analytics Platform</p>
      </header>
      <main className="app-main" style={{ display: 'flex', gap: '2rem', padding: '1rem', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 60%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <AlertsInbox />
          <CameraMap
            plateNumber={selectedPlate}
            timeline={timeline}
            selectedEventId={selectedEventId}
            onSelectEvent={handleSelectEvent}
          />
          <CameraList />
        </div>
        <div style={{ flex: '1 1 40%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <VehicleSearch onPlateSelect={handlePlateSelect} />
          <VehicleTimeline
            plateNumber={selectedPlate}
            onClose={handleCloseTimeline}
            onTimelineLoaded={handleTimelineLoaded}
            selectedEventId={selectedEventId}
            onSelectEvent={handleSelectEvent}
          />
          <EventFeed onPlateSelect={handlePlateSelect} />
        </div>
      </main>
    </div>
  );
}

export default App;
