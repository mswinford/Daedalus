/** Placeholder for the run-input box, derived from the start node's declared input fields. */
export function runInputPlaceholder(fields: string[]): string {
  if (fields.length === 0) return 'Leave blank to run with no input, or e.g. {"score": 40}'
  return `{${fields.map((f) => `"${f}": ...`).join(', ')}}`
}
