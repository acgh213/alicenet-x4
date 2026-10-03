// alicenet-x4 ambient endpoint. SD updater/recovery remain the stock SDK path.
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
#include <lwip/sockets.h>
#include <cerrno>

#include "ambient.h"
#include "bounded_http.h"
#include "http_line.h"
#include "json_out.h"
#include "x4_secrets.h"

#include <cstdio>
#include <cstring>
#include <atomic>
#include <memory>
#include <new>

namespace ui = freeink::ui;

// CrossInk's SD flasher scans the image for this tag (WRONG_BOARD check). It must
// survive --gc-sections: `used` alone doesn't stop the linker discarding an
// unreferenced .rodata section, so setup() reads it through a volatile pointer.
extern "C" __attribute__((used)) const char kBoardTag[] = "CROSSPOINT-BOARD-V1:x4;";

namespace {


EInkDisplay display(BoardConfig::DEFAULT_DEVICE.display.sclk, BoardConfig::DEFAULT_DEVICE.display.mosi,
                    BoardConfig::DEFAULT_DEVICE.display.cs, BoardConfig::DEFAULT_DEVICE.display.dc,
                    BoardConfig::DEFAULT_DEVICE.display.rst, BoardConfig::DEFAULT_DEVICE.display.busy);
InputManager input;
ui::DisplayTarget* g_target = nullptr;  // native landscape, no rotation

char g_panel[48] = "?";
char g_wake[24] = "?";
Preferences g_prefs;
uint32_t g_boot = 0;
uint32_t g_seq = 0;
char g_etag[80] = "";
char g_card[64] = "";
uint32_t g_nextPollS = 1800;
x4ambient::Session g_session;
x4ambient::Panel g_panelState;
uint32_t g_lastPoll = 0;
constexpr uint32_t kSessionPollMs = 5000;
std::atomic<uint32_t> g_activity{0};
std::atomic<bool> g_buttonsDown{false};
std::atomic<bool> g_manualOff{false};
struct ButtonEvent { uint8_t button; x4ambient::Press press; };
QueueHandle_t g_events = nullptr;
bool g_wifi = false;
bool g_sd = false;
constexpr uint32_t kHttpTransactionMs = 3000;
constexpr int32_t kHttpConnectTimeoutMs = 800;
constexpr size_t kPbmHeaderBytes = 11;  // P4\n800 480\n
constexpr char kButtonNames[][8] = {"back", "confirm", "left", "right", "up", "down", "power"};

void text(int16_t x, int16_t y, int16_t w, const char* s, bool bold, ui::TextAlign a);

using x4http::copyTrimmed;
using x4http::headerValue;

void inputConsumer(void*) {
  x4ambient::Confirm confirm;
  x4ambient::Power power;
  for (;;) {
    const uint32_t now = millis();
    uint8_t button;
    while (input.popPress(button)) {
      g_activity.store(now);
      if (button == InputManager::BTN_CONFIRM) confirm.press(now);
      else if (button <= InputManager::BTN_DOWN) {
        const ButtonEvent ev{button, x4ambient::Press::Short};
        if (xQueueSend(g_events, &ev, 0) != pdTRUE) Serial.println("[x4] input queue full");
      }
    }
    const auto press = confirm.sample(now, input.isPressed(InputManager::BTN_CONFIRM));
    if (press != x4ambient::Press::None) {
      g_activity.store(now);
      const ButtonEvent ev{InputManager::BTN_CONFIRM, press};
      if (xQueueSend(g_events, &ev, 0) != pdTRUE) Serial.println("[x4] input queue full");
    }
    if (power.sample(now, input.isPowerButtonPressed())) g_manualOff.store(true);
    bool held = false;
    for (uint8_t b = 0; b <= InputManager::BTN_POWER; ++b) held |= input.isPressed(b);
    g_buttonsDown.store(held);
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

uint32_t httpClock(uint32_t start) {
  return g_manualOff.load() ? start + kHttpTransactionMs : millis();
}

bool connectGateway(WiFiClient& client, uint32_t start) {
  IPAddress address;
  // The configured LAN gateway must be a literal IP: DNS has no bounded timeout.
  if (!address.fromString(X4_GATEWAY_HOST)) return false;
  const uint32_t elapsed = uint32_t(httpClock(start) - start);
  if (elapsed >= kHttpTransactionMs) return false;
  const int32_t remaining = static_cast<int32_t>(kHttpTransactionMs - elapsed);
  const int32_t timeout = remaining < kHttpConnectTimeoutMs ? remaining : kHttpConnectTimeoutMs;
  if (!client.connect(address, X4_GATEWAY_PORT, timeout)) return false;
  return uint32_t(httpClock(start) - start) < kHttpTransactionMs;
}

struct HttpWriter {
  int fd;
  int writeSome(const uint8_t* data, size_t length) {
    // Arduino NetworkClient::write retries internally; bypass it entirely.
    const int count = ::send(fd, data, length, MSG_DONTWAIT);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    return count;
  }
};

bool writeRequest(WiFiClient& client, const char* data, size_t length, uint32_t start) {
  HttpWriter writer{client.fd()};
  return x4http::writeExact(writer, reinterpret_cast<const uint8_t*>(data), length, start, kHttpTransactionMs,
                           [start] { return httpClock(start); }, [] { delay(1); });
}

bool readHttpLine(WiFiClient& client, char* line, size_t cap, uint32_t start) {
  if (!x4http::readLine(client, line, cap, start, kHttpTransactionMs,
                        [start] { return httpClock(start); }, [] { delay(1); })) return false;
  copyTrimmed(line, cap, line);
  return true;
}

bool readExact(WiFiClient& client, uint8_t* out, size_t n, uint32_t start) {
  return x4http::readExact(client, out, n, start, kHttpTransactionMs,
                          [start] { return httpClock(start); }, [] { delay(1); });
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

// Device-owned top-right corner: after a press, show "<button> sent" (or why
// it wasn't) with a fast partial refresh, so the person holding the X4 can see
// the press landed without waiting for the gateway to change the card.
void showReceipt(const char* button, const char* result) {
  if (!g_target || !g_panelState.conditional()) return;
  // Never redraw an unknown stale panel using a fresh/empty RAM framebuffer.
  // Stay inside x4d's reserved white corner (x 640..800, y 0..28).
  g_target->fill(ui::Rect{640, 0, 160, 30}, ui::Paint::solid(ui::Color::White));
  char line[40];
  snprintf(line, sizeof line, "%s %s", button, result);
  text(640, 0, 156, line, false, ui::TextAlign::Right);
  display.displayBuffer(EInkDisplay::FAST_REFRESH);
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
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && uint32_t(millis() - start) < 12000 && !g_manualOff.load()) delay(50);
  g_wifi = WiFi.status() == WL_CONNECTED;
  Serial.printf("[x4] wifi %s%s\n", g_wifi ? "connected " : "failed ",
                g_wifi ? WiFi.localIP().toString().c_str() : "");
  return g_wifi;
}

bool fetchFrame(const char* wake) {
  if (!g_wifi) return false;
  const uint32_t readStart = millis();
  char conditional[112] = "";
  if (g_panelState.conditional() && g_etag[0]) {
    const int n = snprintf(conditional, sizeof conditional, "If-None-Match: %s\r\n", g_etag);
    if (n < 0 || static_cast<size_t>(n) >= sizeof conditional) return false;
  }
  char request[768];
  const int length = snprintf(request, sizeof request,
                "GET /x4/v1/frame HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
                "X-Wake: %s\r\nX-Battery: %u\r\nX-Rssi: %d\r\nX-Fw: %s\r\n"
                "%sConnection: close\r\n\r\n",
                X4_GATEWAY_HOST, X4_DEVICE_TOKEN, wake,
                BatteryMonitor().readStatus().percentage, WiFi.RSSI(), ALICENET_FW_VERSION, conditional);
  if (length < 0 || static_cast<size_t>(length) >= sizeof request) return false;
  WiFiClient client;
  if (!connectGateway(client, readStart)) {
    Serial.println("[x4] gateway connect failed");
    client.stop();
    return false;
  }
  if (!writeRequest(client, request, static_cast<size_t>(length), readStart)) {
    client.stop();
    return false;
  }
  char line[192];
  if (!readHttpLine(client, line, sizeof line, readStart)) {
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
  uint32_t session = 0;
  uint32_t nextPoll = g_nextPollS;
  bool headersComplete = false;
  while (readHttpLine(client, line, sizeof line, readStart)) {
    if (!line[0]) { headersComplete = true; break; }
    char value[128];
    if (headerValue(line, "Content-Length", value, sizeof value)) contentLength = strtoul(value, nullptr, 10);
    else if (headerValue(line, "ETag", value, sizeof value)) copyTrimmed(newEtag, sizeof newEtag, value);
    else if (headerValue(line, "X-Card", value, sizeof value)) copyTrimmed(newCard, sizeof newCard, value);
    else if (headerValue(line, "X-Session", value, sizeof value)) session = strtoul(value, nullptr, 10);
    else if (headerValue(line, "X-Next-Poll", value, sizeof value)) nextPoll = strtoul(value, nullptr, 10);
  }
  if (!headersComplete) { client.stop(); return false; }
  if (status == 304 || status == 204) {
    // 304 only authenticates a framebuffer fetched in this wake, not whatever
    // an earlier firmware/CrossInk left on the physical panel.
    if (status == 304 && !g_panelState.conditional()) { client.stop(); return false; }
    g_nextPollS = constrain(nextPoll, 300UL, 21600UL);
    g_session.configure(millis(), session);
    Serial.printf("[x4] frame %d: cached panel retained (not refreshed)\n", status);
    client.stop();
    return true;
  }
  if (status != 200 || contentLength != kPbmHeaderBytes + display.getBufferSize()) {
    Serial.printf("[x4] frame rejected: HTTP %d, %u bytes\n", status, static_cast<unsigned>(contentLength));
    client.stop();
    return false;
  }
  uint8_t pbmHeader[kPbmHeaderBytes];
  if (!readExact(client, pbmHeader, sizeof pbmHeader, readStart) ||
      memcmp(pbmHeader, "P4\n800 480\n", kPbmHeaderBytes) != 0) {
    Serial.println("[x4] bad PBM header");
    client.stop();
    return false;
  }
  // Stage a complete PBM before changing RAM or panel. A truncated download
  // must not contaminate the next receipt/partial refresh of the old card.
  std::unique_ptr<uint8_t[]> frame(new (std::nothrow) uint8_t[display.getBufferSize()]);
  if (!frame || !readExact(client, frame.get(), display.getBufferSize(), readStart)) {
    Serial.println("[x4] incomplete PBM or no staging memory; panel retained");
    client.stop();
    return false;
  }
  client.stop();
  for (size_t i = 0; i < display.getBufferSize(); ++i)
    display.getFrameBuffer()[i] = static_cast<uint8_t>(~frame[i]);
  drawDeviceStatus();
  display.displayBuffer(g_panelState.fullRefresh() ? EInkDisplay::FULL_REFRESH : EInkDisplay::HALF_REFRESH);
  g_panelState.accept();
  if (!g_prefs.getBool("had-frame", false)) g_prefs.putBool("had-frame", true);
  g_nextPollS = constrain(nextPoll, 300UL, 21600UL);
  copyTrimmed(g_etag, sizeof g_etag, newEtag);
  copyTrimmed(g_card, sizeof g_card, newCard);
  g_session.configure(millis(), session);
  Serial.printf("[x4] card %s etag %s session %lu poll %lu\n", g_card, g_etag,
                static_cast<unsigned long>(session), static_cast<unsigned long>(g_nextPollS));
  client.stop();
  return true;
}

bool postEvent(uint8_t button, const char* press, const char* wake) {
  if (!g_wifi || !g_panelState.conditional() || button > InputManager::BTN_DOWN) {
    Serial.println("[x4] event not sent: no authenticated current-wake card");
    return false;
  }
  const uint32_t eventSeq = g_seq + 1;
  char body[512];
  const int length = x4json::eventBody(body, sizeof body, X4_DEVICE_ID, static_cast<unsigned long>(g_boot),
                                       static_cast<unsigned long>(eventSeq), g_card, g_etag, kButtonNames[button],
                                       press, wake);
  if (length < 0) {
    showReceipt(kButtonNames[button], "too big");
    return false;
  }
  const uint32_t readStart = millis();
  char request[512];
  const int requestLength = snprintf(request, sizeof request,
                "POST /x4/v1/events HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
                X4_GATEWAY_HOST, X4_DEVICE_TOKEN, length);
  if (requestLength < 0 || static_cast<size_t>(requestLength) >= sizeof request) {
    showReceipt(kButtonNames[button], "too big");
    return false;
  }
  WiFiClient client;
  if (!connectGateway(client, readStart)) {
    client.stop();
    showReceipt(kButtonNames[button], "no link");
    return false;
  }
  if (!writeRequest(client, request, static_cast<size_t>(requestLength), readStart) ||
      !writeRequest(client, body, static_cast<size_t>(length), readStart)) {
    client.stop();
    showReceipt(kButtonNames[button], "not sent");
    return false;
  }
  char line[192];
  const int status = readHttpLine(client, line, sizeof line, readStart) ? x4http::statusCode(line) : -1;
  if (status != 200) {
    client.stop();
    char why[16];
    snprintf(why, sizeof why, "HTTP %d", status);
    showReceipt(kButtonNames[button], why);
    return false;
  }
  bool frameChanged = false;
  bool headersComplete = false;
  while (readHttpLine(client, line, sizeof line, readStart)) {
    if (!line[0]) { headersComplete = true; break; }
    char value[32];
    if (headerValue(line, "X-Frame-Changed", value, sizeof value)) frameChanged = atoi(value) != 0;
  }
  if (!headersComplete) { client.stop(); return false; }
  g_seq = eventSeq;
  g_prefs.putULong("seq", g_seq);
  client.stop();
  Serial.printf("[x4] event #%lu %s %s\n", static_cast<unsigned long>(g_seq), kButtonNames[button], press);
  if (frameChanged) { fetchFrame("session"); g_lastPoll = millis(); }
  else showReceipt(kButtonNames[button], "sent");
  return true;
}

const char* wakeName() {
  switch (esp_sleep_get_wakeup_cause()) {
    case ESP_SLEEP_WAKEUP_GPIO:
    case ESP_SLEEP_WAKEUP_EXT0:
    case ESP_SLEEP_WAKEUP_EXT1: return x4ambient::wakeName(x4ambient::Wake::Button);
    case ESP_SLEEP_WAKEUP_TIMER: return x4ambient::wakeName(x4ambient::Wake::Timer);
    default: return x4ambient::wakeName(x4ambient::Wake::Boot);
  }
}

void text(int16_t x, int16_t y, int16_t w, const char* s, bool bold = false, ui::TextAlign a = ui::TextAlign::Left) {
  ui::TextStyle st;
  st.bold = bold;
  st.align = a;
  g_target->text(ui::Rect{x, y, w, 40}, s, st);
}

void drawSplash() {
  display.clearScreen(0xFF);
  text(24, 130, 752, "alicenet-x4", true);
  text(24, 180, 752, "Connecting for the first card...");
  text(24, 230, 752, "No successful frame yet. Gateway/Wi-Fi may be unavailable.");
  text(24, 400, 752, "Back+Up at wake: CrossInk. Hold Power: off.");
  display.displayBuffer(EInkDisplay::FULL_REFRESH);
}

[[noreturn]] void enterSleep(bool manual, const char* why) {
  const auto policy = x4ambient::sleepPolicy(manual);
  Serial.printf("[x4] %s: %s\n", manual ? "manual off" : "ambient sleep (panel may be stale)", why);
  if (policy.redraw) {
    display.clearScreen(0xFF);
    text(24, 180, 752, "alicenet-x4 is off", true, ui::TextAlign::Center);
    text(24, 280, 752, "Hold Power to wake. Back+Up while waking: CrossInk", false, ui::TextAlign::Center);
    display.displayBuffer(EInkDisplay::HALF_REFRESH);
  }
  display.deepSleep(); // controller sleep only; no framebuffer refresh
  WiFi.disconnect(true, false); // wifiOff=true, eraseAP=false
  WiFi.mode(WIFI_OFF);
  Serial.flush();
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  if (policy.timer) esp_sleep_enable_timer_wakeup(static_cast<uint64_t>(g_nextPollS) * 1000000ULL);
  // SDK holdRailOff pattern, but ambient holds the X4 GPIO13 supply latch HIGH.
  // BoardConfig::holdPowerRails() releases this pad hold first on every wake.
  for (const int8_t pin : {BoardConfig::ACTIVE.power.latch0, BoardConfig::ACTIVE.power.latch1}) {
    if (pin < 0 || BoardConfig::latchConflictsWithBus(pin)) continue;
    const auto latch = static_cast<gpio_num_t>(pin);
    gpio_hold_dis(latch);
    gpio_set_direction(latch, GPIO_MODE_OUTPUT);
    gpio_set_level(latch, policy.latchHigh ? HIGH : LOW);
    gpio_hold_en(latch);
  }
  freeink::PowerManager::powerDownRailsForSleep();
  freeink::PowerManager::waitForPowerButtonRelease();
  freeink::PowerManager::armPowerButtonWakeup();
  freeink::PowerManager::deepSleep(); // SDK isolate/hold-enable + abort recovery
}

[[noreturn]] void ambientSleep(const char* why) { enterSleep(false, why); }
[[noreturn]] void powerOff(const char* why) { enterSleep(true, why); }

}  // namespace

void setup() {
  BoardConfig::holdPowerRails();
  gpio_deep_sleep_hold_dis(); // clear global deep-sleep hold after asserting supply
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
  // No retained framebuffer across deep sleep. Fetch unconditionally each wake;
  // never claim a persisted ETag proves the physical panel is the current card.
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
  g_events = xQueueCreate(32, sizeof(ButtonEvent));
  g_activity.store(millis());
  if (!g_events || xTaskCreate(inputConsumer, "x4_input", 3072, nullptr, 1, nullptr) != pdPASS)
    ambientSleep("input consumer unavailable");

  g_target = new ui::DisplayTarget(display.getFrameBuffer(), display.getDisplayWidth(), display.getDisplayHeight(),
                                   display.getDisplayWidthBytes(), ui::Orientation::LandscapeCounterClockwise);
  if (g_panelState.splash(g_prefs.getBool("had-frame", false))) drawSplash();
  else Serial.println("[x4] retaining unknown/stale physical panel until a complete frame arrives");
  if (connectWifi()) {
    if (!fetchFrame(g_wake)) Serial.println("[x4] initial fetch failed; leaving cached panel untouched");
  } else Serial.println("[x4] wifi unavailable; leaving cached panel untouched");
  g_lastPoll = millis();
  // loop drains inputs received during Wi-Fi/fetch before considering sleep.
}

void loop() {
  if (g_manualOff.load()) powerOff("power held");
  g_session.activity(g_activity.load());
  ButtonEvent ev;
  // One event per loop avoids starving expiry/polling on a busy queue.
  if (xQueueReceive(g_events, &ev, 0) == pdTRUE) {
    postEvent(ev.button, ev.press == x4ambient::Press::Long ? "long" : "short", "button");
    // Use input-consumer time, not request/refresh completion, for a full session.
    g_session.activity(g_activity.load());
  }
  if (g_manualOff.load()) powerOff("power held");
  if (g_session.expired(millis()) && !g_buttonsDown.load() && uxQueueMessagesWaiting(g_events) == 0)
    ambientSleep("session complete or offline");
  if (g_wifi && uint32_t(millis() - g_lastPoll) >= kSessionPollMs) {
    fetchFrame("session"); // ordinary conditional GET, never a blocking long-poll
    g_lastPoll = millis();
  }
  delay(10);
}
