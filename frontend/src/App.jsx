import React, { useState } from 'react';
import CameraList from './components/CameraList';
import EventFeed from './components/EventFeed';
import VehicleTimeline from './components/VehicleTimeline';
import VehicleSearch from './components/VehicleSearch';
import AlertsInbox from './components/AlertsInbox';
import './App.css';

function App() {
  const [selectedPlate, setSelectedPlate] = useState(null);

  const handlePlateSelect = (plateNumber) => {
    setSelectedPlate(plateNumber);
  };

  const handleCloseTimeline = () => {
    setSelectedPlate(null);
  };

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Sentinel</h1>
        <p>Unified CCTV Viewing & Selective Analytics Platform</p>
      </header>
      <main className="app-main" style={{ display: 'flex', gap: '2rem', padding: '1rem', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 60%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <AlertsInbox />
          <CameraList />
        </div>
        <div style={{ flex: '1 1 40%', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <VehicleSearch onPlateSelect={handlePlateSelect} />
          <VehicleTimeline plateNumber={selectedPlate} onClose={handleCloseTimeline} />
          <EventFeed onPlateSelect={handlePlateSelect} />
        </div>
      </main>
    </div>
  );
}

export default App;
