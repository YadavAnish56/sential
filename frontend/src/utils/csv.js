/**
 * CSV helpers for the Records export.
 *
 * Two things matter here beyond joining commas:
 *
 * 1. Quoting. A value containing a comma, quote or newline has to be wrapped
 *    and its quotes doubled, or the row silently gains columns.
 * 2. Formula injection. Spreadsheets execute a cell beginning with = + - @ (or
 *    a tab/carriage return). Plate and camera text reaches us from ANPR and
 *    from whatever an operator typed, so such a value is prefixed with a
 *    single quote and kept as text.
 */

const FORMULA_PREFIXES = ['=', '+', '-', '@', '\t', '\r'];

export function escapeCsvValue(value) {
  if (value === null || value === undefined) return '""';

  let text = String(value);

  if (FORMULA_PREFIXES.some((prefix) => text.startsWith(prefix))) {
    text = `'${text}`;
  }

  return `"${text.replace(/"/g, '""')}"`;
}

export function toCsv(headers, rows) {
  const headerLine = headers.map(escapeCsvValue).join(',');
  const bodyLines = rows.map((row) => row.map(escapeCsvValue).join(','));
  return [headerLine, ...bodyLines].join('\r\n');
}

/**
 * Hand the CSV to the browser as a download.
 *
 * A UTF-8 BOM is prepended so Excel reads non-ASCII place names correctly
 * instead of mojibake.
 */
export function downloadCsv(filename, csvContent) {
  const blob = new Blob(['﻿', csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function timestampedFilename(prefix) {
  return `${prefix}_${new Date().toISOString().slice(0, 10)}.csv`;
}
