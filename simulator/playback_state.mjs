export function shouldApplyEvent(activeTurnId, event) {
  return activeTurnId !== null && activeTurnId === event?.turn_id;
}
