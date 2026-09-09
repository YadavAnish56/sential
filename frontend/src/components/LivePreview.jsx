import React, { useEffect, useRef, useState } from 'react';

const LivePreview = ({ webrtcUrl }) => {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    let peerConnection = new RTCPeerConnection();
    pcRef.current = peerConnection;

    // We only want to receive video/audio
    peerConnection.addTransceiver('video', { direction: 'recvonly' });
    peerConnection.addTransceiver('audio', { direction: 'recvonly' });

    peerConnection.ontrack = (event) => {
      if (!active) return;
      if (videoRef.current) {
        // Assign the stream if not already assigned
        if (videoRef.current.srcObject !== event.streams[0]) {
          videoRef.current.srcObject = event.streams[0];
          setIsLoading(false);
        }
      }
    };

    peerConnection.onconnectionstatechange = () => {
      if (!active) return;
      if (peerConnection.connectionState === 'failed') {
        setError('Connection failed. The stream might be offline.');
        setIsLoading(false);
      }
    };

    const startNegotiation = async () => {
      try {
        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);

        if (!active) return;

        // WHEP POST request
        const response = await fetch(webrtcUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/sdp',
          },
          body: offer.sdp,
        });

        if (!active) return;

        if (!response.ok) {
          throw new Error(`Failed to connect to stream: HTTP ${response.status}`);
        }

        const answerSdp = await response.text();
        await peerConnection.setRemoteDescription({
          type: 'answer',
          sdp: answerSdp,
        });
      } catch (err) {
        if (!active) return;
        setError(err.message || 'Failed to negotiate WebRTC connection');
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
  }, [webrtcUrl]);

  return (
    <div className="live-preview-container" style={{ marginTop: '1rem', background: '#000', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
      {isLoading && !error && (
        <div style={{ padding: '2rem', color: '#fff', textAlign: 'center' }}>Connecting to live stream...</div>
      )}
      {error && (
        <div style={{ padding: '1rem', color: '#ff4d4f', background: '#2b0000', textAlign: 'center' }}>
          {error}
        </div>
      )}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        style={{
          width: '100%',
          display: (isLoading || error) ? 'none' : 'block'
        }}
      />
    </div>
  );
};

export default LivePreview;
