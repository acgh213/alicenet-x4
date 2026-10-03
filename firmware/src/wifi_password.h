#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace x4wifi {
inline int base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Validated CrossInk format ONLY (no ambiguous legacy-XOR fallback): base64
// of XOR_MAC(CPV1 || LE32(FNV1a(MAC || plaintext)) || plaintext).
// Empty encoded strings are CrossInk's representation of open networks.
inline bool decodeWifiPassword(const char* encoded, const uint8_t* mac,
                               char* password, size_t cap) {
  if (!encoded || !mac || !password || cap == 0) return false;
  password[0] = '\0';
  const size_t encodedLength = std::strlen(encoded);
  if (encodedLength == 0) return true;
  uint8_t payload[8 + 64];
  if (encodedLength % 4 != 0 || encodedLength > sizeof(payload) / 3 * 4) return false;
  size_t length = 0;
  for (size_t i = 0; i < encodedLength; i += 4) {
    const int a = base64Value(encoded[i]), b = base64Value(encoded[i + 1]);
    const bool pad2 = encoded[i + 2] == '=', pad3 = encoded[i + 3] == '=';
    const int c = pad2 ? 0 : base64Value(encoded[i + 2]);
    const int d = pad3 ? 0 : base64Value(encoded[i + 3]);
    if (a < 0 || b < 0 || c < 0 || d < 0 || (pad2 && !pad3) ||
        ((pad2 || pad3) && i + 4 != encodedLength) ||
        (pad2 && (b & 15)) || (pad3 && !pad2 && (c & 3))) return false;
    payload[length++] = static_cast<uint8_t>((a << 2) | (b >> 4));
    if (!pad2) payload[length++] = static_cast<uint8_t>((b << 4) | (c >> 2));
    if (!pad3) payload[length++] = static_cast<uint8_t>((c << 6) | d);
  }
  if (length < 8) return false;
  // The marker is encrypted too: it is checked only AFTER the MAC XOR.
  for (size_t i = 0; i < length; ++i) payload[i] ^= mac[i % 6];
  if (std::memcmp(payload, "CPV1", 4) != 0 || length - 8 >= cap) return false;
  const uint32_t expected = uint32_t(payload[4]) | (uint32_t(payload[5]) << 8) |
                            (uint32_t(payload[6]) << 16) | (uint32_t(payload[7]) << 24);
  uint32_t actual = 2166136261u;
  for (size_t i = 0; i < 6; ++i) actual = (actual ^ mac[i]) * 16777619u;
  for (size_t i = 8; i < length; ++i) actual = (actual ^ payload[i]) * 16777619u;
  if (actual != expected) return false;
  std::memcpy(password, payload + 8, length - 8);
  password[length - 8] = '\0';
  return true;
}
}  // namespace x4wifi
