// alicenet-x4 "hello" (vertical slice step 2): no network, no SD writes.
//
// Boots like Escape Hatch, draws one status screen, and measures what the later
// steps depend on:
//   * native orientation: which way is "up" for an 800x480 frame blitted raw
//     (x4d sends exactly that), shown as an arrow + "TOP" label
//   * refresh timings (FULL at boot, HALF/FAST on later redraws)
//   * live button names + raw ADC
// Exits:
//   * Back+Up held at reset/wake -> ota_0 (CrossInk), via the stock SDK hatch
//   * Hold Power 1.5 s           -> power off (CrossInk's C3 path: latch LOW)
//   * 120 s with no input        -> same power off (never sits awake on battery)

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <Preferences.h>
#include <SDCardManager.h>
#include <BatteryMonitor.h>
#include <BoardConfig.h>
#include <EInkDisplay.h>
#include <FreeInkUI.h>
#include <FreeInkUIDisplayTarget.h>
#include <InputManager.h>
#include <PowerManager.h>
#include <RecoveryBoot.h>
#include <SPI.h>
#include <XteinkDetect.h>
#include <driver/gpio.h>
#include <esp_ota_ops.h>
#include <esp_sleep.h>
#include <esp_mac.h>

#include "http_line.h"
#include "x4_secrets.h"

#include <cstdio>
#include <cstring>

namespace ui = freeink::ui;

// CrossInk's SD flasher scans the image for this tag (WRONG_BOARD check). It must
// survive --gc-sections: `used` alone doesn't stop the linker discarding an
// unreferenced .rodata section, so setup() reads it through a volatile pointer.
extern "C" __attribute__((used)) const char kBoardTag[] = "CROSSPOINT-BOARD-V1:x4;";

namespace {

constexpr uint32_t kPowerHoldMs = 1500;
constexpr uint32_t kIdleOffMs = 120000;

EInkDisplay display(BoardConfig::DEFAULT_DEVICE.display.sclk, BoardConfig::DEFAULT_DEVICE.display.mosi,
                    BoardConfig::DEFAULT_DEVICE.display.cs, BoardConfig::DEFAULT_DEVICE.display.dc,
                    BoardConfig::DEFAULT_DEVICE.display.rst, BoardConfig::DEFAULT_DEVICE.display.busy);
InputManager input;
ui::DisplayTarget* g_target = nullptr;  // native landscape, no rotation

char g_panel[48] = "?";
char g_wake[24] = "?";
char g_lastButton[48] = "press any button";
uint32_t g_fullMs = 0, g_lastMs = 0;
const char* g_lastMode = "-";
uint32_t g_lastInput = 0;
int g_redraws = 0;

Preferences g_prefs;
uint32_t g_boot = 0;
uint32_t g_seq = 0;
char g_etag[80] = "";
char g_card[64] = "";
uint32_t g_nextPollS = 1800;
uint32_t g_sessionUntil = 0;
bool g_wifi = false;
bool g_sd = false;
bool g_pendingConfirm = false;
constexpr uint32_t kLongPressMs = 700;
constexpr uint32_t kHttpReadTimeoutMs = 8000;
constexpr int32_t kHttpConnectTimeoutMs = 5000;
uint32_t g_sessionMs = 0;
constexpr size_t kPbmHeaderBytes = 11;  // P4\n800 480\n
constexpr char kButtonNames[][8] = {"back", "confirm", "left", "right", "up", "down", "power"};

void text(int16_t x, int16_t y, int16_t w, const char* s, bool bold, ui::TextAlign a);

using x4http::copyTrimmed;
using x4http::headerValue;

bool readHttpLine(WiFiClient& client, char* line, size_t cap) {
  const size_t n = client.readBytesUntil('\n', reinterpret_cast<uint8_t*>(line), cap - 1);
  if (n == 0) return false;
  line[n] = '\0';
  copyTrimmed(line, cap, line);
  return true;
}


void drawDeviceStatus() {
  if (!g_target) return;
  g_target->fill(ui::Rect{640, 0, 160, 30}, ui::Paint::solid(ui::Color::White));
  char line[32];
  const BatteryMonitor battery;
  const BatteryMonitor::Status st = battery.readStatus();
  snprintf(line, sizeof line, "%u%% %.2fV", st.percentage, st.millivolts / 1000.0);
  text(640, 0, 156, line, false, ui::TextAlign::Right);
}

int base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

size_t decodeBase64(const char* encoded, uint8_t* out, size_t cap) {
  size_t count = 0;
  int accumulator = 0;
  int bits = 0;
  for (const char* p = encoded; *p; ++p) {
    if (*p == '=') break;
    const int value = base64Value(*p);
    if (value < 0) continue;
    accumulator = (accumulator << 6) | value;
    bits += 6;
    while (bits >= 8) {
      bits -= 8;
      if (count >= cap) return 0;
      out[count++] = static_cast<uint8_t>((accumulator >> bits) & 0xFF);
    }
  }
  return count;
}

bool jsonField(const char* json, const char* key, char* out, size_t cap, const char** endOut = nullptr) {
  const char* start = strstr(json, key);
  if (!start) return false;
  start += strlen(key);
  const char* end = strchr(start, '\"');
  if (!end || static_cast<size_t>(end - start) >= cap) return false;
  const size_t n = static_cast<size_t>(end - start);
  memcpy(out, start, n);
  out[n] = '\0';
  if (endOut) *endOut = end;
  return true;
}

uint32_t fnv1a(uint32_t hash, uint8_t byte) {
  hash ^= byte;
  return hash * 16777619UL;
}

bool decodeWifiPassword(const char* encoded, char* password, size_t cap) {
  uint8_t payload[96];
  const size_t length = decodeBase64(encoded, payload, sizeof payload);
  if (length < 8 || memcmp(payload, "CPV1", 4) != 0) return false;
  uint8_t mac[6];
  esp_efuse_mac_get_default(mac);
  for (size_t i = 0; i < length; ++i) payload[i] ^= mac[i % 6];
  if (memcmp(payload, "CPV1", 4) != 0 || length < 8) return false;
  uint32_t expected = static_cast<uint32_t>(payload[4]) | (static_cast<uint32_t>(payload[5]) << 8) |
                      (static_cast<uint32_t>(payload[6]) << 16) | (static_cast<uint32_t>(payload[7]) << 24);
  uint32_t actual = 2166136261UL;
  for (size_t i = 0; i < 6; ++i) actual = fnv1a(actual, mac[i]);
  for (size_t i = 8; i < length; ++i) actual = fnv1a(actual, payload[i]);
  if (actual != expected || length - 8 >= cap) return false;
  memcpy(password, payload + 8, length - 8);
  password[length - 8] = '\0';
  return true;
}

bool loadWifiCredential(char* ssid, size_t ssidCap, char* password, size_t passwordCap) {
  if (!SdMan.ready()) return false;
  const String json = SdMan.readFile("/.crosspoint/wifi.json");
  if (!json.length()) return false;
  char lastSsid[64];
  if (!jsonField(json.c_str(), "\"lastConnectedSsid\":\"", lastSsid, sizeof lastSsid)) return false;
  const char* search = strstr(json.c_str(), "\"credentials\"");
  while (search) {
    char candidate[64];
    const char* afterSsid = nullptr;
    if (jsonField(search, "\"ssid\":\"", candidate, sizeof candidate, &afterSsid) && strcmp(candidate, lastSsid) == 0) {
      char encoded[160];
      if (jsonField(afterSsid, "\"password_obf\":\"", encoded, sizeof encoded) &&
          decodeWifiPassword(encoded, password, passwordCap)) {
        strncpy(ssid, candidate, ssidCap - 1);
        ssid[ssidCap - 1] = '\0';
        return true;
      }
      // Legacy CrossInk files may carry plaintext `password`; read only this local SD file.
      if (jsonField(afterSsid, "\"password\":\"", password, passwordCap)) {
        strncpy(ssid, candidate, ssidCap - 1);
        ssid[ssidCap - 1] = '\0';
        return true;
      }
    }
    search = strstr(search + 1, "\"ssid\":\"");
  }
  return false;
}

bool connectWifi() {
  WiFi.persistent(false);  // never rewrite the CrossInk credential store
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  char ssid[64], password[65];
  if (loadWifiCredential(ssid, sizeof ssid, password, sizeof password)) {
    Serial.printf("[x4] joining saved SSID %s\n", ssid);
    WiFi.begin(ssid, password);
  } else {
    Serial.println("[x4] no readable saved Wi-Fi credential; trying Arduino NVS");
    WiFi.begin();
  }
  const uint32_t deadline = millis() + 12000;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) delay(100);
  g_wifi = WiFi.status() == WL_CONNECTED;
  Serial.printf("[x4] wifi %s%s\n", g_wifi ? "connected " : "failed ",
                g_wifi ? WiFi.localIP().toString().c_str() : "");
  return g_wifi;
}

bool fetchFrame(const char* wake) {
  if (!g_wifi) return false;
  WiFiClient client;
  client.setTimeout(kHttpReadTimeoutMs);  // Stream timeout is in MILLISECONDS
  if (!client.connect(X4_GATEWAY_HOST, X4_GATEWAY_PORT, kHttpConnectTimeoutMs)) {
    Serial.println("[x4] gateway connect failed");
    return false;
  }
  client.printf("GET /x4/v1/frame HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
                "X-Wake: %s\r\nX-Battery: %u\r\nX-Rssi: %d\r\nX-Fw: %s\r\n",
                X4_GATEWAY_HOST, X4_DEVICE_TOKEN, wake,
                BatteryMonitor().readStatus().percentage, WiFi.RSSI(), ALICENET_FW_VERSION);
  if (g_etag[0]) client.printf("If-None-Match: %s\r\n", g_etag);
  client.print("Connection: close\r\n\r\n");

  char line[192];
  if (!readHttpLine(client, line, sizeof line)) {
    client.stop();
    return false;
  }
  const int status = x4http::statusCode(line);
  if (status < 0) {
    Serial.printf("[x4] bad status line: %s\n", line);
    client.stop();
    return false;
  }
  size_t contentLength = 0;
  char newEtag[80] = "";
  char newCard[64] = "";
  char actions[128] = "";
  uint32_t session = 0;
  uint32_t nextPoll = g_nextPollS;
  while (readHttpLine(client, line, sizeof line) && line[0]) {
    char value[128];
    if (headerValue(line, "Content-Length", value, sizeof value)) contentLength = strtoul(value, nullptr, 10);
    else if (headerValue(line, "ETag", value, sizeof value)) copyTrimmed(newEtag, sizeof newEtag, value);
    else if (headerValue(line, "X-Card", value, sizeof value)) copyTrimmed(newCard, sizeof newCard, value);
    else if (headerValue(line, "X-Card-Actions", value, sizeof value)) copyTrimmed(actions, sizeof actions, value);
    else if (headerValue(line, "X-Session", value, sizeof value)) session = strtoul(value, nullptr, 10);
    else if (headerValue(line, "X-Next-Poll", value, sizeof value)) nextPoll = strtoul(value, nullptr, 10);
  }
  g_nextPollS = constrain(nextPoll, 300UL, 21600UL);
  if (status == 304) {
    if (newCard[0]) copyTrimmed(g_card, sizeof g_card, newCard);
    g_sessionMs = session * 1000UL;
    g_sessionUntil = session ? millis() + g_sessionMs : 0;
  }
  if (status == 304 || status == 204) {
    Serial.printf("[x4] frame %d, keeping %s\n", status, g_card[0] ? g_card : "current screen");
    client.stop();
    return true;
  }
  if (status != 200 || contentLength < kPbmHeaderBytes + display.getBufferSize()) {
    Serial.printf("[x4] frame rejected: HTTP %d, %u bytes\n", status, static_cast<unsigned>(contentLength));
    client.stop();
    return false;
  }
  uint8_t pbmHeader[kPbmHeaderBytes];
  if (client.readBytes(pbmHeader, sizeof pbmHeader) != sizeof pbmHeader ||
      memcmp(pbmHeader, "P4\n800 480\n", kPbmHeaderBytes) != 0) {
    Serial.println("[x4] bad PBM header");
    client.stop();
    return false;
  }
  uint8_t* frame = display.getFrameBuffer();
  size_t remaining = display.getBufferSize();
  size_t offset = 0;
  while (remaining) {
    const size_t want = remaining > 1024 ? 1024 : remaining;
    const size_t got = client.readBytes(frame + offset, want);
    if (got != want) {
      Serial.println("[x4] short PBM body");
      client.stop();
      return false;
    }
    for (size_t i = 0; i < got; ++i) frame[offset + i] = static_cast<uint8_t>(~frame[offset + i]);
    offset += got;
    remaining -= got;
  }
  display.displayBuffer(EInkDisplay::HALF_REFRESH);
  drawDeviceStatus();
  display.displayBuffer(EInkDisplay::FAST_REFRESH);
  copyTrimmed(g_etag, sizeof g_etag, newEtag);
  copyTrimmed(g_card, sizeof g_card, newCard);
  g_sessionMs = session * 1000UL;
  g_sessionUntil = session ? millis() + g_sessionMs : 0;
  Serial.printf("[x4] card %s etag %s session %lu poll %lu\n", g_card, g_etag,
                static_cast<unsigned long>(session), static_cast<unsigned long>(g_nextPollS));
  client.stop();
  return true;
}

bool postEvent(uint8_t button, const char* press, const char* wake) {
  if (!g_wifi || button > InputManager::BTN_DOWN) return false;
  const uint32_t eventSeq = g_seq + 1;
  char cardJson[80] = "null";
  char etagJson[96] = "null";
  if (g_card[0]) snprintf(cardJson, sizeof cardJson, "\"%s\"", g_card);
  if (g_etag[0]) snprintf(etagJson, sizeof etagJson, "\"%s\"", g_etag);
  char body[512];
  const int length = snprintf(body, sizeof body,
                              "{\"device\":\"%s\",\"boot\":%lu,\"events\":[{\"seq\":%lu,"
                              "\"card\":%s,\"etag\":%s,\"button\":\"%s\",\"press\":\"%s\","
                              "\"wake\":\"%s\"}]}",
                              X4_DEVICE_ID, static_cast<unsigned long>(g_boot), static_cast<unsigned long>(eventSeq),
                              cardJson, etagJson, kButtonNames[button], press, wake);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof body) return false;
  WiFiClient client;
  client.setTimeout(kHttpReadTimeoutMs);  // Stream timeout is in MILLISECONDS
  if (!client.connect(X4_GATEWAY_HOST, X4_GATEWAY_PORT, kHttpConnectTimeoutMs)) return false;
  client.printf("POST /x4/v1/events HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
                X4_GATEWAY_HOST, X4_DEVICE_TOKEN, length, body);
  char line[192];
  if (!readHttpLine(client, line, sizeof line) || x4http::statusCode(line) != 200) {
    client.stop();
    return false;
  }
  bool frameChanged = false;
  while (readHttpLine(client, line, sizeof line) && line[0]) {
    char value[32];
    if (headerValue(line, "X-Frame-Changed", value, sizeof value)) frameChanged = atoi(value) != 0;
  }
  g_seq = eventSeq;
  g_prefs.putULong("seq", g_seq);
  client.stop();
  Serial.printf("[x4] event #%lu %s %s\n", static_cast<unsigned long>(g_seq), kButtonNames[button], press);
  if (frameChanged) fetchFrame("session");
  return true;
}

const char* wakeName() {
  switch (esp_sleep_get_wakeup_cause()) {
    case ESP_SLEEP_WAKEUP_GPIO: return "power button";
    case ESP_SLEEP_WAKEUP_TIMER: return "timer";
    case ESP_SLEEP_WAKEUP_UNDEFINED: return esp_reset_reason() == ESP_RST_POWERON ? "power-on" : "reset";
    default: return "other";
  }
}

void text(int16_t x, int16_t y, int16_t w, const char* s, bool bold = false, ui::TextAlign a = ui::TextAlign::Left) {
  ui::TextStyle st;
  st.bold = bold;
  st.align = a;
  g_target->text(ui::Rect{x, y, w, 40}, s, st);
}

void fill(int16_t x, int16_t y, int16_t w, int16_t h) {
  g_target->fill(ui::Rect{x, y, w, h}, ui::Paint::solid(ui::Color::Black));
}

void drawStatus() {
  display.clearScreen(0xFF);
  char line[96];

  // Orientation marker: a solid up-arrow + "TOP" at native (0..800, y=0) edge.
  for (int16_t i = 0; i < 24; ++i) fill(static_cast<int16_t>(400 - i), static_cast<int16_t>(4 + i), static_cast<int16_t>(2 * i + 1), 1);
  fill(392, 28, 17, 30);
  text(420, 14, 120, "TOP", true);

  // Device-owned status corner (top-right), same box x4d keeps white.
  const BatteryMonitor battery;
  const BatteryMonitor::Status st = battery.readStatus();
  snprintf(line, sizeof line, "%u%%  %.2fV", st.percentage, st.millivolts / 1000.0);
  text(640, 2, 156, line, false, ui::TextAlign::Right);

  text(24, 70, 752, "alicenet-x4  hello", true);
  fill(24, 112, 752, 3);

  const esp_partition_t* running = esp_ota_get_running_partition();
  snprintf(line, sizeof line, "firmware %s  in %s", ALICENET_FW_VERSION, running ? running->label : "?");
  text(24, 130, 752, line);
  snprintf(line, sizeof line, "panel: %s", g_panel);
  text(24, 170, 752, line);
  snprintf(line, sizeof line, "woke by: %s", g_wake);
  text(24, 210, 752, line);
  snprintf(line, sizeof line, "refresh: full %lu ms, last %s %lu ms (#%d)", static_cast<unsigned long>(g_fullMs),
           g_lastMode, static_cast<unsigned long>(g_lastMs), g_redraws);
  text(24, 250, 752, line);
  snprintf(line, sizeof line, "button: %s", g_lastButton);
  text(24, 290, 752, line, true);

  fill(24, 400, 752, 2);
  text(24, 410, 752, "Back+Up at wake: CrossInk     Hold Power: off     idle 2 min: off");
  text(24, 446, 752, "Is the arrow pointing to the top of the screen as you hold it?");
}

void refresh(EInkDisplay::RefreshMode mode, const char* name) {
  const uint32_t t0 = millis();
  display.displayBuffer(mode);
  g_lastMs = millis() - t0;
  g_lastMode = name;
  if (mode == EInkDisplay::FULL_REFRESH && g_fullMs == 0) g_fullMs = g_lastMs;
  Serial.printf("[hello] refresh %s %lu ms\n", name, static_cast<unsigned long>(g_lastMs));
}

// Power off, exactly like CrossInk's ESP32-C3 sleep path (HalPowerManager.cpp):
// on battery, dropping the GPIO13 latch cuts power; on USB the chip deep-sleeps
// and the power button wakes it.
[[noreturn]] void powerOff(const char* why) {
  Serial.printf("[x4] power off: %s\n", why);
  display.clearScreen(0xFF);
  text(24, 180, 752, "alicenet-x4 is off", true, ui::TextAlign::Center);
  text(24, 230, 752, why, false, ui::TextAlign::Center);
  text(24, 280, 752, "Hold Power to wake.  Back+Up while waking: CrossInk", false, ui::TextAlign::Center);
  display.displayBuffer(EInkDisplay::HALF_REFRESH);
  display.deepSleep();
  WiFi.disconnect(true, false);
  WiFi.mode(WIFI_OFF);
  Serial.flush();
  esp_sleep_enable_timer_wakeup(static_cast<uint64_t>(g_nextPollS) * 1000000ULL);
  for (const int8_t pin : {BoardConfig::ACTIVE.power.latch0, BoardConfig::ACTIVE.power.latch1}) {
    if (pin < 0 || BoardConfig::latchConflictsWithBus(pin)) continue;
    const auto latch = static_cast<gpio_num_t>(pin);
    gpio_set_direction(latch, GPIO_MODE_OUTPUT);
    gpio_set_level(latch, 0);
    gpio_hold_en(latch);
  }
  freeink::PowerManager::powerDownRailsForSleep();
  freeink::PowerManager::waitForPowerButtonRelease();
  esp_sleep_config_gpio_isolate();
  freeink::PowerManager::armPowerButtonWakeup();
  gpio_deep_sleep_hold_en();
  esp_deep_sleep_start();
  esp_restart();  // unreachable unless sleep entry was rejected
}

}  // namespace

void setup() {
  BoardConfig::holdPowerRails();
  Serial.begin(115200);
  delay(50);
  freeink::recovery::checkBootCombo();  // Back+Up held -> ota_0 (CrossInk); otherwise no-op

  snprintf(g_wake, sizeof g_wake, "%s", wakeName());
  const bool promoted = freeink::applyXteinkDisplayController();
  const freeink::XteinkDisplayProbeDiag& d = freeink::getXteinkDisplayProbeDiag();
  snprintf(g_panel, sizeof g_panel, "%s (VER %02X.. FLG %02X)%s",
           d.verdict == static_cast<uint8_t>(freeink::DisplayControllerVerdict::Uc81xxConfirmed) ? "UltraChip"
                                                                                                 : "SSD1677",
           d.ver[2], d.flg, promoted ? " promoted" : "");
  const char* volatile tag = kBoardTag;  // keeps the tag in the image (see kBoardTag)
  Serial.printf("[x4] %s %s, %s, wake=%s\n", ALICENET_FW_VERSION, tag, g_panel, g_wake);

  g_prefs.begin("alicenet", false);
  g_boot = g_prefs.getULong("boot", 0) + 1;
  g_seq = g_prefs.getULong("seq", 0);
  // No persisted ETag: every wake redraws the status screen, so the panel no
  // longer shows the old card and a 304 would leave the device with nothing.
  g_etag[0] = '\0';
  g_prefs.putULong("boot", g_boot);

  // X4: the SD card shares the display SPI bus; mount it before reading
  // CrossInk's saved Wi-Fi credential, then let the display reclaim the bus.
  SPI.begin(BoardConfig::ACTIVE.display.sclk, BoardConfig::ACTIVE.sd.miso, BoardConfig::ACTIVE.display.mosi,
            BoardConfig::ACTIVE.display.cs);
  g_sd = SdMan.begin();
  Serial.printf("[x4] SD %s\n", g_sd ? "ready" : "unavailable");
  display.begin();
  delay(50);
  input.begin();
  input.beginAsync();

  g_target = new ui::DisplayTarget(display.getFrameBuffer(), display.getDisplayWidth(), display.getDisplayHeight(),
                                   display.getDisplayWidthBytes(), ui::Orientation::LandscapeCounterClockwise);
  drawStatus();
  refresh(EInkDisplay::FULL_REFRESH, "FULL");
  drawStatus();  // show the measured FULL time while Wi-Fi joins
  refresh(EInkDisplay::FAST_REFRESH, "FAST");

  freeink::PowerManager::waitForPowerButtonRelease();
  g_lastInput = millis();
  if (connectWifi()) {
    if (!fetchFrame(g_wake)) powerOff("gateway fetch failed");
    if (!g_sessionUntil) powerOff("frame shown");
  } else {
    powerOff("wifi unavailable");
  }
}

void loop() {
  static bool armed = false;
  if (!input.isPowerButtonPressed()) armed = true;
  if (armed && input.isPowerButtonPressed() && input.getPowerButtonHeldTime() >= kPowerHoldMs) powerOff("power held");
  if (g_sessionUntil && millis() >= g_sessionUntil) powerOff("session complete");
  if (millis() - g_lastInput > kIdleOffMs) powerOff("idle for 2 minutes");

  uint8_t b;
  while (input.popPress(b)) {
    if (b == InputManager::BTN_POWER) continue;
    g_lastInput = millis();
    // First-slice rule: emit a short press on the edge immediately. This keeps
    // the event path reliable even if the device is about to leave its session;
    // long-confirm classification will move to an explicit release event later.
    postEvent(b, "short", "button");
    if (g_sessionUntil) g_sessionUntil = millis() + g_sessionMs;
  }
  delay(10);
}
