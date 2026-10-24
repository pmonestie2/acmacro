//! Wire-compatible Westwood RSA/RC4. These fixed legacy key constants are public
//! protocol material in the upstream server, not production account credentials.
use crate::protocol::{invalid, Frame, MAX_FRAME};
use num_bigint::BigUint;
use std::io::{self, Read, Write};
use std::net::TcpStream;

const MODULUS: &str = "10385578014804950221065190195736491193847541479389728420426514083771326945639729736695791225573893793119489336012297845146104637691941242485732839277543427";
const PRIVATE_EXPONENT: &str = "10088847214381951643320470475858305731166183151407164751271470824235003318621252307969752086088076499395823874814123350292603347408732347765156628342107995";

pub struct Rc4 {
    state: [u8; 256],
    x: u8,
    y: u8,
}
impl Rc4 {
    pub fn new(key: &[u8]) -> io::Result<Self> {
        if key.is_empty() || key.len() > 256 {
            return Err(invalid("invalid RC4 key length"));
        }
        let mut state = [0u8; 256];
        for (i, value) in state.iter_mut().enumerate() {
            *value = i as u8;
        }
        let mut j = 0u8;
        for i in 0..256 {
            j = j.wrapping_add(state[i]).wrapping_add(key[i % key.len()]);
            state.swap(i, j as usize);
        }
        Ok(Self { state, x: 0, y: 0 })
    }
    pub fn transform(&mut self, data: &mut [u8]) {
        for value in data {
            self.x = self.x.wrapping_add(1);
            self.y = self.y.wrapping_add(self.state[self.x as usize]);
            self.state.swap(self.x as usize, self.y as usize);
            *value ^= self.state
                [self.state[self.x as usize].wrapping_add(self.state[self.y as usize]) as usize];
        }
    }
}

pub struct EncryptedConnection {
    pub stream: TcpStream,
    incoming: Rc4,
    outgoing: Rc4,
}
impl EncryptedConnection {
    pub fn accept(mut stream: TcpStream) -> io::Result<Self> {
        let modulus = BigUint::parse_bytes(MODULUS.as_bytes(), 10)
            .ok_or_else(|| invalid("invalid built-in modulus"))?;
        let exponent = BigUint::parse_bytes(PRIVATE_EXPONENT.as_bytes(), 10)
            .ok_or_else(|| invalid("invalid built-in exponent"))?;
        let mut public = Vec::from(65u32.to_be_bytes());
        public.push(0);
        public.extend(modulus.to_bytes_be());
        public.extend_from_slice(&1u32.to_be_bytes());
        public.push(35);
        stream.write_all(&public)?;
        let mut length = [0; 4];
        stream.read_exact(&mut length)?;
        let size = u32::from_be_bytes(length) as usize;
        if size != 64 && size != 65 {
            return Err(invalid("invalid RSA session key block size"));
        }
        let mut encrypted = vec![0; size];
        stream.read_exact(&mut encrypted)?;
        if size == 65 && encrypted[0] != 0 {
            return Err(invalid("invalid RSA key leading byte"));
        }
        let cipher = BigUint::from_bytes_be(&encrypted);
        if cipher >= modulus {
            return Err(invalid("RSA key block exceeds modulus"));
        }
        let decoded = cipher.modpow(&exponent, &modulus).to_bytes_be();
        if decoded.len() > 64 {
            return Err(invalid("oversized decrypted session key"));
        }
        let mut block = [0; 64];
        block[64 - decoded.len()..].copy_from_slice(&decoded);
        let mut key = [0; 8];
        for i in 0..8 {
            key[i] = block[63 - i];
        }
        Ok(Self {
            stream,
            incoming: Rc4::new(&key)?,
            outgoing: Rc4::new(&key)?,
        })
    }
    pub fn read(&mut self) -> io::Result<Frame> {
        let mut header = [0; 4];
        self.stream.read_exact(&mut header)?;
        self.incoming.transform(&mut header);
        let size = u16::from_le_bytes([header[0], header[1]]) as usize;
        if !(4..=MAX_FRAME).contains(&size) {
            return Err(invalid("invalid encrypted frame length"));
        }
        let mut body = vec![0; size - 4];
        self.stream.read_exact(&mut body)?;
        self.incoming.transform(&mut body);
        Ok(Frame::new(u16::from_le_bytes([header[2], header[3]]), body))
    }
    pub fn write(&mut self, frame: &Frame) -> io::Result<()> {
        let mut encoded = frame.encode()?;
        self.outgoing.transform(&mut encoded);
        self.stream.write_all(&encoded)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn standard_rc4_vector_and_continuous_state() {
        let mut cipher = Rc4::new(b"Key").unwrap();
        let mut a = b"Plain".to_vec();
        let mut b = b"text".to_vec();
        cipher.transform(&mut a);
        cipher.transform(&mut b);
        a.extend(b);
        assert_eq!(a, [0xbb, 0xf3, 0x16, 0xe8, 0xd9, 0x40, 0xaf, 0x0a, 0xd3]);
    }
}
