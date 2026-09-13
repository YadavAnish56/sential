import React, { useEffect, useRef, useState } from 'react';

const LivePreview = ({
  webrtcUrl,
  isTestMode = false,
  onStreamStatusChange = null,
  enableEnhancements = false,
  zoom = 1,
  pan = { x: 0, y: 0 },
  filterPreset = 'normal',
  onPanChange = null,
}) => {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const containerRef = useRef(null);
  const isDraggingRef = useRef(false);
  const dragStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 });
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(!isTestMode);

  // Calculate pan clamping strictly within rendered video content bounds (accounts for object-fit: contain and letterboxing)
  const clampPan = (targetX, targetY, currentZoom) => {
    if (!containerRef.current || currentZoom <= 1) {
      return { x: 0, y: 0 };
    }
    const rect = containerRef.current.getBoundingClientRect();
    const containerW = rect.width;
    const containerH = rect.height;
    if (containerW <= 0 || containerH <= 0) {
      return { x: 0, y: 0 };
    }

    // Determine intrinsic video aspect ratio (fallback to 16:9 standard for CCTV)
    const videoEl = videoRef.current;
    let videoAspect = 16 / 9;
    if (videoEl && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
      videoAspect = videoEl.videoWidth / videoEl.videoHeight;
    }

    const containerAspect = containerW / containerH;
    let renderedW = containerW;
    let renderedH = containerH;

    if (videoAspect > containerAspect) {
      // Letterboxed on top/bottom
      renderedW = containerW;
      renderedH = containerW / videoAspect;
    } else {
      // Pillarboxed on left/right
      renderedH = containerH;
      renderedW = containerH * videoAspect;
    }

    // Maximum pan bounds to allow reaching video edges while preventing black gap voids
    const maxPanX = Math.max(0, (renderedW * currentZoom - containerW) / 2);
    const maxPanY = Math.max(0, (renderedH * currentZoom - containerH) / 2);

    return {
      x: Math.max(-maxPanX, Math.min(maxPanX, targetX)),
      y: Math.max(-maxPanY, Math.min(maxPanY, targetY)),
    };
  };

  const handlePointerDown = (e) => {
    if (!enableEnhancements || zoom <= 1) return;
    if (e.button !== undefined && e.button !== 0) return;
    isDraggingRef.current = true;
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: pan.x,
      panY: pan.y,
    };
    if (e.currentTarget && typeof e.currentTarget.setPointerCapture === 'function') {
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch (_) {}
    }
  };

  const handlePointerMove = (e) => {
    if (!isDraggingRef.current || !enableEnhancements || zoom <= 1) return;
    const dx = e.clientX - dragStartRef.current.x;
    const dy = e.clientY - dragStartRef.current.y;
    const rawX = dragStartRef.current.panX + dx;
    const rawY = dragStartRef.current.panY + dy;
    const clamped = clampPan(rawX, rawY, zoom);
    if (onPanChange) {
      onPanChange(clamped);
    }
  };

  const handlePointerUp = (e) => {
    if (isDraggingRef.current) {
      isDraggingRef.current = false;
      if (e.currentTarget && typeof e.currentTarget.releasePointerCapture === 'function') {
        try {
          e.currentTarget.releasePointerCapture(e.pointerId);
        } catch (_) {}
      }
    }
  };

  useEffect(() => {
    // 1. If explicit test camera, don't attempt WebRTC negotiation
    if (isTestMode) {
      setIsLoading(false);
      setError('Test camera node has no active stream configured.');
      if (onStreamStatusChange) {
        onStreamStatusChange('OFFLINE', 'Test camera has no active stream configured.');
      }
      return;
    }

    if (typeof RTCPeerConnection === 'undefined') {
      const msg = 'WebRTC is not supported in this environment.';
      setError(msg);
      setIsLoading(false);
      if (onStreamStatusChange) onStreamStatusChange('OFFLINE', msg);
      return;
    }

    if (!webrtcUrl) {
      setIsLoading(false);
      return;
    }

    let active = true;
    let peerConnection = new RTCPeerConnection();
    pcRef.current = peerConnection;

    // Receive only video/audio
    peerConnection.addTransceiver('video', { direction: 'recvonly' });
    peerConnection.addTransceiver('audio', { direction: 'recvonly' });

    peerConnection.ontrack = (event) => {
      if (!active) return;
      if (videoRef.current) {
        if (videoRef.current.srcObject !== event.streams[0]) {
          videoRef.current.srcObject = event.streams[0];
          setIsLoading(false);
          setError(null);
          if (onStreamStatusChange) onStreamStatusChange('LIVE', null);
        }
      }
    };

    peerConnection.onconnectionstatechange = () => {
      if (!active) return;
      if (peerConnection.connectionState === 'failed') {
        const msg = 'Connection failed. Stream might be offline or gateway timed out.';
        setError(msg);
        setIsLoading(false);
        if (onStreamStatusChange) onStreamStatusChange('OFFLINE', msg);
      } else if (peerConnection.connectionState === 'connected') {
        setIsLoading(false);
        setError(null);
        if (onStreamStatusChange) onStreamStatusChange('LIVE', null);
      }
    };

    const startNegotiation = async () => {
      try {
        setIsLoading(true);
        setError(null);

        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);

        // Wait for ICE gathering to complete (WHEP standard)
        if (peerConnection.iceGatheringState && peerConnection.iceGatheringState !== 'complete') {
          await new Promise((resolve) => {
            const timeout = setTimeout(resolve, 1200);
            if (typeof peerConnection.addEventListener === 'function') {
              const checkState = () => {
                if (peerConnection.iceGatheringState === 'complete') {
                  try {
                    peerConnection.removeEventListener('icegatheringstatechange', checkState);
                  } catch (_) {}
                  clearTimeout(timeout);
                  resolve();
                }
              };
              peerConnection.addEventListener('icegatheringstatechange', checkState);
            } else {
              peerConnection.onicegatheringstatechange = () => {
                if (peerConnection.iceGatheringState === 'complete') {
                  clearTimeout(timeout);
                  resolve();
                }
              };
            }
          });
        }

        if (!active) return;

        // WHEP POST request to Sentinel FastAPI proxy
        const offerSdp = peerConnection.localDescription ? peerConnection.localDescription.sdp : offer.sdp;
        const response = await fetch(webrtcUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/sdp',
          },
          body: offerSdp,
        });

        if (!active) return;

        if (!response.ok) {
          const errText = await response.text();
          let detail = `HTTP ${response.status}`;
          try {
            const errJson = JSON.parse(errText);
            if (errJson.detail) detail = errJson.detail;
          } catch {
            if (errText && errText.length < 120) detail = errText;
          }

          let category = 'OFFLINE';
          let humanReason = detail;

          if (detail.includes('AUTHENTICATION_REQUIRED') || response.status === 401) {
            category = 'AUTH REQUIRED';
            humanReason = 'Stream gateway authentication required. Verify server credentials.';
          } else if (detail.includes('UPSTREAM_NOT_FOUND') || response.status === 404) {
            category = 'UPSTREAM NOT FOUND';
            humanReason = 'Stream route unavailable on gateway (HTTP 404).';
          } else if (detail.includes('NETWORK_ERROR')) {
            category = 'OFFLINE';
            humanReason = 'Stream gateway unreachable.';
          }

          if (onStreamStatusChange) onStreamStatusChange(category, humanReason);
          throw new Error(humanReason);
        }

        const answerSdp = await response.text();
        await peerConnection.setRemoteDescription({
          type: 'answer',
          sdp: answerSdp,
        });

        if (videoRef.current) {
          videoRef.current.muted = true;
          try {
            const playPromise = videoRef.current.play();
            if (playPromise && typeof playPromise.catch === 'function') {
              playPromise.catch(() => {});
            }
          } catch (_) {}
        }
      } catch (err) {
        if (!active) return;
        setError(err.message || 'Stream offline: Failed to negotiate WebRTC connection');
        setIsLoading(false);
      }
    };

    startNegotiation();

    return () => {
      active = false;
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
      }
      if (videoRef.current && videoRef.current.srcObject) {
        const tracks = videoRef.current.srcObject.getTracks();
        tracks.forEach(track => track.stop());
        videoRef.current.srcObject = null;
      }
    };
  }, [webrtcUrl, isTestMode]);

  // Display presentation filter string
  let filterCss = 'none';
  if (enableEnhancements) {
    if (filterPreset === 'contrast') {
      filterCss = 'contrast(1.35) brightness(1.05) saturate(1.1)';
    } else if (filterPreset === 'night') {
      filterCss = 'contrast(1.45) brightness(1.25) grayscale(0.25)';
    }
  }

  // Active clamped pan for style
  const activePan = enableEnhancements && zoom > 1 ? clampPan(pan.x, pan.y, zoom) : { x: 0, y: 0 };

  const videoTransform = enableEnhancements && zoom > 1
    ? `translate(${activePan.x}px, ${activePan.y}px) scale(${zoom})`
    : (zoom > 1 ? `scale(${zoom})` : 'none');

  const videoCursor = enableEnhancements && zoom > 1 ? 'grab' : 'default';

  return (
    <div
      ref={containerRef}
      className={`live-preview-container ${enableEnhancements && zoom > 1 ? 'zoom-active' : ''}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      style={{
        overflow: 'hidden',
        position: 'relative',
        touchAction: enableEnhancements && zoom > 1 ? 'none' : 'auto',
      }}
    >
      {isTestMode ? (
        <div className="test-camera-placeholder" role="note">
          <div className="placeholder-tag">[TEST NODE]</div>
          <div className="placeholder-title">TEST CAMERA • VIDEO OFFLINE</div>
          <p className="placeholder-desc">
            This node is configured as a test/development camera. No active RTSP/WHEP live feed exists on the government gateway.
          </p>
        </div>
      ) : (
        <>
          {isLoading && !error && (
            <div className="preview-loading-deck">
              <div className="preview-spinner"></div>
              <span>Connecting to live stream...</span>
            </div>
          )}

          {error && (
            <div className="preview-error-deck" role="alert">
              <div className="error-title">STREAM OFFLINE</div>
              <div className="error-reason">{error}</div>
            </div>
          )}

          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`live-video-element ${enableEnhancements && filterPreset !== 'normal' ? `filter-${filterPreset}` : ''}`}
            style={{
              display: (isLoading || error) ? 'none' : 'block',
              transform: videoTransform,
              transformOrigin: 'center center',
              filter: filterCss,
              cursor: videoCursor,
              transition: isDraggingRef.current ? 'none' : 'transform 0.15s ease-out',
            }}
          />
        </>
      )}
    </div>
  );
};

export default LivePreview;
