#pragma once
#include <cstdint>
#include <limits>

namespace x4event {
// Called once for each distinct dequeued event, BEFORE any network attempt.
// A failed/ambiguous POST burns its identity: no automatic retry in this MVP.
// Persist failure vetoes sending, and exhaustion fails closed (never wraps).
template<class Persist>
bool reserveDistinctEvent(uint32_t& last, uint32_t& sequence, Persist persist) {
  if (last == std::numeric_limits<uint32_t>::max()) return false;
  const uint32_t next = ++last;
  if (!persist(next)) return false;
  sequence = next;
  return true;
}
}  // namespace x4event
