#include "event_sequence.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <limits>

int main() {
  uint32_t last = 40, persisted = last, first = 0, second = 0;
  unsigned writes = 0;
  auto persist = [&](uint32_t value) { persisted = value; ++writes; return true; };
  assert(x4event::reserveDistinctEvent(last, first, persist));
  // Before any HTTP attempt, allocation must already be durable.
  const bool durableBeforeNetwork = first == 41 && last == 41 && persisted == first && writes == 1;
  // Server committed event 41 but its ACK was lost: there is deliberately NO
  // success callback. The next distinct physical press must not reuse 41.
  assert(x4event::reserveDistinctEvent(last, second, persist));
  assert(second == 42 && second != first && persisted == second && writes == 2);
  assert(durableBeforeNetwork);
  // Reconstruct after a reboot using the persisted value.
  uint32_t restored = persisted, third = 0;
  assert(x4event::reserveDistinctEvent(restored, third, persist));
  assert(third == 43 && persisted == third);
  // An NVS write failure must veto sending, not silently reuse an ID.
  uint32_t rejected = 0;
  assert(!x4event::reserveDistinctEvent(restored, rejected, [](uint32_t) { return false; }));
  assert(rejected == 0 && restored == 44);
  assert(x4event::reserveDistinctEvent(restored, third, persist));
  assert(third == 45);
  // Exhaustion fails closed rather than wrapping and aliasing an old event.
  restored = std::numeric_limits<uint32_t>::max();
  const unsigned before = writes;
  assert(!x4event::reserveDistinctEvent(restored, third, persist));
  assert(restored == std::numeric_limits<uint32_t>::max() && writes == before);
  puts("event_sequence: durable allocation/lost-ACK/new-event/failure/exhaustion PASS");
}
