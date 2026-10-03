#include "ambient.h"
#include <cassert>
#include <cstring>
#include <iostream>
using namespace x4ambient;
int main() {
  Confirm c;
  c.press(100);
  assert(c.sample(799, true) == Press::None);
  assert(c.sample(799, false) == Press::Short);
  assert(c.sample(800, false) == Press::None);
  c.press(1000);
  assert(c.sample(1700, true) == Press::Long);
  assert(c.sample(9000, true) == Press::None);
  assert(c.sample(9001, false) == Press::None);
  c.press(0xffffff00u);
  assert(c.sample(0x000001bcu, false) == Press::Long);
  Power p;
  assert(!p.sample(0, true));
  assert(!p.sample(2000, true)); // waking hold cannot switch off
  assert(!p.sample(2001, false));
  assert(!p.sample(2100, true));
  assert(!p.sample(3599, true));
  assert(p.sample(3600, true));
  assert(!p.sample(5000, true));
  Session s;
  s.configure(100, 120);
  assert(!s.expired(120099));
  assert(s.expired(120100));
  s.activity(120000);
  assert(!s.expired(240000 - 1));
  assert(s.expired(240000));
  s.configure(125000, 120); // periodic 304 must not create an eternal session
  assert(s.expired(240000));
  s.configure(250000, 0);
  assert(s.expired(250000));
  Session stale;
  stale.configure(5000, 120);
  stale.activity(1000); // queued startup activity cannot shorten a new session
  assert(!stale.expired(124999));
  assert(stale.expired(125000));
  Session wrapped;
  wrapped.configure(0xfffffff0u, 120);
  assert(!wrapped.expired(0x10));
  assert(wrapped.expired(0xfffffff0u + 120000u));
  assert(std::strcmp(wakeName(Wake::Timer), "timer") == 0);
  assert(std::strcmp(wakeName(Wake::Button), "button") == 0);
  assert(std::strcmp(wakeName(Wake::Boot), "boot") == 0);
  assert(sleepPolicy(false).latchHigh && sleepPolicy(false).timer && !sleepPolicy(false).redraw);
  assert(!sleepPolicy(true).latchHigh && !sleepPolicy(true).timer && sleepPolicy(true).redraw);
  Panel panel;
  assert(panel.splash(false));
  assert(!panel.splash(true));
  assert(!panel.conditional()); // no retained framebuffer on wake, must fetch 200
  assert(panel.fullRefresh());
  panel.accept();
  assert(panel.conditional() && !panel.fullRefresh());
  std::cout << "ambient helpers: all assertions passed\n";
}
