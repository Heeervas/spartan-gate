import { createHash } from 'node:crypto';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NATIVE_ID = /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/;

// This versioned mapping is a persistence contract. Keep native session IDs/files
// untouched; Route's binding and usage APIs retain their canonical UUID contract.
export function routeSessionId(value) {
  if (typeof value !== 'string' || value.length > 256 || !NATIVE_ID.test(value)) return undefined;
  if (UUID.test(value)) return value.toLowerCase();
  const bytes = createHash('sha256').update(`spartan-route:pi-session:v1\0${value}`).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x80;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
