"""Hardware-independent safety/wiring guards; physical behavior still needs X4."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'src/main.cpp').read_text()
INI = (ROOT / 'platformio.ini').read_text()

class AmbientWiring(unittest.TestCase):
    def test_ambient_and_manual_sleep_are_separate(self):
        self.assertIn('void ambientSleep(', SRC)
        self.assertIn('x4ambient::sleepPolicy(manual)', SRC)
        self.assertIn('policy.latchHigh ? HIGH : LOW', SRC)
        self.assertIn('if (policy.timer)', SRC)
    def test_no_concurrent_input_update(self):
        self.assertNotIn('input.update(', SRC)
        self.assertIn('input.beginAsync()', SRC)
        self.assertIn('xTaskCreate(inputConsumer', SRC)
    def test_bounded_reads_and_cached_panel(self):
        self.assertNotIn('readBytes', SRC)
        self.assertIn('kHttpTransactionMs', SRC)
        self.assertIn('g_panelState.fullRefresh()', SRC)
        self.assertIn('g_panelState.conditional()', SRC)
        self.assertIn('g_panelState.splash(', SRC)
    def test_credentials_and_recovery_preserved(self):
        self.assertIn('WiFi.persistent(false)', SRC)
        self.assertIn('WiFi.disconnect(true, false)', SRC)
        self.assertNotIn('nvs_flash_erase', SRC)
        self.assertNotIn('g_prefs.clear(', SRC)
        self.assertIn('freeink::recovery::checkBootCombo()', SRC)
        self.assertIn('CROSSPOINT-BOARD-V1:x4;', SRC)
        self.assertIn('REFUSED: pio upload', INI)
    def test_input_consumer_precedes_splash(self):
        setup = SRC[SRC.index('void setup()'):]
        self.assertLess(setup.index('xTaskCreate(inputConsumer'), setup.index('drawSplash()'))
    def test_full_network_budget_and_no_dns(self):
        self.assertIn('address.fromString(X4_GATEWAY_HOST)', SRC)
        self.assertIn('x4http::writeExact', SRC)
        self.assertNotIn('client.printf(', SRC)
        fetch = SRC[SRC.index('bool fetchFrame('):SRC.index('bool postEvent(')]
        self.assertLess(fetch.index('readStart = millis()'), fetch.index('connectGateway('))
    def test_release_version(self):
        self.assertIn('0.3.0-ambient', INI)
if __name__ == '__main__':
    unittest.main()
