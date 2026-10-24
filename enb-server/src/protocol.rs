use serde::{Deserialize, Serialize};
use std::io::{self, Read, Write};

pub const SECTOR_PORT: u16 = 3501;

pub const MAX_FRAME: usize = 32_767;
pub const UDP_HEADER: usize = 12;
pub const MTU_PAYLOAD: usize = 1_400;

pub fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub opcode: u16,
    pub body: Vec<u8>,
}

impl Frame {
    pub fn new(opcode: u16, body: Vec<u8>) -> Self {
        Self { opcode, body }
    }

    pub fn encode(&self) -> io::Result<Vec<u8>> {
        let size = self
            .body
            .len()
            .checked_add(4)
            .filter(|n| *n <= MAX_FRAME)
            .ok_or_else(|| invalid("frame exceeds the supported wire limit"))?;
        let mut data = Vec::with_capacity(size);
        data.extend_from_slice(&(size as u16).to_le_bytes());
        data.extend_from_slice(&self.opcode.to_le_bytes());
        data.extend_from_slice(&self.body);
        Ok(data)
    }

    pub fn decode(data: &[u8]) -> io::Result<Self> {
        let mut reader = Reader::new(data);
        let size = reader.u16()? as usize;
        let opcode = reader.u16()?;
        if size != data.len() || !(4..=MAX_FRAME).contains(&size) {
            return Err(invalid("invalid frame length"));
        }
        Ok(Self::new(opcode, reader.rest().to_vec()))
    }

    pub fn read(connection: &mut impl Read) -> io::Result<Self> {
        let mut header = [0; 4];
        connection.read_exact(&mut header)?;
        let size = u16::from_le_bytes([header[0], header[1]]) as usize;
        if !(4..=MAX_FRAME).contains(&size) {
            return Err(invalid("invalid TCP frame length"));
        }
        let mut body = vec![0; size - 4];
        connection.read_exact(&mut body)?;
        Ok(Self::new(u16::from_le_bytes([header[2], header[3]]), body))
    }

    pub fn write(&self, connection: &mut impl Write) -> io::Result<()> {
        connection.write_all(&self.encode()?)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Datagram {
    pub opcode: u16,
    pub player: u32,
    pub sequence: u32,
    pub body: Vec<u8>,
}

impl Datagram {
    pub fn decode(data: &[u8]) -> io::Result<Self> {
        let mut reader = Reader::new(data);
        let size = reader.u16()? as usize;
        let opcode = reader.u16()?;
        let player = reader.u32()?;
        let sequence = reader.u32()?;
        if size != data.len() || size > 65_507 {
            return Err(invalid("invalid UDP length"));
        }
        Ok(Self {
            opcode,
            player,
            sequence,
            body: reader.rest().to_vec(),
        })
    }

    pub fn encode(&self) -> io::Result<Vec<u8>> {
        let size = self
            .body
            .len()
            .checked_add(UDP_HEADER)
            .filter(|n| *n <= 65_507)
            .ok_or_else(|| invalid("UDP payload too large"))?;
        let mut data = Vec::with_capacity(size);
        data.extend_from_slice(&(size as u16).to_le_bytes());
        data.extend_from_slice(&self.opcode.to_le_bytes());
        data.extend_from_slice(&self.player.to_le_bytes());
        data.extend_from_slice(&self.sequence.to_le_bytes());
        data.extend_from_slice(&self.body);
        Ok(data)
    }
}

pub struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, offset: 0 }
    }
    pub fn rest(&self) -> &'a [u8] {
        &self.data[self.offset..]
    }
    pub fn take(&mut self, size: usize) -> io::Result<&'a [u8]> {
        let end = self
            .offset
            .checked_add(size)
            .filter(|end| *end <= self.data.len())
            .ok_or_else(|| invalid("truncated packet field"))?;
        let data = &self.data[self.offset..end];
        self.offset = end;
        Ok(data)
    }
    pub fn u16(&mut self) -> io::Result<u16> {
        let b = self.take(2)?;
        Ok(u16::from_le_bytes([b[0], b[1]]))
    }
    pub fn u32(&mut self) -> io::Result<u32> {
        let b = self.take(4)?;
        Ok(u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }
    pub fn be_u32(&mut self) -> io::Result<u32> {
        let b = self.take(4)?;
        Ok(u32::from_be_bytes([b[0], b[1], b[2], b[3]]))
    }
    pub fn f32(&mut self) -> io::Result<f32> {
        let value = f32::from_bits(self.u32()?);
        if !value.is_finite() {
            return Err(invalid("non-finite coordinate"));
        }
        Ok(value)
    }
    pub fn string(&mut self) -> io::Result<String> {
        let size = self.u16()? as usize;
        let value = self.take(size)?;
        let text = std::str::from_utf8(value).map_err(|_| invalid("invalid text field"))?;
        Ok(text.trim_end_matches('\0').to_owned())
    }
}

pub fn string(data: &mut Vec<u8>, value: &str) {
    data.extend_from_slice(&(value.len() as u16).to_le_bytes());
    data.extend_from_slice(value.as_bytes());
}

pub fn split_frames(
    player: u32,
    sequence: &mut u32,
    frames: &[Frame],
) -> io::Result<Vec<Datagram>> {
    let mut packets = Vec::new();
    let mut pending = Vec::new();
    for frame in frames {
        let encoded = frame.encode()?;
        if !pending.is_empty() && pending.len() + encoded.len() > MTU_PAYLOAD {
            packets.push(Datagram {
                opcode: 0x2016,
                player,
                sequence: *sequence,
                body: std::mem::take(&mut pending),
            });
            *sequence += 1;
        }
        if encoded.len() > MTU_PAYLOAD {
            for (index, chunk) in encoded.chunks(MTU_PAYLOAD).enumerate() {
                packets.push(Datagram {
                    opcode: if index == 0 { 0x2016 } else { 0x201a },
                    player,
                    sequence: *sequence,
                    body: chunk.to_vec(),
                });
                *sequence += 1;
            }
        } else {
            pending.extend_from_slice(&encoded);
        }
    }
    if !pending.is_empty() {
        packets.push(Datagram {
            opcode: 0x2016,
            player,
            sequence: *sequence,
            body: pending,
        });
        *sequence += 1;
    }
    Ok(packets)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_every_truncation() {
        let data = Datagram {
            opcode: 2,
            player: 7,
            sequence: 1,
            body: vec![1; 40],
        }
        .encode()
        .unwrap();
        for end in 0..data.len() {
            assert!(Datagram::decode(&data[..end]).is_err());
        }
        assert_eq!(Datagram::decode(&data).unwrap().player, 7);
    }
    #[test]
    fn large_aux_is_split_and_sequence_is_monotonic() {
        let frames = vec![Frame::new(0x1b, vec![5; 4000]), Frame::new(5, vec![1; 4])];
        let mut sequence = 0;
        let packets = split_frames(7, &mut sequence, &frames).unwrap();
        assert_eq!(
            packets.iter().map(|p| p.opcode).collect::<Vec<_>>(),
            [0x2016, 0x201a, 0x201a, 0x2016]
        );
        let joined = packets
            .iter()
            .flat_map(|p| p.body.clone())
            .collect::<Vec<_>>();
        assert_eq!(
            joined,
            frames
                .iter()
                .flat_map(|f| f.encode().unwrap())
                .collect::<Vec<_>>()
        );
    }
    #[test]
    fn hostile_lengths_are_rejected() {
        assert!(Frame::decode(&[0, 0, 5, 0]).is_err());
        assert!(Frame::new(1, vec![0; MAX_FRAME]).encode().is_err());
        assert!(Reader::new(&f32::NAN.to_le_bytes()).f32().is_err());
    }
}
