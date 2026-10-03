// Host test for JSON string output used in POST /x4/v1/events. Build & run:
//   g++ -std=c++17 -Wall -Werror -I../src test_json_out.cpp -o /tmp/t && /tmp/t
// Regression: the ETag header value carries its own quotes ("0530..."), and
// 0.2.3 wrapped it in another pair -> invalid JSON -> x4d 400 on every press.
#include "json_out.h"

#include <cstdio>
#include <cstring>

static int failures = 0;
#define CHECK(cond)                                               \
  do {                                                            \
    if (!(cond)) {                                                \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
      ++failures;                                                 \
    }                                                             \
  } while (0)

int main() {
  char out[96];

  CHECK(x4json::quoted(out, sizeof out, "network-smoke"));
  CHECK(std::strcmp(out, "\"network-smoke\"") == 0);

  // The real ETag from x4d, quotes and all, must come out as valid JSON.
  CHECK(x4json::quoted(out, sizeof out, "\"0530d69b7be984b1\""));
  CHECK(std::strcmp(out, "\"\\\"0530d69b7be984b1\\\"\"") == 0);

  CHECK(x4json::quoted(out, sizeof out, "a\\b"));
  CHECK(std::strcmp(out, "\"a\\\\b\"") == 0);

  CHECK(x4json::quoted(out, sizeof out, "tab\there"));
  CHECK(std::strcmp(out, "\"tab\\u0009here\"") == 0);

  // Empty or missing -> JSON null (the protocol's "no card / no etag").
  CHECK(x4json::quoted(out, sizeof out, ""));
  CHECK(std::strcmp(out, "null") == 0);
  CHECK(x4json::quoted(out, sizeof out, nullptr));
  CHECK(std::strcmp(out, "null") == 0);

  // Too small a buffer fails cleanly instead of emitting truncated JSON.
  char tiny[6];
  CHECK(!x4json::quoted(tiny, sizeof tiny, "network-smoke"));
  CHECK(tiny[0] == '\0');

  if (failures) { std::printf("%d failure(s)\n", failures); return 1; }
  std::printf("all json_out checks passed\n");
  return 0;
}
