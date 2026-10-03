// Host-side test for the firmware's HTTP response parsing. Build & run:
//   g++ -std=c++17 -Wall -Werror -I../src test_http_line.cpp -o /tmp/t && /tmp/t
// The fixture is a real response head captured from the live x4d.
#include "http_line.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

static int failures = 0;
#define CHECK(cond)                                                    \
  do {                                                                 \
    if (!(cond)) {                                                     \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
      ++failures;                                                      \
    }                                                                  \
  } while (0)

static std::vector<std::string> fixtureLines() {
  std::ifstream f("x4d_response_head.txt", std::ios::binary);
  std::stringstream ss;
  ss << f.rdbuf();
  std::vector<std::string> out;
  std::string line;
  for (char c : ss.str()) {
    if (c == '\n') { out.push_back(line); line.clear(); }
    else line += c;
  }
  return out;
}

int main() {
  // Status line: Python's http.server answers HTTP/1.0 even to a 1.1 request.
  CHECK(x4http::statusCode("HTTP/1.0 200 OK") == 200);
  CHECK(x4http::statusCode("HTTP/1.1 200 OK") == 200);
  CHECK(x4http::statusCode("HTTP/1.0 304 Not Modified") == 304);
  CHECK(x4http::statusCode("HTTP/1.1 204 No Content") == 204);
  CHECK(x4http::statusCode("garbage") == -1);
  CHECK(x4http::statusCode("HTTP/2 200") == -1);
  CHECK(x4http::statusCode("") == -1);

  // The real x4d response head, line by line, exactly as readHttpLine sees it.
  const std::vector<std::string> lines = fixtureLines();
  CHECK(lines.size() >= 12);
  char trimmed[192];
  std::strncpy(trimmed, lines[0].c_str(), sizeof trimmed - 1);
  trimmed[sizeof trimmed - 1] = '\0';
  x4http::copyTrimmed(trimmed, sizeof trimmed, trimmed);
  CHECK(x4http::statusCode(trimmed) == 200);

  char etag[80] = "", card[64] = "", actions[128] = "";
  unsigned long session = 0, length = 0, poll = 0;
  for (size_t i = 1; i < lines.size(); ++i) {
    char line[192], value[128];
    std::strncpy(line, lines[i].c_str(), sizeof line - 1);
    line[sizeof line - 1] = '\0';
    x4http::copyTrimmed(line, sizeof line, line);
    if (!line[0]) break;
    if (x4http::headerValue(line, "Content-Length", value, sizeof value)) length = std::strtoul(value, nullptr, 10);
    else if (x4http::headerValue(line, "ETag", value, sizeof value)) x4http::copyTrimmed(etag, sizeof etag, value);
    else if (x4http::headerValue(line, "X-Card", value, sizeof value)) x4http::copyTrimmed(card, sizeof card, value);
    else if (x4http::headerValue(line, "X-Card-Actions", value, sizeof value)) x4http::copyTrimmed(actions, sizeof actions, value);
    else if (x4http::headerValue(line, "X-Session", value, sizeof value)) session = std::strtoul(value, nullptr, 10);
    else if (x4http::headerValue(line, "X-Next-Poll", value, sizeof value)) poll = std::strtoul(value, nullptr, 10);
  }
  CHECK(length == 11 + 48000);
  CHECK(std::strcmp(card, "network-smoke") == 0);
  CHECK(std::strcmp(actions, "confirm,confirm_long") == 0);
  CHECK(session == 120);
  CHECK(poll == 1800);
  CHECK(etag[0] == '"' && std::strlen(etag) == 18);
  // "X-Card" must not swallow "X-Card-Actions".
  char v[32];
  CHECK(!x4http::headerValue("X-Card-Actions: confirm", "X-Card", v, sizeof v));

  if (failures) { std::printf("%d failure(s)\n", failures); return 1; }
  std::printf("all http_line checks passed\n");
  return 0;
}
