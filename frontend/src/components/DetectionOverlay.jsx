import { useEffect, useState } from 'react';

/**
 * Draws the AI's bounding boxes on top of the live video.
 *
 * The video is laid out with `object-fit: contain`, so the picture is
 * letterboxed inside its element and does not fill it. Boxes arrive as
 * fractions of the source frame, so this measures where the picture actually
 * sits and maps the fractions onto that rectangle. Any zoom/pan transform
 * applied to the video is applied here too, so the boxes travel with it.
 *
 * Green marks a detected vehicle. Red marks the vehicle being investigated.
 */

function useRenderedVideoRect(videoRef, containerRef) {
  const [rect, setRect] = useState(null);

  useEffect(() => {
    const video = videoRef?.current;
    const container = containerRef?.current;
    if (!video || !container) return undefined;

    const measure = () => {
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (!cw || !ch || !vw || !vh) {
        setRect(null);
        return;
      }
      const containerAspect = cw / ch;
      const videoAspect = vw / vh;

      let width;
      let height;
      if (videoAspect > containerAspect) {
        // Limited by width: bars above and below.
        width = cw;
        height = cw / videoAspect;
      } else {
        // Limited by height: bars left and right.
        height = ch;
        width = ch * videoAspect;
      }
      setRect({
        left: (cw - width) / 2,
        top: (ch - height) / 2,
        width,
        height,
      });
    };

    measure();

    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (observer) observer.observe(container);
    video.addEventListener('loadedmetadata', measure);
    video.addEventListener('resize', measure);
    window.addEventListener('resize', measure);

    return () => {
      if (observer) observer.disconnect();
      video.removeEventListener('loadedmetadata', measure);
      video.removeEventListener('resize', measure);
      window.removeEventListener('resize', measure);
    };
  }, [videoRef, containerRef]);

  return rect;
}

function normalisePlate(value) {
  return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

export default function DetectionOverlay({
  boxes,
  videoRef,
  containerRef,
  targetPlate = null,
  transform = 'none',
  visible = true,
}) {
  const rect = useRenderedVideoRect(videoRef, containerRef);
  // Derived straight from the prop: a ref here would only risk going stale.
  const target = normalisePlate(targetPlate);

  if (!visible || !rect || !Array.isArray(boxes) || boxes.length === 0) {
    return null;
  }

  return (
    <div
      className="detection-overlay"
      data-testid="detection-overlay"
      aria-hidden="true"
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        pointerEvents: 'none',
        transform,
        transformOrigin: 'center center',
      }}
    >
      {boxes.map((item, index) => {
        const box = item?.box;
        if (!Array.isArray(box) || box.length < 4) return null;
        const [x, y, w, h] = box;
        if (!(w > 0) || !(h > 0)) return null;

        const plate = normalisePlate(item.plate);
        const isTarget = Boolean(target) && plate === target;
        const colour = isTarget ? '#ff3b30' : '#22c55e';
        const label = item.plate || item.label || 'vehicle';

        return (
          <div
            key={item.track_id ?? `box-${index}`}
            className={`detection-box ${isTarget ? 'detection-box-target' : 'detection-box-vehicle'}`}
            data-testid={isTarget ? 'detection-box-target' : 'detection-box-vehicle'}
            data-plate={item.plate || ''}
            style={{
              position: 'absolute',
              left: `${x * 100}%`,
              top: `${y * 100}%`,
              width: `${w * 100}%`,
              height: `${h * 100}%`,
              border: `2px solid ${colour}`,
              borderRadius: '2px',
              boxShadow: isTarget ? `0 0 0 1px rgba(0,0,0,0.5), 0 0 12px ${colour}` : '0 0 0 1px rgba(0,0,0,0.4)',
              boxSizing: 'border-box',
            }}
          >
            <span
              className="detection-box-label"
              style={{
                position: 'absolute',
                bottom: '100%',
                left: 0,
                marginBottom: '2px',
                padding: '1px 5px',
                background: colour,
                color: isTarget ? '#fff' : '#052e16',
                font: '600 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace',
                whiteSpace: 'nowrap',
                borderRadius: '2px',
              }}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
