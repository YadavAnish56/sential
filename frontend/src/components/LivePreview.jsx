import React, { useEffect, useRef, useState } from 'react';

const LivePreview = ({ webrtcUrl, isTestMode = false, onStreamStatusChange = null }) => {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(!isTestMode);

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

  return (
    <div className="live-preview-container">
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
            className="live-video-element"
            style={{
              display: (isLoading || error) ? 'none' : 'block'
            }}
          />
        </>
      )}
    </div>
  );
};

export default LivePreview;
