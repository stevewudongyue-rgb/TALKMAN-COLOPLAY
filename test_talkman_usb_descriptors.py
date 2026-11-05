"""Fake USB devices: no native descriptor requests, drivers or real hardware."""
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from talkman_airdata import BUTTONS, PING, PONG, STATE, HEADER, Frame, packet
from talkman_coloplay_protocol import validate_config, config_id
from talkman_coloplay_usb import UsbWire, VID, PID


class Interface:
    bInterfaceNumber = 3
    bInterfaceClass = 255

    @property
    def iInterface(self):
        raise AssertionError('Device string descriptors must not be requested')

    def __init__(self, incoming=True):
        self.endpoints = [SimpleNamespace(bEndpointAddress=3, bmAttributes=2)]
        if incoming:
            self.endpoints.append(SimpleNamespace(bEndpointAddress=0x83, bmAttributes=2))

    def __iter__(self):
        return iter(self.endpoints)


class Device:
    def __init__(self, wire, version=2, paired=True, incoming=True, build=66):
        self.wire, self.version, self.paired = wire, version, paired
        self.interface = Interface(incoming)
        self.writes = []
        self.build = build

    @property
    def langids(self):
        raise ValueError('The device has no langid')

    def get_active_configuration(self):
        return [self.interface]

    def write(self, endpoint, data, **kwargs):
        assert endpoint == 3
        _, kind, priority, length, seq, tag, sub, _ = HEADER.unpack_from(data)
        self.writes.append(Frame(kind, priority, seq, tag, sub, data[HEADER.size:]))
        return len(data)

    def read(self, endpoint, size, **kwargs):
        assert endpoint == 0x83
        self.wire.stop.set()  # One iteration is enough; do not block on real time.
        ping = next(f for f in self.writes if f.kind == PING)
        payload = struct.pack('<HH', self.version, ping.seq) + bytes(34)
        message = dict(v=1, token=self.wire.config['token'] if self.paired else 'wrong',
                       config=config_id(self.wire.config), seq=1, mask=0, frame=-1, device_build=self.build)
        return packet(PONG, payload) + packet(BUTTONS, json.dumps(message).encode(), priority=1)


class DescriptorTests(unittest.TestCase):
    def run_wire(self, **options):
        with patch('threading.Thread.start'):
            wire = UsbWire(validate_config({}))
        device = Device(wire, **options)
        wire.publish(dict(ready=True, phase='idle'), ('test',))
        with patch('usb.core.find', return_value=[device]) as find, \
             patch('libusb_package.get_libusb1_backend', return_value=object()), \
             patch('usb.util.get_string', side_effect=ValueError('The device has no langid')) as string, \
             patch('usb.util.claim_interface') as claim, \
             patch('usb.util.release_interface') as release, \
             patch('usb.util.dispose_resources') as dispose:
            wire.run()
        self.assertEqual(find.call_args.kwargs['idVendor'], VID)
        self.assertEqual(find.call_args.kwargs['idProduct'], PID)
        string.assert_not_called()
        dispose.assert_called_once_with(device)
        events = []
        while not wire.events.empty():
            events.append(wire.events.get_nowait()[:2])
        return wire, device, events, claim, release

    def test_no_language_table_connects_v2_and_paired_66(self):
        wire, device, events, claim, release = self.run_wire()
        self.assertTrue(wire.v2 and wire.paired)
        self.assertIn(('device_build', 66), events)
        self.assertFalse(any(kind == 'error' for kind, _ in events))
        self.assertTrue(any(frame.kind == STATE for frame in device.writes))
        claim.assert_called_once_with(device, 3)
        release.assert_called_once_with(device, 3)
        self.assertTrue(all(frame.kind in (PING, STATE) for frame in device.writes))

    def test_old_protocol_does_not_accept_buttons_or_publish_state(self):
        wire, device, events, _, _ = self.run_wire(version=1)
        self.assertFalse(wire.v2 or wire.paired)
        self.assertFalse(any(frame.kind == STATE for frame in device.writes))
        self.assertFalse(any(kind in ('buttons', 'wake') for kind, _ in events))

    def test_legacy_67_unsolicited_wake_still_connects(self):
        wire, device, events, _, _ = self.run_wire(build=67)
        self.assertTrue(wire.v2 and wire.paired)
        self.assertIn(('device_build', 67), events)
        self.assertTrue(any(frame.kind == STATE for frame in device.writes))

    def test_wrong_pairing_does_not_publish_state(self):
        wire, device, _, _, _ = self.run_wire(paired=False)
        self.assertTrue(wire.v2)
        self.assertFalse(wire.paired)
        self.assertFalse(any(frame.kind == STATE for frame in device.writes))

    def test_missing_bulk_input_rejected_before_claim(self):
        _, device, events, claim, release = self.run_wire(incoming=False)
        claim.assert_not_called()
        release.assert_not_called()
        self.assertFalse(device.writes)
        self.assertTrue(any(kind == 'error' and '双向数据' in value for kind, value in events))
