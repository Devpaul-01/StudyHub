/**
 * HTML escaping for values interpolated into template strings.
 *
 * Every user-controlled value (names, bios, departments, skills, goals,
 * post titles, ...) must go through escapeHtml() before it is placed in
 * innerHTML, in text content and in attribute values alike.
 */
export function escapeHtml(value) {
  if (value == null) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
