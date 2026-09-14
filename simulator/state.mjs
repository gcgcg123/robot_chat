export function mayRecord(state) {
  return state === 'listening' || state === 'enrollment_recording';
}
