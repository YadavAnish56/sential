import React, { useState } from 'react';
import { analyticsService } from '../api/analyticsService';

export default function VehicleSearch({ onPlateSelect }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);

  const handleSearch = async (e) => {
    e.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;

    setLoading(true);
    setError(null);
    setHasSearched(true);

    try {
      const data = await analyticsService.searchVehicles(trimmedQuery);
      setResults(data || []);
    } catch (err) {
      setError('Search failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="vehicle-search" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px', marginBottom: '1rem' }}>
      <h2 style={{ marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #333', paddingBottom: '0.5rem' }}>Vehicle Search</h2>
      
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
        <input 
          type="text" 
          value={query} 
          onChange={(e) => setQuery(e.target.value)} 
          placeholder="Enter plate number..." 
          style={{ flex: 1, padding: '0.5rem', borderRadius: '4px', border: '1px solid #333', background: '#2c2c2c', color: '#fff' }}
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !query.trim()}>
          Search
        </button>
      </form>

      {loading && <div className="search-loading" style={{ color: '#888' }}>Searching...</div>}
      
      {error && <div className="search-error" style={{ color: '#ff4d4f' }}>{error}</div>}
      
      {!loading && !error && hasSearched && results.length === 0 && (
        <div className="search-empty" style={{ color: '#888' }}>No vehicles found matching "{query}".</div>
      )}

      {!loading && !error && results.length > 0 && (
        <ul className="search-results" style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: '300px', overflowY: 'auto' }}>
          {results.map((vehicle) => (
            <li key={vehicle.id} style={{ padding: '0.75rem', background: '#2c2c2c', marginBottom: '0.5rem', borderRadius: '4px', borderLeft: '4px solid #1890ff', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 'bold' }}>{vehicle.plate_number}</div>
                {(vehicle.vehicle_type || vehicle.color || vehicle.make || vehicle.model) && (
                  <div style={{ fontSize: '0.85rem', color: '#aaa', marginTop: '0.25rem' }}>
                    {[vehicle.color, vehicle.make, vehicle.model, vehicle.vehicle_type].filter(Boolean).join(' ')}
                  </div>
                )}
              </div>
              <button 
                className="btn btn-secondary" 
                onClick={() => onPlateSelect(vehicle.plate_number)}
                style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
              >
                View Timeline
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
