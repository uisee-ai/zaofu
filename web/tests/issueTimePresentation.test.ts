import { issueOpenedAgo, issueCreatedAt } from "../src/pages/issueTimePresentation.js";
function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }
const now = Date.parse('2026-09-11T12:00:00Z');
assert(issueOpenedAgo('2026-09-08T12:00:00Z', now) === 'opened 3d ago', 'age must use the upstream created_at');
assert(issueOpenedAgo('2026-09-11T10:00:00Z', now) === 'opened 2h ago', 'hour formatting');
assert(issueOpenedAgo('2026-09-11T11:58:00Z', now) === 'opened 2m ago', 'minute formatting');
assert(issueOpenedAgo('2026-09-11T12:00:10Z', now) === 'opened just now', 'future clock skew cannot show negative age');
assert(issueOpenedAgo('', now) === 'Creation date unavailable', 'missing date cannot use updated_at or today');
assert(issueOpenedAgo('not-a-date', now) === 'Creation date unavailable', 'invalid date must be safe');
const absolute = issueCreatedAt('2026-09-08T12:00:00Z');
assert(absolute.dateTime === '2026-09-08T12:00:00.000Z', 'machine-readable time is unambiguous');
assert(/^2026-09-08 \d{2}:\d{2}:\d{2}$/.test(absolute.text), 'exact time includes seconds');
assert(/^Local time \(UTC[+-]\d{2}:\d{2}\)$/.test(absolute.timeZone), 'timezone must be available in the hint');
assert(issueCreatedAt('').text === '—' && !issueCreatedAt('').dateTime, 'missing absolute time has no fake datetime');
console.log('issueTimePresentation assertions passed');
