#include "wifi_password.h"
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

// Independent synthetic encoder mirrors CrossInk ObfuscationUtils.cpp: CPV1 +
// little-endian FNV1a(MAC || plaintext), XOR the ENTIRE payload, then base64.
static std::string encode(const std::string& plaintext, const uint8_t* mac) {
  uint32_t hash = 2166136261u;
  for (size_t i = 0; i < 6; ++i) hash = (hash ^ mac[i]) * 16777619u;
  for (unsigned char c : plaintext) hash = (hash ^ c) * 16777619u;
  std::vector<uint8_t> data{'C', 'P', 'V', '1'};
  for (unsigned shift = 0; shift < 32; shift += 8) data.push_back(uint8_t(hash >> shift));
  data.insert(data.end(), plaintext.begin(), plaintext.end());
  for (size_t i = 0; i < data.size(); ++i) data[i] ^= mac[i % 6];
  const char* alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string encoded;
  for (size_t i = 0; i < data.size(); i += 3) {
    const uint32_t value = (uint32_t(data[i]) << 16) |
        (i + 1 < data.size() ? uint32_t(data[i + 1]) << 8 : 0) |
        (i + 2 < data.size() ? data[i + 2] : 0);
    encoded += alphabet[(value >> 18) & 63];
    encoded += alphabet[(value >> 12) & 63];
    encoded += i + 1 < data.size() ? alphabet[(value >> 6) & 63] : '=';
    encoded += i + 2 < data.size() ? alphabet[value & 63] : '=';
  }
  return encoded;
}

int main() {
  const uint8_t mac[6] = {0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc};
  char password[65] = {};
  for (const std::string& plain : {std::string("synthetic-not-a-real-password"),
                                 std::string("a"), std::string("ab"), std::string("abc"),
                                 std::string(64, 'x')}) {
    const auto encoded = encode(plain, mac);
    assert(x4wifi::decodeWifiPassword(encoded.c_str(), mac, password, sizeof password));
    assert(std::string(password) == plain);
  }
  auto encoded = encode("synthetic-not-a-real-password", mac);
  encoded[12] = encoded[12] == 'A' ? 'B' : 'A';
  assert(!x4wifi::decodeWifiPassword(encoded.c_str(), mac, password, sizeof password));
  auto wrongMac = std::array<uint8_t, 6>{0x12, 0x34, 0x56, 0x78, 0x9a, 0xbd};
  encoded = encode("synthetic-not-a-real-password", mac);
  assert(!x4wifi::decodeWifiPassword(encoded.c_str(), wrongMac.data(), password, sizeof password));
  encoded = encode("abc", mac);
  assert(!x4wifi::decodeWifiPassword(encoded.c_str(), mac, password, 3));
  assert(x4wifi::decodeWifiPassword(encoded.c_str(), mac, password, 4));
  assert(!x4wifi::decodeWifiPassword(encode(std::string(65, 'x'), mac).c_str(), mac, password, sizeof password));
  assert(!x4wifi::decodeWifiPassword("Q1BWMQ==", mac, password, sizeof password));
  assert(!x4wifi::decodeWifiPassword("not base64!", mac, password, sizeof password));
  assert(!x4wifi::decodeWifiPassword("AAAA=AAA", mac, password, sizeof password));
  assert(!x4wifi::decodeWifiPassword("AAAA", mac, password, sizeof password));
  // CrossInk emits an empty string for an open network.
  assert(x4wifi::decodeWifiPassword("", mac, password, sizeof password));
  assert(password[0] == '\0');
  assert(!x4wifi::decodeWifiPassword(nullptr, mac, password, sizeof password));
  assert(!x4wifi::decodeWifiPassword("", mac, password, 0));
  puts("wifi_password: synthetic CrossInk roundtrip/corruption/wrong-MAC/bounds PASS");
}
