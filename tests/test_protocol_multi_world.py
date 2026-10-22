import struct
import unittest

from protocol.capture_world import ProxyWorld, contact_positions


def movement(token):
    return struct.pack('<HHIIfffI', 28, 0x1004, token, 1, 1000, 2000, 0, 0)


def named_object():
    body = bytearray(48)
    struct.pack_into('<I', body, 0, 99)
    struct.pack_into('<H', body, 46, 8)
    inner = struct.pack('<HH', 60, 0x2019) + body + b'Asteroid'
    return struct.pack('<HHII', len(inner) + 12, 0x2016, 123, 1) + inner


def packet(payload, outgoing=False, remote='1.2.3.4:3522'):
    return {'source': '192.168.1.2:1000' if outgoing else remote,
            'destination': remote if outgoing else '192.168.1.2:1000', 'payload': payload}


class MultiWorldTests(unittest.TestCase):
    def test_contact_export_keeps_sector_and_does_not_assume_hostility(self):
        obj = {'id': '0x1', 'movement_observed': True, 'raw_xyz': [1000, 2000, 0], 'position_seen': 5}
        scenery = {'id': '0x2', 'raw_xyz': [0, 0, 0]}
        data = {'session': {'epoch': 1, 'recent': {'sector': 'Saturn'}, 'catalog': [obj, scenery],
                            'previous_epochs': [{'epoch': 0, 'sector': 'ABA', 'objects': [obj]}]}}
        result = contact_positions(data)
        self.assertEqual(len(result), 2)
        self.assertEqual([r['sector'] for r in result], ['Saturn', 'ABA'])
        self.assertEqual(result[0]['map_xyz'], [1, 2, 0])
        self.assertEqual(result[0]['hostility'], 'unknown')

    def test_separate_proxy_catalogs_do_not_reset_each_others_tokens(self):
        first, second = ProxyWorld({'Id': 1}), ProxyWorld({'Id': 2})
        first.observe(packet(movement(123), True), True, 10)
        first.observe(packet(named_object()), False, 11)
        second.observe(packet(movement(456), True), True, 12)
        self.assertEqual(first.world.token, 123)
        self.assertEqual(second.world.token, 456)
        self.assertEqual(first.world.objects['0x00000063']['name'], 'Asteroid')
        self.assertFalse(second.world.objects)

    def test_session_change_preserves_previous_id_catalog(self):
        world = ProxyWorld({'Id': 1})
        world.observe(packet(movement(123), True), True, 10)
        world.observe(packet(named_object()), False, 11)
        world.observe(packet(movement(456), True), True, 12)
        self.assertFalse(world.world.objects)
        self.assertEqual(world.archives[0]['objects'][0]['name'], 'Asteroid')
        self.assertEqual(world.epoch, 1)
        self.assertFalse(world.transitions[0]['confirmed_sector_change'])

    def test_remote_endpoint_change_is_evidence_not_confirmed_sector(self):
        world = ProxyWorld({'Id': 1})
        world.observe(packet(named_object()), False, 10)
        world.observe(packet(named_object(), remote='1.2.3.4:3523'), False, 11)
        self.assertEqual(len(world.transitions), 1)
        self.assertEqual(world.transitions[0]['kind'], 'remote_endpoint_changed')
        self.assertFalse(world.transitions[0]['confirmed_sector_change'])
