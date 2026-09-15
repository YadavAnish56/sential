import { describe, expect, it } from 'vitest';
import { escapeCsvValue, toCsv, timestampedFilename } from './csv';

describe('escapeCsvValue', () => {
  it('quotes plain values', () => {
    expect(escapeCsvValue('GJ05AB1234')).toBe('"GJ05AB1234"');
  });

  it('keeps a value containing a comma in one column', () => {
    expect(escapeCsvValue('Ahmedabad, Gujarat')).toBe('"Ahmedabad, Gujarat"');
  });

  it('doubles embedded quotes so the row does not break', () => {
    expect(escapeCsvValue('say "hello"')).toBe('"say ""hello"""');
  });

  it('renders null and undefined as empty, not as the words', () => {
    expect(escapeCsvValue(null)).toBe('""');
    expect(escapeCsvValue(undefined)).toBe('""');
  });

  it('keeps zero and false rather than blanking them', () => {
    expect(escapeCsvValue(0)).toBe('"0"');
    expect(escapeCsvValue(false)).toBe('"false"');
  });

  // A spreadsheet executes a cell starting with these, and plate/camera text
  // comes from OCR and from operator input.
  it.each(['=1+1', '+1', '-1', '@SUM(A1)', '\tx', '\rx'])(
    'defuses the formula prefix in %j',
    (value) => {
      expect(escapeCsvValue(value)).toBe(`"'${value.replace(/"/g, '""')}"`);
    },
  );

  it('defuses a formula that also contains quotes', () => {
    expect(escapeCsvValue('=HYPERLINK("http://x")')).toBe('"\'=HYPERLINK(""http://x"")"');
  });

  it('leaves a plain minus inside a value alone', () => {
    expect(escapeCsvValue('CAM-001')).toBe('"CAM-001"');
  });
});

describe('toCsv', () => {
  it('writes a header row and CRLF-separated rows', () => {
    const csv = toCsv(['Plate', 'Camera'], [['GJ05AB1234', 'CAM-001'], ['GJ01XY9999', 'CAM-002']]);
    expect(csv).toBe(
      '"Plate","Camera"\r\n"GJ05AB1234","CAM-001"\r\n"GJ01XY9999","CAM-002"',
    );
  });

  it('writes just the header when there are no rows', () => {
    expect(toCsv(['Plate'], [])).toBe('"Plate"');
  });
});

describe('timestampedFilename', () => {
  it('ends in .csv and carries an ISO date', () => {
    const name = timestampedFilename('sentinel_alerts');
    expect(name).toMatch(/^sentinel_alerts_\d{4}-\d{2}-\d{2}\.csv$/);
  });
});
