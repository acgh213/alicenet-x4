// Prints event bodies exactly as the firmware builds them, one per line, so
// test_event_body_contract.py can feed them to the gateway's real validator.
#include "json_out.h"

#include <cstdio>

int main() {
  char body[512];
  // The live failure case: real card + quoted ETag from x4d.
  if (x4json::eventBody(body, sizeof body, "x4-01", 7, 1, "network-smoke", "\"0530d69b7be984b1\"", "confirm",
                        "short", "button") < 0)
    return 1;
  std::puts(body);
  // No card shown yet / no ETag -> nulls.
  if (x4json::eventBody(body, sizeof body, "x4-01", 7, 2, "", "", "back", "short", "session") < 0) return 1;
  std::puts(body);
  return 0;
}
