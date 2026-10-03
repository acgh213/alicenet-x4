#pragma once
#include <cstddef>
#include <cstdint>
namespace x4http {
// One absolute budget for connection + request + status + all headers + body.
// Slow trickles cannot reset the deadline. I/O must be nonblocking; yield on stalls.
template <class Writer, class Clock, class Yield>
bool writeExact(Writer& writer, const uint8_t* data, size_t length, uint32_t start, uint32_t budget,
                Clock clock, Yield yield) {
  size_t sent = 0;
  while (sent < length) {
    if (uint32_t(clock() - start) >= budget) return false;
    const int count = writer.writeSome(data + sent, length - sent);
    if (count < 0 || static_cast<size_t>(count) > length - sent) return false;
    if (count == 0) yield();
    else sent += static_cast<size_t>(count);
  }
  return true;
}
template <class Client, class Clock, class Yield>
int readByte(Client& client, uint32_t start, uint32_t budget, Clock clock, Yield yield) {
  while (uint32_t(clock() - start) < budget) {
    if (client.available()) return client.read();
    if (!client.connected()) return -1;
    yield();
  }
  return -1;
}
template <class Client, class Clock, class Yield>
bool readLine(Client& client, char* line, size_t cap, uint32_t start, uint32_t budget,
              Clock clock, Yield yield) {
  if (!cap) return false;
  size_t count = 0;
  for (;;) {
    const int c = readByte(client, start, budget, clock, yield);
    if (c < 0) return false;
    if (c == '\n') { line[count] = '\0'; return true; }
    if (count + 1 >= cap) return false;
    line[count++] = static_cast<char>(c);
  }
}
template <class Client, class Clock, class Yield>
bool readExact(Client& client, uint8_t* out, size_t length, uint32_t start, uint32_t budget,
               Clock clock, Yield yield) {
  for (size_t i = 0; i < length; ++i) {
    const int c = readByte(client, start, budget, clock, yield);
    if (c < 0) return false;
    out[i] = static_cast<uint8_t>(c);
  }
  return true;
}
} // namespace x4http
