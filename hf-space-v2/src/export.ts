// P2 utilities: CSV/JSON export with spreadsheet formula-injection protection.
// CSV cells beginning with =, +, -, @ are prefixed with a single quote so
// Excel/LibreOffice/Sheets render them as text instead of formulas.
export function safeCsvCell(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
}

export function toCsv(headers: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    const cell = safeCsvCell(v).replace(/"/g, '""');
    return /[",\n]/.test(cell) ? `"${cell}"` : cell;
  };
  return [headers.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\r\n");
}

export function download(filename: string, content: string, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
