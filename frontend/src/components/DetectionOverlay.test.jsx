import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import DetectionOverlay from './DetectionOverlay';

// jsdom reports every element as 0x0 and never decodes video, so the rendered
// picture rectangle is stubbed to a known size and the box maths checked
// against it.
function makeRefs({ containerW = 800, containerH = 400, videoW = 1920, videoH = 1080 } = {}) {
  const container = document.createElement('div');
  Object.defineProperty(container, 'clientWidth', { value: containerW, configurable: true });
  Object.defineProperty(container, 'clientHeight', { value: containerH, configurable: true });

  const video = document.createElement('video');
  Object.defineProperty(video, 'videoWidth', { value: videoW, configurable: true });
  Object.defineProperty(video, 'videoHeight', { value: videoH, configurable: true });

  return { videoRef: { current: video }, containerRef: { current: container } };
}

const boxes = [
  { track_id: 1, box: [0.1, 0.2, 0.3, 0.4], label: 'car', plate: 'GJ05AB1234' },
  { track_id: 2, box: [0.5, 0.1, 0.2, 0.2], label: 'truck', plate: null },
];

afterEach(cleanup);

describe('DetectionOverlay', () => {
  it('draws one box per tracked vehicle', () => {
    render(<DetectionOverlay boxes={boxes} {...makeRefs()} />);
    expect(screen.getAllByTestId(/detection-box-/)).toHaveLength(2);
  });

  it('marks only the investigated plate as the target', () => {
    render(<DetectionOverlay boxes={boxes} targetPlate="GJ05AB1234" {...makeRefs()} />);

    const target = screen.getAllByTestId('detection-box-target');
    expect(target).toHaveLength(1);
    expect(target[0]).toHaveAttribute('data-plate', 'GJ05AB1234');
    expect(screen.getAllByTestId('detection-box-vehicle')).toHaveLength(1);
  });

  it('matches the target plate regardless of spacing or case', () => {
    render(<DetectionOverlay boxes={boxes} targetPlate=" gj05 ab 1234 " {...makeRefs()} />);
    expect(screen.getAllByTestId('detection-box-target')).toHaveLength(1);
  });

  it('positions a box as a percentage of the rendered picture', () => {
    render(<DetectionOverlay boxes={[boxes[0]]} {...makeRefs()} />);
    const box = screen.getByTestId('detection-box-vehicle');
    expect(box.style.left).toBe('10%');
    expect(box.style.top).toBe('20%');
    expect(box.style.width).toBe('30%');
    expect(box.style.height).toBe('40%');
  });

  it('letterboxes the overlay onto the picture, not the whole element', () => {
    // 16:9 video inside a 2:1 container: bars on the left and right.
    render(<DetectionOverlay boxes={boxes} {...makeRefs()} />);
    const overlay = screen.getByTestId('detection-overlay');
    // height 400 -> width 400 * 16/9 = 711.11, centred in 800.
    expect(Math.round(parseFloat(overlay.style.width))).toBe(711);
    expect(Math.round(parseFloat(overlay.style.height))).toBe(400);
    expect(Math.round(parseFloat(overlay.style.left))).toBe(44);
    expect(Math.round(parseFloat(overlay.style.top))).toBe(0);
  });

  it('renders nothing when there are no boxes, or while hidden', () => {
    const refs = makeRefs();
    const { rerender } = render(<DetectionOverlay boxes={[]} {...refs} />);
    expect(screen.queryByTestId('detection-overlay')).not.toBeInTheDocument();

    rerender(<DetectionOverlay boxes={boxes} visible={false} {...refs} />);
    expect(screen.queryByTestId('detection-overlay')).not.toBeInTheDocument();

    rerender(<DetectionOverlay boxes={null} {...refs} />);
    expect(screen.queryByTestId('detection-overlay')).not.toBeInTheDocument();
  });

  it('skips malformed boxes instead of crashing', () => {
    const bad = [
      { track_id: 9, box: [0.1, 0.1, 0, 0.2] },     // zero width
      { track_id: 10, box: [0.1, 0.1] },            // too short
      { track_id: 11, box: null },                  // missing
      { track_id: 12, box: [0.2, 0.2, 0.2, 0.2] },  // valid
    ];
    render(<DetectionOverlay boxes={bad} {...makeRefs()} />);
    expect(screen.getAllByTestId(/detection-box-/)).toHaveLength(1);
  });

  it('carries the video transform so boxes follow zoom and pan', () => {
    render(<DetectionOverlay boxes={boxes} transform="scale(2)" {...makeRefs()} />);
    expect(screen.getByTestId('detection-overlay').style.transform).toBe('scale(2)');
  });
});
