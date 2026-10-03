#include "bounded_http.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <string>
struct Source {
 std::string bytes; size_t pos = 0; uint32_t now = 0; bool connected_ = true;
 int available() { return pos < bytes.size() ? 1 : 0; }
 int read() { return pos < bytes.size() ? bytes[pos++] : -1; }
 bool connected() { return connected_; }
};
struct Sink {
 uint32_t now = 0; int calls = 0; bool stall = false, fail = false;
 std::string bytes;
 int writeSome(const uint8_t* data, size_t) {
  ++calls;
  if (fail) return -1;
  if (stall) return 0;
  bytes.push_back(static_cast<char>(*data)); return 1;
 }
};
int main() {
 char line[20];
 Source s{"HTTP/1.1 200 OK\r\n\r\n"};
 auto clock = [&]() { return s.now; };
 auto yield = [&]() { ++s.now; };
 assert(x4http::readLine(s, line, sizeof line, 0, 50, clock, yield));
 assert(std::strcmp(line,"HTTP/1.1 200 OK\r") == 0);
 assert(x4http::readLine(s,line,sizeof line,0,50,clock,yield));
 assert(std::strcmp(line,"\r") == 0);
 assert(!x4http::readLine(s,line,sizeof line,0,50,clock,yield));
 assert(s.now == 50);
 s = Source{"incomplete"}; s.connected_ = false;
 assert(!x4http::readLine(s,line,sizeof line,0,50,clock,yield));
 s = Source{std::string(30,'x')};
 assert(!x4http::readLine(s,line,sizeof line,0,50,clock,yield));
 s = Source{"abc"}; uint8_t body[4];
 assert(!x4http::readExact(s,body,4,0,5,clock,yield));
 assert(s.now == 5);
 s = Source{"abcd"};
 assert(x4http::readExact(s,body,4,0,5,clock,yield));
 assert(memcmp(body,"abcd",4) == 0);
 s = Source{"abcd"}; s.now = 10;
 assert(!x4http::readExact(s,body,4,0,5,clock,yield));
 s = Source{"abcd"}; s.now = 3;
 assert(!x4http::readExact(s,body,5,0xfffffffeu,5,clock,yield));
 Sink sink;
 auto writeClock = [&]() { return sink.now; };
 auto writeYield = [&]() { ++sink.now; };
 const auto* data = reinterpret_cast<const uint8_t*>("hello");
 assert(x4http::writeExact(sink, data, 5, 0, 5, writeClock, writeYield));
 assert(sink.bytes == "hello");
 sink = Sink{}; sink.stall = true;
 assert(!x4http::writeExact(sink, data, 5, 0, 5, writeClock, writeYield));
 assert(sink.now == 5);
 sink = Sink{}; sink.fail = true;
 assert(!x4http::writeExact(sink, data, 5, 0, 5, writeClock, writeYield));
 sink = Sink{}; sink.now = 5;
 assert(!x4http::writeExact(sink, data, 5, 0, 5, writeClock, writeYield));
 assert(sink.calls == 0);
 std::cout << "bounded HTTP: all assertions passed\n";
}
