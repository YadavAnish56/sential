import React from 'react';

const STATUS_CONFIG = {
  STOPPED: { label: 'Stopped', className: 'status-stopped' },
  STARTING: { label: 'Starting...', className: 'status-starting' },
  RUNNING: { label: 'Running', className: 'status-running' },
  STOPPING: { label: 'Stopping...', className: 'status-stopping' },
  ERROR: { label: 'Error', className: 'status-error' },
  unregistered: { label: 'Unregistered', className: 'status-stopped' },
  unknown: { label: 'Unknown', className: 'status-unknown' },
};

export default function PipelineStatusBadge({ status }) {
  const normalizedStatus = status ? status.toUpperCase() : 'unknown';
  const config = STATUS_CONFIG[normalizedStatus] || STATUS_CONFIG[status] || STATUS_CONFIG.unknown;

  return (
    <span className={`status-badge ${config.className}`}>
      {config.label}
    </span>
  );
}
