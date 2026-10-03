import base64
import struct
import unittest


def fnv1a(data):
    h = 2166136261
    for b in data:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def encode(password, mac):
    payload = b"CPV1" + struct.pack("<I", fnv1a(mac + password.encode())) + password.encode()
    return base64.b64encode(bytes(b ^ mac[i % 6] for i, b in enumerate(payload))).decode()


def decode(encoded, mac):
    payload = bytearray(base64.b64decode(encoded))
    for i in range(len(payload)):
        payload[i] ^= mac[i % 6]
    if payload[:4] != b"CPV1":
        return None
    expected = struct.unpack_from("<I", payload, 4)[0]
    password = bytes(payload[8:])
    return password.decode() if fnv1a(mac + password) == expected else None


class WifiCredentialCompatTest(unittest.TestCase):
    def test_crossink_validated_payload_round_trip(self):
        mac = bytes.fromhex("9ccc0161f230")
        encoded = encode("test-pass", mac)
        self.assertEqual(decode(encoded, mac), "test-pass")

    def test_wrong_device_mac_rejects_checksum(self):
        encoded = encode("test-pass", bytes.fromhex("9ccc0161f230"))
        self.assertIsNone(decode(encoded, bytes.fromhex("9ccc0161f231")))


if __name__ == "__main__":
    unittest.main()
