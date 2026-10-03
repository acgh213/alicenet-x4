// JSON string output for the event POST, shared by firmware and host test
// (test_host/test_json_out.cpp). No Arduino dependencies.
#pragma once

#include <cstddef>
#include <cstdio>

namespace x4json {

// Write `s` as a JSON string literal (with quotes) into out, or `null` when s
// is null/empty. Escapes ", \ and control characters. Returns false and leaves
// out empty if it doesn't fit — never emits truncated JSON.
inline bool quoted(char* out, size_t cap, const char* s) {
  if (cap == 0) return false;
  out[0] = '\0';
  if (!s || !*s) {
    if (cap < 5) return false;
    std::snprintf(out, cap, "null");
    return true;
  }
  size_t n = 0;
  auto put = [&](char c) {
    if (n + 1 >= cap) return false;
    out[n++] = c;
    return true;
  };
  if (!put('"')) { out[0] = '\0'; return false; }
  for (const char* p = s; *p; ++p) {
    const unsigned char c = static_cast<unsigned char>(*p);
    bool ok = true;
    if (c == '"' || c == '\\') {
      ok = put('\\') && put(static_cast<char>(c));
    } else if (c < 0x20) {
      char esc[7];
      std::snprintf(esc, sizeof esc, "\\u%04x", c);
      for (const char* e = esc; *e && ok; ++e) ok = put(*e);
    } else {
      ok = put(static_cast<char>(c));
    }
    if (!ok) { out[0] = '\0'; return false; }
  }
  if (!put('"')) { out[0] = '\0'; return false; }
  out[n] = '\0';
  return true;
}

// The whole POST /x4/v1/events body for one press. Returns its length, or -1
// if any field doesn't fit (the caller must not send a partial body).
inline int eventBody(char* out, size_t cap, const char* device, unsigned long boot, unsigned long seq,
                     const char* card, const char* etag, const char* button, const char* press,
                     const char* wake) {
  char dev[48], cardJson[80], etagJson[96], btn[16], prs[16], wk[16];
  if (!quoted(dev, sizeof dev, device) || !quoted(cardJson, sizeof cardJson, card) ||
      !quoted(etagJson, sizeof etagJson, etag) || !quoted(btn, sizeof btn, button) ||
      !quoted(prs, sizeof prs, press) || !quoted(wk, sizeof wk, wake)) {
    if (cap) out[0] = '\0';
    return -1;
  }
  const int n = std::snprintf(out, cap,
                              "{\"device\":%s,\"boot\":%lu,\"events\":[{\"seq\":%lu,\"card\":%s,\"etag\":%s,"
                              "\"button\":%s,\"press\":%s,\"wake\":%s}]}",
                              dev, boot, seq, cardJson, etagJson, btn, prs, wk);
  if (n <= 0 || static_cast<size_t>(n) >= cap) {
    if (cap) out[0] = '\0';
    return -1;
  }
  return n;
}

}  // namespace x4json
