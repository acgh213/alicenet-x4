#pragma once
#include <cstdint>

namespace x4ambient {
enum class Press { None, Short, Long };

// Called by the input consumer, independently of HTTP/render latency.
class Confirm {
 public:
  void press(uint32_t now) {
    if (!pending_) { pending_ = true; sent_ = false; start_ = now; }
  }
  Press sample(uint32_t now, bool down) {
    if (!pending_) return Press::None;
    Press result = Press::None;
    if (!sent_ && uint32_t(now - start_) >= 700) { result = Press::Long; sent_ = true; }
    if (!down) {
      if (!sent_) result = Press::Short;
      pending_ = false;
    }
    return result;
  }
 private:
  bool pending_ = false, sent_ = false;
  uint32_t start_ = 0;
};

class Power {
 public:
  bool sample(uint32_t now, bool down) {
    if (!down) { armed_ = true; down_ = false; sent_ = false; return false; }
    if (!armed_) return false;
    if (!down_) { down_ = true; start_ = now; }
    if (!sent_ && uint32_t(now - start_) >= 1500) { sent_ = true; return true; }
    return false;
  }
 private:
  bool armed_ = false, down_ = false, sent_ = false;
  uint32_t start_ = 0;
};

class Session {
 public:
  void configure(uint32_t now, uint32_t seconds) {
    // A malformed remote value must not overflow a wrap-safe deadline.
    const uint32_t duration = (seconds > 600 ? 600 : seconds) * 1000;
    if (!configured_ || duration != duration_) last_ = now;
    configured_ = true;
    duration_ = duration;
  }
  void activity(uint32_t now) {
    // Ignore older queued activity after a new card/session was configured.
    if (static_cast<int32_t>(now - last_) >= 0) last_ = now;
  }
  bool expired(uint32_t now) const { return duration_ == 0 || uint32_t(now - last_) >= duration_; }
 private:
  bool configured_ = false;
  uint32_t duration_ = 0, last_ = 0;
};

enum class Wake { Timer, Button, Boot };
inline const char* wakeName(Wake wake) {
  return wake == Wake::Timer ? "timer" : wake == Wake::Button ? "button" : "boot";
}
struct SleepPolicy { bool latchHigh, timer, redraw; };
inline SleepPolicy sleepPolicy(bool manual) { return {!manual, !manual, manual}; }

class Panel {
 public:
  bool splash(bool everSucceeded) const { return !everSucceeded; }
  bool conditional() const { return ready_; }
  bool fullRefresh() const { return !ready_; }
  void accept() { ready_ = true; }
 private:
  bool ready_ = false;
};
} // namespace x4ambient
