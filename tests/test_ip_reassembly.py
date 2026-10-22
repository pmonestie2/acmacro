import struct
import unittest
from protocol.ip_reassembly import IPv4Reassembler


def fragment(payload,offset=0,more=False,identity=1):
    header=bytearray(20); header[0]=0x45; header[9]=17
    header[12:16]=bytes([1,2,3,4]); header[16:20]=bytes([5,6,7,8])
    struct.pack_into('!HHH',header,2,20+len(payload),identity,(0x2000 if more else 0)|(offset//8))
    return bytes(header)+payload


class ReassemblyTests(unittest.TestCase):
    def test_out_of_order_and_duplicate_fragments_reconstruct_exact_payload(self):
        r=IPv4Reassembler()
        self.assertIsNone(r.feed(fragment(b'ijklmnop',8,True),now=0))
        self.assertIsNone(r.feed(fragment(b'ijklmnop',8,True),now=1))
        self.assertIsNone(r.feed(fragment(b'qrst',16),now=1))
        result=r.feed(fragment(b'abcdefgh',0,True),now=2)
        self.assertEqual(result[20:],b'abcdefghijklmnopqrst')
        self.assertEqual(struct.unpack_from('!H',result,6)[0],0)
        self.assertEqual(r.completed,1)

    def test_conflicting_overlap_is_rejected(self):
        r=IPv4Reassembler()
        r.feed(fragment(b'abcdefghABCDEFGH',0,True),now=0)
        self.assertIsNone(r.feed(fragment(b'xxxxxxxx',8),now=1))
        self.assertEqual(r.rejected,1)

    def test_expiry_capacity_and_datagram_identity_are_bounded(self):
        r=IPv4Reassembler(ttl=2,max_pending=2)
        for identity in range(3): r.feed(fragment(b'abcdefgh',0,True,identity),now=0)
        self.assertEqual(len(r.pending),2)
        r.feed(fragment(b'ijkl',8,False,3),now=3)
        self.assertEqual(len(r.pending),1)
        self.assertEqual(r.completed,0)

    def test_unfragmented_packet_is_unchanged(self):
        packet=fragment(b'abcdefgh')
        self.assertEqual(IPv4Reassembler().feed(packet),packet)
