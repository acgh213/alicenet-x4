// Pure HTTP response-line helpers, shared by the firmware and the host test
// (test_host/test_http_line.cpp). No Arduino dependencies.
#pragma once

#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <strings.h>

namespace x4http {

inline void copyTrimmed(char* dst, size_t cap, const char* src) {
  if (cap == 0) return;
  while (*src == ' ' || *src == '\t') ++src;
  size_t n = std::strlen(src);
  while (n && (src[n - 1] == '\r' || src[n - 1] == '\n' || src[n - 1] == ' ' || src[n - 1] == '\t')) --n;
  if (n >= cap) n = cap - 1;
  std::memmove(dst, src, n);
  dst[n] = '\0';
}

// "HTTP/1.x NNN ..." -> NNN, else -1. Accepts 1.0 and 1.1: Python's
// http.server (x4d) answers HTTP/1.0 even to an HTTP/1.1 request.
inline int statusCode(const char* line) {
  if (std::strncmp(line, "HTTP/1.", 7) != 0) return -1;
  if (line[7] != '0' && line[7] != '1') return -1;
  if (line[8] != ' ') return -1;
  const int code = std::atoi(line + 9);
  return (code >= 100 && code <= 599) ? code : -1;
}

inline bool headerValue(const char* line, const char* name, char* out, size_t cap) {
  const size_t n = std::strlen(name);
  if (strncasecmp(line, name, n) != 0 || line[n] != ':') return false;
  copyTrimmed(out, cap, line + n + 1);
  return true;
}

}  // namespace x4http
